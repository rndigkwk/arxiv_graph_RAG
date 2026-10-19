"""관련 논문 슬롯에서 PPR과 어댑터를 같은 정답으로 비교합니다. API를 부르지 않습니다.

    uv run python -m evals.related_study
    uv run python -m evals.related_study --limit 50      # 빨리 확인할 때

**왜.** 앱에서 자체 학습 모델이 들어갈 수 있는 자리는 `rank_related_papers` 하나이고
([ADR-027](../docs/adr/ADR-027-cocitation-gold-for-the-related-slot.md)), 그 자리에는 이미
개인화 PageRank가 있습니다. 외부 검토가 "어댑터를 배포하라"고 했는데 그 자리의 **현직과
붙여 본 적이 없습니다.** 이 비교가 그것을 합니다 — 이기면 올리고, 지면 "PPR이 이겼다"가
또 하나의 측정된 기각입니다.

**에이전트를 거치지 않습니다.** 다섯 변형 모두 `시드 → 순위` 함수이고, 모델이 PPR의 시드를
무엇으로 할지는 그때그때 다릅니다(ADR-027). 함수를 직접 재면 순위만 재게 됩니다. LLM 호출이
없으므로 **적격 시드 전부**를 매번 돌립니다 - 3,740 시드에 PPR이 11분이고, 표본을 쓰면
구간에 표본추출 변동이 섞입니다(ADR-034).

## 변형

| 이름 | 무엇인가 |
|---|---|
| `cocited` | 공동 인용자 수 우선, 부족분은 PPR 보충. **앱이 지금 쓰는 것**(ADR-030) |
| `ppr` | 무방향 인용망의 개인화 PageRank. 2026-10-19까지 배포됐던 것 |
| `vector` | 시드 임베딩의 코사인 최근접. 기준선 |
| `adapter_cite` | 직접 인용으로 학습한 선형 어댑터(ADR-013의 정식화) |
| `adapter_cocite` | **공동인용으로 학습한** 어댑터. 정답과 같은 목표 |

`adapter_cite`를 함께 재는 이유는 ADR-013의 어댑터가 "그 논문이 인용할 논문"으로 학습됐는데
이 정답은 공동인용이어서, 그대로 가져오면 **학습 목표가 다른 모델**을 재게 되기 때문입니다.
둘을 모두 재면 "어댑터가 안 되는가"와 "목표가 달랐는가"를 구별할 수 있습니다.

## 학습·평가 분리

평가 시드 전부(3,740개)를 **학습 앵커에서 제외**합니다. 다만 이것은 **시드 단위 분리**이고,
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
VARIANTS = ("cocited", "ppr", "vector", "adapter_cite", "adapter_cocite")
# **배포된 경로가 현직입니다.** 2026-10-19까지 `ppr`이었고 ADR-030이 공동인용 우선으로
# 바꿨는데, 이 하네스의 현직은 그대로 `ppr`이었습니다 - 그러면 구간이 "배포된 것을 이기는가"가
# 아니라 "옛 배포를 이기는가"를 답합니다(ADR-034).
INCUMBENT = "cocited"
# `chatbot.COCITED_FETCH`와 같은 값입니다. 배포는 DB에서 `top_k * COCITED_FETCH`만 받아
# 오므로, 한도를 흉내 내지 않으면 **배포가 보지 못하는 후보**로 점수를 매기게 됩니다.
COCITED_FETCH = 4


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


def fill_by_score(
    graph: Graph, score: np.ndarray, *, limit: int, exclude: set[str]
) -> list[str]:
    """Top papers by score outside `exclude`, dropping non-positive scores.

    `chatbot.top_by_score`와 같은 규칙입니다 - 점수가 0인 논문은 시드와 인용망으로 이어지지
    않은 논문이라 순위에 넣지 않습니다.
    """
    if limit <= 0:
        return []
    usable = score.copy()
    for arxiv_id in exclude:
        if arxiv_id in graph.position:
            usable[graph.position[arxiv_id]] = -1.0
    order = np.argsort(-usable)[:limit]
    return [graph.ids[position] for position in order if usable[position] > 0]


def cocited_ranking(
    graph: Graph, seed: str, score: np.ndarray, top_k: int, *, fetch: int = COCITED_FETCH
) -> list[str]:
    """What `chatbot.rank_related_papers` ranks: co-citation count first, PPR filling the rest.

    배포 코드를 그대로 흉내 냅니다 — 순서가 셋입니다.

    1. 공동 인용자 수 내림차순, 동점은 arxiv_id로 끊어 `top_k * fetch`편만 받습니다(DB의 LIMIT).
    2. 그 안에서 **동점을 PPR로** 다시 끊습니다.
    3. `top_k`에 못 미치면 PPR로 채웁니다 — 말뭉치의 17.2%는 공동인용 후보가 다섯 편이
       안 됩니다(ADR-030).

    **이 변형의 점수는 상한입니다.** 배포 질의에는 `p.embedding IS NOT NULL`이 없어서 임베딩이
    없는 논문도 상위에 올 수 있고, 그 논문은 정답에 들어 있지 않습니다(정답은 어댑터 공정성을
    위해 임베딩 논문으로 한정). 스냅샷에는 임베딩이 있는 논문의 인용만 들어 있으므로
    (참조 간선 69,438개의 100%가 양끝 모두 임베딩) 이 하네스는 그 손실을 볼 수 없습니다.
    """
    counts = graph.cocited(seed)
    counts.pop(seed, None)
    pool = sorted(counts, key=lambda paper: (-counts[paper], paper))[: top_k * fetch]
    ranked = sorted(
        pool, key=lambda paper: (-counts[paper], -float(score[graph.position[paper]]), paper)
    )[:top_k]
    if len(ranked) < top_k:
        ranked = [
            *ranked,
            *fill_by_score(
                graph, score, limit=top_k - len(ranked), exclude={seed, *ranked}
            ),
        ]
    return ranked


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
            # 벡터 점수가 필요한 변형만입니다. `ppr`과 `cocited`는 그래프에서 나옵니다.
            for name in chosen if name not in ("ppr", "cocited")
        }
        row: dict[str, Any] = {"id": case["id"], "seed_id": seed, "gold_size": len(gold)}
        # 공동인용 후보가 몇 편인지. 이것이 `top_k`보다 적으면 배포 경로는 **PPR로 채웁니다**
        # (ADR-030: 말뭉치의 17.2%). 전수로 재야 그 집합을 따로 볼 수 있습니다 - 300 시드
        # 표본에서는 그 안의 부분집합이 너무 작았습니다(ADR-034).
        row["cocited_candidates"] = len(graph.cocited(seed)) - 1
        # `cocited`도 PPR을 씁니다(동점 끊기와 보충). 시드당 한 번만 계산합니다.
        rank = (
            pagerank.personalized(
                graph.matrix, graph.degree, graph.size, [graph.position[seed]]
            )
            if {"ppr", "cocited"} & set(chosen)
            else None
        )
        for variant in chosen:
            if variant == "ppr":
                ranked = rank_by_score(graph, rank, seed, args.top_k)
            elif variant == "cocited":
                ranked = cocited_ranking(graph, seed, rank, args.top_k)
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
    # 공동인용만으로 상위 k를 채울 수 있는 시드와 그렇지 않은 시드를 가릅니다. 후자에서는
    # 배포 경로가 PPR 보충에 기대므로, **남은 여지가 있다면 거기에** 있습니다.
    slices = {
        "cocitation_fills_k": [
            row for row in rows if row["cocited_candidates"] >= args.top_k
        ],
        "ppr_has_to_fill": [
            row for row in rows if row["cocited_candidates"] < args.top_k
        ],
    }
    by_slice = {
        name: {
            "seeds": len(group),
            "share": round(len(group) / len(rows), 4) if rows else 0.0,
            **{
                variant: round(
                    statistics.fmean(row[variant]["recall_at_k"] for row in group), 4
                )
                for variant in chosen
            },
        }
        for name, group in slices.items()
        if group
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
        # 공동인용이 상위 k를 채우는 시드와 PPR이 채워야 하는 시드를 나눠 본 recall@5입니다.
        "by_candidate_slice": by_slice,
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
