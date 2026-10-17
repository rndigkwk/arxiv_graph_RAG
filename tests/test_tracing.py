"""추적 배선이 **끊긴 상태를 기본으로** 하는지 검증합니다.

dograg가 중요하지 않은 테스트까지 전부 추적해서 한 달치 무료 사용량을 소진했습니다. 그래서
이 프로젝트는 키가 있어도 **명시로 켤 때만** 보냅니다. 그리고 추적이 답변을 막아서는 안 되므로
전송 실패는 삼키고 사유만 남깁니다.
"""

from pipeline.tracing import ENABLE_FLAG, REQUIRED_KEYS, send_trace, status

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
