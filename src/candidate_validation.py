"""Check final paper IDs against the question's content and graph conditions."""

import json

from pydantic import BaseModel, Field

from src import prompts
from src.answer_validation import validate_mixed_candidates, validate_self_reference


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


def structured_judge(structured, usage):
    """이미 만들어진 구조화 호출을 감싸 토큰 사용량을 `usage`에 누적합니다.

    공급자가 달라도 이 껍데기는 같아야 합니다. 토큰 누적을 공급자별로 베껴 두면 한쪽만
    고쳐지고, 두 공급자의 비용을 **다른 방식으로 센 값**을 같은 표에 올리게 됩니다
    (ADR-058의 조건 판정 공급자 교차가 이 함수를 씁니다).

    **`chatbot`이 아니라 여기 있습니다.** 이 껍데기는 앱 설정과 아무 상관이 없는데,
    `chatbot`에 두면 쓰는 쪽이 `.env`의 Neo4j·OpenAI 값까지 끌고 와야 합니다 -
    2026-10-22에 CI의 `test` job에서 그것으로 터졌습니다. 공급자를 바꿔 재는 코드가
    앱 자격증명을 요구하면 안 됩니다.
    """
    def invoke(messages):
        result = structured.invoke(messages)
        if not isinstance(result, dict):
            return result
        reported = getattr(result.get("raw"), "usage_metadata", None)
        if isinstance(reported, dict):
            for key in ("input_tokens", "output_tokens"):
                value = reported.get(key)
                if isinstance(value, int):
                    usage[key] = (usage.get(key) or 0) + value
        parsed = result.get("parsed")
        if parsed is None:
            raise ValueError("인용 논문의 내용 판정 결과가 불완전합니다.")
        return parsed

    return invoke


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
