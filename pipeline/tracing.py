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

**장치가 둘입니다**(ADR-023).

1. **스위치** — 패키지·키·`LANGFUSE_TRACING=1`이 모두 있어야 보냅니다.
2. **예산** — **하루에** `LANGFUSE_MAX_TRACES`(기본 50)개까지만 보냅니다. 켜 둔 채로
   루프를 돌려도 그 지점에서 멈춥니다. 추적이 끊기는 것이 쿼터가 끊기는 것보다 낫습니다.

**예산의 단위가 "프로세스당"이었고, 그것이 틀렸습니다.** 계수기가 모듈 전역이라 프로세스가
죽으면 0으로 돌아갔습니다. 평가는 한 프로세스에서 끝나니 맞게 동작했지만, 앱은 다릅니다 —
Streamlit Cloud는 유휴로 잠들고 방문 때 깨므로 **깰 때마다 새 프로세스**이고, 그러면 상한이
아니라 **"재시작마다 앞 50건"** 이 됩니다. 상한도 아니고 표본도 아닙니다.

그래서 계수기를 **UTC 날짜별로 파일에 남깁니다**(`LANGFUSE_BUDGET_STATE`,
기본 `pipeline/state/langfuse_budget.json`). 한도가 월 단위인데 창을 하루로 잡은 이유는,
월 단위로 잡으면 "이번 달 앞 N건"이라는 같은 치우침이 **한 달 규모로** 생기기 때문입니다.
하루 창은 매일 다시 열리므로 표본이 날짜에 고르게 퍼집니다.

**파일이 남지 않는 곳에서는 프로세스당으로 되돌아갑니다.** CI 러너는 매번 새로 만들어지므로
거기서는 예전과 같이 동작합니다 — 평가 한 번이 한 프로세스라 그래도 맞습니다. 쓸 수 없으면
메모리 계수기로 떨어지고, 그 사실을 사유에 적습니다. 조용히 무제한이 되지 않습니다.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 켜는 스위치. 키가 있어도 이것이 없으면 보내지 않습니다.
ENABLE_FLAG = "LANGFUSE_TRACING"
REQUIRED_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
# 한 프로세스가 보낼 수 있는 trace 수. 켜 둔 채로 루프를 돌리는 사고를 막습니다 —
# dograg가 한 달치를 태운 방식이 그것입니다. 평가 한 번(30건)이 30 trace이므로
# 기본값 50은 "평가 한 번 + 여유"입니다.
BUDGET_KEY = "LANGFUSE_MAX_TRACES"
DEFAULT_BUDGET = 50

# 하루치 계수기를 남기는 곳. `pipeline/state/`는 이미 gitignore입니다 — 런타임 상태이고
# 커밋할 기록이 아닙니다.
STATE_KEY = "LANGFUSE_BUDGET_STATE"
# 모듈 기준으로 잡습니다. 작업 디렉터리 기준이면 앱을 다른 곳에서 띄웠을 때 계수기가
# 딴 데 생기고, 그러면 상한이 조용히 되살아납니다.
DEFAULT_STATE = Path(__file__).resolve().parent / "state" / "langfuse_budget.json"

# 파일을 쓸 수 없을 때의 대체 계수기. 어느 날의 것인지 함께 둡니다 — 날짜가 없으면
# 자정을 넘긴 계수인지 알 수 없고, 그러면 하루 창이 창이 아닙니다.
_sent = 0
_sent_day = ""


def budget(environ: dict[str, str] | None = None) -> int:
    env = environ if environ is not None else os.environ
    raw = (env.get(BUDGET_KEY) or "").strip()
    if not raw:
        return DEFAULT_BUDGET
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_BUDGET


