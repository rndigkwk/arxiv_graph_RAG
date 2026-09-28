"""Small, dependency-free metrics for the local GraphRAG evaluation harness."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from statistics import fmean

RETRIEVAL_METRICS = ("hit_at_k", "recall_at_k", "reciprocal_rank")
ANSWER_METRICS = ("answer_correct", "answer_evidence_precision", "answer_evidence_recall")
RELATED_METRICS = ("related_retrieval_precision", "related_retrieval_recall")


def score_retrieval(
    expected_ids: Sequence[str], retrieved_ids: Sequence[str], *, k: int = 5
) -> dict[str, float]:
    """Score ranked retrieval results against one or more relevant paper IDs."""
    if k < 1:
        raise ValueError("k must be at least 1")
    expected = set(expected_ids)
    if not expected:
        raise ValueError("expected_ids must not be empty")
    ranked = list(dict.fromkeys(retrieved_ids[:k]))
    hits = [rank for rank, paper_id in enumerate(ranked, start=1) if paper_id in expected]
    return {
        "hit_at_k": float(bool(hits)),
        "recall_at_k": len(set(ranked) & expected) / len(expected),
        "reciprocal_rank": 1.0 / hits[0] if hits else 0.0,
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
