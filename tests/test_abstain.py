"""보류 설명이 **모든 사유 코드를 덮는지** 검증합니다.

사유가 늘었는데 화면이 "알 수 없는 사유"로 떨어지면, 보류가 다시 고장처럼 보입니다. 그래서
`answer_validation`의 코드 표와 1:1인지 여기서 고정합니다 - 한쪽만 고치는 일을 막는 것이
이 테스트의 전부입니다.

표시 계층만 바꿨다는 것도 함께 봅니다. `ask()`가 여전히 예외를 올려야 평가 하네스의
`answer_failure_rate` 기준선이 유효합니다(ADR-017).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.abstain import MISMATCH, REASONS, SLIP, STAGES, explain
from src.answer_validation import _VALIDATION_REASON_CODES

ALL_CODES = [code for _, code in _VALIDATION_REASON_CODES]


def test_every_validation_reason_has_a_sentence():
    assert sorted(REASONS) == sorted(ALL_CODES)


@pytest.mark.parametrize("code", ALL_CODES)
def test_each_reason_says_what_happened_and_what_to_try(code):
    told = explain({"reason": code, "stage": "answer_format_citation", "attempts": 2,
                    "request_id": "abc", "cyphers": []})

    assert told["known"] is True
    assert told["what"].endswith(".")
    assert told["advice"]
    assert told["kind"] in {SLIP, MISMATCH}


def test_an_unknown_reason_still_explains_itself():
    """분류되지 않은 사유도 빈 화면이 되면 안 됩니다."""
    told = explain({"reason": "unclassified_TypeError", "attempts": 2})

    assert told["known"] is False
    assert told["headline"]
    assert told["what"]
    assert told["advice"]


def test_the_stage_is_named_in_korean_when_it_is_known():
    told = explain({"reason": "unsupported_citation_id", "stage": "mixed_candidate_conditions"})

    assert told["stage"] == STAGES["mixed_candidate_conditions"]


def test_an_unrecognised_stage_is_passed_through_not_dropped():
    told = explain({"reason": "unsupported_citation_id", "stage": "something_new"})

    assert told["stage"] == "something_new"


def test_empty_diagnostics_do_not_raise():
    """예외 쪽이 비어 있어도 화면은 떠야 합니다."""
    told = explain({})

    assert told["attempts"] == 0
    assert told["cyphers"] == []
    assert told["request_id"] == ""


def test_the_explanation_never_carries_question_or_answer_text():
    """운영 로그에 데이터를 남기지 않는다는 선과 같습니다."""
    told = explain({"reason": "unsupported_citation_id", "attempts": 2,
                    "question": "비밀 질문", "answer": "비밀 답변"})

    assert "비밀" not in str(told)


def test_the_cyphers_are_shown_so_a_stop_is_not_mistaken_for_doing_nothing():
    told = explain({"reason": "unsupported_citation_id", "cyphers": ["MATCH (p) RETURN p"]})

    assert told["cyphers"] == ["MATCH (p) RETURN p"]


def test_ask_still_raises_so_the_gate_baseline_stays_valid():
    """보류를 답변으로 바꾸면 하네스가 "답했다"로 세고 기준선이 무효가 됩니다."""
    source = Path("src/chatbot.py").read_text(encoding="utf-8")

    assert "raise AnswerValidationError(" in source
    # 설명은 앱이 합니다. `chatbot`이 `abstain`을 가져오면 측정 경로에 표시 계층이 섞입니다.
    assert "import abstain" not in source


def test_abstain_needs_no_streamlit_or_chatbot():
    """접속이나 UI 없이 전수 검사할 수 있어야 사유가 늘 때마다 확인됩니다."""
    source = Path("src/abstain.py").read_text(encoding="utf-8")

    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("import streamlit", "import chatbot", "from chatbot")), line
