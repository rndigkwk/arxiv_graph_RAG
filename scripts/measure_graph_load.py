"""PageRank 탭이 쓰는 두 질의의 지연을 재서 30초 타임아웃이 위험한지 판단합니다.

    uv run python scripts/measure_graph_load.py --runs 8

2026-10-15 변형 비교 중 `citation_graph()`가 Aura에서 간선 69,438개를 읽다
`TransactionTimedOutClientConfiguration`으로 끊겼습니다. `chatbot.run_read`가 모든 질의에
`timeout=30`을 걸기 때문입니다.

앱의 PageRank 탭이 같은 경로를 쓰므로 운영에서 사용자가 보는 오류입니다. 그런데 그때는 한 번
겪고 넘어갔고, **얼마나 자주 재현되는지, 30초가 빡빡한지 여유인지** 재지 않았습니다. 고칠지
말지를 수치로 정하기 위한 측정입니다.

`run_read`를 그대로 쓰지 않습니다. 그 함수는 질의마다 `EXPLAIN`을 한 번 더 돌려서, 재는
값에 그 비용이 섞입니다. 여기서는 본 질의만 재고 `EXPLAIN` 비용을 따로 적습니다.

읽기만 합니다. 실행할 때마다 Aura에서 같은 데이터를 다시 받으므로 캐시 효과가 끼지 않도록
드라이버를 질의마다 새로 열지는 않습니다 — 앱도 한 세션에서 연달아 두 질의를 돌립니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

OUTPUT = Path("reports/graph_load_latency.json")
STATEMENT_TIMEOUT = 30  # chatbot.run_read 가 본 질의에 거는 값
EXPLAIN_TIMEOUT = 15


def timed(session: Any, query: Any, label: str) -> dict[str, Any]:
    """Run one query and report how long it took, counting rows without keeping them."""
    start = perf_counter()
    try:
        rows = sum(1 for _ in session.run(query))
    except Exception as exc:
        return {
            "label": label,
            "failed": True,
            "error": f"{type(exc).__name__}: {str(exc)[:160]}",
            "seconds": round(perf_counter() - start, 3),
        }
    return {
        "label": label,
        "failed": False,
        "rows": rows,
        "seconds": round(perf_counter() - start, 3),
    }


def summarize(samples: list[dict[str, Any]], timeout: int) -> dict[str, Any]:
    times = [row["seconds"] for row in samples if not row["failed"]]
    failures = [row for row in samples if row["failed"]]
    if not times:
        return {"runs": len(samples), "failures": len(failures), "note": "모든 실행이 실패했습니다"}
    ordered = sorted(times)
    return {
        "runs": len(samples),
        "failures": len(failures),
        "rows": next(row["rows"] for row in samples if not row["failed"]),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))], 3),
        "max": round(max(ordered), 3),
        "mean": round(statistics.fmean(ordered), 3),
        "timeout": timeout,
        # 타임아웃까지 몇 배 남았는지. 1에 가까우면 평상시에도 위험합니다.
        "headroom_ratio": round(timeout / max(ordered), 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PageRank 탭 질의의 지연을 측정합니다.")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from neo4j import Query
    from neo4j.api import READ_ACCESS

    from src import chatbot

    queries = {
        "paper_rows": chatbot.PAPER_ROWS,
        "citation_edges": chatbot.CITATION_EDGES,
    }
    samples: dict[str, list[dict[str, Any]]] = {name: [] for name in queries}
    explain: dict[str, dict[str, Any]] = {}

    with chatbot.driver.session(
        database=chatbot.neo4j_database, default_access_mode=READ_ACCESS
    ) as session:
        for name, cypher in queries.items():
            explain[name] = timed(
                session, Query("EXPLAIN " + cypher, timeout=EXPLAIN_TIMEOUT), f"explain:{name}"
            )
        for attempt in range(args.runs):
            for name, cypher in queries.items():
                row = timed(
                    session, Query(cypher, timeout=STATEMENT_TIMEOUT), f"{name}:{attempt}"
                )
                samples[name].append(row)
                state = "실패" if row["failed"] else f"{row['seconds']:.2f}초"
                print(f"  {name:16s} 실행 {attempt + 1}/{args.runs}: {state}")

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "statement_timeout_seconds": STATEMENT_TIMEOUT,
        "note": (
            "chatbot.run_read가 모든 질의에 거는 타임아웃이 30초입니다. 앱의 PageRank 탭이 "
            "이 두 질의를 연달아 돌립니다. EXPLAIN 비용은 따로 적었습니다."
        ),
        "explain": explain,
        "summary": {name: summarize(rows, STATEMENT_TIMEOUT) for name, rows in samples.items()},
        "samples": samples,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
