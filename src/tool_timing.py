"""`generate` 노드 **안쪽**을 잽니다 - 도구 호출마다, 그리고 모델 턴 전체.

[ADR-065](docs/adr/ADR-065-say-how-each-paper-was-found.md)가 지연을 노드별로 갈랐을 때
결론은 두 노드가 전부라는 것이었습니다 - `generate` 72.8%, `check_conditions` 27.2%.
그런데 그 72.8%가 한 덩어리였습니다. 번역·Text2Cypher·벡터 검색·모델 턴이 모두 그 안에
있는데 **어느 것이 긴지 말할 수 없었습니다.** 그래서 화면은 "답변 생성 35.1초" 뒤에
괄호로 "번역·Cypher·벡터·모델 턴 포함"을 적어 두었습니다 - 재지 못했다는 고백이었습니다.

여기서 그것을 잽니다. **도구 본문을 건드리지 않습니다.** LangChain 콜백이 도구와 모델의
시작·끝을 알려 주고, 도구의 끝에는 `ToolMessage`가 따라오므로 `tool_call_id`로 어느
호출인지 정확히 붙일 수 있습니다. 호출 시각 순서로 추측하지 않습니다.

**세 가지를 일부러 다르게 다룹니다.**

1. **도구 안에서 모델을 부르는 경우.** `translate_to_english`가 그렇습니다. 콜백은
   도구와 모델 양쪽에 오므로 그냥 더하면 **같은 시간을 두 번 셉니다.** 도구 프레임이
   쌓여 있는 동안의 모델 호출은 모델 몫에서 빼고 도구 몫에만 둡니다.
2. **오류로 끝난 도구.** 끝에 `ToolMessage`가 없어 어느 호출인지 모릅니다. 버리지 않고
   `unattributed_seconds`로 따로 적습니다 - 쓴 시간을 0으로 적으면 나머지가 그만큼
   부풀어 "설명되지 않은 시간"으로 보입니다.
3. **한 모델 턴이 도구를 여럿 부르는 경우.** LangGraph는 그것들을 동시에 돌립니다. 호출마다의
   초를 그냥 더하면 겹친 구간을 두 번 셉니다(2026-10-26 `citing-02`: 나머지가 -0.194초).
   도구 몫은 **구간의 합집합**이고, 호출마다의 초는 그대로 남깁니다.
4. **나머지(`other_seconds`)는 잰 값이 아니라 뺀 값입니다.** `generate` 전체에서 위를
   덜어낸 것이고, 스트리밍 루프·부분 JSON 파싱·직렬화가 거기 들어갑니다. 음수가 나오면
   분해가 틀린 것이므로 `adds_up`이 false가 되고, 화면은 수를 적지 않습니다.

**여전히 재지 못하는 것**은 모델 턴 **하나의 안쪽**입니다 - 대기와 토큰 생성의 몫은
공급자가 알려 주지 않습니다.
"""

from __future__ import annotations

import threading
from time import perf_counter
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler

# 뺀 나머지가 이보다 더 음수면 분해가 틀린 것입니다. 반올림과 콜백 사이의 틈을 감안한 폭입니다.
OVERLAP_TOLERANCE = 0.05

TOOL, MODEL = "tool", "model"


