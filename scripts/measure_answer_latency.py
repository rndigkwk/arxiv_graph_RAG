"""답변 지연을 **노드별로** 분해합니다 — 새로 돌리지 않고 이미 저장된 실행에서.

    uv run python scripts/measure_answer_latency.py
    uv run python scripts/measure_answer_latency.py --output reports/answer_latency.json

배포 앱에서 한 질문이 약 35초 걸리는 것을 보고 "어디가 긴지"를 재려 했습니다. **돈을 쓸
필요가 없었습니다.** `agent_graph.summarize_trace`가 노드마다 초를 남기고
([`agent_graph.py`](../agent_graph.py)), 기준선 보고서가 그것을 `graph_rag.graph_trace`에
그대로 담고 있습니다. 90건이 이미 디스크에 있습니다.

**이 스크립트가 재지 못하는 것을 먼저 적습니다.** `generate` 노드 안에서 번역·Text2Cypher·
벡터 검색·모델 턴이 각각 얼마를 쓰는지는, **이 파일들에서는** 알 수 없습니다. 저장된 90건은
도구 호출에 소요 시간이 없던 때의 실행이기 때문입니다.

ADR-066이 그 계측을 넣었고 `response["generate_trace"]`에 남습니다. 이 스크립트는 그 값이
있으면 읽고, **없으면 몇 건에 없는지를 적습니다** — 0건을 조용히 넘기면 "안쪽은 원래 못 재는
것"으로 굳습니다. 안쪽을 보려면 평가를 **다시 돌려야** 하고, 그것은 돈이 듭니다.

**하네스와 앱은 같은 입구를 씁니다**(`chatbot.ask`). 그래서 이 수는 앱의 하한입니다 — 앱은
위에 Streamlit 렌더링, 한국어 번역 도구 호출, 부분 그래프 조회, PageRank 패널을 더합니다.
"앱에서 35초"와 "하네스 p50 15.5초"는 **어긋나는 수가 아니라 서로 다른 것**입니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 두 모드를 같이 읽습니다. 한쪽만 읽으면 ADR-024의 표와 왜 다른지 말할 수 없습니다.
MODES = ("routed", "resolve")
RUNS = (1, 2, 3)

# ADR-024가 적은 가족별 p50입니다. **출처에 묶여 있지 않습니다.** 이 스크립트가 재현되는지
# 봅니다 - 재현되지 않으면 그 표는 덮여 쓰인 실행에서 나온 것이고, 그 사실을 적어야 합니다.
ADR_024_P50 = {
    "author": 12.2, "citing": 10.5, "coauthor": 17.5, "reference": 13.9, "topcited": 11.5,
}

# 이 둘 밖의 노드가 이만큼을 넘으면 지연의 **모양이 바뀐 것**이라 분해를 다시 읽어야 합니다.
EXPECTED_HEAVY = ("generate", "check_conditions")
OTHER_SHARE_BAR = 0.05


def percentile(values: list[float], fraction: float) -> float:
    """정렬 후 위치로 읽습니다. 90건에 보간을 쓰면 없는 정밀도가 생깁니다."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return round(ordered[index], 3)


def read_mode(mode: str, root: Path) -> list[dict[str, Any]]:
    """한 모드의 세 실행에서 사례를 모읍니다. 없는 파일은 건너뜁니다."""
    cases = []
    for run in RUNS:
        path = root / f"reports/baseline_{mode}_run{run}.json"
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        for result in report["results"]:
            answer = result.get("graph_rag") or {}
            trace = answer.get("graph_trace") or {}
            if not trace.get("seconds"):
                continue
            cases.append({
                "id": result.get("id"),
                "family": result.get("category"),
                "run": run,
                "source": path.name,
                "seconds": trace["seconds"],
                "total_seconds": trace["total_seconds"],
                "elapsed_seconds": answer.get("elapsed_seconds"),
                "attempts": trace.get("attempts"),
                # ADR-066 이후의 실행에만 있습니다. 없으면 None으로 남겨 세어집니다.
                "inside": answer.get("generate_trace"),
            })
    return cases


def inside_generate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """`generate` 안쪽이 **몇 건에 기록됐는지**부터 적고, 있으면 몫을 냅니다.

    기록이 없을 때 빈 값을 돌려주면 "안쪽은 원래 못 재는 것"으로 읽힙니다. 그래서 분모를
    먼저 적습니다 - 0/90은 "재지 못함"이 아니라 **"다시 돌려야 나옴"**입니다.
    """
    measured = [case["inside"] for case in cases if isinstance(case.get("inside"), dict)]
    summary: dict[str, Any] = {"cases": len(measured), "of": len(cases)}
    if not measured:
        summary["note"] = ("이 보고서들은 ADR-066 이전 실행입니다. 안쪽을 보려면 평가를 "
                           "다시 돌려야 합니다.")
        return summary
    generate = sum(entry.get("generate_seconds", 0.0) for entry in measured)
    for key in ("tools_seconds", "model_seconds", "unattributed_seconds", "other_seconds"):
        total = sum(entry.get(key, 0.0) for entry in measured)
        summary[key] = round(total, 1)
        summary[key.replace("_seconds", "_share")] = (
            round(total / generate, 4) if generate else 0.0
        )
    summary["generate_seconds"] = round(generate, 1)
    summary["model_turns"] = sum(entry.get("model_turns", 0) for entry in measured)
    summary["tool_calls"] = sum(entry.get("tool_calls", 0) for entry in measured)
    # 한 건이라도 두 번 셌으면 합계의 몫이 전부 흔들립니다. 세어서 적습니다.
    summary["did_not_add_up"] = sum(1 for entry in measured if not entry.get("adds_up", True))
    return summary


