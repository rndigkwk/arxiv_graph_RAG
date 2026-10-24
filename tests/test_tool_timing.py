"""`generate` 안쪽 계측이 **같은 시간을 두 번 세지 않는지** 지킵니다(ADR-066).

ADR-065는 `generate`가 72.8%라는 것까지만 말할 수 있었습니다. 그 안의 번역·Cypher·벡터·
모델 턴을 가르려면 도구 호출마다 초가 필요한데, 그 값이 어디에도 없었습니다.

이 파일이 보는 것은 **붙이는 방식**입니다. 세 가지가 조용히 틀릴 수 있습니다.

1. 도구가 안에서 모델을 부르면(`translate_to_english`) 콜백이 양쪽에 와서 **같은 시간이
   두 번** 더해집니다.
2. 한 모델 턴이 도구를 여러 개 부르면 LangGraph가 스레드로 나눕니다. 더미가 하나면 시작과
   끝이 엇갈려 **서로의 시간을 받습니다** - 합계는 맞고 개별만 틀리므로 눈에 띄지 않습니다.
3. 콜백이 아예 걸리지 않으면 예외 없이 **"도구는 공짜"**가 됩니다. 그래서 `attach`가 붙인
   개수를 돌려줍니다.

맨 아래 하나는 LangChain 콜백 경로를 **실제로** 지나갑니다. `tool_call_id`가 도구의 끝에
따라오는 `ToolMessage`에만 있다는 것이 이 계측의 전제이고, 그 전제가 깨지면 모든 수가
`unattributed`로 흘러갑니다.
"""

import threading
import time
from pathlib import Path

import pytest
from langchain_core.messages import ToolMessage

from src import tool_timing


def ticking(*values):
    """정해진 눈금을 차례로 돌려주는 시계. 시작·끝마다 하나씩 씁니다."""
    clock = iter(values)
    return lambda: next(clock)


def ended(call_id):
    return ToolMessage(content="{}", tool_call_id=call_id)


def test_a_tool_gets_the_time_of_its_own_call():
    inside = tool_timing.InsideGenerate(ticking(10.0, 12.5))

    inside.on_tool_start({}, "")
    inside.on_tool_end(ended("call_a"))

    assert inside.seconds() == {"call_a": 2.5}


def test_a_model_call_inside_a_tool_is_not_counted_twice():
    """**이 테스트가 이 파일의 이유입니다.** 번역 도구가 안에서 모델을 부릅니다."""
    inside = tool_timing.InsideGenerate(ticking(0.0, 0.1, 1.9, 2.0))

    inside.on_tool_start({}, "")          # 0.0
    inside.on_chat_model_start({}, [])    # 0.1 - 도구 안쪽
    inside.on_llm_end(None)               # 1.9
    inside.on_tool_end(ended("call_a"))   # 2.0

    summary = inside.summary(2.0)
    assert summary["tools_seconds"] == 2.0
    assert summary["model_seconds"] == 0.0
    assert summary["model_turns"] == 0
    # 두 번 셌다면 나머지가 -1.8이 되고 adds_up이 꺾입니다.
    assert summary["other_seconds"] == 0.0
    assert summary["adds_up"] is True


def test_a_model_call_outside_a_tool_is_counted():
    inside = tool_timing.InsideGenerate(ticking(0.0, 3.0))

    inside.on_chat_model_start({}, [])
    inside.on_llm_end(None)

    summary = inside.summary(3.0)
    assert summary["model_seconds"] == 3.0
    assert summary["model_turns"] == 1
    assert summary["tools_seconds"] == 0.0


