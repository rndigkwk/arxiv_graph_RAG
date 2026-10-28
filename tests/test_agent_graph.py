"""생성·검증·수정 흐름이 손으로 쓴 2회 루프와 **같게** 도는지 검증합니다.

구조만 바꾸는 작업이므로 지켜야 할 것은 "그래프가 돈다"가 아니라 **판정과 행동이 그대로다**
입니다. 그래서 여기서 고정하는 것은 세 가지입니다 — 어떤 순서로 노드를 지나는가, 몇 번
고쳐 보는가, 두 번 실패하면 보류로 가는가.

`route` 노드는 뒤에 더해진 것이고(ADR-024) 여기서는 **경로를 한 번만 정하는지**와 **힌트가
이번 턴에만 붙는지**를 봅니다. 분류 규칙 자체는 `tests/test_routing.py`에 있습니다.

`chatbot`은 임포트만으로 Aura와 OpenAI에 붙으므로 가져오지 않습니다. `agent_graph`는 무거운
것을 전부 주입받으므로 **네트워크 없이** 돌 수 있습니다.
"""

from __future__ import annotations

import pytest

from src.agent_graph import (
    CONDITION_STAGE,
    FORMAT_STAGE,
    MAX_ATTEMPTS,
    Dependencies,
    build_graph,
    summarize_trace,
)

FORMAT_ERROR = "조회 결과의 evidence_ids가 arxiv_id 목록이 아닙니다. 다시 질문해 주세요."
CONDITION_ERROR = "그래프 조건을 통과하지 않은 논문이 인용됐습니다."


def make_deps(*, format_failures=0, condition_failures=0, verdict=None, incomplete=False):
    """Build dependencies whose failures are scripted, so routing is the only variable."""
    state = {"format": 0, "conditions": 0}
    logs: list[tuple[int, str, str]] = []
    generated: list[list] = []

    def stream_answer(messages, attempt):
        generated.append(list(messages))
        if incomplete:
            return {"messages": list(messages)}
        return {"structured_response": object(), "messages": [*messages, ("ai", "draft")]}

    def collect(result, question):
        return {"answer": "A", "evidence_ids": ["2601.00001"], "cyphers": ["MATCH (p) RETURN p"],
                "tool_calls": []}

    def check_format(response):
        state["format"] += 1
        if state["format"] <= format_failures:
            raise ValueError(FORMAT_ERROR)

    def check_conditions(question, response):
        state["conditions"] += 1
        if state["conditions"] <= condition_failures:
            raise ValueError(CONDITION_ERROR)
        return verdict

    def log_failure(attempt, stage, exc):
        logs.append((attempt, stage, str(exc)))

    deps = Dependencies(stream_answer, collect, check_format, check_conditions, log_failure)
    return deps, logs, generated


def run(deps, question="q", history=()):
    graph = build_graph(deps)
    return graph.invoke({
        "question": question,
        "history": list(history),
        "messages": [*history, ("user", question)],
        "attempt": 0,
        "trace": [],
    })


def nodes(state):
    return [entry["node"] for entry in state["trace"]]


def test_a_clean_answer_goes_straight_to_accept():
    deps, logs, generated = make_deps(verdict={"requires_content": True})

    state = run(deps)

    assert nodes(state) == [
        "route", "generate", "collect", "check_format", "check_conditions", "accept",
    ]
    assert state["abstained"] is False
    assert logs == []
    assert len(generated) == 1


def test_the_condition_verdict_is_attached_to_the_response():
    """`condition_validation`은 계산만 되고 어디에도 드러나지 않던 값입니다."""
    deps, _, _ = make_deps(verdict={"requires_graph": True, "graph_conditions": ["cs.AI"]})

    state = run(deps)

    assert state["response"]["condition_validation"] == {
        "requires_graph": True,
        "graph_conditions": ["cs.AI"],
    }


def test_a_format_failure_is_revised_once_and_then_accepted():
    deps, logs, generated = make_deps(format_failures=1)

    state = run(deps)

    assert nodes(state) == [
        "route",
        "generate", "collect", "check_format",
        "revise",
        # 수정은 `generate`로 돌아갑니다. 질문이 그대로이므로 경로를 다시 정하지 않습니다.
        "generate", "collect", "check_format", "check_conditions", "accept",
    ]
    assert state["abstained"] is False
    # 손으로 쓴 루프와 같이 한 번만 고쳐 봅니다.
    assert len(generated) == 2
    assert logs == [(1, FORMAT_STAGE, FORMAT_ERROR)]


