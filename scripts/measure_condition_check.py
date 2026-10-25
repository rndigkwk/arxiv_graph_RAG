"""조건 판정 게이트가 받는 값과 내는 값을 저장된 평가 리포트에서 셉니다.

**모델을 부르지 않습니다.** 기준선 리포트가 이미 노드별 초와 방문 횟수를 담고 있어서
(ADR-065), 이 스크립트는 디스크만 읽습니다 - 0원입니다. 새로 재려면 평가를 다시 돌려야
하고 그것은 유료입니다.

게이트가 **막은 자리**는 두 가지 방법으로 셉니다.

- `rejections`가 리포트에 있으면 그대로 읽습니다(ADR-069에서 추적에 넣었습니다).
- 없으면 방문 횟수로 추론합니다. 두 게이트는 순서가 정해져 있어서, 조건 판정이 막으면
  `check_conditions`도 두 번 돌고, 형식 검사가 막으면 `check_conditions`는 한 번만
  돕니다. 추론한 값에는 `attribution`에 `"inferred"`가 붙습니다 - **읽은 값과 추론한
  값을 같은 표에 섞으면 안 됩니다.**
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import statistics
from collections import Counter
from typing import Any

# `_ragas`·`_setmetrics`·`_rescored`는 같은 실행을 다시 채점한 파일입니다. 같이 읽으면
# 한 실행을 두 번 세게 됩니다.
DEFAULT_PATTERN = "reports/baseline_*_run?.json"
DERIVED_SUFFIX = re.compile(r"_(ragas|setmetrics|rescored)\.json$")
GATES = ("check_format", "check_conditions")


def report_paths(pattern: str = DEFAULT_PATTERN) -> list[str]:
    """채점만 다시 한 파일을 빼고, 실행 리포트만 돌려줍니다.

    경로의 구분자를 `/`로 맞춥니다. 리포트에 적히는 값이라 Windows와 CI에서 **같은 문자열**
    이어야 합니다 - 안 맞추면 같은 실행의 리포트가 플랫폼마다 달라집니다.
    """
    return sorted(path.replace(os.sep, "/") for path in glob.glob(pattern)
                  if not DERIVED_SUFFIX.search(path))


def load_runs(paths: list[str]) -> list[dict[str, Any]]:
    """리포트들의 질문별 행을 한 줄씩 펴서 모읍니다."""
    runs = []
    for path in paths:
        report = json.load(open(path, encoding="utf-8"))
        for item in report["results"]:
            answer = item["graph_rag"]
            trace = answer["graph_trace"]
            runs.append({
                "report": os.path.basename(path),
                "id": item["id"],
                "category": item["category"],
                "visits": trace["visits"],
                "seconds": trace["seconds"],
                "total_seconds": trace["total_seconds"],
                "rejections": trace.get("rejections"),
            })
    return runs


def rejecting_gate(run: dict[str, Any]) -> tuple[str | None, str]:
    """**처음** 막은 게이트와, 그 값을 어떻게 알았는지를 돌려줍니다.

    돌려주는 둘째 값이 `"recorded"`면 리포트가 적어 둔 값이고, `"inferred"`면 방문
    횟수에서 추론한 값입니다. 한 번에 통과했으면 게이트는 `None`입니다.
    """
    recorded = run.get("rejections")
    if recorded is not None:
        nodes = [entry["node"] for entry in recorded]
        return (nodes[0] if nodes else None), "recorded"
    visits = run["visits"]
    if visits.get("revise", 0) == 0 and visits.get("abstain", 0) == 0:
        return None, "inferred"
    # 조건 판정까지 갔다가 막혔으면 둘 다 두 번, 형식에서 막혔으면 조건 판정은 한 번입니다.
    if visits.get("check_conditions", 0) >= visits.get("check_format", 0):
        return "check_conditions", "inferred"
    return "check_format", "inferred"


def node_cost(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """노드별 합·비중·중앙값과, 전체 합을 돌려줍니다."""
    grand = sum(run["total_seconds"] for run in runs)
    nodes = sorted({name for run in runs for name in run["seconds"]})
    table = {}
    for name in nodes:
        values = [run["seconds"].get(name, 0.0) for run in runs]
        table[name] = {
            "sum_seconds": round(sum(values), 1),
            "share_percent": round(100 * sum(values) / grand, 1) if grand else 0.0,
            "p50_seconds": round(statistics.median(values), 2),
            "p90_seconds": round(sorted(values)[int(0.9 * len(values))], 2),
            "visits": sum(run["visits"].get(name, 0) for run in runs),
        }
    return {"total_seconds": round(grand, 1), "nodes": table}


def gate_effect(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """게이트가 몇 번 막았고, 그게 어느 질문 가족이었는지를 셉니다."""
    rejected: list[dict[str, Any]] = []
    methods: Counter[str] = Counter()
    for run in runs:
        gate, method = rejecting_gate(run)
        methods[method] += 1
        if gate is not None:
            rejected.append({"report": run["report"], "id": run["id"],
                             "category": run["category"], "gate": gate})
    by_gate = {
        gate: {
            "rejections": sum(1 for item in rejected if item["gate"] == gate),
            "categories": dict(Counter(
                item["category"] for item in rejected if item["gate"] == gate)),
        }
        for gate in GATES
    }
    return {
        "runs": len(runs),
        "runs_rejected_once": len(rejected),
        "rejected_percent": round(100 * len(rejected) / len(runs), 1) if runs else 0.0,
        "abstained": sum(1 for run in runs if run["visits"].get("abstain", 0) > 0),
        "attribution": dict(methods),
        "by_gate": by_gate,
        "rejected_runs": rejected,
    }


def measure(pattern: str = DEFAULT_PATTERN) -> dict[str, Any]:
    paths = report_paths(pattern)
    if not paths:
        raise SystemExit(f"리포트를 찾지 못했습니다: {pattern}")
    runs = load_runs(paths)
    cost = node_cost(runs)
    effect = gate_effect(runs)
    condition = cost["nodes"]["check_conditions"]
    return {
        "reports": paths,
        "runs": len(runs),
        "cost": cost,
        "effect": effect,
        # 한 줄로 말하면 이것입니다: 모든 답변에서 이만큼을 내고, 이만큼을 바꿉니다.
        "verdict": {
            "condition_share_percent": condition["share_percent"],
            "condition_p50_seconds": condition["p50_seconds"],
            "outcomes_changed_percent": effect["rejected_percent"],
        },
        "note": "모델을 부르지 않습니다 - 저장된 리포트만 읽습니다(0원).",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pattern", default=DEFAULT_PATTERN,
                        help="읽을 실행 리포트 glob (기본: %(default)s)")
    parser.add_argument("--output", default="reports/condition_check_weight.json")
    args = parser.parse_args()
    result = measure(args.pattern)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    verdict = result["verdict"]
    print(f"{result['runs']}회 - 조건 판정 {verdict['condition_share_percent']}% "
          f"(p50 {verdict['condition_p50_seconds']}초), "
          f"결과를 바꾼 비율 {verdict['outcomes_changed_percent']}%")
    for gate, row in result["effect"]["by_gate"].items():
        print(f"  {gate}: {row['rejections']}회 {row['categories'] or ''}")
    print(f"→ {args.output}")


if __name__ == "__main__":
    main()
