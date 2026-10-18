"""추적 배선이 **끊긴 상태를 기본으로** 하는지 검증합니다.

dograg가 중요하지 않은 테스트까지 전부 추적해서 한 달치 무료 사용량을 소진했습니다. 그래서
이 프로젝트는 키가 있어도 **명시로 켤 때만** 보냅니다. 그리고 추적이 답변을 막아서는 안 되므로
전송 실패는 삼키고 사유만 남깁니다.

스위치만으로는 부족합니다. **켠 뒤에** 루프를 돌리면 같은 일이 벌어지므로 프로세스마다
예산이 있습니다(ADR-023). 그래서 여기서 고정하는 것은 둘입니다 - 기본이 꺼짐인가, 켠
뒤에도 상한이 있는가.
"""

from pipeline.tracing import (
    BUDGET_KEY,
    DEFAULT_BUDGET,
    ENABLE_FLAG,
    REQUIRED_KEYS,
    budget,
    reset_count,
    send_trace,
    sent_count,
    status,
)

KEYS = {key: "x" for key in REQUIRED_KEYS}
TRACE = {
    "nodes": [{"node": "generate", "attempt": 1, "seconds": 7.1}],
    "visits": {"generate": 1},
    "attempts": 1,
    "total_seconds": 7.1,
}


def test_keys_alone_do_not_turn_tracing_on():
    """키가 `.env`에 있다는 것만으로 보내기 시작하면 쿼터가 또 탑니다."""
    state = status({**KEYS})

    assert state["keys_present"] is True
    assert state["flag_set"] is False
    assert state["active"] is False


def test_the_flag_alone_does_not_turn_tracing_on():
    state = status({ENABLE_FLAG: "1"})

    assert state["flag_set"] is True
    assert state["keys_present"] is False
    assert state["active"] is False
    assert sorted(state["missing_keys"]) == sorted(REQUIRED_KEYS)


def test_both_together_turn_it_on():
    state = status({**KEYS, ENABLE_FLAG: "1"})

    assert state["active"] is True


def test_a_disabled_send_does_nothing_and_says_why():
    result = send_trace(TRACE, environ={**KEYS})

    assert result["sent"] is False
    assert result["reason"] == "비활성"


def test_a_broken_send_is_swallowed_with_a_reason():
    """추적이 답변을 막으면 안 됩니다. 예외가 올라가면 질문이 실패합니다."""
    # 키가 있고 플래그도 켜졌지만 호스트가 쓰레기라 전송이 실패합니다.
    result = send_trace(
        TRACE,
        environ={**KEYS, ENABLE_FLAG: "1", "LANGFUSE_BASE_URL": "http://127.0.0.1:1"},
    )

    assert result["sent"] is False
    assert result["reason"] and result["reason"] != "비활성"


def test_the_flag_only_accepts_an_explicit_true():
    # "0"이나 빈 문자열로 꺼 둔 것이 조용히 켜지면 안 됩니다.
    for value in ("", "0", "no", "off", "False"):
        assert status({**KEYS, ENABLE_FLAG: value})["active"] is False, value


def test_the_budget_defaults_to_one_evaluation_plus_slack():
    """평가 한 번이 30건이므로, 기본값은 그 한 번을 덮고 루프는 덮지 않아야 합니다."""
    assert budget({}) == DEFAULT_BUDGET
    assert DEFAULT_BUDGET >= 30


def test_a_garbage_budget_falls_back_instead_of_becoming_unlimited():
    """오타가 "무제한"으로 읽히면 안 됩니다. 쿼터를 태우는 쪽으로 틀리지 않습니다."""
    for value in ("abc", "1.5", "-", "∞"):
        assert budget({BUDGET_KEY: value}) == DEFAULT_BUDGET, value
    # 음수는 0으로 깎입니다. 끄는 쪽입니다.
    assert budget({BUDGET_KEY: "-5"}) == 0


def test_a_zero_budget_blocks_the_send_even_when_everything_else_is_on():
    """켜 둔 채로 루프를 돌리는 사고를 막는 장치입니다 - dograg가 그렇게 태웠습니다."""
    reset_count()
    result = send_trace(
        TRACE,
        environ={**KEYS, ENABLE_FLAG: "1", BUDGET_KEY: "0"},
    )

    assert result["sent"] is False
    assert "예산" in result["reason"]
    # 스위치는 켜져 있습니다. 막은 것은 예산입니다 - 두 사유를 섞어 적으면 진단이 어렵습니다.
    assert result["active"] is True
    assert sent_count() == 0


def test_the_count_is_per_process_and_resettable():
    """한 프로세스에서 여러 실행을 재는 하네스가 각각 예산을 갖게 하려면 필요합니다."""
    reset_count()
    assert sent_count() == 0
