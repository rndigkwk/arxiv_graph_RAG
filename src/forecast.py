"""앞으로 함께 인용될 논문을 예측합니다. `graph-text-gbm`이 처음으로 쓰이는 자리입니다.

**관련 논문 패널과 다른 질문입니다.** 관련 논문은 "지금 같이 읽히는 논문"(공동 인용자 수)을
보여 주고, 여기는 "**앞으로** 함께 인용될 논문"을 묻습니다. 둘을 한 패널에 섞으면 무엇을 본
것인지 말할 수 없어서 기능을 따로 둡니다([ADR-032](docs/adr/ADR-032-close-the-model-lifecycle.md)).

## 왜 모델이 여기서는 쓸모가 있는가

이 프로젝트는 모델을 여섯 번 기각했고 이유가 대부분 같았습니다 — **정답의 정의를 Cypher가
그대로 계산**합니다([ADR-030](docs/adr/ADR-030-related-papers-needs-a-query-not-a-model.md)).
미래는 그렇지 않습니다. 어떤 질의도 "앞으로 생길 공동인용"을 계산하지 못하고, 그래서
`graph-text-gbm`이 최고 휴리스틱을 **측정된 구간으로** 이깁니다(MRR +0.0818 [+0.0699,
+0.0936], [ADR-031](docs/adr/ADR-031-the-model-that-wins-is-the-forecast.md)).

## 피처는 서빙 시점에 다시 만듭니다

학습 때와 **같은 12개**이고 순서도 같습니다(`FEATURE_NAMES`). 다만 출처가 다릅니다 — 학습은
스냅샷의 시간 창에서 뽑고, 여기는 **지금 그래프**에서 뽑습니다. 그것이 맞습니다: 서빙 시점의
"지금"이 예측의 피처 창이고 미래는 아직 없습니다.

| 피처 | 서빙 시점 출처 |
|---|---|
| 그래프 8개 | 앱이 이미 들고 있는 인용 간선에서 **공동인용 이웃**을 그때그때 만듭니다 |
| `embedding_cosine` | Neo4j에서 시드와 후보의 벡터를 한 번에 읽습니다 |
| 분류 둘 | 앱의 `citation_graph()`가 들고 있습니다 |
| `month_gap` | arxiv_id에서 바로 나옵니다 |

## 넘지 않는 선

**후보 안에서만 재정렬합니다.** 모델이 37,854편을 전부 채점하지 않습니다 - 벡터 최근접과
공동인용 후보를 합쳐 좁힌 뒤 그 안에서 세웁니다. 정답이 후보에 없으면 어떤 재정렬도 못
가져오므로, 후보 수를 응답에 적습니다.

**레지스트리를 조회하지 않습니다.** 추적 서버가 죽으면 앱이 죽습니다(ADR-032). 읽는 것은
파일이고, 레지스트리와 잇는 것은 **지문**입니다.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

SERVING = Path("models/artifacts")
RUNG = "graph-text-gbm"
# 학습과 **같은 순서**여야 합니다. 어긋나면 모델이 조용히 다른 피처를 읽습니다.
FEATURE_NAMES = (
    "common_neighbors",
    "adamic_adar",
    "jaccard",
    "log_preferential_attachment",
    "log_degree_min",
    "log_degree_max",
    "log_citation_indegree_min",
    "log_citation_indegree_max",
    "embedding_cosine",
    "same_primary_category",
    "category_jaccard",
    "month_gap",
)
# 재정렬 후보 수. 평가에서 쓴 값과 같습니다(`evals/related_study.py`의 `GBM_POOL`).
POOL = 50
TOP_K = 5


class WeightsUnavailable(RuntimeError):
    """가중치가 없거나 지문이 맞지 않습니다. 패널은 **조용히 빠지고** 이유를 적습니다."""


def load_card(serving: Path = SERVING) -> dict[str, Any]:
    """What was exported, including the fingerprint the file must still match."""
    card_path = serving / "manifest.json"
    if not card_path.exists():
        raise WeightsUnavailable(
            f"내보낸 가중치 설명이 없습니다: {card_path}. "
            "uv run --extra ml python -m pipeline.model_registry --export"
        )
    return json.loads(card_path.read_text(encoding="utf-8"))


def load_model(serving: Path = SERVING) -> tuple[Any, dict[str, Any]]:
    """Load the exported weights after checking they are the file that was exported.

    **지문을 맞춰 봅니다.** 파일이 바뀌었는데 설명이 그대로면, 앱이 어느 모델을 쓰고 있는지
    말할 수 없게 됩니다. 그 상태로 숫자를 보여 주는 것이 비어 있는 것보다 나쁩니다.
    """
    import hashlib

    card = load_card(serving)
    weights = serving / f"{RUNG}.joblib"
    if not weights.exists():
        raise WeightsUnavailable(f"가중치 파일이 없습니다: {weights}")
    found = hashlib.sha256(weights.read_bytes()).hexdigest()[:16]
    if card.get("digest") and found != card["digest"]:
        raise WeightsUnavailable(
            f"지문이 다릅니다: 설명 {card['digest']}, 파일 {found}. 다시 내보내세요."
        )
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover - 앱 의존성이 빠진 배포
        # sklearn/joblib은 앱 의존성입니다(`pyproject.toml`). 그래도 빠진 환경에서
        # 추적 역추적이 아니라 **읽을 수 있는 사유**가 나오게 둡니다.
        raise WeightsUnavailable(f"가중치를 읽을 라이브러리가 없습니다: {exc}") from exc

    try:
        return joblib.load(weights), card
    except ImportError as exc:  # pragma: no cover - 같은 이유, 역직렬화 쪽
        raise WeightsUnavailable(f"가중치를 역직렬화할 수 없습니다: {exc}") from exc


def month_of(arxiv_id: str) -> int | None:
    """arXiv의 새 형식 id에서 월 번호. 옛 형식은 None입니다."""
    head, _, _ = arxiv_id.partition(".")
    if len(head) != 4 or not head.isdigit():
        return None
    year, month = int(head[:2]), int(head[2:])
    if not 1 <= month <= 12:
        return None
    return (2000 + year) * 12 + month - 1


def cocitation_neighbours(graph: dict[str, Any], arxiv_id: str) -> set[str]:
    """Papers cited together with this one, built from the edges the app already holds.

    앱의 `citation_graph()`는 인용 간선을 색인 배열로 들고 있습니다. 공동인용은 그 위에서
    두 걸음입니다 - 이 논문을 인용한 논문들이 **함께 인용한** 논문들.
    """
    position = graph["index"].get(arxiv_id)
    if position is None:
        return set()
    rows, cols, ids = graph["rows"], graph["cols"], graph["ids"]
    citers = rows[cols == position]
    if not len(citers):
        return set()
    partners = np.unique(cols[np.isin(rows, citers)])
    return {ids[index] for index in partners if index != position}


def cocitation_neighbours_of(graph: dict[str, Any], papers: list[str]) -> dict[str, set[str]]:
    """`cocitation_neighbours`를 여러 논문에 한 번에 셉니다. 정의는 같습니다.

    하나씩 세면 논문마다 간선 전체를 훑습니다. 인용이 많은 시드는 공동인용 후보가 수천 편이라
    패널 한 번에 17초가 걸렸습니다. 희소 행렬 곱 한 번(인용자 → 함께 인용한 논문)으로 바꿉니다.
    """
    from scipy import sparse

    index, ids = graph["index"], graph["ids"]
    size = len(ids)
    known = [paper for paper in dict.fromkeys(papers) if paper in index]
    result: dict[str, set[str]] = {paper: set() for paper in papers}
    if not known:
        return result
    cites = sparse.csr_matrix(
        (np.ones(len(graph["rows"]), dtype=np.int32), (graph["rows"], graph["cols"])),
        shape=(size, size),
    )
    positions = np.array([index[paper] for paper in known])
    together = (cites.T.tocsr()[positions] @ cites).tocsr()
    for row, paper in enumerate(known):
        partners = together.indices[together.indptr[row]:together.indptr[row + 1]]
        result[paper] = {ids[i] for i in partners if i != positions[row]}
    return result


def _features(
    seed: str,
    candidates: list[str],
    *,
    graph: dict[str, Any],
    similarity: dict[str, float],
    neighbours: dict[str, set[str]],
    indegree: dict[str, int],
) -> np.ndarray:
    """The 12 columns, in training order.

    `similarity`는 후보마다 **시드와의 임베딩 내적**입니다. 임베딩이 없는 후보는 빠지고 0이 됩니다.
    """
    matrix = np.zeros((len(candidates), len(FEATURE_NAMES)), dtype=np.float32)
    index = graph["index"]
    seed_neighbours = neighbours.get(seed, set())
    seed_month = month_of(seed)
    seed_position = index.get(seed)
    seed_primary = graph["primary"][seed_position] if seed_position is not None else None
    seed_categories = (
        graph["categories"][seed_position] if seed_position is not None else set()
    )
    for row, paper in enumerate(candidates):
        other = neighbours.get(paper, set())
        shared = seed_neighbours & other
        union = len(seed_neighbours | other)
        adamic = sum(
            1.0 / math.log(len(neighbours.get(node, ())) + 1e-9)
            for node in shared
            if len(neighbours.get(node, ())) > 1
        )
        degrees = sorted((len(seed_neighbours), len(other)))
        citations = sorted((indegree.get(seed, 0), indegree.get(paper, 0)))
        position = index.get(paper)
        primary = graph["primary"][position] if position is not None else None
        categories = graph["categories"][position] if position is not None else set()
        category_union = len(seed_categories | categories)
        month = month_of(paper)
        matrix[row] = (
            len(shared),
            adamic,
            len(shared) / union if union else 0.0,
            math.log1p(degrees[0] * degrees[1]),
            math.log1p(degrees[0]),
            math.log1p(degrees[1]),
            math.log1p(citations[0]),
            math.log1p(citations[1]),
            similarity.get(paper, 0.0),
            float(bool(seed_primary) and seed_primary == primary),
            (
                len(seed_categories & categories) / category_union
                if category_union else 0.0
            ),
            abs(seed_month - month) if seed_month is not None and month is not None else 0.0,
        )
    return matrix


def rank(
    seed: str,
    candidates: list[str],
    *,
    graph: dict[str, Any],
    similarity: dict[str, float],
    indegree: dict[str, int],
    model: Any,
    top_k: int = TOP_K,
) -> list[dict[str, Any]]:
    """Score the candidates and return the top ones, each with what drove the score."""
    pool = [paper for paper in dict.fromkeys(candidates) if paper != seed]
    if not pool:
        return []
    neighbours = cocitation_neighbours_of(graph, [seed, *pool])
    matrix = _features(
        seed, pool, graph=graph, similarity=similarity, neighbours=neighbours,
        indegree=indegree,
    )
    scores = model.score(matrix)
    order = np.argsort(-scores)[:top_k]
    return [
        {
            "arxiv_id": pool[position],
            "score": round(float(scores[position]), 4),
            # 무엇이 점수를 밀었는지 함께 돌려줍니다. 숫자만 보여 주면 되짚을 수 없습니다.
            "shared_cocitations": int(matrix[position][0]),
            "embedding_cosine": round(float(matrix[position][8]), 4),
            "same_primary_category": bool(matrix[position][9]),
        }
        for position in order
    ]