def test_a_condition_failure_takes_the_same_path():
    deps, logs, _ = make_deps(condition_failures=1)

    state = run(deps)

    assert nodes(state).count("generate") == 2
    assert state["abstained"] is False
    assert logs == [(1, CONDITION_STAGE, CONDITION_ERROR)]


def test_two_failures_end_in_abstain():
    deps, logs, generated = make_deps(format_failures=2)

    state = run(deps)

    assert nodes(state)[-1] == "abstain"
    assert state["abstained"] is True
    # 보류 뒤에 또 생성하지 않습니다.
    assert len(generated) == MAX_ATTEMPTS
    # 두 실패가 모두 기록됩니다. 두 번째는 보류 노드가 남깁니다.
    assert [entry[0] for entry in logs] == [1, 2]


def test_the_revision_request_carries_the_failure_reason():
    """모델이 무엇을 고쳐야 하는지 알아야 합니다. 2026-10-16의 별칭 사고가 그 경로였습니다."""
    deps, _, generated = make_deps(format_failures=1)

    run(deps)

    revision = generated[1][-1]
    assert revision[0] == "user"
    assert FORMAT_ERROR in revision[1]
    assert "Copy every arxiv_id exactly" in revision[1]


def test_incomplete_generation_is_not_treated_as_a_validation_failure():
    """생성이 끝나지 않은 것은 수정으로 고쳐지지 않습니다. 그래프 밖으로 올립니다."""
    deps, logs, _ = make_deps(incomplete=True)

    with pytest.raises(ValueError, match="답변 생성이 완료되지 않았습니다"):
        run(deps)
    assert logs == []


def test_history_is_excluded_from_this_turn_s_tool_calls():
    """과거 대화의 검색 기록이 이번 근거로 섞이면 인용 검증이 무의미해집니다."""
    seen = {}

    def collect(result, question):
        seen["messages"] = list(result["messages"])
        return {"answer": "A", "evidence_ids": [], "cyphers": [], "tool_calls": []}

    deps, _, _ = make_deps(verdict=None)
    deps = Dependencies(deps.stream_answer, collect, deps.check_format,
                        deps.check_conditions, deps.log_failure)

    run(deps, history=[("user", "old"), ("ai", "older")])

    # 과거 두 개를 빼고 이번 턴의 메시지만 수집에 넘어갑니다.
    assert ("user", "old") not in seen["messages"]


def test_the_trace_records_every_node_with_its_attempt():
    deps, _, _ = make_deps(format_failures=1)

    state = run(deps)
    summary = summarize_trace(state["trace"])

    assert summary["visits"]["generate"] == 2
    assert summary["visits"]["accept"] == 1
    assert summary["attempts"] == 2
    assert summary["total_seconds"] >= 0.0
    # 두 번째 시도의 노드들은 attempt=2로 적힙니다.
    assert {entry["attempt"] for entry in state["trace"]} == {1, 2}


def test_the_trace_can_point_at_one_slow_node():
    """이 추적의 목적입니다 - "답변이 느리다"를 "어느 단계가 느리다"로 바꿉니다."""
    state = run(make_deps(verdict=None)[0])
    summary = summarize_trace(state["trace"])

    assert set(summary["seconds"]) == set(summary["visits"])
    assert summary["total_seconds"] == pytest.approx(
        sum(summary["seconds"].values()), abs=0.01
    )


def test_the_route_is_decided_once_even_when_the_answer_is_revised():
    """질문이 그대로인데 경로를 다시 정하면 힌트가 두 번 붙습니다."""
    deps, _, generated = make_deps(format_failures=1)

    state = run(deps, question="Which papers cite the paper titled Attention?")

    assert nodes(state).count("route") == 1
    assert state["route"]["name"] == "graph"
    # 수정 요청 뒤에도 힌트는 하나뿐입니다.
    hints = [m for m in generated[1] if isinstance(m, tuple) and "Routing hint" in str(m[1])]
    assert len(hints) == 1


def test_a_narrowed_route_appends_the_hint_to_this_turn():
    deps, _, generated = make_deps(verdict=None)

    run(deps, question="Which other papers share an author with Hyperbolic Neural Networks?")

    last = generated[0][-1]
    assert last[0] == "user"
    assert "Do not call search_papers" in last[1]


def test_a_broad_route_appends_nothing():
    """의미 검색이 필요한 질문에 힌트를 붙이면 답을 못 찾는 쪽으로 틀립니다."""
    deps, _, generated = make_deps(verdict=None)

    state = run(deps, question="Which papers are about graph neural networks?")

    assert state["route"]["name"] == "any"
    assert generated[0] == [("user", "Which papers are about graph neural networks?")]


