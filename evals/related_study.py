"""관련 논문 슬롯에서 PPR과 어댑터를 같은 정답으로 비교합니다. API를 부르지 않습니다.

    uv run python -m evals.related_study
    uv run python -m evals.related_study --limit 50      # 빨리 확인할 때

**왜.** 앱에서 자체 학습 모델이 들어갈 수 있는 자리는 `rank_related_papers` 하나이고
([ADR-027](../docs/adr/ADR-027-cocitation-gold-for-the-related-slot.md)), 그 자리에는 이미
개인화 PageRank가 있습니다. 외부 검토가 "어댑터를 배포하라"고 했는데 그 자리의 **현직과
붙여 본 적이 없습니다.** 이 비교가 그것을 합니다 — 이기면 올리고, 지면 "PPR이 이겼다"가
또 하나의 측정된 기각입니다.

**에이전트를 거치지 않습니다.** 네 변형 모두 `시드 → 순위` 함수이고, 모델이 PPR의 시드를
무엇으로 할지는 그때그때 다릅니다(ADR-027). 함수를 직접 재면 순위만 재게 됩니다. LLM 호출이
없으므로 300건을 매번 다 돌릴 수 있습니다.

## 변형

| 이름 | 무엇인가 |
|---|---|
| `ppr` | 무방향 인용망의 개인화 PageRank. **앱이 지금 쓰는 것** |
| `vector` | 시드 임베딩의 코사인 최근접. 기준선 |
| `adapter_cite` | 직접 인용으로 학습한 선형 어댑터(ADR-013의 정식화) |
| `adapter_cocite` | **공동인용으로 학습한** 어댑터. 정답과 같은 목표 |

`adapter_cite`를 함께 재는 이유는 ADR-013의 어댑터가 "그 논문이 인용할 논문"으로 학습됐는데
이 정답은 공동인용이어서, 그대로 가져오면 **학습 목표가 다른 모델**을 재게 되기 때문입니다.
둘을 모두 재면 "어댑터가 안 되는가"와 "목표가 달랐는가"를 구별할 수 있습니다.

## 학습·평가 분리

평가 시드 300개는 **학습 앵커에서 제외**합니다. 다만 이것은 **시드 단위 분리**이고,
11-3절이 쓴 **시간 분리**(`arxiv_id_yymm`, 인용하는 논문을 2026-08 이후로 자름)보다
약합니다. 어댑터는 같은 기간의 공동인용 구조를 보게 되므로 그 점수는 **상한**입니다.

그 방향이 결론에 주는 영향을 적어 둡니다 — 어댑터에 유리한 쪽으로 틀린 측정이므로,
"PPR이 이겼다"는 결론은 그만큼 더 강하고 "어댑터가 이겼다"는 결론은 **시간 분리로 다시
확인해야** 합니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

import pagerank
from evals.metrics import score_retrieval
from models.embedding_tuning import adapter
from pipeline import features
from pipeline import snapshot as snapshot_module

OUTPUT = Path("reports/related_study.json")
CASES = Path("evals/cases_related.json")
TOP_K = 5
# 11-3절의 격자 탐색이 고른 값입니다. 여기서 다시 탐색하지 않고 그대로 씁니다 - 같은 공간,
# 같은 방법이므로 다시 고르면 평가 시드로 하이퍼파라미터를 고르는 셈이 됩니다.
RIDGE_MULTIPLIER = 0.1
VARIANTS = ("ppr", "vector", "adapter_cite", "adapter_cocite")
INCUMBENT = "ppr"


class Graph:
    """스냅샷에서 만든 인용 구조와 임베딩. Aura도 OpenAI도 부르지 않습니다."""

    def __init__(self, snap: Any) -> None:
        cache = features.Cache(snap)
        self.ids: list[str] = list(cache.ids)
        self.position: dict[str, int] = dict(cache.position)
        self.vectors = adapter.unit(
            np.asarray(cache.embeddings(mmap=False), dtype=np.float32)
        )
        self.size = len(self.ids)
        cites: dict[str, set[str]] = collections.defaultdict(set)
        cited_by: dict[str, set[str]] = collections.defaultdict(set)
        rows, cols = [], []
        for row in snap.rows("references"):
            citing, cited = row["citing"], row["cited"]
            if citing in self.position and cited in self.position:
                cites[citing].add(cited)
                cited_by[cited].add(citing)
                rows.append(self.position[citing])
                cols.append(self.position[cited])
        self.cites = cites
        self.cited_by = cited_by
        rows = np.asarray(rows, dtype=np.int32)
        cols = np.asarray(cols, dtype=np.int32)
        # 앱과 같은 무방향 그래프입니다(`chatbot.citation_graph`). 인용 방향을 그대로 쓰면
        # 시드가 인용한 쪽으로만 흐릅니다.
        self.matrix, self.degree = pagerank.transition_matrix(
            *pagerank.undirected_pairs(rows, cols), self.size
        )

    def cocited(self, seed: str) -> collections.Counter:
        partners: collections.Counter = collections.Counter()
        for citer in self.cited_by.get(seed, ()):
            for paper in self.cites.get(citer, ()):
                if paper != seed:
                    partners[paper] += 1
        return partners


def rank_by_score(graph: Graph, score: np.ndarray, seed: str, top_k: int) -> list[str]:
    """Top papers by score, with the seed itself removed."""
    usable = score.copy()
    usable[graph.position[seed]] = -np.inf
    order = np.argpartition(-usable, top_k)[:top_k]
    order = order[np.argsort(-usable[order])]
    return [graph.ids[index] for index in order]


def pairs_for(graph: Graph, kind: str, exclude: set[str]) -> tuple[np.ndarray, np.ndarray]:
    """Anchor/positive matrices for one training target, skipping the eval seeds."""
    anchors, positives = [], []
    for seed in graph.ids:
        if seed in exclude:
            continue
        if kind == "cite":
            targets = graph.cites.get(seed, ())
        else:
            targets = [
                paper for paper, shared in graph.cocited(seed).items() if shared >= 2
            ]
        for target in targets:
            anchors.append(graph.vectors[graph.position[seed]])
            positives.append(graph.vectors[graph.position[target]])
    if not anchors:
        raise ValueError(f"{kind} 학습 쌍이 없습니다")
    return np.stack(anchors), np.stack(positives)


def paired_bootstrap(
    baseline: list[float], candidate: list[float], *, samples: int = 2000, seed: int = 20261019
) -> dict[str, float]:
    """Interval for the per-seed difference, resampling seeds in pairs.

    평균이 질의 한두 건만큼 움직인 것은 결과가 아닙니다. 변동의 단위가 시드이므로 시드를
    쌍으로 다시 뽑고 차이에서 구간을 읽습니다(11-3절과 같은 방법).
    """
    diff = np.asarray(candidate, dtype=np.float64) - np.asarray(baseline, dtype=np.float64)
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(diff), size=(samples, len(diff)))
    means = diff[draws].mean(axis=1)
    return {
        "mean": round(float(diff.mean()), 4),
        "low": round(float(np.percentile(means, 2.5)), 4),
        "high": round(float(np.percentile(means, 97.5)), 4),
        "excludes_zero": bool(
            np.percentile(means, 2.5) > 0 or np.percentile(means, 97.5) < 0
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="관련 논문 슬롯에서 변형을 비교합니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부입니다.")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--variants", nargs="+", choices=VARIANTS, default=list(VARIANTS),
                        help="PPR만 재려면 --variants ppr. 어댑터 학습을 건너뛰어 빠릅니다.")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = dataset["cases"][: args.limit] if args.limit else dataset["cases"]
    graph = Graph(snap)

    eval_seeds = {case["seed_id"] for case in cases}
    chosen = tuple(args.variants)
    weights: dict[str, np.ndarray] = {}
    training: dict[str, dict[str, Any]] = {}
    # 어댑터 학습은 114,357쌍의 닫힌 해라 몇 분 걸립니다. 쓰지 않을 변형은 학습하지 않습니다 -
    # 테스트가 PPR만 다시 재서 보고서와 맞춰 보는 데 그 시간을 쓸 이유가 없습니다.
    for kind in (k for k in ("cite", "cocite") if f"adapter_{k}" in chosen):
        anchors, positives = pairs_for(graph, kind, eval_seeds)
        ridge = RIDGE_MULTIPLIER * len(anchors)
        weights[f"adapter_{kind}"] = adapter.fit(anchors, positives, ridge=ridge)
        training[f"adapter_{kind}"] = {
            "pairs": len(anchors),
            "ridge": round(ridge, 1),
            "drift_from_identity": round(
                adapter.drift_from_identity(weights[f"adapter_{kind}"]), 4
            ),
        }

    per_seed: dict[str, dict[str, list[float]]] = {
        variant: collections.defaultdict(list) for variant in chosen
    }
    rows: list[dict[str, Any]] = []
    for case in cases:
        seed, gold = case["seed_id"], case["expected_related_ids"]
        vector = graph.vectors[graph.position[seed]]
        scores = {
            name: (
                graph.vectors @ vector if name == "vector"
                else graph.vectors @ adapter.apply(weights[name], vector[None])[0]
            )
            for name in chosen if name != "ppr"
        }
        row: dict[str, Any] = {"id": case["id"], "seed_id": seed, "gold_size": len(gold)}
        for variant in chosen:
            if variant == "ppr":
                rank = pagerank.personalized(
                    graph.matrix, graph.degree, graph.size, [graph.position[seed]]
                )
                ranked = rank_by_score(graph, rank, seed, args.top_k)
            else:
                ranked = rank_by_score(graph, scores[variant], seed, args.top_k)
            scored = score_retrieval(gold, ranked, k=args.top_k)
            row[variant] = scored
            for metric, value in scored.items():
                per_seed[variant][metric].append(value)
        rows.append(row)

    summary = {
        variant: {
            metric: round(statistics.fmean(values), 4)
            for metric, values in metrics.items()
        }
        for variant, metrics in per_seed.items()
    }
    against = {
        variant: {
            metric: paired_bootstrap(per_seed[INCUMBENT][metric], per_seed[variant][metric])
            for metric in ("recall_at_k", "hit_at_k", "reciprocal_rank")
        }
        for variant in chosen
        if variant != INCUMBENT and INCUMBENT in chosen
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "cases": len(cases),
        "top_k": args.top_k,
        "variants": list(chosen),
        "gold": str(args.cases).replace("\\", "/"),
        "incumbent": INCUMBENT,
        "training": training,
        "split": (
            "평가 시드를 학습 앵커에서 제외한 **시드 단위** 분리입니다. 11-3절의 시간 분리보다 "
            "약하므로 어댑터 점수는 상한입니다 - 어댑터에 유리한 방향의 측정입니다."
        ),
        "summary": summary,
        "vs_incumbent": against,
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {k: report[k] for k in ("cases", "training", "summary", "vs_incumbent")},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
