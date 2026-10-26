"""모델 호출이 실패한 이유를 사람이 할 일로 바꿉니다.

전에는 모든 실패가 "검색 에러: RateLimitError. 연결 상태를 확인하고 다시 질문하세요."였습니다.
**검색은 멀쩡하고, 연결을 확인해도 풀리지 않습니다.** 사용 한도가 다 됐으면 초기화를 기다려야
하고, 제공자가 응답하지 않으면 잠시 뒤 다시 물으면 됩니다. 둘은 할 일이 다릅니다.

dograg의 `src/model_outage.py`(ADR-0011)에서 가져왔습니다. 그쪽은 2026-10-19에 평가와 운영이 같은
키를 써서 한도가 다 됐고, 그 뒤 모든 질문이 같은 한 줄로 끝났습니다.

예외 사슬(`__cause__`·`__context__`)을 따라갑니다. LangGraph와 langchain이 원래 예외를 감쌀 수
있기 때문입니다. 모델 예외가 아니면 `None`이고, 화면은 지금처럼 일반 오류로 안내합니다.
"""

from __future__ import annotations

import openai

QUOTA = "quota"
UNAVAILABLE = "unavailable"

# 모델을 쓰지 않는 탭입니다. 실패 안내가 "할 수 있는 것"을 함께 말하도록 둡니다.
STILL_WORKS = "홈·PageRank 순위·품질 게이트 탭은 모델 없이 동작합니다."
MESSAGES = {
    QUOTA: ("AI 모델의 사용 한도가 다 되어 답을 쓰지 못합니다. 한도가 초기화되면 다시 시도하세요. "
            + STILL_WORKS),
    UNAVAILABLE: ("AI 모델이 응답하지 않습니다(시간 초과·연결 실패·제공자 오류). 잠시 뒤 다시 "
                  "시도하세요. " + STILL_WORKS),
}


def chain(exc: BaseException):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def is_quota(exc: openai.RateLimitError) -> bool:
    """요청 속도 제한과 사용 한도 소진은 같은 429입니다. 본문의 코드로 가릅니다."""
    if getattr(exc, "code", None) == "insufficient_quota":
        return True
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error", body)
        if isinstance(error, dict) and error.get("code") == "insufficient_quota":
            return True
    return "insufficient_quota" in str(exc)


def outage_kind(exc: BaseException) -> str | None:
    """`"quota"`, `"unavailable"`, 모델 문제가 아니면 `None`."""
    for item in chain(exc):
        if isinstance(item, openai.RateLimitError):
            # 한도가 아니면 재시도 뒤에도 속도 제한에 막힌 것입니다. 기다리면 풀립니다.
            return QUOTA if is_quota(item) else UNAVAILABLE
        if isinstance(item, (openai.APIConnectionError, openai.InternalServerError)):
            return UNAVAILABLE  # APITimeoutError는 APIConnectionError의 하위 클래스입니다
    return None


def message(exc: BaseException) -> str | None:
    kind = outage_kind(exc)
    return MESSAGES[kind] if kind else None
