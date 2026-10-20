"""추적 배선이 **끊긴 상태를 기본으로** 하는지 검증합니다.

dograg가 중요하지 않은 테스트까지 전부 추적해서 한 달치 무료 사용량을 소진했습니다. 그래서
이 프로젝트는 키가 있어도 **명시로 켤 때만** 보냅니다. 그리고 추적이 답변을 막아서는 안 되므로
전송 실패는 삼키고 사유만 남깁니다.

스위치만으로는 부족합니다. **켠 뒤에** 루프를 돌리면 같은 일이 벌어지므로 예산이
있습니다(ADR-023). 그래서 여기서 고정하는 것은 셋입니다 - 기본이 꺼짐인가, 켠 뒤에도
상한이 있는가, 그리고 **그 상한의 단위가 하루인가**.

세 번째가 늦게 붙었습니다. 계수기가 모듈 전역이라 단위가 **프로세스당**이었고, 평가는 한
프로세스에서 끝나니 맞게 동작했습니다. 앱은 다릅니다 - 잠들었다 깨면 새 프로세스라
"재시작마다 앞 50건"이 됩니다. 상한도 아니고 표본도 아닙니다. 그래서 아래 테스트들이
**파일에 남는가**와 **날짜가 바뀌면 다시 열리는가**를 함께 묶습니다.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline.tracing import (
    BUDGET_KEY,
    DEFAULT_BUDGET,
    ENABLE_FLAG,
    REQUIRED_KEYS,
    STATE_KEY,
    _record_sent,
    budget,
    monthly_traces,
    reset_count,
    send_trace,
    sent_count,
    state_path,
    status,
    utc_day,
)

KEYS = {key: "x" for key in REQUIRED_KEYS}


def env_at(tmp_path, **extra):
    """계수기 파일을 테스트마다 격리합니다. 저장소의 실제 파일을 건드리면 안 됩니다."""
    return {**KEYS, STATE_KEY: str(tmp_path / "budget.json"), **extra}


DAY = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
NEXT_DAY = datetime(2026, 10, 21, 0, 5, tzinfo=timezone.utc)
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


def test_a_zero_budget_blocks_the_send_even_when_everything_else_is_on(tmp_path):
    """켜 둔 채로 루프를 돌리는 사고를 막는 장치입니다 - dograg가 그렇게 태웠습니다."""
    env = env_at(tmp_path, **{ENABLE_FLAG: "1", BUDGET_KEY: "0"})
    result = send_trace(TRACE, environ=env)

    assert result["sent"] is False
    assert "예산" in result["reason"]
    # 스위치는 켜져 있습니다. 막은 것은 예산입니다 - 두 사유를 섞어 적으면 진단이 어렵습니다.
    assert result["active"] is True
    assert sent_count(env) == 0


def test_the_count_survives_a_restart():
    """**이 테스트가 단위를 고정합니다.**

    예전에는 계수기가 모듈 전역뿐이어서 프로세스가 죽으면 0으로 돌아갔습니다. 앱은 잠들었다
    깨면 새 프로세스이므로, 그 동작은 상한이 아니라 "재시작마다 앞 50건"입니다. 파일이
    메모리보다 **먼저**여야 재시작을 건너 셉니다.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        env = {STATE_KEY: str(Path(tmp) / "budget.json")}
        _record_sent(utc_day(DAY), 7, env)
        # 재시작: 메모리 계수기를 비웁니다. 파일은 그대로입니다.
        reset_memory()

        assert sent_count(env, now=DAY) == 7


def reset_memory():
    """프로세스 재시작을 흉내 냅니다 - 메모리만 비우고 파일은 남깁니다."""
    import pipeline.tracing as module

    module._sent, module._sent_day = 0, ""


def test_the_window_reopens_the_next_day(tmp_path):
    """하루 창이 창이려면 자정을 넘겨 다시 열려야 합니다."""
    env = env_at(tmp_path)
    _record_sent(utc_day(DAY), 50, env)

    assert sent_count(env, now=DAY) == 50
    assert sent_count(env, now=NEXT_DAY) == 0