class InsideGenerate(BaseCallbackHandler):
    """한 질문 동안 도구와 모델이 각각 얼마를 썼는지 모읍니다.

    프레임 더미를 **스레드마다** 따로 둡니다. 한 모델 턴이 도구를 여러 개 부르면 LangGraph가
    그것들을 스레드로 나눠 돌리고, 더미가 하나면 시작과 끝이 엇갈려 서로의 시간을 받습니다.
    """

    def __init__(self, clock=perf_counter):
        self._clock = clock
        self._frames = threading.local()
        self._lock = threading.Lock()
        self._calls: dict[str, float] = {}
        self._spans: list[tuple[float, float]] = []
        self._model_seconds = 0.0
        self._model_turns = 0
        self._unattributed = 0.0
        self._stray_ends = 0

    # ── 프레임 더미 ───────────────────────────────────────────────────────
    def _stack(self) -> list[dict[str, Any]]:
        stack = getattr(self._frames, "stack", None)
        if stack is None:
            stack = self._frames.stack = []
        return stack

    def _push(self, kind: str) -> None:
        stack = self._stack()
        # 도구 안에서 부른 모델은 세지 않습니다. 그 시간은 이미 도구 프레임이 재고 있습니다.
        nested = any(frame["kind"] == TOOL for frame in stack)
        stack.append({"kind": kind, "start": self._clock(), "counted": not nested})

    def _pop(self, kind: str) -> dict[str, Any] | None:
        stack = self._stack()
        for index in range(len(stack) - 1, -1, -1):
            if stack[index]["kind"] == kind:
                frame = stack.pop(index)
                return {**frame, "seconds": self._clock() - frame["start"]}
        with self._lock:
            self._stray_ends += 1
        return None

    # ── 도구 ─────────────────────────────────────────────────────────────
    def on_tool_start(self, serialized, input_str, **kwargs) -> None:
        self._push(TOOL)

    def on_tool_end(self, output, **kwargs) -> None:
        frame = self._pop(TOOL)
        if frame is None:
            return
        # 도구의 끝에는 `ToolMessage`가 옵니다. 여기에만 `tool_call_id`가 있습니다.
        call_id = getattr(output, "tool_call_id", None)
        with self._lock:
            if isinstance(call_id, str) and call_id:
                self._calls[call_id] = self._calls.get(call_id, 0.0) + frame["seconds"]
                self._spans.append((frame["start"], frame["start"] + frame["seconds"]))
            else:
                self._unattributed += frame["seconds"]

    def on_tool_error(self, error, **kwargs) -> None:
        frame = self._pop(TOOL)
        if frame is None:
            return
        # 어느 호출이었는지 알 수 없습니다. 쓴 시간은 사실이므로 따로 적습니다.
        with self._lock:
            self._unattributed += frame["seconds"]

    # ── 모델 ─────────────────────────────────────────────────────────────
    def on_llm_start(self, serialized, prompts, **kwargs) -> None:
        self._push(MODEL)

    def on_chat_model_start(self, serialized, messages, **kwargs) -> None:
        self._push(MODEL)

    def on_llm_end(self, response, **kwargs) -> None:
        frame = self._pop(MODEL)
        if frame is None or not frame["counted"]:
            return
        with self._lock:
            self._model_seconds += frame["seconds"]
            self._model_turns += 1

    def on_llm_error(self, error, **kwargs) -> None:
        frame = self._pop(MODEL)
        if frame is None or not frame["counted"]:
            return
        with self._lock:
            self._model_seconds += frame["seconds"]
            self._model_turns += 1

    # ── 읽기 ─────────────────────────────────────────────────────────────
    def seconds(self) -> dict[str, float]:
        """`tool_call_id` -> 초. 시도가 두 번이면 양쪽 호출이 모두 들어 있습니다."""
        with self._lock:
            return dict(self._calls)

    def summary(self, generate_seconds: float) -> dict[str, Any]:
        """`generate` 전체를 도구·모델·나머지로 가릅니다.

        `generate_seconds`는 `graph_trace`가 잰 노드 시간입니다. 재시도가 있으면 그 값도
        시도 전부의 합이고, 여기 모인 호출도 시도 전부이므로 같은 범위를 비교합니다.
        """
        with self._lock:
            summed = round(sum(self._calls.values()), 3)
            tools = round(covered_seconds(self._spans), 3)
            model = round(self._model_seconds, 3)
            unattributed = round(self._unattributed, 3)
            turns, strays, calls = self._model_turns, self._stray_ends, len(self._calls)
        other = round(generate_seconds - tools - model - unattributed, 3)
        return {
            "generate_seconds": round(generate_seconds, 3),
            # 겹친 구간을 한 번만 센 벽시계 시간입니다. 호출마다의 합은 아래에 따로 둡니다.
            "tools_seconds": tools,
            "tools_seconds_summed": summed,
            "tool_calls": calls,
            "model_seconds": model,
            "model_turns": turns,
            "unattributed_seconds": unattributed,
            # 잰 값이 아니라 뺀 값입니다. 스트리밍·부분 JSON 파싱·직렬화가 여기 있습니다.
            "other_seconds": other,
            "adds_up": other >= -OVERLAP_TOLERANCE,
            "stray_ends": strays,
        }


def covered_seconds(spans: list[tuple[float, float]]) -> float:
    """구간들이 덮는 길이. 동시에 돈 도구 둘은 겹친 만큼 한 번만 셉니다."""
    total, reach = 0.0, None
    for start, end in sorted(spans):
        if reach is None or start > reach:
            total += end - start
            reach = end
        elif end > reach:
            total += end - reach
            reach = end
    return total


def attach(calls: list[dict[str, Any]], seconds: dict[str, float]) -> int:
    """호출 기록에 `seconds`를 붙이고 **붙인 개수**를 돌려줍니다.

    개수를 돌려주는 이유는 0이 조용한 실패이기 때문입니다 - 콜백이 걸리지 않으면 예외 없이
    아무 일도 일어나지 않고, 화면은 "도구는 공짜"라고 읽힙니다.
    """
    attached = 0
    for call in calls:
        value = seconds.get(call.get("id"))
        if value is None:
            continue
        call["seconds"] = round(value, 3)
        attached += 1
    return attached
