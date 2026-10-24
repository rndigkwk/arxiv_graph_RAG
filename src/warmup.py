"""무거운 캐시를 **배경에서 미리** 올립니다 — 첫 방문자가 그 값을 혼자 내지 않도록.

ADR-066이 `generate` 안쪽을 가르자마자 첫 질문의 정체가 드러났습니다. 배포 앱에서 재부팅
직후와 바로 다음 질문을 연달아 재면 이렇습니다.

| | 1회차 | 2회차 |
|---|---:|---:|
| 합계 | 46.9초 | 22.7초 |
| `rank_related_papers` | **26.49초** | **0.83초** |

차이는 전부 `chatbot.citation_graph()`입니다 — 논문 38,665편과 간선 69,438개를 읽어 행렬을
만드는 **일회성** 작업입니다. 그 한 덩어리만 따로 재면 p50 15.39초이고 캐시된 조회는
0.0초입니다(`scripts/measure_citation_graph_warmup.py`, 3회). Streamlit Community Cloud는
앱을 재우므로, 깨어난 뒤 **첫 질문**이 그 값을 전부 냅니다. 포트폴리오를 보러 온 사람이
던지는 유일한 질문이 보통 그 질문입니다.

**이 모듈은 아무것도 빠르게 하지 않습니다.** 값을 내는 **시점**만 옮깁니다 — 사람이 홈
화면을 읽는 동안으로. 그래서 두 가지를 숨기지 않습니다.

1. **공짜가 아닙니다.** `scripts/measure_app_memory.py`가 단계별로 재 둡니다 -
   `chatbot_imported` 223.0MB에서 `citation_graph_loaded` 266.9MB로 **+43.9MB**이고
   최고점은 268.5MB, 남은 여유가 421.5MB입니다(`reports/app_memory.json`). 예전에는
   추천을 한 번도 안 쓴 세션이면 내지 않던 값인데, 이제 항상 냅니다.
2. **다 올리기 전에 물으면 여전히 기다립니다.** `citation_graph()`가 `graph_lock`을 쥐고
   짓기 때문에, 그 사이에 들어온 질문은 **같은 작업을 두 번 하지 않고** 끝날 때까지
   막힙니다. 빨라지는 것이 아니라 겹쳐지는 것입니다.

그래서 화면에는 **예상 시간을 적지 않습니다.** 지금까지 걸린 초를 그대로 보여 줍니다 —
예상값은 늙지만 경과는 늙지 않습니다(ADR-016에서 화면의 수가 조용히 늙은 적이 있습니다).
"""

from __future__ import annotations

import logging
import threading
from time import perf_counter
from typing import Any, Callable

warmup_logger = logging.getLogger("arxiv_graph_rag.warmup")

IDLE, RUNNING, DONE, FAILED = "idle", "running", "done", "failed"


class Warmup:
    """한 가지를 배경에서 미리 짓고, **언제 끝났는지와 왜 실패했는지**를 들고 있습니다.

    실패를 삼키지 않는 것이 이 클래스의 절반입니다. 배경 스레드에서 터진 예외는 아무 데도
    나타나지 않으므로, 예열이 조용히 죽고 첫 질문이 여전히 느린 상태가 **고쳐진 것처럼**
    보입니다. 그래서 사유를 남기고 화면이 읽습니다.
    """

    def __init__(self, build: Callable[[], Any], clock: Callable[[], float] = perf_counter):
        self._build = build
        self._clock = clock
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._status = IDLE
        self._started: float | None = None
        self._seconds: float | None = None
        self._reason: str | None = None

    def start(self) -> threading.Thread:
        """한 번만 시작합니다. 두 번 불러도 같은 스레드를 돌려줍니다."""
        with self._lock:
            if self._thread is not None:
                return self._thread
            self._status = RUNNING
            self._started = self._clock()
            # 데몬입니다 - 예열이 끝나지 않아도 앱 종료를 붙잡지 않습니다.
            self._thread = threading.Thread(
                target=self._work, name="warmup", daemon=True)
            self._thread.start()
            return self._thread

    def _work(self) -> None:
        try:
            self._build()
        except Exception as exc:  # noqa: BLE001 - 어떤 실패든 화면이 읽을 수 있어야 합니다.
            seconds = self._clock() - (self._started or self._clock())
            with self._lock:
                self._status, self._reason, self._seconds = FAILED, type(exc).__name__, seconds
            warmup_logger.warning(
                "warmup_failed reason=%s seconds=%.1f", type(exc).__name__, seconds)
            return
        seconds = self._clock() - (self._started or self._clock())
        with self._lock:
            self._status, self._seconds = DONE, seconds
        warmup_logger.info("warmup_done seconds=%.1f", seconds)

    def state(self) -> dict[str, Any]:
        """지금 상태와 **지금까지 걸린 초**. 진행 중이면 경과가 계속 늘어납니다."""
        with self._lock:
            if self._status == RUNNING and self._started is not None:
                seconds = self._clock() - self._started
            else:
                seconds = self._seconds
            return {
                "status": self._status,
                "seconds": None if seconds is None else round(seconds, 1),
                "reason": self._reason,
            }


