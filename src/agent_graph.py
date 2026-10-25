"""`ask()`의 생성·검증·수정 흐름을 LangGraph `StateGraph`로 세웁니다.

Day 50~53 교안의 구조입니다. 바꾸는 것은 **흐름의 모양**이고, 각 단계가 하는 일과 판정 결과는
그대로입니다. 손으로 쓴 2회 루프를 그래프로 옮겨서 얻는 것이 셋입니다.

1. **단계가 이름을 갖습니다.** 어디서 얼마나 걸렸고 몇 번 들렀는지가 `response["graph_trace"]`에
   남습니다. 지금까지 "답변이 느리다"를 말할 때 어느 단계인지 가리킬 수 없었습니다.
2. **조건 판정이 노드가 됩니다.** `condition_validation`은 계산돼서 응답에 담기기만 했고
   화면에도 추적에도 드러나지 않았습니다.
3. **보류 경로가 자리를 얻습니다.** 두 번 실패하면 `abstain` 노드를 지나고, 그 사실이 추적에
   남습니다.
4. **경로 선택이 노드가 됩니다.** `route`가 질문을 보고 저장된 관계만 묻는 질문을 가려냅니다
   ([`routing.py`](routing.py), ADR-024). 그 전까지 경로 선택은 시스템 프롬프트의 문장으로만
   있었고, 모델이 지키는지 세지 않았습니다.

**행동은 바꾸지 않았습니다.** `abstain`은 지금도 `AnswerValidationError`를 올립니다. 평가
하네스가 그 예외를 "답하지 못함"으로 세고 게이트의 `answer_failure_rate` 기준선이 그 정의로
측정돼 있기 때문입니다. 사용자에게 보류 문장을 보여 주는 쪽으로 바꾸려면 기준선을 다시 재야
하므로 분리합니다.

무거운 것은 전부 주입받습니다. 이 모듈은 `chatbot`을 임포트하지 않으므로 **네트워크 없이
라우팅과 보류 경로를 테스트할 수 있습니다.**
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from src import routing

# 한 질문에 허용하는 생성 시도 횟수입니다. 손으로 쓴 루프의 `range(2)`와 같은 값입니다.
MAX_ATTEMPTS = 2
# 검증 단계 이름입니다. 실패 로그의 `stage=`로 그대로 나갑니다.
FORMAT_STAGE = "answer_format_citation"
CONDITION_STAGE = "mixed_candidate_conditions"
REVISION_REQUEST = (
    "Answer validation failed: {reason}. Check both the topic and graph conditions for "
    "each cited paper. Search again if needed. Remove papers that fail either condition. "
    "Copy every arxiv_id exactly from this turn's direct search results. If no paper "
    "satisfies all conditions, say so in English with empty evidence_ids."
)


class AnswerState(TypedDict, total=False):
    """노드들이 나눠 쓰는 값입니다."""

    question: str
    history: list[Any]
    messages: list[Any]
    route: dict[str, Any]
    attempt: int
    result: dict[str, Any]
    response: dict[str, Any]
    stage: str
    failure: Exception | None
    abstained: bool
    trace: list[dict[str, Any]]


@dataclass(frozen=True)
class Dependencies:
    """그래프가 바깥에서 받는 것들. 전부 호출 가능한 값이라 테스트에서 바꿔 끼웁니다."""

    stream_answer: Callable[[list[Any], int], dict[str, Any]]
    collect: Callable[[dict[str, Any], str], dict[str, Any]]
    check_format: Callable[[dict[str, Any]], None]
    check_conditions: Callable[[str, dict[str, Any]], Any]
    log_failure: Callable[[int, str, Exception], None]


def _timed(name: str, state: AnswerState, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """한 노드를 재고, 걸린 시간과 방문 순서를 추적에 더합니다."""
    start = perf_counter()
    update = work()
    entry = {
        "node": name,
        "attempt": state.get("attempt", 0) + 1,
        "seconds": round(perf_counter() - start, 3),
    }
    # 막은 게이트가 스스로 이름을 적습니다. **방문 횟수만으로는 두 게이트를 가를 수
    # 없습니다** - `check_format`이 막아도, `check_conditions`가 막아도 다음 시도에서
    # `check_format`을 다시 지나므로 횟수가 겹칩니다. ADR-069에서 저장된 180회의 재시도
    # 네 건이 어느 단계였는지 손으로 추론해야 했습니다. 단계 이름과 시도 번호만 남기므로
    # ADR-023의 추적 payload 제한 안입니다.
    if update.get("failure") is not None:
        entry["rejected"] = update["stage"]
    # 리듀서를 쓰지 않습니다. 노드가 순서대로 한 번씩 돌므로 직접 이어 붙이는 쪽이 읽기 쉽습니다.
    return {**update, "trace": [*state.get("trace", []), entry]}


def build_graph(deps: Dependencies):
    """Compile the generate → validate → revise → accept/abstain graph."""

    def route(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            decided = routing.classify(state["question"])
            if not decided.hint:
                return {"route": decided.as_dict()}
            # 힌트는 이번 턴에만 붙습니다. 과거 대화에 남기면 다음 질문의 경로까지 좁힙니다.
            return {
                "route": decided.as_dict(),
                "messages": [*state["messages"], ("user", decided.hint)],
            }

        return _timed("route", state, work)

    def generate(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            result = deps.stream_answer(state["messages"], state.get("attempt", 0))
            if "structured_response" not in result:
                # 생성이 끝나지 않은 것은 검증 실패와 다릅니다. 수정으로 고쳐지지 않습니다.
                raise ValueError("답변 생성이 완료되지 않았습니다. 다시 질문해 주세요.")
            return {"result": result}

        return _timed("generate", state, work)

    def collect(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            result = state["result"]
            # 과거 대화를 제외하고, 이번 질문의 검색과 수정 호출만 모읍니다.
            current = {**result, "messages": result["messages"][len(state["history"]):]}
            response = deps.collect(current, state["question"])
            # 경로를 응답에 실어 둡니다. 지시를 지켰는지는 보고서에서 세어야 알 수 있고,
            # 세려면 판정이 답변과 같은 줄에 남아 있어야 합니다(ADR-024).
            response["route"] = state.get("route", {})
            return {"response": response}

        return _timed("collect", state, work)

    def check_format(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            try:
                deps.check_format(state["response"])
            except ValueError as exc:
                return {"failure": exc, "stage": FORMAT_STAGE}
            return {"failure": None, "stage": FORMAT_STAGE}

        return _timed("check_format", state, work)

    def check_conditions(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            response = state["response"]
            try:
                verdict = deps.check_conditions(state["question"], response)
            except ValueError as exc:
                return {"failure": exc, "stage": CONDITION_STAGE}
            # 지금까지 응답에만 담기고 어디에도 드러나지 않던 값입니다.
            response["condition_validation"] = verdict
            return {"failure": None, "stage": CONDITION_STAGE}

        return _timed("check_conditions", state, work)

    def revise(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            failure = state["failure"]
            deps.log_failure(state.get("attempt", 0) + 1, state["stage"], failure)
            # 검색한 근거와 실패 이유를 그대로 전달해 한 번만 수정하고 다시 검사합니다.
            request = REVISION_REQUEST.format(reason=failure)
            return {
                "messages": [*state["result"]["messages"], ("user", request)],
                "attempt": state.get("attempt", 0) + 1,
            }

        return _timed("revise", state, work)

    def accept(state: AnswerState) -> dict[str, Any]:
        return _timed("accept", state, lambda: {"abstained": False})

    def abstain(state: AnswerState) -> dict[str, Any]:
        def work() -> dict[str, Any]:
            deps.log_failure(state.get("attempt", 0) + 1, state["stage"], state["failure"])
            return {"abstained": True}

        return _timed("abstain", state, work)

    def after_format(state: AnswerState) -> str:
        return "check_conditions" if state.get("failure") is None else "route_failure"

    def after_conditions(state: AnswerState) -> str:
        return "accept" if state.get("failure") is None else "route_failure"

    def route_failure(state: AnswerState) -> str:
        """한 번만 고쳐 봅니다. 그 뒤에는 보류합니다."""
        return "revise" if state.get("attempt", 0) + 1 < MAX_ATTEMPTS else "abstain"

    graph = StateGraph(AnswerState)
    for name, node in (
        ("route", route),
        ("generate", generate),
        ("collect", collect),
        ("check_format", check_format),
        ("check_conditions", check_conditions),
        ("revise", revise),
        ("accept", accept),
        ("abstain", abstain),
    ):
        graph.add_node(name, node)

    graph.add_edge(START, "route")
    graph.add_edge("route", "generate")
    graph.add_edge("generate", "collect")
    graph.add_edge("collect", "check_format")
    graph.add_conditional_edges(
        "check_format",
        lambda state: (
            "check_conditions" if after_format(state) == "check_conditions"
            else route_failure(state)
        ),
        {"check_conditions": "check_conditions", "revise": "revise", "abstain": "abstain"},
    )
    graph.add_conditional_edges(
        "check_conditions",
        lambda state: "accept" if after_conditions(state) == "accept" else route_failure(state),
        {"accept": "accept", "revise": "revise", "abstain": "abstain"},
    )
    graph.add_edge("revise", "generate")
    graph.add_edge("accept", END)
    graph.add_edge("abstain", END)
    return graph.compile()


def summarize_trace(trace: list[dict[str, Any]]) -> dict[str, Any]:
    """Node visits and seconds, so a slow answer can be pointed at a node."""
    visits: dict[str, int] = {}
    seconds: dict[str, float] = {}
    for entry in trace:
        visits[entry["node"]] = visits.get(entry["node"], 0) + 1
        seconds[entry["node"]] = round(seconds.get(entry["node"], 0.0) + entry["seconds"], 3)
    return {
        "nodes": trace,
        "visits": visits,
        "seconds": seconds,
        # 막힌 자리입니다. 비어 있으면 한 번에 통과했다는 뜻이고, 방문 횟수에서 추론할
        # 필요가 없습니다(ADR-069).
        "rejections": [
            {"node": entry["node"], "stage": entry["rejected"], "attempt": entry["attempt"]}
            for entry in trace if "rejected" in entry
        ],
        "attempts": max((entry["attempt"] for entry in trace), default=0),
        "total_seconds": round(sum(entry["seconds"] for entry in trace), 3),
    }
