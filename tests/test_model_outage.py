"""모델이 실패했을 때의 안내와 제한 시간을 지킵니다.

안내가 틀리면 할 일이 틀립니다. 전에는 한도 소진도 "검색 에러 - 연결을 확인하라"였습니다.
반대 방향도 막습니다 - **DB가 멈춘 것을 모델 장애로 적으면** 기다려도 안 풀리는 일을 기다리게
합니다. 그래서 Neo4j 예외를 대조군으로 둡니다.
"""

import ast
from pathlib import Path

import httpx
import openai
import pytest
from neo4j.exceptions import ServiceUnavailable

from scripts.measure_condition_check import load_runs, report_paths
from src import model_outage

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def rate_limit(code):
    body = {"code": code, "message": "x"}
    response = httpx.Response(429, request=REQUEST, json={"error": body})
    return openai.RateLimitError("Error code: 429", response=response, body=body)


def test_a_spent_quota_says_wait_for_the_reset():
    assert model_outage.outage_kind(rate_limit("insufficient_quota")) == model_outage.QUOTA
    assert "한도" in model_outage.message(rate_limit("insufficient_quota"))


def test_a_rate_limit_that_is_not_the_quota_is_temporary():
    """같은 429라도 속도 제한은 기다리면 풀립니다. 한도라고 적으면 초기화를 기다리게 됩니다."""
    assert model_outage.outage_kind(rate_limit("rate_limit_exceeded")) == model_outage.UNAVAILABLE


@pytest.mark.parametrize("exc", [
    openai.APITimeoutError(request=REQUEST),
    openai.APIConnectionError(request=REQUEST),
    openai.InternalServerError("boom", response=httpx.Response(503, request=REQUEST), body=None),
])
def test_a_provider_that_does_not_answer_is_unavailable(exc):
    assert model_outage.outage_kind(exc) == model_outage.UNAVAILABLE


def test_a_wrapped_model_error_is_still_found():
    """LangGraph·langchain이 원래 예외를 감쌀 수 있습니다. 사슬을 따라가야 합니다."""
    try:
        try:
            raise rate_limit("insufficient_quota")
        except openai.RateLimitError as inner:
            raise RuntimeError("graph step failed") from inner
    except RuntimeError as outer:
        assert model_outage.outage_kind(outer) == model_outage.QUOTA


@pytest.mark.parametrize("exc", [
    ServiceUnavailable("Aura paused"),
    ValueError("근거 검증 실패"),
    KeyError("arxiv_id"),
])
def test_a_database_or_code_error_is_not_called_a_model_outage(exc):
    """대조군입니다. 모델 탓으로 적으면 사람은 기다리고, 원인은 그대로 남습니다."""
    assert model_outage.outage_kind(exc) is None
    assert model_outage.message(exc) is None


def test_a_cycle_in_the_chain_does_not_hang():
    a, b = RuntimeError("a"), RuntimeError("b")
    a.__context__, b.__context__ = b, a
    assert model_outage.outage_kind(a) is None


def client_kwargs(name):
    tree = ast.parse(Path("src/chatbot.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and getattr(node.targets[0], "id", None) == name
                and isinstance(node.value, ast.Call)):
            return {k.arg: ast.literal_eval(k.value) for k in node.value.keywords
                    if k.arg in ("timeout", "max_retries")}
    raise AssertionError(name)


def test_the_timeout_does_not_cut_any_measured_call():
    """측정된 가장 긴 `generate` 노드 한 번(모델 호출 여럿 포함)보다 짧으면 정상 답을 끊습니다."""
    runs = load_runs(report_paths())
    assert len(runs) >= 100  # 저장된 실행이 없으면 이 시험은 아무것도 재지 않습니다
    longest = max(run["seconds"]["generate"] / run["visits"]["generate"]
                  for run in runs if run["visits"].get("generate"))
    judge = max(run["seconds"]["check_conditions"] / run["visits"]["check_conditions"]
                for run in runs if run["visits"].get("check_conditions"))

    timeout = client_kwargs("llm")["timeout"]
    assert timeout > longest and timeout > judge


@pytest.mark.parametrize("name", ["llm", "translator", "embedder"])
def test_a_stalled_provider_is_retried_once(name):
    """재시도 2번이면 멈춘 제공자 앞에서 기다림이 세 배가 됩니다."""
    assert client_kwargs(name)["max_retries"] == 1


def test_every_model_failure_on_screen_goes_through_the_classifier():
    """모델을 부르는 화면 자리는 넷입니다(답변·번역·논문 비교 둘). 빠진 자리만 옛 안내가 됩니다."""
    source = Path("app.py").read_text(encoding="utf-8")
    assert source.count("model_outage.message(exc)") == 4
    body = source[source.index("def answer_question("):source.index("def clear_chat(")]
    assert "st.error(model_outage.message(exc)" in body
