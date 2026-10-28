"""답 쓰는 에이전트는 추론을 끄고 부릅니다(ADR-091). 판정(ADR-089)과 같은 방식입니다.

- 바뀌는 것은 추론 강도 하나입니다. 모델·제한 시간·재시도는 원본 `llm`과 같습니다.
- 원본 `llm`은 그대로라 논문 비교는 기본 강도입니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SOURCE = Path("src/chatbot.py").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def chatbot():
    try:
        from src import chatbot
    except Exception as exc:
        pytest.skip(f"chatbot을 가져올 수 없습니다: {type(exc).__name__}")
    return chatbot


def test_the_agent_turns_reasoning_off(chatbot):
    assert chatbot.AGENT_REASONING == {"effort": "none"}
    assert chatbot.agent_llm.reasoning == {"effort": "none"}


def test_only_the_effort_changes(chatbot):
    for name in ("model_name", "request_timeout", "max_retries", "streaming", "use_responses_api"):
        assert getattr(chatbot.agent_llm, name) == getattr(chatbot.llm, name), name


def test_the_original_model_keeps_its_default(chatbot):
    assert chatbot.llm.reasoning is None


def test_the_agent_is_built_from_the_copy():
    assert "agent = create_agent(\n        model=agent_llm," in SOURCE
    assert "llm.with_structured_output(PaperComparison" in SOURCE


def test_the_report_says_which_effort_answered():
    run = Path("evals/run.py").read_text(encoding="utf-8")
    assert 'metadata["agent_reasoning"] = getattr(chatbot, "AGENT_REASONING", None)' in run
