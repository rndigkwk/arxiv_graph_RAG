"""예열이 **조용히 죽지 않는지** 지킵니다(ADR-067).

배포 앱에서 재부팅 직후와 바로 다음 질문의 차이가 46.9초 대 22.7초였고, 그 차이는 전부
`citation_graph()`였습니다(`rank_related_papers` 26.49초 → 0.83초). 그래서 홈 화면을 읽는
동안 배경에서 올립니다.

**배경 스레드의 실패는 아무 데도 나타나지 않습니다.** 예열이 죽어도 질문은 되고, 첫 질문이
여전히 느릴 뿐입니다 — 즉 **고친 것이 안 고쳐진 상태와 똑같이 보입니다.** 이 파일이 지키는
것은 주로 그 구별입니다.

그리고 화면에 **예상 시간을 적지 않는지** 봅니다. 경과는 늙지 않지만 예상은 늙습니다.
"""

import re
import threading
from pathlib import Path

import pytest

from src import warmup


def ticking(*values):
    """정해진 눈금을 차례로 돌려주고, 다 쓰면 마지막 값을 반복합니다.

    바닥나면 배경 스레드 안에서 `StopIteration`이 터지는데, 그것은 **이 시험이 재려는 것이
    아닙니다.** 시계가 시험을 깨뜨리면 무엇이 깨졌는지 알 수 없습니다.
    """
    remaining = list(values)

    def clock():
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return clock


def test_the_build_runs_and_the_time_is_kept():
    done = threading.Event()
    task = warmup.Warmup(done.set)

    task.start().join(5)

    assert done.is_set()
    assert task.state()["status"] == warmup.DONE
    assert task.state()["seconds"] is not None


def test_a_failure_is_not_swallowed():
    """**이 테스트가 이 파일의 이유입니다.** 삼키면 안 고쳐진 것과 구별되지 않습니다."""
    def explode():
        raise TimeoutError("aura is asleep")

    task = warmup.Warmup(explode)
    task.start().join(5)
    state = task.state()

    assert state["status"] == warmup.FAILED
    assert state["reason"] == "TimeoutError"
    assert "실패" in warmup.warmup_line(state)


def test_starting_twice_does_not_build_twice():
    """Streamlit은 상호작용마다 스크립트를 다시 돌립니다. 그때마다 지으면 안 됩니다."""
    builds = []
    task = warmup.Warmup(lambda: builds.append(1))

    first, second = task.start(), task.start()
    first.join(5)

    assert first is second
    assert builds == [1]


def test_a_finished_warmup_says_nothing():
    """다 된 뒤에도 줄이 남으면 매 답변 위에 쓸모없는 한 줄이 붙습니다.

    **IDLE을 여기서 뺐습니다(2026-10-25).** 처음에는 이 시험이 "끝남도 조용하고 시작
    안 함도 조용하다"를 지키고 있었는데, 그 둘이 같다는 것이 바로 배포 앱에서 갈라내지
    못한 원인이었습니다. 시험이 결함을 지키고 있었습니다(ADR-067).
    """
    assert warmup.warmup_line({"status": warmup.DONE, "seconds": 16.6, "reason": None}) == ""


def test_the_running_line_reports_elapsed_not_a_forecast():
    """예상값은 늙습니다. ADR-016에서 화면의 수가 조용히 늙은 적이 있습니다."""
    line = warmup.warmup_line({"status": warmup.RUNNING, "seconds": 12.0, "reason": None})

    assert "12초째" in line
    # 기다리면 끝나는 것이 아니라 **겹치는** 것입니다. 그 말이 빠지면 오해합니다.
    assert "기다립니다" in line


def test_the_elapsed_grows_while_it_runs():
    """진행 중에는 경과가 계속 늘어야 합니다. 멈춰 있으면 죽은 것처럼 보입니다."""
    blocked = threading.Event()
    task = warmup.Warmup(lambda: blocked.wait(5), clock=ticking(0.0, 3.0, 9.0))

    thread = task.start()
    try:
        assert task.state()["seconds"] == 3.0
        assert task.state()["seconds"] == 9.0
    finally:
        blocked.set()
        thread.join(5)


def test_nothing_is_reported_before_it_starts():
    task = warmup.Warmup(lambda: None)

    assert task.state() == {"status": warmup.IDLE, "seconds": None, "reason": None}


