"""Pure validation helpers for answers returned by the GraphRAG agent."""

from __future__ import annotations


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
