"""Small, dependency-free metrics for the local GraphRAG evaluation harness."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from statistics import fmean

RETRIEVAL_METRICS = (
    "hit_at_k",
    "recall_at_k",
    "reciprocal_rank",
    "retrieval_precision",
    "retrieval_recall",
)
# 위 다섯 중 앞 셋은 **순위**를 전제하고 뒤 둘은 전제하지 않습니다. 그 차이가
# `score_retrieval`의 docstring에 측정과 함께 적혀 있습니다.
ANSWER_METRICS = ("answer_correct", "answer_evidence_precision", "answer_evidence_recall")
RELATED_METRICS = ("related_retrieval_precision", "related_retrieval_recall")


def score_retrieval(
    expected_ids: Sequence[str], retrieved_ids: Sequence[str], *, k: int = 5
) -> dict[str, float]:
    """Score retrieval two ways: as a ranking (top-k) and as a set (order-free).

    **앞 셋은 순위를 전제하고, 이 하네스의 그래프 경로는 순위를 만들지 않습니다.**
    2026-10-19에 기준선 3회분을 들여다보고 측정한 것입니다.

    | 무엇 | 측정 |
    |---|---|
    | 답변 조회가 무엇으로 정렬했나 | **식별자 24건**(`title`·`arxiv_id`), 점수 6건 |
    | `retrieved_ids` 집합 == 골드 집합 | **30/30** (초과 0건, 누락 0건) |
    | 3회 실행에서 순서가 같은가 | 30/30 |

    즉 `reciprocal_rank`의 1위는 24건에서 **제목이나 arxiv_id의 첫 글자**가 고른 줄이고,
    목록 전체가 골드이므로 1.0이 나옵니다. 세 지표는 "조회가 골드 집합을 돌려줬다"를 재는
    것이고 순위 품질을 재는 것이 아닙니다. 그래서 **지우지 않고 뜻을 고쳐 적습니다** -
    검색이 깨지면 내려가므로 회귀 탐지기로는 유효합니다.

    **뒤 둘은 순서에 의존하지 않습니다.** `retrieval_precision`·`retrieval_recall`은 자른
    목록이 아니라 **전체 목록**의 집합 연산입니다. 이것이 필요한 이유는 측정으로 나왔습니다 -
    라우팅 전(2026-10-16) `coauthor`가 hit@5 0.1667이었는데, 같은 보고서의 집합 recall은
    **1.0000**입니다. 골드를 다 찾아 놓고 평균 7.3개를 함께 돌려줘서, 알파벳순 상위 5개 밖으로
    밀려난 것입니다. `recall_at_k`는 그 둘(못 찾음 / 너무 많이 찾음)을 구별하지 못하고 한
    숫자에 섞습니다.

    | 보고서 | `retrieval_precision` | `retrieval_recall` | `recall_at_k` | `hit_at_k` |
    |---|---:|---:|---:|---:|
    | 2026-10-16 라우팅 전 | 0.7908 | 0.9667 | 0.7111 | 0.7333 |
    | 2026-10-18 라우팅 후 | 0.8244 | 1.0000 | 0.9933 | 1.0000 |
    | 2026-10-19 `resolve_paper` 후 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |

    빈 목록에서 precision은 **0.0**입니다. `score_related`가 None을 돌려주는 것과 다릅니다 -
    관련 패널은 골드가 없으면 채점하지 않는 선택 기능이지만, 검색이 아무것도 못 돌려준 것은
    이 경로의 실패입니다. None으로 두면 `summarize`가 건너뛰어 **전면 실패가 집계에서 보이지
    않습니다.**
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    expected = set(expected_ids)
    if not expected:
        raise ValueError("expected_ids must not be empty")
    ranked = list(dict.fromkeys(retrieved_ids[:k]))
    hits = [rank for rank, paper_id in enumerate(ranked, start=1) if paper_id in expected]
    whole = list(dict.fromkeys(retrieved_ids))
    found = set(whole) & expected
    return {
        "hit_at_k": float(bool(hits)),
        "recall_at_k": len(set(ranked) & expected) / len(expected),
        "reciprocal_rank": 1.0 / hits[0] if hits else 0.0,
        "retrieval_precision": len(found) / len(whole) if whole else 0.0,
        "retrieval_recall": len(found) / len(expected),
    }


def score_answer(
    answer: str,
    *,
    expected_answer_contains: Sequence[str],
    expected_ids: Sequence[str],
    evidence_ids: Sequence[str],
) -> dict[str, float | None]:
    """Score required answer phrases and direct answer-evidence IDs."""
    normalized_answer = " ".join(answer.casefold().split())
    expected_phrases = [" ".join(value.casefold().split()) for value in expected_answer_contains]
    citations = set(evidence_ids)
    relevant = set(expected_ids)
    return {
        "answer_correct": (
            float(all(phrase in normalized_answer for phrase in expected_phrases))
            if expected_phrases
            else None
        ),
        "answer_evidence_precision": (
            len(citations & relevant) / len(citations) if citations else None
        ),
        "answer_evidence_recall": (len(citations & relevant) / len(relevant) if relevant else None),
    }


def score_related(
    related_ids: Sequence[str], expected_related_ids: Sequence[str] | None
) -> dict[str, float | None]:
    """Score PageRank suggestions separately; an absent gold set is not a zero score."""
    if not expected_related_ids:
        return {
            "related_retrieval_precision": None,
            "related_retrieval_recall": None,
        }
    retrieved = set(related_ids)
    expected = set(expected_related_ids)
    return {
        "related_retrieval_precision": (
            len(retrieved & expected) / len(retrieved) if retrieved else None
        ),
        "related_retrieval_recall": len(retrieved & expected) / len(expected),
    }


def summarize(rows: Iterable[dict[str, float | None]]) -> dict[str, int | float]:
    """Return macro averages while omitting answer metrics that do not apply."""
    materialized = list(rows)
    summary: dict[str, int | float] = {"case_count": len(materialized)}
    for metric in (*RETRIEVAL_METRICS, *ANSWER_METRICS, *RELATED_METRICS):
        values = [row[metric] for row in materialized if row.get(metric) is not None]
        if values:
            summary[metric] = fmean(values)
    return summary


def score_ceiling(
    expected_ids: Sequence[str], candidate_ids: Sequence[str], *, k: int = 5
) -> dict[str, float]:
    """Separate "is a relevant paper among the candidates" from "is it ranked into the top k".

    Reranking can only reorder what retrieval already found, so measuring its headroom needs
    both limits. `ceiling_recall_in_pool` is the recall a perfect reranker would reach from this
    candidate set; `ceiling_recall_by_k` is what k allows no matter how good the ranking is,
    which binds hard when a case lists far more than k relevant papers.
    """
    if k < 1:
        raise ValueError("k must be at least 1")
    expected = set(expected_ids)
    if not expected:
        raise ValueError("expected_ids must not be empty")
    found = len(expected & set(candidate_ids))
    return {
        "hit_in_pool": float(bool(found)),
        "found_in_pool": float(found),
        "ceiling_recall_in_pool": min(found, k) / len(expected),
        "ceiling_recall_by_k": min(k, len(expected)) / len(expected),
    }
