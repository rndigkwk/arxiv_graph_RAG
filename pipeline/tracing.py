"""노드별 추적을 Langfuse로 보냅니다. 키가 없거나 패키지가 없으면 아무 일도 하지 않습니다.

[ADR-022](../docs/adr/ADR-022-answer-flow-as-a-stategraph.md)에서 답변 흐름에 노드 경계가
생겼고, `response["graph_trace"]`에 노드별 지연과 방문 횟수가 남습니다. 그것을 Langfuse의
trace/span으로 보냅니다.

**배선은 항상 끊을 수 있어야 합니다.** 세 가지 중 하나라도 없으면 조용히 no-op입니다 —
패키지, 키, 그리고 `LANGFUSE_TRACING=1`. 추적이 답변을 막으면 안 되므로 전송 실패도 삼키고
상태만 돌려줍니다.

**기본값이 꺼짐인 이유가 있습니다.** dograg가 중요하지 않은 테스트까지 전부 추적해서 한 달치
무료 사용량을 소진했습니다. 그래서 이 프로젝트는 **명시로 켤 때만** 보냅니다. `ROADMAP.md`
5절이 "전체를 남기고 분포를 분석"이라고 적어 뒀는데, 그 방식이 한도를 태운 방식입니다.
"""

from __future__ import annotations

import importlib.util
import os
from typing import Any

# 켜는 스위치. 키가 있어도 이것이 없으면 보내지 않습니다.
ENABLE_FLAG = "LANGFUSE_TRACING"
REQUIRED_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")


def status(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """Why tracing is on or off, without touching the network."""
    env = environ if environ is not None else os.environ
    missing = [key for key in REQUIRED_KEYS if not env.get(key)]
    enabled = env.get(ENABLE_FLAG, "").strip() in {"1", "true", "True"}
    # 임포트하지 않고 설치 여부만 봅니다. `import langfuse`는 앱 상주를 5.2MB 늘리는데
    # (ADR-016 재측정) 추적이 꺼져 있으면 그 비용을 낼 이유가 없습니다.
    installed = importlib.util.find_spec("langfuse") is not None
    return {
        "installed": installed,
        "keys_present": not missing,
        "missing_keys": missing,
        "flag_set": enabled,
        "active": installed and not missing and enabled,
        "host": env.get("LANGFUSE_BASE_URL") or env.get("LANGFUSE_HOST") or "",
    }


def send_trace(
    graph_trace: dict[str, Any],
    *,
    name: str = "ask",
    metadata: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Send one answer's node trace as a Langfuse trace with a span per node.

    질문 문장도 답변 본문도 보내지 않습니다. 보내는 것은 **노드 이름·시도 번호·지연**입니다.
    운영 로그에 데이터를 남기지 않는다는 기존 규칙과 같은 선입니다. 필요해지면 그때 무엇을
    더 보낼지 따로 정합니다.
    """
    state = status(environ)
    if not state["active"]:
        return {**state, "sent": False, "reason": "비활성"}

    env = environ if environ is not None else os.environ
    try:
        from langfuse import Langfuse

        client = Langfuse(
            public_key=env["LANGFUSE_PUBLIC_KEY"],
            secret_key=env["LANGFUSE_SECRET_KEY"],
            host=state["host"] or None,
        )
        # 키가 맞는지 먼저 봅니다. 틀렸으면 스팬을 만들어 버리지 않고 사유를 돌려줍니다.
        if not client.auth_check():
            return {**state, "sent": False, "reason": "auth_check 실패(키 또는 호스트 확인)"}
        nodes = graph_trace.get("nodes", [])
        with client.start_as_current_observation(name=name, as_type="chain") as root:
            root.update(metadata={
                **(metadata or {}),
                "attempts": graph_trace.get("attempts"),
                "visits": graph_trace.get("visits"),
                "total_seconds": graph_trace.get("total_seconds"),
            })
            for entry in nodes:
                with client.start_as_current_observation(
                    name=entry["node"], as_type="span"
                ) as span:
                    span.update(metadata={
                        "attempt": entry["attempt"],
                        "seconds": entry["seconds"],
                    })
            # URL은 루트 스팬이 살아 있을 때만 읽힙니다. 밖에서 부르면 빈 문자열이 옵니다.
            trace_url = client.get_trace_url() or ""
        client.flush()
    except Exception as exc:  # noqa: BLE001 - 추적이 답변을 막으면 안 됩니다.
        return {**state, "sent": False, "reason": f"{type(exc).__name__}: {exc}"[:200]}
    return {**state, "sent": True, "spans": len(nodes) + 1, "trace_url": trace_url}
