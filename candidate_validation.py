"""Check final paper IDs against the question's content and graph conditions."""

import json

from pydantic import BaseModel, Field

import prompts
from answer_validation import validate_mixed_candidates, validate_self_reference


class CandidateVerdict(BaseModel):
    arxiv_id: str
    content_matches: bool = Field(description="Title or abstract supports the requested topic")


class QuestionVerdict(BaseModel):
    requires_content: bool = Field(description="Question has a paper topic or content condition")
    graph_conditions: list[str] = Field(description=(
        "Every author, citation, category, publication date, or count condition the question "
        "asks for, each quoted from the question's own words. Empty list when the question asks "
        "for none. Do not invent a condition the question does not state."))
    graph_conditions_covered: bool = Field(description=(
        "At least one supplied Cypher query enforces every condition listed in graph_conditions. "
        "True when graph_conditions is empty, because there is nothing to enforce."))
    candidates: list[CandidateVerdict]


def judge_content(question, papers, invoke_model, graph_queries=()):
    """Get a complete, per-paper topic assessment from the configured model."""
    messages = [
        ("system", prompts.JUDGE_CONTENT),
        ("user", json.dumps({"question": question, "papers": papers,
                             "graph_queries": graph_queries}, ensure_ascii=False)),
    ]
    verdict = invoke_model(messages)
    matches = {item.arxiv_id: item.content_matches for item in verdict.candidates}
    expected = {paper["arxiv_id"] for paper in papers}
    if len(verdict.candidates) != len(papers) or set(matches) != expected:
        raise ValueError("인용 논문의 내용 판정 결과가 불완전합니다.")
    # 조건을 이름으로 적게 해, 불리언 하나만 뒤집혀 없는 조건이 생기는 일을 막습니다.
    conditions = [
        text.strip() for text in verdict.graph_conditions
        if isinstance(text, str) and text.strip()
    ]
    return {
        "requires_content": verdict.requires_content,
        "requires_graph": bool(conditions),
        "graph_conditions": conditions,
        # 조건이 없으면 지킬 것도 없으므로 충족으로 봅니다.
        "graph_conditions_covered": bool(verdict.graph_conditions_covered or not conditions),
        "content_matches": matches,
    }


def assess_and_validate(question, response, fetch_papers, assess):
    """Fetch cited papers, assess topic support, and enforce mixed-question evidence."""
    ids = response["evidence_ids"]
    if not ids:
        return
    papers = fetch_papers(ids)
    if {paper["arxiv_id"] for paper in papers} != set(ids):
        raise ValueError("인용 논문 정보를 모두 찾지 못했습니다.")
    # 질문이 명시적으로 제외한 논문은 모델 판정 없이 걸러집니다. 판정 호출보다 앞에
    # 두어, 걸릴 때 불필요한 호출을 하지 않습니다.
    validate_self_reference(question, papers, response)
    assessment = assess(question, papers)
    validate_mixed_candidates(response, assessment)
    return assessment
