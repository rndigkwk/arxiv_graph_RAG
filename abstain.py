"""보류를 사람이 읽을 말로 바꿉니다.

[ADR-022](docs/adr/ADR-022-answer-flow-as-a-stategraph.md)에서 보류가 경로를 얻었고
(`abstain` 노드), `AnswerValidationError`가 사유 코드·단계·시도 수·모델이 쓴 Cypher를 들고
나옵니다. 그런데 **화면에는 빨간 한 줄만** 나갔습니다 — "답변의 형식·인용을 확인하지
못했습니다. 다시 질문해 주세요."

그 한 줄은 보류를 **고장처럼** 보이게 합니다. 보류는 고장이 아니라 판단입니다. 이 앱은 근거를
확인하지 못한 답변을 내놓지 않기로 했고
([ADR-019](docs/adr/ADR-019-validate-evidence-alias-at-the-tool.md)), 두 번 고쳐 보고도 안 되면
멈춥니다. 멈춘 것이 **설계대로 동작한 것**이라면 화면이 그렇게 말해야 합니다.

**`ask()`는 그대로 예외를 올립니다.** 보류를 답변으로 바꾸면 평가 하네스가 그것을 "답했다"로
세고 게이트의 `answer_failure_rate` 기준선이 무효가 됩니다(ADR-017). 그래서 바꾸는 것은
**표시 계층뿐**이고, 측정은 건드리지 않습니다.

이 모듈은 Streamlit도 `chatbot`도 가져오지 않습니다. 사유 코드를 문장으로 바꾸는 순수
함수여서 접속 없이 전수 검사할 수 있습니다.
"""

from __future__ import annotations

from dataclasses import dataclass

# 사유 코드가 가리키는 두 종류입니다. 할 수 있는 일이 다릅니다.
SLIP = "slip"  # 모델이 규칙을 어겼습니다. 다시 물으면 될 수 있습니다.
MISMATCH = "mismatch"  # 질문과 찾은 것이 맞지 않았습니다. 질문을 고치는 쪽이 낫습니다.

RETRY = "같은 질문을 한 번 더 물어보세요. 모델이 규칙을 어긴 것이므로 다시 하면 될 수 있습니다."
REPHRASE = (
    "조건을 나눠서 물어보세요. 예를 들어 논문을 먼저 찾고, 그 논문에 대해 다시 질문하면 "
    "조건을 하나씩 확인할 수 있습니다."
)


@dataclass(frozen=True)
class Reason:
    """사유 코드 하나를 사람 말로."""

    kind: str
    what: str

    @property
    def advice(self) -> str:
        return RETRY if self.kind == SLIP else REPHRASE


# `answer_validation._VALIDATION_REASON_CODES`의 코드와 **1:1**로 맞춥니다. 어긋나면
# 테스트가 막습니다 - 사유가 늘었는데 화면이 "알 수 없는 사유"로 떨어지면 안 됩니다.
REASONS: dict[str, Reason] = {
    "invalid_graph_evidence_shape": Reason(
        SLIP, "그래프 조회가 근거 논문 ID 목록을 약속한 형태로 돌려주지 않았습니다."
    ),
    "invalid_answer_evidence_shape": Reason(
        SLIP, "답변이 근거 논문 ID를 목록 형태로 내놓지 않았습니다."
    ),
    "duplicate_answer_evidence": Reason(
        SLIP, "같은 논문을 근거로 두 번 들었습니다."
    ),
    "unsupported_citation_id": Reason(
        SLIP, "이번에 검색하지 않은 논문을 근거로 들었습니다. 지어낸 인용을 막은 것입니다."
    ),
    "incomplete_content_verdict": Reason(
        SLIP, "인용한 논문들의 내용 판정이 끝까지 돌지 않았습니다."
    ),
    "cited_paper_lookup_incomplete": Reason(
        MISMATCH, "인용한 논문의 정보를 그래프에서 모두 찾지 못했습니다."
    ),
    "graph_conditions_not_covered": Reason(
        MISMATCH, "질문이 요구한 조건을 그래프 조회가 다 담지 못했습니다."
    ),
    "citation_missing_graph_match": Reason(
        MISMATCH, "그래프 조건을 통과하지 않은 논문이 근거에 들어 있었습니다."
    ),
    "citation_missing_content_match": Reason(
        MISMATCH, "내용 조건을 확인하지 못한 논문이 근거에 들어 있었습니다."
    ),
    "self_reference_citation": Reason(
        MISMATCH, "질문이 빼라고 한 논문 자신을 근거로 들었습니다."
    ),
}

STAGES = {
    "answer_format_citation": "답변 형식과 인용 검사",
    "mixed_candidate_conditions": "질문 조건과 근거 대조",
}

HEADLINE = "근거를 확인하지 못해 답변을 보류했습니다."
# 보류가 설계대로 동작한 것임을 한 문장으로 말합니다. 이 문장이 없으면 고장으로 읽힙니다.
WHY_WE_STOP = (
    "이 앱은 확인되지 않은 인용을 붙여 답하지 않습니다. 검사를 통과하지 못한 답변은 "
    "{attempts}번 고쳐 본 뒤 내보내지 않고 멈춥니다."
)
UNKNOWN = Reason(SLIP, "검사에서 걸렸지만 분류되지 않은 사유입니다.")


def explain(diagnostics: dict) -> dict:
    """What to show for one abstention.

    `diagnostics()`가 주는 것만 씁니다. 질문도 답변 본문도 보지 않습니다 — 그 둘을 화면
    문구에 끌어오면 로그·추적에 데이터를 남기지 않기로 한 선과 어긋납니다.
    """
    reason_code = diagnostics.get("reason") or ""
    reason = REASONS.get(reason_code, UNKNOWN)
    attempts = diagnostics.get("attempts") or 0
    return {
        "headline": HEADLINE,
        "why": WHY_WE_STOP.format(attempts=attempts),
        "what": reason.what,
        "advice": reason.advice,
        "kind": reason.kind,
        "stage": STAGES.get(diagnostics.get("stage") or "", diagnostics.get("stage") or ""),
        "attempts": attempts,
        "request_id": diagnostics.get("request_id") or "",
        "cyphers": list(diagnostics.get("cyphers") or []),
        "known": reason_code in REASONS,
    }
