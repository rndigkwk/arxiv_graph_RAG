"""여러 번 돌린 RAGAS 보고서의 **변동폭**을 재서, 임계값을 정할 수 있는지 봅니다.

    uv run python -m evals.ragas_spread reports/ragas_study_full*.json

ADR-017에서 2회 실행으로 정한 허용폭이 3회차에서 깨졌습니다. 그래서 RAGAS 지표도 한 번
재고 임계값을 정하지 않았습니다. 이 스크립트는 그 변동폭을 셉니다.

**두 종류의 변동을 구별합니다.** 같은 질문을 다시 돌리면 두 곳이 달라집니다.

| 종류 | 무엇이 다른가 | 어떻게 재는가 |
|---|---|---|
| 전체 변동 | 에이전트가 새로 답하고 판정도 새로 함 | 매번 새로 돌린 보고서들 |
| 판정 변동 | **같은 답변**을 판정만 다시 함 | 같은 입력 보고서를 여러 번 채점 |

게이트에 필요한 값은 **전체 변동**입니다(주간 cron이 매번 새로 돌립니다). 그런데 둘을 나누지
않으면 "지표가 흔들린다"와 "앱이 흔들린다"를 구별할 수 없습니다. 판정 변동이 전체 변동의
대부분이면 고칠 것은 앱이 아니라 **판정을 여러 번 모으는 설계**입니다.

사례별 비교는 **id로 맞춰** 합니다. 사례 집합이 실행마다 다르면(실패로 빠지면) 교집합만 셉니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any

METRICS = ("faithfulness", "answer_relevancy", "context_precision", "context_relevance")


def load(paths: list[Path]) -> list[dict[str, Any]]:
    runs = []
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        runs.append({
            "path": str(path),
            "label": report.get("label", ""),
            "source_report": report.get("source_report", ""),
            "judge_model": report.get("summary", {}).get("judge_model", ""),
            "metrics": report.get("summary", {}).get("metrics", {}),
            "by_family": report.get("summary", {}).get("by_family", {}),
            "cases": {row["id"]: row for row in report.get("results", [])},
        })
    return runs


def spread(values: list[float]) -> dict[str, Any]:
    return {
        "runs": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "mean": round(statistics.fmean(values), 4),
        "spread": round(max(values) - min(values), 4),
    }


def across_runs(runs: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Spread of each metric's run-level mean."""
    result = {}
    for name in METRICS:
        values = [
            run[key][name]
            for run in runs
            if isinstance(run[key].get(name), (int, float))
        ]
        if len(values) > 1:
            result[name] = spread(values)
    return result


def per_case(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Spread within each case, and the worst case per metric.

    실행 평균이 안정적이어도 사례 하나가 크게 흔들릴 수 있습니다. 게이트의 사례별 규칙은
    그 값을 봐야 합니다.
    """
    shared = set(runs[0]["cases"])
    for run in runs[1:]:
        shared &= set(run["cases"])
    worst: dict[str, Any] = {}
    cases: dict[str, Any] = {}
    for case_id in sorted(shared):
        entry = {}
        for name in METRICS:
            values = [
                run["cases"][case_id][name]
                for run in runs
                if isinstance(run["cases"][case_id].get(name), (int, float))
            ]
            if len(values) > 1:
                entry[name] = spread(values)
                current = worst.get(name)
                if current is None or entry[name]["spread"] > current["spread"]:
                    worst[name] = {"case": case_id, **entry[name]}
        if entry:
            cases[case_id] = entry
    return {
        "cases_compared": len(shared),
        "cases_only_in_some_runs": sorted(
            set().union(*(set(run["cases"]) for run in runs)) - shared
        ),
        "worst_case_per_metric": worst,
        "by_case": cases,
    }


def by_family(runs: list[dict[str, Any]]) -> dict[str, Any]:
    families = sorted(set().union(*(set(run["by_family"]) for run in runs)))
    result = {}
    for family in families:
        entry = {}
        for name in METRICS:
            values = [
                run["by_family"][family][name]
                for run in runs
                if family in run["by_family"]
                and isinstance(run["by_family"][family].get(name), (int, float))
            ]
            if len(values) > 1:
                entry[name] = spread(values)
        if entry:
            result[family] = entry
    return result


def kind_of(runs: list[dict[str, Any]]) -> str:
    """Whether these runs share their answers, which decides what the spread means."""
    sources = {run["source_report"] for run in runs}
    if len(sources) == 1 and sources != {""}:
        return "judge_only"
    return "end_to_end"


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    kind = kind_of(runs)
    return {
        "runs": len(runs),
        "kind": kind,
        "kind_note": (
            "같은 입력 보고서를 여러 번 채점했습니다. 이 변동은 **판정 모델만**의 것입니다."
            if kind == "judge_only"
            else "매번 새로 돌린 보고서입니다. 이 변동은 에이전트와 판정을 **합한** 것입니다."
        ),
        "judge_models": sorted({run["judge_model"] for run in runs}),
        "inputs": [run["source_report"] for run in runs],
        "overall": across_runs(runs, "metrics"),
        "by_family": by_family(runs),
        "per_case": per_case(runs),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS 보고서들의 변동폭을 셉니다.")
    parser.add_argument("reports", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, default=Path("reports/ragas_spread.json"))
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    if len(args.reports) < 2:
        parser.error("변동폭을 재려면 보고서가 둘 이상 필요합니다.")
    runs = load(args.reports)
    report = {"summary": summarize(runs), "reports": [run["path"] for run in runs]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    printable = {key: value for key, value in report["summary"].items() if key != "per_case"}
    printable["per_case"] = {
        key: value for key, value in report["summary"]["per_case"].items() if key != "by_case"
    }
    print(json.dumps(printable, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
