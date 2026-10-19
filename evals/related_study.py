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
from models.link_prediction.train import parse_month
from pipeline import features
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_index

OUTPUT = Path("reports/related_study.json")
CASES = Path("evals/cases_related.json")
TOP_K = 5
# 11-3절의 격자 탐색이 고른 값입니다. 여기서 다시 탐색하지 않고 그대로 씁니다 - 같은 공간,
# 같은 방법이므로 다시 고르면 평가 시드로 하이퍼파라미터를 고르는 셈이 됩니다.
RIDGE_MULTIPLIER = 0.1
VARIANTS = ("cocited", "ppr", "vector", "adapter_cite", "adapter_cocite", "gbm")
# ADR-031에서 최고 휴리스틱을 **측정된 구간으로** 이긴 유일한 모델입니다. 저장된 가중치를
# 그대로 씁니다 - 여기서 다시 학습하면 평가 시드로 하이퍼파라미터를 고르는 셈입니다.
GBM_MODEL = Path("derived/link_prediction/graph-text-gbm.joblib")
# 재정렬 모델이므로 후보가 필요합니다. 벡터 상위 N과 **보이는 공동인용 후보의 합**을 씁니다 -
# 벡터만 주면 공동인용이 이기는 집단에서 모델이 그 후보를 아예 못 봅니다.
GBM_POOL = 50
# **배포된 경로가 현직입니다.** 2026-10-19까지 `ppr`이었고 ADR-030이 공동인용 우선으로
# 바꿨는데, 이 하네스의 현직은 그대로 `ppr`이었습니다 - 그러면 구간이 "배포된 것을 이기는가"가
# 아니라 "옛 배포를 이기는가"를 답합니다(ADR-034).
INCUMBENT = "cocited"
# `chatbot.COCITED_FETCH`와 같은 값입니다. 배포는 DB에서 `top_k * COCITED_FETCH`만 받아
# 오므로, 한도를 흉내 내지 않으면 **배포가 보지 못하는 후보**로 점수를 매기게 됩니다.
COCITED_FETCH = 4


class Graph:
    """스냅샷에서 만든 인용 구조와 임베딩. Aura도 OpenAI도 부르지 않습니다.

    `feature_cut`을 주면 **그 달 이전에 나온 논문이 만든 인용만** 남깁니다. 미래를 정답으로
    쓰는 과제에서 필수입니다 - 전체 스냅샷에는 라벨 창의 인용자가 들어 있어서, 자르지 않으면
    공동인용 변형이 **정답을 그대로 봅니다**
    ([ADR-035](../docs/adr/ADR-035-the-fill-slice-cannot-be-labelled.md)).

    자르는 기준은 `models/link_prediction/dataset.py`의 `build_windows`와 **같습니다** -
    인용하는 논문의 `month_index`로 가르고, 월을 해석할 수 없는 옛 형식 id는 창에 넣지
    않습니다. 두 곳의 기준이 다르면 같은 W3라고 부르면서 다른 그래프를 쓰게 됩니다.

    어댑터 학습도 이 그래프에서 쌍을 뽑으므로(`pairs_for`), 한 번 자르면 순위와 학습이
    함께 잘립니다.
    """

    def __init__(
        self,
        snap: Any,
        *,
        feature_cut: int | None = None,
        embeddings: Path | None = None,
    ) -> None:
        cache = features.Cache(snap)
        self.ids: list[str] = list(cache.ids)
        self.position: dict[str, int] = dict(cache.position)
        # `embeddings`를 주면 **그 행렬로 바꿔** 잽니다. 다른 임베딩 모델이 이 과제에서
        # 나은지 보려면 그것만 바꾸고 나머지는 같아야 합니다(ADR-039). 행 순서가
        # 파생 캐시와 같아야 하므로 길이를 확인합니다 - 어긋나면 조용히 다른 논문의
        # 벡터로 점수가 나옵니다.
        raw = (
            np.load(embeddings) if embeddings is not None
            else cache.embeddings(mmap=False)
        )
        if raw.shape[0] != len(self.ids):
            raise ValueError(
                f"임베딩 행 수가 말뭉치와 다릅니다: {raw.shape[0]} != {len(self.ids)}"
            )
        self.embedding_source = str(embeddings).replace("\\", "/") if embeddings else None
        self.embedding_dimensions = int(raw.shape[1])
        self.vectors = adapter.unit(np.asarray(raw, dtype=np.float32))
        self.size = len(self.ids)
        self.feature_cut = feature_cut
        cites: dict[str, set[str]] = collections.defaultdict(set)
        cited_by: dict[str, set[str]] = collections.defaultdict(set)
        rows, cols = [], []
        self.dropped_edges = 0
        for row in snap.rows("references"):
            citing, cited = row["citing"], row["cited"]
            if citing not in self.position or cited not in self.position:
                continue
            if feature_cut is not None:
                month = month_index(citing)
                if month is None or month >= feature_cut:
                    self.dropped_edges += 1
                    continue
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


