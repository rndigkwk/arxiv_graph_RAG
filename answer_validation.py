"""Pure validation helpers for answers returned by the GraphRAG agent."""

from __future__ import annotations

import logging

validation_logger = logging.getLogger("arxiv_graph_rag.validation")

_VALIDATION_REASON_CODES = (
    ("조회 결과의 evidence_ids", "invalid_graph_evidence_shape"),
    ("답변의 evidence_ids가", "invalid_answer_evidence_shape"),
    ("답변의 evidence_ids에 중복", "duplicate_answer_evidence"),
    ("직접 검색된 근거에 없는 인용 ID", "unsupported_citation_id"),
    ("인용 논문의 내용 판정 결과가 불완전", "incomplete_content_verdict"),
    ("인용 논문 정보를 모두 찾지 못", "cited_paper_lookup_incomplete"),
    ("그래프 검색 조건이 질문의 모든", "graph_conditions_not_covered"),
    ("그래프 조건을 통과하지 않은 논문", "citation_missing_graph_match"),
    ("내용 조건을 확인하지 못한 논문", "citation_missing_content_match"),
    ("질문이 제외를 요구한 논문", "self_reference_citation"),
)

# 질문이 기준 논문 자신을 답에서 빼라고 **명시한** 표현만 봅니다. 제외를 추론하지 않습니다.
EXCLUDING_PHRASES = (
    "other papers",
    "other paper",
    "papers other than",
    "papers besides",
    "papers excluding",
)
# 짧은 제목이 질문 문장에 우연히 들어가 오탐이 되는 것을 막습니다.
MIN_MATCHED_TITLE_LENGTH = 20


def reason_code(error: ValueError) -> str:
    """Classify a validation failure into a code that is safe to record anywhere.

    로그에만 두면 사례와 이어 붙일 수 없습니다. 보고서에도 같은 코드를 적어야 "어느 사례가 어떤
    사유로 멈췄는가"를 다시 돌리지 않고 답할 수 있습니다.
    """
    message = str(error)
    return next(
        (code for prefix, code in _VALIDATION_REASON_CODES if message.startswith(prefix)),
        f"unclassified_{type(error).__name__}",
    )


def log_validation_failure(request_id: str, attempt: int, stage: str, error: ValueError) -> None:
    """Log a safe reason code without recording question or retrieved-paper text."""
    reason = reason_code(error)
    validation_logger.warning(
        "answer_validation_failed request_id=%s attempt=%d stage=%s reason=%s",
        request_id, attempt, stage, reason,
    )


def direct_evidence_ids(response: dict[str, object]) -> set[str]:
    """Collect answer evidence from graph rows and vector hits, never related suggestions."""
    direct_ids = {
        paper_id
        for row in response.get("rows", [])
        for paper_id in row.get("evidence_ids", [])
    }
    direct_ids.update(
        paper["arxiv_id"]
        for paper in response.get("papers", [])
        if paper.get("arxiv_id")
    )
    return direct_ids


def validate_evidence_ids(evidence_ids: list[str], direct_ids: set[str]) -> None:
    """Require unique, nonblank citations drawn from direct search evidence."""
    if not isinstance(evidence_ids, list) or not all(
        isinstance(paper_id, str) and paper_id.strip() for paper_id in evidence_ids
    ):
        raise ValueError("답변의 evidence_ids가 arxiv_id 목록이 아닙니다. 다시 질문해 주세요.")
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("답변의 evidence_ids에 중복 ID가 있습니다. 다시 질문해 주세요.")
    unsupported = set(evidence_ids) - direct_ids
    if unsupported:
        raise ValueError(f"직접 검색된 근거에 없는 인용 ID: {sorted(unsupported)}")


def validate_mixed_candidates(response: dict, assessment: dict) -> None:
    """Require each cited mixed-answer paper to pass both retrieval conditions."""
    if not (assessment["requires_content"] and assessment["requires_graph"]):
        return
    if not assessment["graph_conditions_covered"]:
        raise ValueError("그래프 검색 조건이 질문의 모든 관계·속성 조건을 반영하지 않았습니다.")
    graph_ids = {
        paper_id
        for row in response["rows"]
        for paper_id in row["evidence_ids"]
    }
    cited_ids = set(response["evidence_ids"])
    missing_graph = cited_ids - graph_ids
    if missing_graph:
        raise ValueError(f"그래프 조건을 통과하지 않은 논문: {sorted(missing_graph)}")
    missing_content = {
        paper_id for paper_id in cited_ids
        if not assessment["content_matches"].get(paper_id, False)
    }
    if missing_content:
        raise ValueError(f"내용 조건을 확인하지 못한 논문: {sorted(missing_content)}")


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def self_reference_citations(
    question: str, papers: list[dict], evidence_ids: list[str]
) -> set[str]:
    """Cited papers whose own title the question quotes while asking for *other* papers.

    "Which other papers share an author with <title>" names one paper only to exclude it, so
    citing it is wrong however it was retrieved. Evidence-side checks cannot catch this on
    their own: the agent needs a lookup query to turn the title into an ID, and that query's
    rows legitimately contain the paper, so it passes every "came from the graph" test.

    Only an explicit exclusion in the question triggers this. A question that merely names a
    paper ("what is the primary category of <title>") is answered *by* that paper, and nothing
    here may reject it.
    """
    normalized_question = _normalized(question)
    if not any(phrase in normalized_question for phrase in EXCLUDING_PHRASES):
        return set()
    cited = set(evidence_ids)
    return {
        paper["arxiv_id"]
        for paper in papers
        if paper.get("arxiv_id") in cited
        and len(title := _normalized(paper.get("title"))) >= MIN_MATCHED_TITLE_LENGTH
        and title in normalized_question
    }


def validate_self_reference(question: str, papers: list[dict], response: dict) -> None:
    """Reject citing the paper the question named in order to exclude it."""
    offenders = self_reference_citations(question, papers, response["evidence_ids"])
    if offenders:
        raise ValueError(f"질문이 제외를 요구한 논문을 인용했습니다: {sorted(offenders)}")