def test_two_overlapping_tool_calls_do_not_swap_their_times():
    """더미가 스레드마다 따로가 아니면 **겹친** 두 호출이 서로의 시작을 가져갑니다.

    겹치기만 하고 포개지지 않게 둔 것이 이 시험의 핵심입니다. 하나가 다른 하나 안에 완전히
    들어가면 더미 하나로도 (우연히) 맞아서, 이 시험이 아무것도 가르지 못합니다.

        먼저 시작 ├──────────┤            (0.00 ~ 0.25)
        나중 시작        ├────────────┤   (0.20 ~ 0.40)
    """
    inside = tool_timing.InsideGenerate()
    first_started, second_started, first_ended = (threading.Event() for _ in range(3))

    def starts_first():
        inside.on_tool_start({}, "")
        first_started.set()
        second_started.wait(2)
        time.sleep(0.05)
        inside.on_tool_end(ended("starts_first"))
        first_ended.set()

    def starts_second():
        first_started.wait(2)
        time.sleep(0.2)
        inside.on_tool_start({}, "")
        second_started.set()
        first_ended.wait(2)
        time.sleep(0.15)
        inside.on_tool_end(ended("starts_second"))

    threads = [threading.Thread(target=starts_first), threading.Thread(target=starts_second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    seconds = inside.seconds()
    # 더미가 하나면 먼저 시작한 쪽이 나중 시작의 출발점을 받아 0.05초로 줄어듭니다.
    assert seconds["starts_first"] > 0.2, seconds
    # 그리고 나중 시작한 쪽은 먼저 시작의 출발점을 받아 0.4초로 늘어납니다.
    assert seconds["starts_second"] < 0.3, seconds


def test_a_failed_tool_keeps_its_time_but_loses_its_name():
    """오류의 끝에는 ToolMessage가 없습니다. 0으로 적으면 나머지가 그만큼 부풉니다."""
    inside = tool_timing.InsideGenerate(ticking(0.0, 1.5))

    inside.on_tool_start({}, "")
    inside.on_tool_error(ValueError("boom"))

    summary = inside.summary(1.5)
    assert inside.seconds() == {}
    assert summary["unattributed_seconds"] == 1.5
    assert summary["other_seconds"] == 0.0


def test_an_error_does_not_leave_a_frame_for_the_next_call_to_take():
    """프레임이 남으면 다음 호출의 끝이 **이전 호출의 시작**과 짝지어집니다."""
    inside = tool_timing.InsideGenerate(ticking(0.0, 1.0, 5.0, 5.5))

    inside.on_tool_start({}, "")
    inside.on_tool_error(ValueError("boom"))
    inside.on_tool_start({}, "")
    inside.on_tool_end(ended("call_b"))

    assert inside.seconds() == {"call_b": 0.5}


def test_a_stray_end_is_counted_instead_of_crashing():
    """콜백이 반쪽만 오는 경우에 화면이 죽는 것보다 수가 모자란 쪽이 낫습니다."""
    inside = tool_timing.InsideGenerate(ticking(1.0))

    inside.on_tool_end(ended("ghost"))

    assert inside.seconds() == {}
    assert inside.summary(0.0)["stray_ends"] == 1


def test_the_remainder_is_what_is_left_of_generate():
    inside = tool_timing.InsideGenerate(ticking(0.0, 2.0, 2.0, 30.0))

    inside.on_tool_start({}, "")
    inside.on_tool_end(ended("call_a"))
    inside.on_chat_model_start({}, [])
    inside.on_llm_end(None)

    summary = inside.summary(35.0)
    assert summary["tools_seconds"] == 2.0
    assert summary["model_seconds"] == 28.0
    assert summary["other_seconds"] == 5.0
    assert summary["adds_up"] is True


def test_a_negative_remainder_is_reported_not_hidden():
    """잰 안쪽이 노드 전체보다 크면 분해가 틀린 것입니다. 0으로 깎지 않습니다."""
    inside = tool_timing.InsideGenerate(ticking(0.0, 10.0))

    inside.on_tool_start({}, "")
    inside.on_tool_end(ended("call_a"))

    summary = inside.summary(4.0)
    assert summary["other_seconds"] == -6.0
    assert summary["adds_up"] is False


def test_nothing_measured_says_nothing_rather_than_zero():
    summary = tool_timing.InsideGenerate().summary(0.0)

    assert summary["tool_calls"] == 0
    assert summary["model_turns"] == 0


def test_attach_says_how_many_it_attached():
    """0은 조용한 실패입니다 - 콜백이 안 걸리면 예외 없이 "도구는 공짜"가 됩니다."""
    calls = [{"id": "a", "name": "search_graph"}, {"id": "b", "name": "search_papers"}]

    attached = tool_timing.attach(calls, {"a": 1.234567, "b": 0.5})

    assert attached == 2
    assert calls[0]["seconds"] == 1.235
    assert calls[1]["seconds"] == 0.5


def test_attach_leaves_a_call_it_has_no_time_for():
    """시간이 없는 호출에 0을 적으면 재지 못한 것을 공짜라고 말하게 됩니다."""
    calls = [{"id": "a", "name": "search_graph"}, {"name": "search_papers"}]

    attached = tool_timing.attach(calls, {})

    assert attached == 0
    assert "seconds" not in calls[0]
    assert "seconds" not in calls[1]


# ── 전제 확인: tool_call_id는 도구의 끝에만 온다 ────────────────────────────


def test_the_real_callback_path_gives_the_tool_call_id():
    """LangGraph를 실제로 지나갑니다. 이 전제가 깨지면 전부 unattributed로 흘러갑니다."""
    langgraph = pytest.importorskip("langgraph.graph")
    prebuilt = pytest.importorskip("langgraph.prebuilt")
    from langchain_core.messages import AIMessage
    from langchain_core.tools import tool

    @tool
    def wait_a_moment(seconds: float) -> dict:
        """Sleep and report."""
        time.sleep(seconds)
        return {"slept": seconds}

    graph = langgraph.StateGraph(langgraph.MessagesState)
    graph.add_node("tools", prebuilt.ToolNode([wait_a_moment]))
    graph.add_edge(langgraph.START, "tools")
    graph.add_edge("tools", langgraph.END)
    runner = graph.compile()

    inside = tool_timing.InsideGenerate()
    asked = AIMessage(content="", tool_calls=[
        {"name": "wait_a_moment", "args": {"seconds": 0.12}, "id": "call_one",
         "type": "tool_call"},
        {"name": "wait_a_moment", "args": {"seconds": 0.0}, "id": "call_two",
         "type": "tool_call"},
    ])
    runner.invoke({"messages": [asked]}, config={"callbacks": [inside]})

    seconds = inside.seconds()
    assert sorted(seconds) == ["call_one", "call_two"]
    assert seconds["call_one"] > 0.1
    assert seconds["call_two"] < 0.1
    assert inside.summary(1.0)["unattributed_seconds"] == 0.0


# ── 보고서: 몇 건에 기록됐는지를 **먼저** 적는다 ────────────────────────────


def test_the_report_says_how_many_runs_carry_the_inside():
    """0건을 빈 값으로 넘기면 "안쪽은 원래 못 재는 것"으로 굳습니다."""
    from scripts.measure_answer_latency import inside_generate

    summary = inside_generate([{"inside": None}, {"inside": None}])

    assert summary == {"cases": 0, "of": 2, "note": summary["note"]}
    assert "다시 돌려야" in summary["note"]


def test_the_report_divides_the_inside_by_generate_not_by_itself():
    """몫의 분모는 `generate` 노드 시간입니다. 안쪽 합으로 나누면 `그 외`가 사라집니다."""
    from scripts.measure_answer_latency import inside_generate

    summary = inside_generate([{"inside": {
        "generate_seconds": 10.0, "tools_seconds": 2.0, "model_seconds": 7.0,
        "unattributed_seconds": 0.0, "other_seconds": 1.0, "model_turns": 3,
        "tool_calls": 2, "adds_up": True}}])

    assert summary["cases"] == 1
    assert summary["model_share"] == 0.7
    assert summary["tools_share"] == 0.2
    assert summary["other_share"] == 0.1
    assert summary["did_not_add_up"] == 0


def test_the_report_counts_the_runs_whose_inside_does_not_add_up():
    """한 건이라도 두 번 셌으면 합계의 몫이 전부 흔들립니다."""
    from scripts.measure_answer_latency import inside_generate

    summary = inside_generate([
        {"inside": {"generate_seconds": 10.0, "adds_up": True}},
        {"inside": {"generate_seconds": 10.0, "adds_up": False}},
    ])

    assert summary["cases"] == 2
    assert summary["did_not_add_up"] == 1


# ── 배선: 앱이 실제로 쓰는 호출 모양 ───────────────────────────────────────


def test_the_agent_stream_call_shape_carries_the_callbacks():
    """`chatbot.stream_agent_answer`와 **같은 호출 모양**으로 지나갑니다.

    단위 시험은 핸들러만 봅니다. 콜백을 넘기는 줄이 사라지면 그 시험들은 전부 통과하고
    계측만 조용히 죽습니다. 그래서 `create_agent`와 `stream_mode`까지 같게 둡니다.
    """
    pytest.importorskip("langchain.agents")
    from langchain.agents import create_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool

    @tool
    def wait_a_moment(seconds: float) -> dict:
        """Sleep and report."""
        time.sleep(seconds)
        return {"slept": seconds}

    class ScriptedModel(BaseChatModel):
        """적어 둔 턴을 차례로 돌려주는 모델. 호출이 0건이라 비용이 없습니다."""

        turns: list

        @property
        def _llm_type(self) -> str:
            return "scripted"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            return ChatResult(generations=[ChatGeneration(message=self.turns.pop(0))])

    agent = create_agent(model=ScriptedModel(turns=[
        AIMessage(content="", tool_calls=[
            {"name": "wait_a_moment", "args": {"seconds": 0.12}, "id": "call_one",
             "type": "tool_call"}]),
        AIMessage(content="done"),
    ]), tools=[wait_a_moment])

    inside = tool_timing.InsideGenerate()
    for _ in agent.stream({"messages": [("user", "hi")]},
                          stream_mode=["messages", "values"],
                          config={"callbacks": [inside]}):
        pass

    summary = inside.summary(10.0)
    assert inside.seconds()["call_one"] > 0.1
    assert summary["model_turns"] == 2
    assert summary["unattributed_seconds"] == 0.0
    assert summary["adds_up"] is True


def test_the_app_passes_the_recorder_into_the_stream():
    """계측이 죽는 가장 조용한 방법은 이 한 줄이 사라지는 것입니다."""
    source = Path("src/chatbot.py").read_text(encoding="utf-8")

    assert "tool_timing.InsideGenerate()" in source
    assert 'config={"callbacks": list(callbacks)}' in source
    assert "stream_agent_answer(\n            messages, on_answer, [inside_generate]" in source
    # 붙인 개수를 보고서에 남기지 않으면 0을 알아차릴 방법이 없습니다.
    assert "tool_timing.attach(" in source