def by_node(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """노드마다 p50과 **전체 합에서의 비중**을 냅니다.

    비중을 같이 내는 이유는, p50만 보면 가끔 느린 노드가 작아 보이기 때문입니다.
    """
    totals: dict[str, list[float]] = {}
    for case in cases:
        for node, seconds in case["seconds"].items():
            totals.setdefault(node, []).append(seconds)
    grand = sum(sum(values) for values in totals.values())
    rows = []
    for node, values in totals.items():
        rows.append({
            "node": node,
            "cases": len(values),
            "p50": round(statistics.median(values), 3),
            "max": round(max(values), 3),
            "sum": round(sum(values), 1),
            "share": round(sum(values) / grand, 4) if grand else 0.0,
        })
    return sorted(rows, key=lambda row: -row["sum"])


def by_family(cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    families: dict[str, list[float]] = {}
    for case in cases:
        families.setdefault(case["family"], []).append(case["total_seconds"])
    return {
        family: {
            "cases": len(values),
            "p50": round(statistics.median(values), 3),
            "p90": percentile(values, 0.9),
            "max": round(max(values), 3),
        }
        for family, values in sorted(families.items())
    }


def measure(root: Path) -> dict[str, Any]:
    modes: dict[str, Any] = {}
    for mode in MODES:
        cases = read_mode(mode, root)
        if not cases:
            continue
        totals = [case["total_seconds"] for case in cases]
        nodes = by_node(cases)
        heavy = [row for row in nodes if row["node"] in EXPECTED_HEAVY]
        other = [row for row in nodes if row["node"] not in EXPECTED_HEAVY]
        modes[mode] = {
            "cases": len(cases),
            "p50": round(statistics.median(totals), 3),
            "p90": percentile(totals, 0.9),
            "max": round(max(totals), 3),
            "nodes": nodes,
            "families": by_family(cases),
            "heavy_share": round(sum(row["share"] for row in heavy), 4),
            "other_share": round(sum(row["share"] for row in other), 4),
            "shape_held": sum(row["share"] for row in other) <= OTHER_SHARE_BAR,
            "inside_generate": inside_generate(cases),
        }

    # ADR-024의 표가 어느 모드로도 재현되지 않으면, 그 수는 지금 저장소에 없는 실행에서
    # 나온 것입니다. 맞다/틀리다가 아니라 **재현 불가**로 적습니다.
    reproduces = {
        mode: all(
            abs(entry["families"][family]["p50"] - value) <= 0.5
            for family, value in ADR_024_P50.items()
            if family in entry["families"]
        )
        for mode, entry in modes.items()
    }
    return {
        "modes": modes,
        "adr_024": {
            "recorded_p50": ADR_024_P50,
            "reproduced_by": [mode for mode, ok in reproduces.items() if ok],
            "reproducible": any(reproduces.values()),
            "note": (
                "ADR-024의 가족별 p50은 출처에 묶여 있지 않습니다. 저장소의 두 모드 "
                "어느 쪽으로도 재현되지 않으면 그 수는 덮여 쓰인 실행에서 나온 것입니다."
            ),
        },
        "not_measured": (
            "generate 노드 **안쪽**. 이 보고서들은 ADR-066 이전 실행이라 도구·모델 시간이 "
            "없습니다. 안쪽을 보려면 평가를 다시 돌려야 합니다."
            if not any(entry["inside_generate"]["cases"] for entry in modes.values())
            else "모델 턴 **하나의 안쪽**. 대기와 토큰 생성의 몫은 공급자가 알려 주지 않습니다."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="보고서를 쓸 경로")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    report = measure(root)
    if not report["modes"]:
        print("기준선 보고서를 찾지 못했습니다.")
        return 1

    for mode, entry in report["modes"].items():
        print(f'[{mode}] {entry["cases"]}건 · p50 {entry["p50"]}초 · '
              f'p90 {entry["p90"]} · 최대 {entry["max"]}')
        for row in entry["nodes"]:
            if row["sum"] <= 0:
                continue
            print(f'  {row["node"]:<17} p50 {row["p50"]:>7.2f}  비중 {row["share"]:.1%}')
        print('  가족별 p50: ' + '  '.join(
            f'{family} {value["p50"]}' for family, value in entry["families"].items()))
        if not entry["shape_held"]:
            print(f'  ** generate·check_conditions 밖이 {entry["other_share"]:.1%}입니다 '
                  '— 분해를 다시 읽으세요.')
        deeper = entry["inside_generate"]
        print(f'  generate 안쪽 기록: {deeper["cases"]}/{deeper["of"]}건')
        if deeper["cases"]:
            print(f'    모델 {deeper["model_share"]:.1%} ({deeper["model_turns"]}턴) · '
                  f'도구 {deeper["tools_share"]:.1%} ({deeper["tool_calls"]}회) · '
                  f'그 외(뺀 값) {deeper["other_share"]:.1%}')
            if deeper["did_not_add_up"]:
                print(f'    ** {deeper["did_not_add_up"]}건은 안쪽 합이 노드 시간보다 큽니다 '
                      '— 두 번 센 것이라 몫을 믿지 마세요.')

    adr = report["adr_024"]
    print(f'ADR-024 가족별 p50 재현: {"예" if adr["reproducible"] else "아니오"}'
          f'{" (" + ", ".join(adr["reproduced_by"]) + ")" if adr["reproduced_by"] else ""}')
    print(f'재지 못한 것: {report["not_measured"]}')

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"기록: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