def test_an_unwritable_counter_falls_back_to_the_process_and_says_so(tmp_path):
    """무제한으로 떨어지지 않습니다. 그리고 되돌아갔다는 사실을 숨기지 않습니다."""
    # 디렉터리를 파일 자리에 놓으면 읽기도 쓰기도 실패합니다.
    blocked = tmp_path / "budget.json"
    blocked.mkdir()
    env = {**KEYS, ENABLE_FLAG: "1", BUDGET_KEY: "0", STATE_KEY: str(blocked)}

    result = send_trace(TRACE, environ=env)

    assert result["sent"] is False
    assert result["budget_persisted"] is False
    # 사유가 "프로세스당으로 대체"를 말해야 진단이 됩니다.
    assert "프로세스당" in result["reason"]


def test_a_corrupt_counter_does_not_become_unlimited(tmp_path):
    """깨진 파일이 "0건 보냄"으로 읽히면 매 재시작마다 예산이 되살아납니다."""
    path = tmp_path / "budget.json"
    path.write_text("{ 이건 json이 아닙니다", encoding="utf-8")
    env = {**KEYS, ENABLE_FLAG: "1", BUDGET_KEY: "0", STATE_KEY: str(path)}
    reset_memory()

    result = send_trace(TRACE, environ=env)

    assert result["sent"] is False
    assert "예산" in result["reason"]
    assert result["budget_persisted"] is False


def test_the_counter_file_is_not_committed():
    """런타임 상태입니다. 기본 경로가 gitignore 안에 있어야 합니다."""
    from pipeline.tracing import DEFAULT_STATE

    ignored = Path(".gitignore").read_text(encoding="utf-8").splitlines()
    # 작업 디렉터리가 아니라 모듈 기준이어야 합니다 - 앱을 다른 곳에서 띄워도 같은 파일을
    # 세야 상한이 상한입니다.
    assert DEFAULT_STATE.is_absolute()
    assert DEFAULT_STATE.parent == Path("pipeline/state").resolve()
    assert "pipeline/state/" in ignored


def test_the_monthly_consequence_of_the_knob_is_visible():
    """한도는 월 단위인데 손잡이는 하루 단위입니다. 31을 곱하지 않으면 50이 작아 보입니다."""
    assert monthly_traces(DEFAULT_BUDGET) == DEFAULT_BUDGET * 31
    assert monthly_traces(0) == 0
    # 음수 예산(=끄기)이 음수 노출로 읽히면 안 됩니다.
    assert monthly_traces(-5) == 0


def test_the_state_path_is_overridable(tmp_path):
    """CI와 테스트가 저장소의 실제 계수기를 건드리지 않게 하려면 필요합니다."""
    from pipeline.tracing import DEFAULT_STATE

    assert state_path({STATE_KEY: str(tmp_path / "x.json")}) == tmp_path / "x.json"
    assert state_path({STATE_KEY: "  "}) == DEFAULT_STATE


def test_the_day_is_utc_not_the_host_timezone():
    """호스트 시간대에 따라 창이 달라지면 두 기계의 계수가 다른 뜻이 됩니다."""
    assert utc_day(datetime(2026, 10, 20, 23, 59, tzinfo=timezone.utc)) == "2026-10-20"
    assert utc_day(datetime(2026, 10, 21, 0, 1, tzinfo=timezone.utc)) == "2026-10-21"


def test_reset_clears_both_the_file_and_the_memory(tmp_path):
    """한 프로세스에서 여러 실행을 재는 하네스가 각각 예산을 갖게 하려면 둘 다 지워야 합니다.

    파일만 남으면 다음 실행이 앞 실행의 계수를 물려받습니다.
    """
    env = env_at(tmp_path)
    _record_sent(utc_day(DAY), 9, env)
    assert sent_count(env, now=DAY) == 9

    reset_count(env)

    assert sent_count(env, now=DAY) == 0
    assert not (tmp_path / "budget.json").exists()


def test_the_counter_round_trips(tmp_path):
    env = env_at(tmp_path)
    assert _record_sent(utc_day(DAY), 3, env) is True
    saved = json.loads((tmp_path / "budget.json").read_text(encoding="utf-8"))
    assert saved == {"day": "2026-10-20", "sent": 3}