def test_the_app_warms_the_citation_graph_before_it_draws_the_tabs():
    """예열을 부르는 줄이 사라지면 테스트는 전부 통과하고 첫 질문만 다시 느려집니다.

    **"어딘가에서 부른다"로는 모자랍니다.** `chat()` 안에도 같은 호출이 있어서(상태 줄을
    읽으려고), 문자열이 있는지만 보면 `main()`에서 지워도 통과합니다 — 처음 쓴 시험이
    실제로 그랬습니다. 그래서 **탭을 그리기 전에** 부르는지를 봅니다.
    """
    pytest.importorskip("streamlit")
    import app

    source = Path("app.py").read_text(encoding="utf-8")

    assert hasattr(app, "citation_warmup")
    assert "warmup.Warmup(_chatbot.citation_graph)" in source
    assert source.index("citation_warmup(chatbot)") < source.index("st.tabs(")


def test_a_warmup_that_never_started_says_so():
    """IDLE은 진행 상황이 아니라 **고장 신호**입니다.

    `main()`이 탭을 그리기 전에 반드시 `start()`를 부르므로, 화면이 IDLE을 보고 있다면 그
    줄이 사라졌거나 돌고 있는 코드가 옛것입니다. 침묵하면 끝난 것과 구별되지 않습니다.
    """
    line = warmup.warmup_line({"status": warmup.IDLE, "seconds": None, "reason": None})

    assert line
    assert "시작되지 않았습니다" in line


def test_the_idle_line_is_not_the_finished_silence():
    """**이 테스트가 이 변경의 전부입니다.** 둘이 같으면 갈라낼 수 없습니다.

    2026-10-25에 실제로 갈라내지 못했습니다 - 배포가 옛 코드라 예열이 안 돌았는데 화면은
    다 끝난 앱과 똑같이 조용했고, 24.47초라는 다른 증거로 소거해야 했습니다(ADR-067).
    """
    idle = warmup.warmup_line({"status": warmup.IDLE, "seconds": None, "reason": None})
    done = warmup.warmup_line({"status": warmup.DONE, "seconds": 15.4, "reason": None})

    assert done == ""
    assert idle != done


def test_the_idle_line_does_not_forecast():
    """고장을 알리면서 예상 시간을 적으면 ADR-016의 늙는 수가 다시 생깁니다."""
    line = warmup.warmup_line({"status": warmup.IDLE, "seconds": None, "reason": None})

    assert not re.search(r"\d", line)


@pytest.mark.parametrize("state, expected", [
    ({"status": warmup.IDLE, "seconds": None, "reason": None}, "시작되지 않음"),
    ({"status": warmup.RUNNING, "seconds": 3.0, "reason": None}, "진행 중"),
    ({"status": warmup.DONE, "seconds": 15.4, "reason": None}, "완료"),
    ({"status": warmup.FAILED, "seconds": 2.0, "reason": "RuntimeError"}, "실패"),
])
def test_the_evidence_panel_line_tells_the_four_states_apart(state, expected):
    """근거 패널은 펼쳐 봐야 나오는 자리라 **조용할 이유가 없습니다.**"""
    line = warmup.warmup_state_line(state)

    assert expected in line
    assert line


def test_the_evidence_panel_line_carries_the_seconds_and_the_reason():
    """측정하는 사람이 추론하지 않고 읽을 수 있어야 합니다."""
    done = warmup.warmup_state_line(
        {"status": warmup.DONE, "seconds": 15.4, "reason": None})
    failed = warmup.warmup_state_line(
        {"status": warmup.FAILED, "seconds": 2.0, "reason": "RuntimeError"})

    assert "15.4초" in done
    assert "RuntimeError" in failed


def test_the_evidence_panel_shows_the_warmup_state():
    """패널에서 이 줄이 빠지면 테스트는 전부 통과하고, 다음에 또 소거법을 씁니다."""
    pytest.importorskip("streamlit")
    source = Path("app.py").read_text(encoding="utf-8")

    panel = source.index('st.expander("단계별 소요 시간")')
    tables = source.index('st.write("노드별 소요 시간")')

    assert panel < source.index("warmup.warmup_state_line(") < tables