def test_the_route_is_attached_to_the_response_so_it_can_be_counted():
    """지시를 지켰는지는 보고서에서 세야 하고, 세려면 답변과 같은 줄에 있어야 합니다."""
    deps, _, _ = make_deps(verdict=None)

    state = run(deps, question="Which papers cite the paper titled Attention?")

    assert state["response"]["route"]["name"] == "graph"
    assert state["response"]["route"]["cues"] == ["citation"]


def test_the_trace_names_the_gate_that_rejected():
    """방문 횟수로는 두 게이트를 가를 수 없습니다.

    어느 쪽이 막아도 다음 시도에서 `check_format`을 다시 지나므로 횟수가 겹칩니다.

    ADR-069에서 저장된 180회 중 재시도 네 건이 어느 단계였는지 손으로 추론해야 했습니다.
    같은 값을 다시 추론하지 않으려고 막은 게이트가 스스로 이름을 적습니다.
    """
    by_format = summarize_trace(run(make_deps(format_failures=1)[0])["trace"])
    by_condition = summarize_trace(run(make_deps(condition_failures=1)[0])["trace"])

    # 가르려는 두 경우가 방문 횟수에서는 같습니다 - 이것이 없으면 시험이 아무것도 재지 않습니다.
    assert by_format["visits"]["check_format"] == 2
    assert by_condition["visits"]["check_format"] == 2
    assert [entry["stage"] for entry in by_format["rejections"]] == [FORMAT_STAGE]
    assert [entry["stage"] for entry in by_condition["rejections"]] == [CONDITION_STAGE]
    assert by_condition["rejections"][0]["node"] == "check_conditions"


def test_a_clean_answer_records_no_rejection():
    """빈 목록은 "한 번에 통과했다"입니다. 없는 키와 다릅니다."""
    assert summarize_trace(run(make_deps()[0])["trace"])["rejections"] == []


def test_both_failures_are_named_when_the_answer_is_abstained():
    summary = summarize_trace(run(make_deps(format_failures=2)[0])["trace"])

    assert [entry["attempt"] for entry in summary["rejections"]] == [1, 2]
    assert {entry["stage"] for entry in summary["rejections"]} == {FORMAT_STAGE}


# ── 범위 밖 질문(ADR-095) ─────────────────────────────────────────────────────

def _with_scope(deps, scope, recent=None):
    from dataclasses import replace
    return replace(deps, scope=scope, recent=recent)


def test_an_out_of_scope_question_never_reaches_the_agent():
    deps, _, generated = make_deps()
    record = {"name": "search_papers", "by": "app", "status": "success", "output": {}}
    deps = _with_scope(deps, lambda q, en: {"decline": True, "answer": "outside", "record": record})
    state = run(deps, "What's the weather?")
    assert generated == []  # 에이전트를 한 번도 부르지 않았습니다.
    assert nodes(state) == ["scope", "decline"]
    response = state["response"]
    assert response["answer"] == "outside" and response["evidence_ids"] == []
    assert response["tool_calls"] == [record] and response["scope"] == {"declined": True}
    assert state["abstained"] is False
    # 대화 기록에 이 답이 남습니다.
    assert state["result"]["messages"][-1].content == "outside"


def test_a_loose_match_goes_to_the_agent_untouched():
    deps, _, generated = make_deps()
    record = {"name": "search_papers", "by": "app", "status": "success", "output": {}}
    deps = _with_scope(deps, lambda q, en: {"record": record})
    state = run(deps, "What is protein structure prediction?")
    assert nodes(state)[:3] == ["scope", "route", "generate"]
    # 메시지를 붙이지 않습니다 - 붙였더니 범위 안 질문까지 거절했습니다(ADR-095 후속).
    assert generated[0] == [("user", "What is protein structure prediction?")]
    assert state["response"]["tool_calls"][0] is record
    assert state["response"]["scope"] == {"declined": False}


def test_an_in_scope_question_is_untouched():
    deps, _, generated = make_deps()
    deps = _with_scope(deps, lambda q, en: None)
    state = run(deps, "papers by X")
    assert nodes(state)[:2] == ["scope", "route"]
    assert generated[0] == [("user", "papers by X")]
    assert "scope" not in state["response"]


def test_a_recent_search_that_found_papers_skips_the_scope_check():
    deps, _, _ = make_deps()
    deps = _with_scope(deps, lambda q, en: pytest.fail("범위를 보면 안 됩니다"),
                       recent=lambda q, en: {"record": None, "hint": "RECENT"})
    run(deps, "recent papers")
