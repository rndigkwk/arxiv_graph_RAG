"""배포된 VM API에서 답변 지연을 잽니다 — 질문을 실제로 보내므로 **돈이 듭니다**(질문당 1센트 안팎).

    uv run --locked python scripts/measure_vm_answer_latency.py
    uv run --locked python scripts/measure_vm_answer_latency.py --per-family 3

목표는 p50 10초입니다(2026-10-27). 로컬 측정(`reports/inside_generate_2026-10-26.json`, Aura,
30문항)은 VM으로 옮기기 전 것이고, VM 첫날(`reports/vm_day1.json`)은 질문 하나가 30~42초였습니다 -
그때는 Streamlit이 같은 VM에서 돌았고 스왑을 썼습니다. **지금 VM의 수를 모르는 채로 흐름을 고치면
무엇이 효과였는지 가를 수 없습니다.** 그래서 먼저 잽니다.

**로컬과 같은 질문을 씁니다**(`evals/cases_graph.json`의 가족마다 앞 N개). 문항이 같아야 두 수를
나란히 놓을 수 있습니다. 서버가 잰 시간(`graph_trace`)과 브라우저 쪽에서 잰 시간을 따로 적습니다 -
둘의 차이가 네트워크·SSE·Caddy입니다.

**상한(ADR-081)을 지킵니다.** IP마다 분당 3회라 요청 사이를 21초 띄우고, 429가 오면
`Retry-After`만큼 기다립니다. 하루 30회 상한이 있으므로 한 번에 30문항을 넘기지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "https://8-230-25-138.sslip.io"
CASES = Path("evals/cases_graph.json")
LOCAL = Path("reports/inside_generate_2026-10-26.json")
OUTPUT = Path("reports/vm_answer_latency.json")
GAP_SECONDS = 21  # 분당 3회 상한 아래로.
TARGET_P50 = 10.0


def percentile(values: list[float], fraction: float) -> float | None:
    """정렬 후 위치로 읽습니다(`measure_answer_latency.py`와 같은 방식). 보간하지 않습니다."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3)


def pick_cases(cases: list[dict[str, Any]], per_family: int) -> list[dict[str, Any]]:
    seen: dict[str, int] = defaultdict(int)
    picked = []
    for case in cases:
        if seen[case["category"]] < per_family:
            seen[case["category"]] += 1
            picked.append(case)
    return picked


def read_events(lines) -> list[tuple[str, dict[str, Any], float]]:
    """SSE를 (이벤트, 데이터, 받은 시각)으로. 받은 시각은 첫 부분 답을 재는 데 씁니다."""
    events, kind, data = [], None, []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
        elif not line and kind:
            events.append((kind, json.loads("\n".join(data)), time.perf_counter()))
            kind, data = None, []
    return events


