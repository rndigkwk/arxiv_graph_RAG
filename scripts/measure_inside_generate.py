"""`generate` 노드 안쪽을 저장된 한 실행에서 요약합니다 — 모델·도구·나머지, 도구별, SDK 재시도.

    uv run python scripts/measure_inside_generate.py
    uv run python scripts/measure_inside_generate.py --output reports/inside_generate.json

ADR-066이 안쪽을 재는 장치를 넣었지만 저장된 실행은 전부 그 전의 것이라 `0/90건`이었습니다.
2026-10-26에 30문항을 새로 돌렸고(유료), 이 스크립트는 그 보고서만 읽습니다(0원).

**첫 관련 논문 호출은 따로 셉니다.** 하네스 프로세스에는 앱의 부팅 예열(ADR-067)이 없어서
처음 `rank_related_papers`를 부르는 질문이 인용망을 올리는 값을 혼자 냅니다. 그 한 번을
섞으면 앱에서는 일어나지 않는 일이 도구 몫을 부풀립니다. 빼지 않고 이름을 붙여 둡니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

REPORT = Path("reports/inside_generate_2026-10-26.json")
OUTPUT = Path("reports/inside_generate.json")
COLD_TOOL = "rank_related_papers"


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    rows = report["results"]
    traces = [row["generate_trace"] for row in rows if row.get("generate_trace")]
    total = sum(trace["generate_seconds"] for trace in traces)

    seconds: dict[str, list[float]] = defaultdict(list)
    cold: dict[str, Any] | None = None
    for row in rows:
        for call in row.get("tool_seconds", []):
            if call["name"] == COLD_TOOL and cold is None:
                cold = {"case": row["id"], "seconds": call["seconds"]}
                continue
            seconds[call["name"]].append(call["seconds"])

    retries: dict[str, int] = defaultdict(int)
    for row in rows:
        for endpoint, count in row.get("sdk_retries", {}).items():
            retries[endpoint] += count

    return {
        "source": str(REPORT),
        "cases": len(rows),
        "failed": sum(1 for row in rows if row.get("failed")),
        "model": report["metadata"]["model"],
        "generate": {
            "traced": len(traces),
            "adds_up": sum(1 for trace in traces if trace["adds_up"]),
            "seconds": round(total, 1),
            "max_seconds": round(max(trace["generate_seconds"] for trace in traces), 1),
            "model_share": round(sum(t["model_seconds"] for t in traces) / total, 3),
            "tools_share": round(sum(t["tools_seconds"] for t in traces) / total, 3),
            "other_share": round(sum(t["other_seconds"] for t in traces) / total, 3),
            # 앱에서는 예열이 받는 첫 관련 논문 호출을 뺀 도구 몫입니다.
            "tools_share_warm": round(
                (sum(t["tools_seconds"] for t in traces) - (cold or {}).get("seconds", 0.0))
                / total, 3),
        },
        "cold_first_related_call": cold,
        "tools": {
            name: {
                "calls": len(values),
                "seconds": round(sum(values), 1),
                "p50": round(statistics.median(values), 2),
                "max": round(max(values), 2),
            }
            for name, values in sorted(seconds.items(), key=lambda item: -sum(item[1]))
        },
        "sdk_retries": {
            "recorded_cases": sum(1 for row in rows if "sdk_retries" in row),
            "total": sum(retries.values()),
            "by_endpoint": dict(retries),
        },
        "total_seconds_max": round(max(row["graph_trace"]["total_seconds"] for row in rows
                                       if row.get("graph_trace")), 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="generate 노드 안쪽을 요약합니다.")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    summary = summarize(json.loads(args.report.read_text(encoding="utf-8")))
    summary["source"] = args.report.as_posix()
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