def utc_day(now: datetime | None = None) -> str:
    """오늘을 `YYYY-MM-DD`로. **UTC입니다** — 호스트 시간대에 따라 창이 달라지면 안 됩니다."""
    return (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y-%m-%d")


def state_path(environ: dict[str, str] | None = None) -> Path:
    env = environ if environ is not None else os.environ
    return Path((env.get(STATE_KEY) or "").strip() or DEFAULT_STATE)


def sent_count(
    environ: dict[str, str] | None = None, *, now: datetime | None = None
) -> int:
    """오늘(UTC) 보낸 수. 예산이 보이지 않으면 관리할 수 없습니다."""
    return _used_today(utc_day(now), environ)[0]


def _used_today(day: str, environ: dict[str, str] | None) -> tuple[int, bool]:
    """(오늘 보낸 수, 파일에서 읽었는가).

    파일이 먼저입니다. 못 읽으면 메모리로 떨어지고, **어느 쪽인지 돌려줍니다** — 호출자가
    "프로세스당으로 되돌아갔다"를 사유에 적을 수 있어야 합니다.
    """
    try:
        data = json.loads(state_path(environ).read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("day") == day:
            return max(0, int(data.get("sent", 0))), True
        # 파일은 있는데 어제 것입니다. 창이 새로 열렸습니다.
        return 0, True
    except FileNotFoundError:
        # 아직 아무것도 안 보냈습니다. 쓸 수 있는지는 쓸 때 압니다.
        return 0, True
    except (OSError, ValueError, TypeError):
        # 깨졌거나 읽을 수 없습니다. 무제한으로 가지 않고 메모리로 떨어집니다.
        return (_sent if _sent_day == day else 0), False


def _record_sent(day: str, count: int, environ: dict[str, str] | None) -> bool:
    """오늘치 계수를 남깁니다. 메모리는 **항상** 갱신합니다 — 파일이 죽어도 대체가 살아야 합니다.

    잠금을 쓰지 않습니다. 두 프로세스가 동시에 읽으면 같은 값에서 올려 **예산을 조금 넘길 수**
    있고, 초과분은 동시 실행 수만큼입니다. 이 앱의 동시성은 1 근처이고, 잠금 하나를 들이는
    값보다 그 오차가 쌉니다. 잠금이 있는 척하지 않으려고 여기 적습니다.
    """
    global _sent, _sent_day
    _sent, _sent_day = count, day
    path = state_path(environ)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"day": day, "sent": count}), encoding="utf-8"
        )
        # 반쯤 쓰다 죽은 파일이 남으면 다음 읽기가 메모리로 떨어집니다. 바꿔치기가 원자적입니다.
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def monthly_traces(allowance: int) -> int:
    """이 예산을 매일 다 쓰면 한 달에 몇 trace인가. **손잡이의 결과를 보이게 합니다.**

    한도는 월 단위인데 손잡이는 하루 단위입니다. 31을 곱하지 않으면 50이 작아 보입니다.
    유닛은 이보다 큽니다 — trace 하나에 노드 수만큼 observation이 붙습니다.
    """
    return max(0, allowance) * 31


def reset_count(environ: dict[str, str] | None = None) -> None:
    """테스트와, 한 프로세스에서 여러 실행을 재는 하네스를 위해 둡니다."""
    global _sent, _sent_day
    _sent, _sent_day = 0, ""
    try:
        state_path(environ).unlink(missing_ok=True)
    except OSError:
        pass


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
    now: datetime | None = None,
) -> dict[str, Any]:
    """Send one answer's node trace as a Langfuse trace with a span per node.

    질문 문장도 답변 본문도 보내지 않습니다. 보내는 것은 **노드 이름·시도 번호·지연**입니다.
    운영 로그에 데이터를 남기지 않는다는 기존 규칙과 같은 선입니다. 필요해지면 그때 무엇을
    더 보낼지 따로 정합니다.
    """
    state = status(environ)
    if not state["active"]:
        return {**state, "sent": False, "reason": "비활성"}

    day = utc_day(now)
    allowance = budget(environ)
    used, persisted = _used_today(day, environ)
    # 창이 하루라는 것과, 계수가 파일에 남았는지를 둘 다 사유에 적습니다. 파일이 없으면
    # 프로세스당으로 되돌아간 것이고, 그건 "상한"이 아니라 "재시작마다 앞 N건"입니다.
    window = f"{day} UTC" if persisted else f"{day} UTC, 프로세스당으로 대체"
    if used >= allowance:
        # 예산을 넘기면 조용히 멈춥니다. 추적이 끊기는 것이 쿼터가 끊기는 것보다 낫습니다.
        return {
            **state,
            "sent": False,
            "reason": f"하루 예산 소진({used}/{allowance}, {window})",
            "sent_so_far": used,
            "budget": allowance,
            "day": day,
            "budget_persisted": persisted,
        }

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
    wrote = _record_sent(day, used + 1, environ)
    return {
        **state,
        "sent": True,
        "spans": len(nodes) + 1,
        "trace_url": trace_url,
        "sent_so_far": used + 1,
        "budget": allowance,
        "day": day,
        "budget_persisted": persisted and wrote,
    }