def ask(api: str, question: str) -> dict[str, Any]:
    body = json.dumps({"question": question, "history": []}).encode("utf-8")
    while True:
        request = urllib.request.Request(api + "/api/ask", data=body,
                                         headers={"content-type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                events = read_events(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("retry-after") or 60)
                print(f"  상한에 걸려 {wait}초 기다립니다", flush=True)
                time.sleep(wait)
                continue
            raise
        break
    finished = time.perf_counter()
    first = next((at for kind, _, at in events if kind == "partial"), None)
    kind, data, _ = events[-1]
    return {
        "outcome": kind,
        "client_seconds": round(finished - started, 3),
        "first_partial_seconds": round(first - started, 3) if first else None,
        "graph_trace": data.get("graph_trace") if kind == "final" else None,
        "generate_trace": data.get("generate_trace") if kind == "final" else None,
        "tools": [{"name": c.get("name"), "seconds": c.get("seconds")}
                  for c in data.get("tool_calls", [])] if kind == "final" else [],
        "detail": None if kind == "final" else data,
    }


def local_totals(report: dict[str, Any] | None) -> dict[str, float]:
    if not report:
        return {}
    return {row["id"]: row["graph_trace"]["total_seconds"]
            for row in report.get("results", []) if row.get("graph_trace")}


def summarize(rows: list[dict[str, Any]], local: dict[str, float]) -> dict[str, Any]:
    """서버가 잰 합계가 목표의 기준입니다. 브라우저 쪽 시간은 그 위에 얹히는 몫을 보려고 둡니다."""
    done = [row for row in rows if row["graph_trace"]]
    server = [row["graph_trace"]["total_seconds"] for row in done]
    nodes: dict[str, list[float]] = defaultdict(list)
    for row in done:
        for node, value in row["graph_trace"]["seconds"].items():
            nodes[node].append(value)
    inside = [row["generate_trace"] for row in done
              if row.get("generate_trace") and row["generate_trace"].get("adds_up")]
    tools: dict[str, list[float]] = defaultdict(list)
    for row in done:
        for call in row["tools"]:
            if call["seconds"] is not None:
                tools[call["name"]].append(call["seconds"])
    paired = [(local[row["id"]], row["graph_trace"]["total_seconds"])
              for row in done if row["id"] in local]
    total = sum(server) or 1.0
    families: dict[str, list[float]] = defaultdict(list)
    for row in done:
        families[row["category"]].append(row["graph_trace"]["total_seconds"])
    return {
        "asked": len(rows),
        "answered": len(done),
        "not_answered": [{"id": row["id"], "outcome": row["outcome"]} for row in rows
                         if not row["graph_trace"]],
        "server_seconds": {"p50": percentile(server, 0.5), "p90": percentile(server, 0.9),
                           "max": max(server, default=None)},
        "client_seconds": {"p50": percentile([r["client_seconds"] for r in done], 0.5),
                           "p90": percentile([r["client_seconds"] for r in done], 0.9)},
        "first_partial_seconds_p50": percentile(
            [r["first_partial_seconds"] for r in done if r["first_partial_seconds"]], 0.5),
        "target_p50": TARGET_P50,
        "meets_target": (percentile(server, 0.5) or 1e9) <= TARGET_P50,
        "nodes": sorted(({"node": node, "p50": percentile(values, 0.5),
                          "share": round(sum(values) / total, 3)}
                         for node, values in nodes.items()), key=lambda n: -n["share"]),
        "inside_generate": {
            "traced": len(inside),
            "model_turns_mean": round(statistics.mean(t["model_turns"] for t in inside), 2)
            if inside else None,
            "seconds_per_turn_p50": percentile(
                [t["model_seconds"] / t["model_turns"] for t in inside if t["model_turns"]], 0.5),
            "model_share": round(sum(t["model_seconds"] for t in inside)
                                 / (sum(t["generate_seconds"] for t in inside) or 1), 3),
            "tools_share": round(sum(t["tools_seconds"] for t in inside)
                                 / (sum(t["generate_seconds"] for t in inside) or 1), 3),
        },
        "tools": {name: {"calls": len(v), "p50": percentile(v, 0.5)} for name, v in tools.items()},
        "families_p50": {name: percentile(v, 0.5) for name, v in sorted(families.items())},
        # 같은 문항의 로컬(Aura, 2026-10-26) 합계와 나란히. 짝이 없으면 비교하지 않습니다.
        "vs_local": {
            "pairs": len(paired),
            "local_p50": percentile([a for a, _ in paired], 0.5),
            "vm_p50": percentile([b for _, b in paired], 0.5),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=API)
    parser.add_argument("--per-family", type=int, default=3)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    cases = pick_cases(json.loads(CASES.read_text(encoding="utf-8"))["cases"], args.per_family)
    if len(cases) > 30:
        raise SystemExit("하루 상한(30회)을 넘깁니다. --per-family를 줄이세요.")
    health = json.loads(urllib.request.urlopen(args.api + "/api/health", timeout=30).read())
    rows = []
    for index, case in enumerate(cases):
        if index:
            time.sleep(GAP_SECONDS)
        result = ask(args.api, case["question"])
        rows.append({"id": case["id"], "category": case["category"], **result})
        server = (result["graph_trace"] or {}).get("total_seconds")
        print(f"{index + 1}/{len(cases)} {case['id']}: {result['outcome']} "
              f"서버 {server}초 · 받는 쪽 {result['client_seconds']}초", flush=True)

    local = local_totals(json.loads(LOCAL.read_text(encoding="utf-8")) if LOCAL.exists() else None)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "api": args.api,
        "commit": health.get("commit"),
        "module_commit": health.get("module_commit"),
        "dataset": str(CASES),
        "summary": summarize(rows, local),
        "results": rows,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