# 근거 패널에 적는 말. 네 상태를 **모두 구별해서** 적습니다 - 화면 위의 줄과 달리 여기는
# 펼쳐 봐야 나오는 자리라, 조용할 이유가 없습니다.
STATE_LABEL = {IDLE: "시작되지 않음", RUNNING: "진행 중", DONE: "완료", FAILED: "실패"}


def warmup_line(state: dict[str, Any]) -> str:
    """예열 상태를 화면 한 줄로. **끝났으면 아무 말도 하지 않습니다.**

    다 된 뒤에도 줄이 남으면 매 답변 위에 쓸모없는 한 줄이 붙습니다. 사람이 알아야 하는
    때는 기다리는 중일 때, 실패했을 때, 그리고 **시작조차 안 했을 때**입니다.

    ## 시작 안 함을 말하게 된 이유 (2026-10-25)

    처음에는 끝남(DONE)과 시작 안 함(IDLE)이 **둘 다 침묵**했습니다. 그래서 배포 앱에서
    첫 질문이 느렸을 때 "예열이 끝났는데 효과가 없다"와 "예열이 아예 안 돌았다"가 화면에서
    구별되지 않았습니다. 실제로 뒤쪽이었고(배포가 옛 코드였습니다), 소거법으로 갈라야
    했습니다 - 화면이 말했어야 할 것을 추론으로 맞힌 것입니다(ADR-067).

    **IDLE은 정상 상태가 아닙니다.** `main()`이 탭을 그리기 전에 반드시 `start()`를
    부르므로, 화면이 IDLE을 보고 있다면 그 줄이 사라졌거나 돌고 있는 코드가 옛것입니다.
    그래서 진행 상황이 아니라 **고장 신호**로 적습니다.
    """
    status = state.get("status")
    if status == IDLE:
        # 사라진 호출·옛 배포 둘 다 여기로 옵니다. 어느 쪽인지는 화면이 모릅니다.
        return ("인용망 예열이 시작되지 않았습니다(부팅 호출이 빠졌거나 배포가 옛 "
                "코드입니다). 질문은 되지만 관련 논문을 처음 부를 때 인용망을 그 자리에서 "
                "짓습니다.")
    if status == RUNNING:
        return (f'인용망을 배경에서 올리는 중입니다 ({state["seconds"]:.0f}초째). '
                "지금 물으면 이 작업이 끝날 때까지 기다립니다.")
    if status == FAILED:
        # 예열이 죽어도 질문은 됩니다 - 그때 다시 짓고, 그만큼 느립니다.
        return (f'인용망 예열이 실패했습니다({state["reason"]}). 질문은 되지만 '
                "관련 논문을 처음 부를 때 그만큼 기다립니다.")
    return ""


def warmup_state_line(state: dict[str, Any]) -> str:
    """근거 패널에 적는 **한 줄 진단**. 침묵하지 않고 네 상태를 그대로 적습니다.

    화면 위의 줄은 끝나면 조용해야 하지만(군더더기가 됩니다) 이 줄은 펼쳐 봐야 나옵니다.
    측정하는 사람이 "이 답변을 낼 때 예열이 어느 상태였는가"를 **추론하지 않고 읽게**
    하는 것이 목적입니다.
    """
    status = state.get("status")
    parts = [f'인용망 예열: {STATE_LABEL.get(status, status or "알 수 없음")}']
    if state.get("seconds") is not None:
        parts.append(f'{state["seconds"]:.1f}초')
    if state.get("reason"):
        parts.append(str(state["reason"]))
    return " · ".join(parts)