class Reranker:
    """`graph-text-gbm`을 관련 논문 자리에 세웁니다. 쌍 점수 모델이므로 후보를 재정렬합니다.

    **피처는 모델이 학습한 그래프에서 뽑습니다.** `build_windows`의 W3 피처 그래프와
    인용 피인용 수이고, 참고문헌 150편 초과 인용자를 버리는 상한까지 같습니다
    (`MAX_REFERENCES_FOR_COCITATION`). 순위용 `Graph`는 앱과 같게 상한을 두지 않으므로
    둘이 1,377 시드 중 1건에서 다릅니다 - 그 1건의 원인이 상한뿐이라는 것을 테스트가
    확인합니다([ADR-037](../docs/adr/ADR-037-the-gbm-wins-the-average-and-loses-both-halves.md)).

    후보는 **벡터 상위 `pool`편 ∪ 보이는 공동인용 후보**입니다. 모델의 상한이 이 후보
    집합의 recall이므로 그 상한을 보고서에 함께 적습니다 - 적지 않으면 "재정렬이 못 했다"와
    "후보에 없었다"를 구별할 수 없습니다(ADR-029가 같은 구분을 합니다).
    """

    def __init__(self, snap: Any, *, feature_cut: int, pool: int = GBM_POOL) -> None:
        import joblib

        from models.link_prediction.dataset import PaperTable, build_windows
        from models.link_prediction.train import DEFAULT_TRAIN_CUT

        if not GBM_MODEL.exists():
            raise FileNotFoundError(
                f"저장된 가중치가 없습니다: {GBM_MODEL}. "
                "uv run --extra ml python -m models.link_prediction.train 으로 만드세요."
            )
        self.model = joblib.load(GBM_MODEL)
        windows = build_windows(
            snap, train_cut=parse_month(DEFAULT_TRAIN_CUT), test_cut=feature_cut
        )
        self.adjacency = windows.test_feature
        self.indegree = windows.test_indegree
        self.table = PaperTable(snap)
        self.pool = pool
        self.pool_sizes: list[int] = []

    def candidates(self, graph: Graph, seed: str, scores: np.ndarray) -> list[str]:
        order = np.argpartition(-scores, self.pool)[: self.pool]
        pooled = {graph.ids[index] for index in order}
        pooled.update(graph.cocited(seed))
        pooled.discard(seed)
        return sorted(pooled)

    def rank(self, graph: Graph, seed: str, scores: np.ndarray, top_k: int) -> list[str]:
        from models.link_prediction.dataset import pair_features

        pooled = self.candidates(graph, seed, scores)
        self.pool_sizes.append(len(pooled))
        if not pooled:
            return []
        matrix = pair_features(
            [(seed, paper) for paper in pooled], self.adjacency, self.indegree, self.table
        )
        ranked = self.model.score(matrix)
        order = np.argsort(-ranked)[:top_k]
        return [pooled[index] for index in order]


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
    parser.add_argument(
        "--embeddings", type=Path,
        help="파생 캐시 대신 쓸 임베딩 행렬(.npy). 행 순서가 embedding_ids.json과 같아야 합니다.",
    )
    parser.add_argument(
        "--feature-cut",
        help="YYYY-MM. 이 달 이전 인용자가 만든 인용만 씁니다. 정답 파일이 feature_cut을 "
             "적어 두면 그 값을 쓰며, 다르게 주면 거부합니다.",
    )
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = dataset["cases"][: args.limit] if args.limit else dataset["cases"]

    # **정답이 자르는 선을 정합니다.** 미래를 라벨로 쓰는 정답은 `feature_cut`을 적어 두고,
    # 자르지 않은 그래프로 재면 공동인용 변형이 정답을 그대로 봅니다(ADR-035). 그래서 명령줄
    # 인자가 아니라 **정답 파일**이 기본값이고, 둘이 다르면 추측하지 않고 멈춥니다.
    declared = dataset.get("feature_cut")
    if args.feature_cut and declared and args.feature_cut != declared:
        parser.error(
            f"정답이 적어 둔 feature_cut({declared})과 준 값({args.feature_cut})이 다릅니다. "
            "자르는 선을 정답이 정합니다."
        )
    cut_label = declared or args.feature_cut
    feature_cut = parse_month(cut_label) if cut_label else None
    graph = Graph(snap, feature_cut=feature_cut, embeddings=args.embeddings)
    if feature_cut is not None:
        print(f"그래프를 {cut_label} 이전으로 잘랐습니다: 간선 {graph.dropped_edges}개 제외")

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

    reranker = None
    if "gbm" in chosen:
        if feature_cut is None:
            parser.error(
                "gbm 변형은 feature_cut이 필요합니다. 피처 그래프를 자르지 않으면 "
                "모델이 미래를 보고 점수를 냅니다."
            )
        reranker = Reranker(snap, feature_cut=feature_cut)

    per_seed: dict[str, dict[str, list[float]]] = {
        variant: collections.defaultdict(list) for variant in chosen
    }
    # 재정렬 모델의 상한입니다. 후보에 없는 정답은 어떤 재정렬로도 못 가져옵니다.
    pool_ceiling: list[float] = []
    rows: list[dict[str, Any]] = []
    for case in cases:
        seed, gold = case["seed_id"], case["expected_related_ids"]
        vector = graph.vectors[graph.position[seed]]
        scores = {
            name: (
                graph.vectors @ vector if name == "vector"
                else graph.vectors @ adapter.apply(weights[name], vector[None])[0]
            )
            # 벡터 점수가 필요한 변형만입니다. `ppr`과 `cocited`는 그래프에서 나오고,
            # `gbm`은 벡터 점수를 **후보 생성에만** 쓰므로 따로 다룹니다.
            for name in chosen if name not in ("ppr", "cocited", "gbm")
        }
        row: dict[str, Any] = {"id": case["id"], "seed_id": seed, "gold_size": len(gold)}
        # 공동인용 후보가 몇 편인지. 이것이 `top_k`보다 적으면 배포 경로는 **PPR로 채웁니다**
        # (ADR-030: 말뭉치의 17.2%). 전수로 재야 그 집합을 따로 볼 수 있습니다 - 300 시드
        # 표본에서는 그 안의 부분집합이 너무 작았습니다(ADR-034).
        row["cocited_candidates"] = len(graph.cocited(seed)) - 1
        # 시드가 (잘린) 인용망에 **연결돼 있는지**. 0이면 그래프 계열은 점수를 낼 수 없습니다 -
        # PPR 점수가 양수로 나오지 않고 `chatbot.top_by_score`가 0점을 뺍니다. 집계만 보면
        # 그 사실이 평균에 섞여 사라집니다(ADR-036).
        row["citation_degree"] = len(graph.cites.get(seed, ())) + len(graph.cited_by.get(seed, ()))
        # `cocited`도 PPR을 씁니다(동점 끊기와 보충). 시드당 한 번만 계산합니다.
        rank = (
            pagerank.personalized(
                graph.matrix, graph.degree, graph.size, [graph.position[seed]]
            )
            if {"ppr", "cocited"} & set(chosen)
            else None
        )
        if reranker is not None:
            pooled = set(reranker.candidates(graph, seed, graph.vectors @ vector))
            # 사례별로 적어 둡니다. 집단마다 상한이 전혀 다르고(간선 1~4개 집단은 0.99),
            # 그 차이를 모르면 "재정렬이 못 했다"와 "후보에 없었다"를 구별할 수 없습니다.
            row["gbm_pool_recall"] = round(
                len(pooled & set(gold)) / len(gold) if gold else 0.0, 4
            )
            pool_ceiling.append(row["gbm_pool_recall"])
        for variant in chosen:
            if variant == "ppr":
                ranked = rank_by_score(graph, rank, seed, args.top_k)
            elif variant == "cocited":
                ranked = cocited_ranking(graph, seed, rank, args.top_k)
            elif variant == "gbm":
                ranked = reranker.rank(graph, seed, graph.vectors @ vector, args.top_k)
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
    # 연결 정도로 나눠 봅니다. **이것을 적지 않으면 집계가 두 체제를 섞습니다** - 간선이
    # 없는 시드에서 그래프 계열은 0.0000이고 간선이 있는 시드에서는 0.98입니다. 평균 0.18은
    # 그 둘의 가중합이라 어느 쪽도 설명하지 않습니다(ADR-036).
    degree_groups = {
        "no_citation_edges": [row for row in rows if row["citation_degree"] == 0],
        "degree_1_to_4": [row for row in rows if 1 <= row["citation_degree"] < 5],
        "degree_5_or_more": [row for row in rows if row["citation_degree"] >= 5],
    }
    by_degree = {
        name: {
            "seeds": len(group),
            "share": round(len(group) / len(rows), 4) if rows else 0.0,
            **(
                {
                    "gbm_pool_recall_ceiling": round(
                        statistics.fmean(row["gbm_pool_recall"] for row in group), 4
                    )
                }
                if all("gbm_pool_recall" in row for row in group) else {}
            ),
            **{
                variant: {
                    metric: round(
                        statistics.fmean(row[variant][metric] for row in group), 4
                    )
                    for metric in ("recall_at_k", "hit_at_k", "reciprocal_rank")
                }
                for variant in chosen
            },
        }
        for name, group in degree_groups.items()
        if group
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
        # 어느 그래프에서 잰 점수인지. 없으면 전체 스냅샷이고, 미래 라벨 과제에서는
        # 반드시 있어야 합니다(ADR-035).
        "feature_cut": cut_label,
        # 어느 임베딩으로 잰 점수인지. 비어 있으면 파생 캐시(text-embedding-3-large)입니다.
        "embeddings": graph.embedding_source,
        "embedding_dimensions": graph.embedding_dimensions,
        "dropped_edges_outside_feature_window": graph.dropped_edges,
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
        # 간선 수로 나눈 세 지표. 집계보다 이쪽을 먼저 읽어야 합니다.
        "by_degree_slice": by_degree,
        # 재정렬 후보가 정답을 얼마나 담고 있었는지. gbm의 상한입니다.
        "gbm_pool": (
            {
                "size": GBM_POOL,
                "mean_candidates": round(statistics.fmean(reranker.pool_sizes), 1),
                "recall_ceiling": round(statistics.fmean(pool_ceiling), 4),
            }
            if reranker is not None and pool_ceiling else None
        ),
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
