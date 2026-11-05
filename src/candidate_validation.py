"""Check final paper IDs against the question's content and graph conditions."""

import json
from collections import Counter

from langchain_core.utils.json import parse_partial_json
from pydantic import BaseModel, Field

from src import prompts
from src.answer_validation import (
    validate_dictated_ids,
    validate_mixed_candidates,
    validate_self_reference,
    validation_logger,
)

# 판정 호출을 몇 번까지 부를지입니다(ADR-101). 판정이 깨진 것은 답의 잘못이 아닙니다. 그런데 이
# 자리에서 올리면 에이전트가 "주제 조건도 확인하라"는 수정 요청을 받고, 질문에 없던 주제 조건을
# 지어내 정답 논문을 뺍니다(11-02 정기 평가 두 번의 실패가 모두 이것이었습니다).
JUDGE_ATTEMPTS = 2


class IncompleteVerdict(ValueError):
    """판정 모델이 논문마다 하나씩 판정을 돌려주지 못했습니다. 다시 부르면 됩니다.

    `shape`는 깨진 판정의 **모양**입니다(ADR-102). 원문은 담지 않습니다 - 판정 출력에는
    질문에서 따온 조건과 논문 ID가 들어 있고, 실패 로그는 그것을 남기지 않기로 했습니다
    (`answer_validation.log_validation_failure`).
    """

    def __init__(self, message: str, shape: dict | None = None):
        super().__init__(message)
        self.shape = shape or {}


def _repeat(items) -> int:
    """목록에서 가장 많이 되풀이된 항목의 횟수입니다. 끝없는 판정이 무엇을 되풀이했는지 봅니다."""
    keys = [json.dumps(item, sort_keys=True, ensure_ascii=False) for item in items]
    return max(Counter(keys).values(), default=0)


def unreadable_shape(raw, error) -> dict:
    """읽지 못한 판정의 모양: 출력 토큰, 잘린 이유, 읽을 수 있던 데까지의 필드 길이."""
    shape: dict = {"error": type(error).__name__ if error else None}
    usage = getattr(raw, "usage_metadata", None)
    if isinstance(usage, dict):
        shape["output_tokens"] = usage.get("output_tokens")
    metadata = getattr(raw, "response_metadata", None) or {}
    shape["status"] = metadata.get("status")
    details = metadata.get("incomplete_details")
    shape["incomplete"] = details.get("reason") if isinstance(details, dict) else details
    text = getattr(raw, "text", "") if raw is not None else ""
    text = text if isinstance(text, str) else ""
    shape["chars"] = len(text)
    try:
        partial = parse_partial_json(text) if text else None
    except (json.JSONDecodeError, ValueError):
        partial = None
    shape["partial_json"] = isinstance(partial, dict)
    if isinstance(partial, dict):
        for key in ("graph_conditions", "candidates"):
            value = partial.get(key)
            if isinstance(value, list):
                shape[f"{key}_len"] = len(value)
                shape[f"{key}_max_repeat"] = _repeat(value)
    return shape


def mismatch_shape(candidates, expected: set[str]) -> dict:
    """읽었지만 논문 수가 맞지 않는 판정의 모양입니다. ID 자체는 담지 않습니다."""
    ids = [item.arxiv_id for item in candidates]
    return {"error": "count_mismatch", "expected": len(expected), "candidates": len(ids),
            "duplicates": len(ids) - len(set(ids)), "unknown": len(set(ids) - expected),
            "missing": len(expected - set(ids))}


def log_judge_retry(attempt: int, shape: dict) -> None:
    """판정을 다시 부를 때 한 줄을 남깁니다. 값은 모두 숫자와 상태 이름입니다."""
    fields = " ".join(f"{key}={value}" for key, value in shape.items())
    validation_logger.warning("condition_judge_unreadable attempt=%d %s", attempt, fields)


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
            raise IncompleteVerdict(
                "인용 논문의 내용 판정 결과가 불완전합니다.",
                unreadable_shape(result.get("raw"), result.get("parsing_error")),
            )
        return parsed

    return invoke


def judge_content(question, papers, invoke_model, graph_queries=()):
    """Get a complete, per-paper topic assessment from the configured model."""
    messages = [
        ("system", prompts.JUDGE_CONTENT),
        ("user", json.dumps({"question": question, "papers": papers,
                             "graph_queries": graph_queries}, ensure_ascii=False)),
    ]
    expected = {paper["arxiv_id"] for paper in papers}
    for attempt in range(1, JUDGE_ATTEMPTS + 1):
        try:
            verdict = invoke_model(messages)
            matches = {item.arxiv_id: item.content_matches for item in verdict.candidates}
            if len(verdict.candidates) != len(papers) or set(matches) != expected:
                raise IncompleteVerdict("인용 논문의 내용 판정 결과가 불완전합니다.",
                                        mismatch_shape(verdict.candidates, expected))
            break
        except IncompleteVerdict as exc:
            # 다시 부를 때도, 두 번 다 깨져 올릴 때도 모양을 남깁니다.
            log_judge_retry(attempt, exc.shape)
            # 제한 시간·연결 같은 다른 예외는 다시 부르지 않습니다. 기다림만 두 배가 됩니다.
            if attempt == JUDGE_ATTEMPTS:
                raise
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
    validate_dictated_ids(question, response)
    assessment = assess(question, papers)
    validate_mixed_candidates(response, assessment)
    return assessment
