"""조건 검사를 답 쓰기와 겹칩니다 — 판정은 그대로, 시작 시점만 앞당깁니다(ADR-085).

VM에서 잰 답변 p50 13.6초 중 `check_conditions`가 4.6초였습니다(ADR-084). 이 노드는
`generate`가 **끝난 뒤에** 돕니다. 그런데 판정에 들어가는 것은 질문, 근거 논문(제목·초록),
그래프 조회의 Cypher 셋뿐이고, 셋 다 모델이 **답 글을 쓰기 전에** 정해집니다.

- 질문은 처음부터 있습니다.
- Cypher는 마지막 턴 전에 도구가 이미 돌았습니다.
- 근거 ID는 구조화 답의 첫 필드입니다(`GroundedAnswer`의 필드 순서를 `evidence_ids` →
  `answer`로 둡니다). 스트림에서 `answer`가 시작되면 그 앞의 목록은 끝난 것입니다.

그래서 `answer`가 시작되는 순간 판정을 다른 스레드에서 시작하고, 그래프의 `check_conditions`
노드는 **입력이 같으면** 그 결과를 받습니다. 다르면(수정 턴이 근거를 바꿨거나, 스트림에서 본
목록이 최종 답과 다르면) 지금처럼 새로 판정합니다. **결과를 쓰는 조건이 입력의 일치**이므로,
판정이 받는 것과 내는 것은 겹치기 전과 같습니다.

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

# 질문마다 판정 하나입니다. API는 워커 하나에 동시 질문이 몇 개뿐이라 넷이면 충분합니다.
POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="early-judge")

Key = tuple[tuple[str, ...], tuple[str, ...]]


def final_evidence(value: Any) -> list[str] | None:
    """부분 JSON에서 **확정된** 근거 ID. `answer`가 시작되기 전이면 늘어날 수 있어 None입니다."""
    if not isinstance(value, dict) or "answer" not in value:
        return None
    ids = value.get("evidence_ids")
    if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
        return None
    return ids


def graph_queries(calls: Sequence[dict[str, Any]]) -> list[str]:
    """판정에 넘기는 Cypher. 성공했고 오류를 돌려주지 않은 `search_graph` 호출만입니다."""
    return [
        call["args"]["cypher"] for call in calls
        if call["name"] == "search_graph" and call.get("status") == "success"
        and not (call.get("output") or {}).get("error")
    ]


class EarlyJudge:
    """한 질문의 앞당긴 판정 하나. 근거 ID와 Cypher가 열쇠입니다.

    `work(ids, queries)`는 `(papers, verdict)`를 돌려줍니다. 판정이 실패하면 verdict 자리에
    **예외를 담아** 돌려줍니다 - 논문 조회의 예외와 판정의 예외는 검사 흐름에서 서로 다른 자리에서
    올라와야 하기 때문입니다(`assess_and_validate`는 조회 → 자기 인용 검사 → 판정 순서입니다).
    """

    def __init__(self, work: Callable[[list[str], list[str]], tuple[Any, Any]],
                 pool: ThreadPoolExecutor = POOL) -> None:
        self._work, self._pool = work, pool
        self._lock = threading.Lock()
        self._key: Key | None = None
        self._future: Future | None = None

    def offer(self, ids: Sequence[str], queries: Sequence[str]) -> bool:
        """판정을 시작합니다. 근거가 없거나 같은 입력으로 이미 시작했으면 하지 않습니다."""
        key = (tuple(ids), tuple(queries))
        if not ids:
            return False
        with self._lock:
            if key == self._key:
                return False
            self._key = key
            times: dict[str, float] = {}
            self._future = self._pool.submit(self._timed, times, list(ids), list(queries))
            # 판정이 언제 시작해 언제 끝났는지 결과와 함께 넘깁니다. 기다린 시간만으로는
            # "판정이 느렸나, 겹친 시간이 짧았나"를 가를 수 없었습니다(ADR-087).
            self._future.times = times  # type: ignore[attr-defined]
            return True

    def _timed(self, times: dict[str, float], ids: list[str], queries: list[str]):
        times["started"] = time.perf_counter()
        try:
            return self._work(ids, queries)
        finally:
            times["finished"] = time.perf_counter()

    def take(self, ids: Sequence[str], queries: Sequence[str]) -> Future | None:
        """입력이 같을 때만 앞당긴 결과를 넘깁니다. 한 번 넘기면 비웁니다(다음 시도는 새로 판정)."""
        with self._lock:
            if self._future is None or self._key != (tuple(ids), tuple(queries)):
                return None
            future, self._key, self._future = self._future, None, None
            return future


def timing(future: Future, check_started: float, waited: float) -> dict[str, Any]:
    """앞당긴 판정 하나의 시간. `check_started`는 검사 노드가 결과를 기다리기 시작한 시각입니다.

    - `judge_seconds`: 판정(논문 조회 포함) 자체가 걸린 시간
    - `ahead_seconds`: 검사 노드가 오기 전에 이미 돈 시간 - 답 글과 겹친 몫
    - `waited_seconds`: 검사 노드가 결과를 기다린 시간 - 겹치지 못한 몫
    """
    times = getattr(future, "times", {})
    started, finished = times.get("started"), times.get("finished")
    if started is None or finished is None:
        return {"early": True, "waited_seconds": round(waited, 3)}
    return {
        "early": True,
        "judge_seconds": round(finished - started, 3),
        "ahead_seconds": round(max(0.0, min(finished, check_started) - started), 3),
        "waited_seconds": round(waited, 3),
    }


def unpack_verdict(verdict: Any) -> Any:
    """`work`가 담아 둔 판정 예외를 검사 흐름의 그 자리에서 다시 올립니다."""
    if isinstance(verdict, BaseException):
        raise verdict
    return verdict
