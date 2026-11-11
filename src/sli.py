"""질문마다 SLI 한 줄을 남기고, 그 줄로 지연 분포와 목표 달성률을 셉니다(ADR-126).

지금까지 `/api/ask`는 **실패할 때만** 로그를 남겼습니다. 평소 지연은 측정 스크립트
(`scripts/measure_vm_answer_latency.py`)를 돌릴 때만 보였고, 그것은 우리가 고른 15문항입니다.
교안(Day 79)의 SLI는 실제 요청에서 "좋은 요청 비율"을 셉니다. 지표 서버를 두지 않고(워커
하나, 적은 트래픽) 요청마다 같은 모양의 JSON 한 줄을 표준 오류에 씁니다.
컨테이너 로그가 그것을 모읍니다.

**질문 내용은 남기지 않습니다.** 결과, 시간, 모델 턴, 토큰, 그 답을 만든 코드의 커밋뿐입니다.

이 모듈은 표준 라이브러리만 씁니다. VM 호스트의 `python3`로 집계 스크립트를 돌리려는 것입니다.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from collections.abc import Iterable
from typing import Any

PREFIX = "ask_sli "
# 답변 지연 목표(ADR-084). 측정 스크립트와 같은 값입니다.
TARGET_SECONDS = 10.0

logger = logging.getLogger("arxiv_graph_rag.sli")
if not logger.handlers:
    # uvicorn은 우리 로거의 INFO를 내보내지 않습니다. 이 로거만 따로 한 줄씩 씁니다.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sum(values: Iterable[Any]) -> int | None:
    """공급자가 한 턴이라도 값을 주면 그 합, 하나도 없으면 None - 0으로 적지 않습니다."""
    seen = [v for v in (_int(x) for x in values) if v is not None]
    return sum(seen) if seen else None


def record(outcome: str, seconds: float, first_partial_seconds: float | None,
           response: dict[str, Any] | None = None, commit: str | None = None,
           error: str | None = None) -> dict[str, Any]:
    """한 질문의 SLI. `outcome`은 final·abstain·error입니다."""
    response = response or {}
    trace = response.get("generate_trace") or {}
    turns = trace.get("turns") or []
    graph = response.get("graph_trace") or {}
    row: dict[str, Any] = {
        "outcome": outcome,
        "seconds": round(seconds, 3),
        "first_partial_seconds": None if first_partial_seconds is None
        else round(first_partial_seconds, 3),
        "model_turns": _int(trace.get("model_turns")),
        "attempts": _int(graph.get("attempts")),
        "input_tokens": _sum(turn.get("input_tokens") for turn in turns),
        "cached_input_tokens": _sum(turn.get("cached_input_tokens") for turn in turns),
        "output_tokens": _sum(turn.get("output_tokens") for turn in turns),
        "commit": commit,
    }
    if error:
        row["error"] = error
    return row


def log(row: dict[str, Any]) -> None:
    logger.info(PREFIX + json.dumps(row, ensure_ascii=False, sort_keys=True))


def parse(lines: Iterable[str]) -> list[dict[str, Any]]:
    """로그에서 SLI 줄만 읽습니다. 다른 줄과 깨진 줄은 건너뜁니다."""
    rows = []
    for line in lines:
        at = line.find(PREFIX)
        if at < 0:
            continue
        try:
            row = json.loads(line[at + len(PREFIX):])
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("seconds"), (int, float)):
            rows.append(row)
    return rows


def percentile(values: list[float], fraction: float) -> float | None:
    """정렬 후 위치로 읽습니다(측정 스크립트와 같은 방식). 보간하지 않습니다."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3)


def summarize(rows: list[dict[str, Any]], target: float = TARGET_SECONDS) -> dict[str, Any]:
    """좋은 요청 = 답(final)이 `target`초 안에 끝난 것. 지연 분포는 답이 나온 질문만 셉니다.

    기권(abstain)은 실패가 아니지만 답도 아니므로 좋은 요청에 넣지 않고 따로 셉니다.
    """
    answered = [row["seconds"] for row in rows if row.get("outcome") == "final"]
    firsts = [row["first_partial_seconds"] for row in rows
              if isinstance(row.get("first_partial_seconds"), (int, float))]
    inputs = [(row.get("input_tokens"), row.get("cached_input_tokens")) for row in rows]
    counted = [(i, c) for i, c in inputs if _int(i) and _int(c) is not None]
    total_in = sum(i for i, _ in counted)
    return {
        "requests": len(rows),
        "outcomes": dict(Counter(row.get("outcome") for row in rows)),
        "p50_seconds": percentile(answered, 0.5),
        "p90_seconds": percentile(answered, 0.9),
        "first_partial_p50_seconds": percentile(firsts, 0.5),
        "target_seconds": target,
        "good_ratio": round(sum(s <= target for s in answered) / len(rows), 3) if rows else None,
        "cached_input_ratio": round(sum(c for _, c in counted) / total_in, 3) if total_in else None,
        "commits": dict(Counter(row.get("commit") for row in rows)),
    }
