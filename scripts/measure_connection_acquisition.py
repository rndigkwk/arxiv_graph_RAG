"""연결 획득이 실제로 얼마나 걸리는지 재서 `connection_acquisition_timeout`을 정합니다.

    uv run python -m scripts.measure_connection_acquisition --runs 12 --warm 40

[ADR-015](../docs/adr/ADR-015-retry-not-higher-timeout.md)가 앱 경로의 최악값을 계산하고
**지배 항을 지목했습니다** — 우리 재시도가 아니라 드라이버의 연결 획득 60초입니다. 그리고
고치지 않고 이렇게 적었습니다.

> 올바른 처방은 재시도 전체에 **마감 시간**을 두는 것이지만, 첫 시도 자체가 90초를 끌 수
> 있으므로 그 아래로 내리려면 `connection_acquisition_timeout`을 낮춰야 합니다. 그 값은
> **모든 질의**에 적용되므로 챗봇 경로까지 영향을 받고, 지금 그것을 재지 않았습니다.

이 스크립트가 그 측정입니다. **읽기만 합니다**(`RETURN 1`).

## 세 가지를 잽니다

**① 평상시 획득 비용.** `connection_acquisition_timeout`은 **풀에서 연결을 받아 오는 데**
허용하는 시간입니다. 풀이 비어 있으면 그 안에서 새로 만들고(TCP·TLS·Bolt 핸드셰이크·인증),
차 있으면 꺼내 씁니다. 그래서 콜드와 웜을 나눠 잽니다.

**② 저하 구간에서 실패가 매달리는가 빨리 끝나는가.** 이것이 결정적인데 ADR-015가 재지
않았습니다. Aura 없이 로컬로 잽니다 — 패킷이 버려지는 주소(TEST-NET-1)와 닫힌 포트.
전자는 "라우팅이 죽었다", 후자는 "서버가 거부한다"에 해당합니다.

**③ 그 둘을 재시도 일정에 넣은 결과.** 90초 저하를 넘길 시도가 남는지.

## 저하 구간의 시도당 비용은 `T`입니다 — `T + 45`가 아닙니다

여기서 두 번 틀릴 수 있고, 둘 다 실제로 틀렸던 자리입니다.

**첫째, 건강한 경로의 시도당 비용에 `EXPLAIN`이 빠져 있었습니다.**
[`chatbot.run_read`](../chatbot.py)는 질의마다 **두 문장**을 돌립니다 —
`EXPLAIN`(timeout 15)으로 유형을 확인하고 본 질의(timeout 30)를 돌립니다. ADR-015의
`3 × (60 + 30) + 6 = 276초`는 그 15초를 빼놓은 값입니다.

**둘째, 저하 구간에서는 그 문장들이 아예 돌지 않습니다.** 연결을 못 얻으면 거기서 끝이므로
시도당 비용은 **획득 하나**입니다. 그래서 "90초를 넘길 시도가 있는가"를 묻는 식은
`2T + 6 >= 90`이고, 건강한 경로의 식과 **다릅니다.** 둘을 섞으면 답이 뒤집힙니다.

## 이 측정이 말하지 않는 것

**저하가 얼마나 오래 가는지는 재지 않았습니다.** 90초는 2026-10-15의 **한 번**입니다.
이 스크립트는 "그 90초를 넘길 시도가 남는가"를 계산할 뿐, 90초가 대표값인지는 모릅니다.

**어느 실패 모드가 실제로 일어나는지도 모릅니다.** 둘의 성질을 재지만, Aura의 그 사고가
블랙홀이었는지 거부였는지는 기록에 없습니다.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

OUTPUT = Path("reports/connection_acquisition.json")
# `chatbot.run_read`가 한 호출에서 거는 두 타임아웃입니다. 둘 다 세야 건강한 경로의
# 시도당 비용이 나옵니다 - ADR-015는 EXPLAIN 쪽을 빼놓았습니다.
EXPLAIN_TIMEOUT = 15
STATEMENT_TIMEOUT = 30
# `chatbot.read_with_retry(attempts=3, delay=2.0)`, 대기는 delay*(n+1)이라 2초+4초입니다.
RETRIES = 3
RETRY_WAITS = 6
# ADR-015가 기록한 Aura 자체 회복 시간. 측정값이 아니라 **한 번의 관측**입니다.
RECOVERY_SECONDS = 90
CANDIDATES = (60, 45, 42, 30, 15, 10, 5, 3)

# 저하 모드를 흉내 내는 주소. 자격 증명도 Aura도 필요 없습니다.
FAILURE_CASES = {
    # TEST-NET-1(RFC 5737). 보통 패킷이 버려져 "라우팅이 죽은" 상태가 됩니다.
    "blackhole": "neo4j://192.0.2.1:7687",
    # 닫힌 포트. 서버가 즉시 거부하는 상태입니다.
    "refused": "neo4j://127.0.0.1:1",
}
FAILURE_TIMEOUTS = (3.0, 6.0, 12.0)


def driver_default_acquisition_timeout() -> float | None:
    """드라이버가 쓰는 기본값을 **직접 읽습니다.** ADR-015의 60초를 글로 믿지 않습니다."""
    try:
        # 사설 모듈입니다. 판이 바뀌면 사라질 수 있어 실패를 None으로 돌려주고,
        # 테스트가 "60이 아니면 계산을 다시 하라"고 막습니다.
        from neo4j._conf import WorkspaceConfig

        return float(WorkspaceConfig.connection_acquisition_timeout)
    except Exception:  # noqa: BLE001
        return None


def spread(times: list[float]) -> dict[str, Any]:
    ordered = sorted(times)
    return {
        "runs": len(ordered),
        "p50": round(statistics.median(ordered), 4),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 4),
        "max": round(max(ordered), 4),
        "mean": round(statistics.fmean(ordered), 4),
    }


def healthy_worst(acquisition: float) -> float:
    """건강한 경로의 질의 하나 최악값. **`EXPLAIN`을 포함합니다.**"""
    return RETRIES * (acquisition + EXPLAIN_TIMEOUT + STATEMENT_TIMEOUT) + RETRY_WAITS


def degraded_last_attempt(acquisition: float) -> float:
    """저하 구간에서 **마지막 시도가 시작되는 때**.

    연결을 못 얻으면 문장이 돌지 않으므로 시도당 비용은 획득 하나입니다. 시도는
    0 / T+2 / 2T+6 에 시작합니다.
    """
    return 2 * acquisition + RETRY_WAITS


def minimum_timeout_for_recovery() -> float:
    """`2T + 6 >= RECOVERY`를 만족하는 가장 작은 T. 지금 값으로 42초입니다."""
    return (RECOVERY_SECONDS - RETRY_WAITS) / 2


def one_cold_run(uri: str, auth: tuple[str, str], database: str) -> dict[str, float]:
    """드라이버를 새로 열어 콜드 경로를 재고 닫습니다. 앱이 뜰 때의 경로입니다."""
    from neo4j import GraphDatabase
    from neo4j.api import READ_ACCESS

    start = perf_counter()
    driver = GraphDatabase.driver(uri, auth=auth, notifications_min_severity="OFF")
    construct = perf_counter() - start
    try:
        start = perf_counter()
        driver.verify_connectivity()
        cold_connect = perf_counter() - start

        start = perf_counter()
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            session.run("RETURN 1 AS one").single()
        cold_query = perf_counter() - start

        # 같은 드라이버, 새 세션. 앱이 질의마다 하는 일이고 풀이 이미 차 있습니다.
        start = perf_counter()
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            session.run("RETURN 1 AS one").single()
        warm_query = perf_counter() - start
    finally:
        driver.close()
    return {
        "construct": construct,
        "cold_connect": cold_connect,
        "cold_query": cold_query,
        "warm_query": warm_query,
    }


def warm_series(uri: str, auth: tuple[str, str], database: str, count: int) -> list[float]:
    """한 드라이버로 세션을 여닫으며 재는 값. **앱의 정상 상태**입니다."""
    from neo4j import GraphDatabase
    from neo4j.api import READ_ACCESS

    driver = GraphDatabase.driver(uri, auth=auth, notifications_min_severity="OFF")
    try:
        driver.verify_connectivity()
        times = []
        for _ in range(count):
            start = perf_counter()
            with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
                session.run("RETURN 1 AS one").single()
            times.append(perf_counter() - start)
    finally:
        driver.close()
    return times


def failure_modes() -> dict[str, dict[str, float]]:
    """실패가 타임아웃을 꽉 채우는가, 그와 무관하게 끝나는가.

    **Aura도 자격 증명도 쓰지 않습니다.** 닿지 않는 주소로 붙어 보는 것이 전부입니다.
    `T`를 바꿔 가며 재야 "비례하는가"를 말할 수 있습니다 - 한 값으로는 모릅니다.
    """
    import logging

    from neo4j import GraphDatabase

    # 드라이버가 라우팅 실패를 경고로 찍습니다. 재는 데 방해만 되므로 잠시 막습니다.
    noisy = logging.getLogger("neo4j")
    before = noisy.level
    noisy.setLevel(logging.CRITICAL)
    try:
        measured: dict[str, dict[str, float]] = {}
        for label, uri in FAILURE_CASES.items():
            measured[label] = {}
            for timeout in FAILURE_TIMEOUTS:
                driver = GraphDatabase.driver(
                    uri,
                    auth=("unused", "unused"),
                    connection_acquisition_timeout=timeout,
                    connection_timeout=timeout,
                )
                start = perf_counter()
                try:
                    driver.verify_connectivity()
                except Exception:  # noqa: BLE001 - 실패가 측정 대상입니다.
                    pass
                finally:
                    measured[label][str(timeout)] = round(perf_counter() - start, 3)
                    driver.close()
        return measured
    finally:
        noisy.setLevel(before)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="연결 획득 지연을 재서 connection_acquisition_timeout을 정합니다."
    )
    parser.add_argument("--runs", type=int, default=12, help="콜드 경로 반복 수")
    parser.add_argument("--warm", type=int, default=40, help="정상 상태 질의 수")
    parser.add_argument(
        "--skip-failure-modes", action="store_true",
        help="닿지 않는 주소로 붙어 보는 부분을 건너뜁니다(약 48초)",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from dotenv import load_dotenv

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    load_dotenv()
    uri = os.environ.get("NEO4J_URI")
    user = os.environ.get("NEO4J_USERNAME")
    password = os.environ.get("NEO4J_PASSWORD")
    database = os.environ.get("NEO4J_DATABASE") or "neo4j"
    if not (uri and user and password):
        parser.error(".env에 NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD가 필요합니다")
    auth = (user, password)

    default_timeout = driver_default_acquisition_timeout()
    print(f"드라이버 기본 connection_acquisition_timeout: {default_timeout}초")

    cold: dict[str, list[float]] = {
        "construct": [], "cold_connect": [], "cold_query": [], "warm_query": []
    }
    for attempt in range(args.runs):
        row = one_cold_run(uri, auth, database)
        for key, value in row.items():
            cold[key].append(value)
        print(
            f"  콜드 {attempt + 1}/{args.runs}: 연결 {row['cold_connect']:.3f}초 / "
            f"첫 질의 {row['cold_query']:.3f}초 / 다음 질의 {row['warm_query']:.3f}초"
        )

    print(f"  정상 상태 {args.warm}회...")
    warm = warm_series(uri, auth, database, args.warm)

    if not args.skip_failure_modes:
        print("  저하 모드(닿지 않는 주소)...")
    failures = {} if args.skip_failure_modes else failure_modes()

    measured = {name: spread(times) for name, times in cold.items()}
    measured["warm_series"] = spread(warm)
    # **타임아웃이 덮어야 하는 쪽**은 콜드입니다. 그 최악값으로 여유를 냅니다.
    worst_cold = max(max(cold["cold_connect"]), max(cold["cold_query"]))
    minimum = minimum_timeout_for_recovery()

    by_candidate = {
        str(candidate): {
            "headroom_ratio": round(candidate / worst_cold, 1),
            "healthy_one_query_seconds": healthy_worst(candidate),
            "healthy_pagerank_tab_seconds": 2 * healthy_worst(candidate),
            "degraded_last_attempt_starts": degraded_last_attempt(candidate),
            # 저하 구간에서 마지막 시도가 회복 이후에 시작하는가. 60초를 지키는 근거입니다.
            "outlasts_recovery": degraded_last_attempt(candidate) >= RECOVERY_SECONDS,
        }
        for candidate in CANDIDATES
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "driver_default_acquisition_timeout": default_timeout,
        "explain_timeout_seconds": EXPLAIN_TIMEOUT,
        "statement_timeout_seconds": STATEMENT_TIMEOUT,
        "retries": RETRIES,
        "retry_waits_seconds": RETRY_WAITS,
        "observed_recovery_seconds": RECOVERY_SECONDS,
        "minimum_timeout_for_recovery": minimum,
        "measured": measured,
        "worst_cold_seconds": round(worst_cold, 4),
        "failure_modes": failures,
        "by_candidate": by_candidate,
        "note": (
            "평상시 값은 건강한 인스턴스에서 쟀습니다. 저하가 얼마나 오래 가는지는 재지 "
            "않았습니다 - 90초는 2026-10-15의 한 번입니다. 저하 구간에서는 연결을 얻지 "
            "못하므로 EXPLAIN도 본 질의도 돌지 않고, 시도당 비용이 획득 하나입니다. 그래서 "
            "'90초를 넘길 시도가 있는가'의 식은 2T+6>=90이고 건강한 경로의 식과 다릅니다."
        ),
        "samples": {"cold": cold, "warm_series": [round(t, 4) for t in warm]},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print()
    print(f"{'구간':14s}{'p50':>9}{'p95':>9}{'최악':>9}")
    for name in ("construct", "cold_connect", "cold_query", "warm_query", "warm_series"):
        row = measured[name]
        print(f"{name:14s}{row['p50']:>9.4f}{row['p95']:>9.4f}{row['max']:>9.4f}")
    print(f"\n타임아웃이 덮어야 하는 최악값(콜드): {worst_cold:.3f}초")

    if failures:
        label_line = "/".join(str(t) for t in FAILURE_TIMEOUTS)
        print(f"\n저하 모드 — 실패가 타임아웃({label_line}초)에 비례하는가")
        for label, rows in failures.items():
            values = "  ".join(f"{rows[str(t)]:>6.2f}초" for t in FAILURE_TIMEOUTS)
            first, last = rows[str(FAILURE_TIMEOUTS[0])], rows[str(FAILURE_TIMEOUTS[-1])]
            print(f"  {label:10s}{values}   -> {'비례함' if last > 2 * first else '무관'}")

    print(f"\n회복 {RECOVERY_SECONDS}초를 넘기려면 T >= {minimum:.0f}초")
    print(f"{'후보':>5}{'여유':>9}{'건강 질의1':>12}{'저하 마지막시도':>16}{'회복 넘김':>11}")
    for candidate in CANDIDATES:
        row = by_candidate[str(candidate)]
        print(
            f"{candidate:>5}{row['headroom_ratio']:>8.1f}배"
            f"{row['healthy_one_query_seconds']:>10.0f}초"
            f"{row['degraded_last_attempt_starts']:>14.0f}초"
            f"{('예' if row['outlasts_recovery'] else '아니오'):>11}"
        )
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
