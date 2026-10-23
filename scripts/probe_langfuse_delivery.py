"""trace 하나를 보내고 **되읽어** 저장됐는지 봅니다(ADR-062).

    uv run --group dev python scripts/probe_langfuse_delivery.py

[ADR-023](../docs/adr/ADR-023-what-to-trace.md)이 구멍 하나를 열어 둔 채 끝났습니다 —
*"코드 쪽에서는 구분할 방법이 없습니다 — **Langfuse UI를 직접 봐야** 합니다."* 한도가 소진되면
전송은 성공하고 UI는 빕니다. 즉 **켜도 동작하는지 모릅니다.**

이 프로브가 그 구멍을 메웁니다. 보낸 trace의 id를 들고 **같은 id를 되읽습니다.**

## 비동기 수집이 함정입니다

SDK가 적어 두었습니다 — *"`flush()`는 API 전달만 보장하고 읽기 가시성은 보장하지 않습니다
(보통 15~30초, 부하가 크면 더)"*. 그래서 한 번 보고 "없음"으로 적으면 **성공한 trace를
버려진 것으로 읽습니다.** 기다리며 다시 봅니다.

**그래도 구별되지 않는 것이 남습니다.** 대기 한도를 넘겨 못 찾으면 "버려졌다"와 "느리다"가
갈라지지 않습니다. 그 둘을 구별하지 못한다고 적는 것이 정확합니다.

## 왜 `check_*`가 아니라 `probe_`인가

CI가 `scripts/check_*.py`를 **전부** 돌립니다. 이 스크립트는 키가 있어야 하고 외부로 전송을
하므로 거기 들어가면 안 됩니다. `probe_cypher_guard.py`와 같은 이유, 같은 이름 규칙입니다.

## 예산을 쓰지 않습니다

`pipeline.tracing`의 하루 예산(`LANGFUSE_MAX_TRACES`)을 **건드리지 않습니다.** 예산이 소진된
날에도 진단은 돌아야 하기 때문입니다. 유닛은 하나 씁니다 — 그 사실을 보고서에 적습니다.

보내는 내용은 **이름과 타임스탬프뿐**입니다. ADR-023 3번(노드 이름·시도 번호·지연만)을
진단용 trace에도 지킵니다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `pipeline`을 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUTPUT = Path("reports/langfuse_delivery.json")
NAME = "delivery-probe"
# SDK가 "보통 15~30초, 부하가 크면 더"라고 적었으므로 30초는 짧습니다.
WAIT_SECONDS = 120
POLL_SECONDS = 2


def load_keys() -> dict[str, str]:
    """로컬 `.env`에서 키를 읽습니다. **값은 출력하지 않습니다.**"""
    import os

    from dotenv import load_dotenv

    load_dotenv(Path(".env"), override=False)
    missing = [
        key for key in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
        if not (os.getenv(key) or "").strip()
    ]
    if missing:
        raise SystemExit(
            f"키가 없습니다: {missing}. 로컬 .env에만 두고 GitHub Secrets에는 올리지 "
            "않습니다(ADR-023)."
        )
    return {
        "public_key": os.environ["LANGFUSE_PUBLIC_KEY"],
        "secret_key": os.environ["LANGFUSE_SECRET_KEY"],
        "host": (os.getenv("LANGFUSE_BASE_URL") or "").strip() or None,
    }


def send(client: Any) -> tuple[str, str]:
    """진단용 trace 하나를 보내고 `(trace_id, url)`을 돌려줍니다."""
    stamp = datetime.now(UTC).isoformat()
    with client.start_as_current_observation(name=NAME, as_type="chain") as root:
        # 이름과 시각뿐입니다. 질문·답변·Cypher는 담지 않습니다(ADR-023 3번).
        root.update(metadata={"probe": True, "sent_at": stamp})
        trace_id = client.get_current_trace_id() or ""
        url = client.get_trace_url() or ""
    client.flush()
    return trace_id, url


def read_back(client: Any, trace_id: str, *, wait: int, poll: int) -> dict[str, Any]:
    """그 trace의 observation이 읽히는지 기다리며 봅니다.

    **`api.trace`에는 읽기 메서드가 없습니다**(`delete`·`delete_multiple`뿐). SDK가 적어 둔
    대로 `api.observations.get_many(trace_id=...)`를 씁니다 - 2026-10-23에 `api.trace.get`을
    불러 60번 같은 `AttributeError`를 내고 그것을 "저장되지 않음"으로 보고했습니다.
    """
    started = time.monotonic()
    attempts = 0
    while time.monotonic() - started < wait:
        attempts += 1
        try:
            page = client.api.observations.get_many(trace_id=trace_id)
        except Exception as exc:  # noqa: BLE001
            # **호출이 틀린 것을 재시도로 덮지 않습니다.** 아직 안 보이는 것은 빈 목록으로
            # 오고, 예외는 거의 항상 우리 잘못이거나 서버 문제입니다. 60번 되묻는 동안
            # "저장되지 않음"처럼 보이는 것이 더 나쁩니다.
            return {
                "found": False,
                "attempts": attempts,
                "waited_seconds": round(time.monotonic() - started, 1),
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "means": "되읽기 호출 자체가 실패했습니다. 저장 여부를 말하지 않습니다.",
            }
        if getattr(page, "data", None):
            return {
                "found": True,
                "observations": len(page.data),
                "attempts": attempts,
                "waited_seconds": round(time.monotonic() - started, 1),
            }
        time.sleep(poll)
    return {
        "found": False,
        "attempts": attempts,
        "waited_seconds": round(time.monotonic() - started, 1),
        # **이 문장이 결과의 절반입니다.** 둘을 구별하지 못합니다.
        "means": f"버려졌거나 {wait}초보다 느립니다. 이 프로브로는 구별되지 않습니다.",
    }


def main() -> int:
    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Langfuse에 trace가 실제로 저장되는지 되읽어 봅니다(ADR-062)."
    )
    parser.add_argument("--wait", type=int, default=WAIT_SECONDS)
    parser.add_argument("--poll", type=int, default=POLL_SECONDS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    keys = load_keys()
    from langfuse import Langfuse

    client = Langfuse(**keys)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "host": keys["host"] or "기본(cloud)",
        "wait_seconds": args.wait,
        "note": (
            "하루 예산(LANGFUSE_MAX_TRACES)을 쓰지 않습니다 - 예산이 소진된 날에도 진단은 "
            "돌아야 합니다. 유닛은 하나 씁니다."
        ),
    }
    if not client.auth_check():
        report["verdict"] = "전송 전에 실패했습니다(auth_check)"
        report["auth_check"] = False
    else:
        report["auth_check"] = True
        trace_id, url = send(client)
        report["trace_id"] = trace_id
        report["trace_url"] = url
        if not trace_id:
            report["verdict"] = "trace id를 받지 못했습니다"
        else:
            report["read_back"] = read_back(
                client, trace_id, wait=args.wait, poll=args.poll
            )
            report["verdict"] = (
                "저장됩니다" if report["read_back"]["found"]
                else "저장되지 않습니다(또는 대기 한도보다 느립니다)"
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {key: value for key, value in report.items() if key != "note"},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
