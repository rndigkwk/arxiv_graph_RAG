"""집계 순위 질문에서 에이전트가 수치를 문자열로 정렬하는 빈도를 잽니다.

    uv run python -m evals.order_study --repeats 3
    uv run python -m evals.order_study --family topcited --repeats 3 --label after

`topcited` 유형(분류별 피인용 상위 N편)의 근거 recall이 0.722로 남았을 때, 실패한 실행의
Cypher에 `toString()`이 끼어 있었습니다.

    실패: ... toString(citation_count) AS citation_count ORDER BY citation_count DESC
    성공: ...          citation_count  AS citation_count ORDER BY citation_count DESC

문자열로 감싼 값으로 정렬하면 숫자 순서가 아니라 사전 순서가 적용됩니다. 내림차순에서
`"9" > "88"`이므로 피인용 9회 논문이 88회 논문보다 위로 올라갑니다. cs.SE에서 에이전트가 답한
`95, 9, 8`이 그 분류의 문자열 내림차순과 정확히 일치했습니다.

같은 코드가 실행마다 다르게 답하므로 한 번 돌려서는 고쳤는지 알 수 없습니다. 이 하네스는
케이스를 여러 번 돌려 두 가지를 함께 셉니다.

- **증상**: 정답을 맞혔는가 (근거 recall)
- **원인**: 정렬 키가 문자열로 변환됐는가 (Cypher에서 직접 탐지)

원인을 따로 세는 것이 중요합니다. 문자열 정렬을 해도 그 분류에 한 자리 피인용 논문이 없으면
우연히 정답이 나올 수 있습니다. 증상만 보면 고쳐진 것처럼 보입니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

from cypher_validation import stringified_order_keys

CASES = Path("evals/cases_graph.json")
OUTPUT = Path("reports/order_study.json")


def graph_cypher(response: dict[str, Any]) -> str:
    """Take the last search_graph query, which is the one the answer rests on."""
    queries = [
        call.get("args", {}).get("cypher", "")
        for call in response.get("tool_calls", [])
        if call.get("name") == "search_graph" and isinstance(call.get("args"), dict)
    ]
    return queries[-1] if queries else ""


def run_case(chatbot: Any, case: dict[str, Any], repeats: int) -> list[dict[str, Any]]:
    gold = set(case["expected_ids"])
    rows = []
    for attempt in range(1, repeats + 1):
        row: dict[str, Any] = {"id": case["id"], "attempt": attempt}
        try:
            response, _ = chatbot.ask(case["question"], [])
        except ValueError as exc:
            rows.append({**row, "failed": True, "error": f"{type(exc).__name__}: {exc}",
                         "recall": 0.0, "string_ordered": False, "cypher": ""})
            continue
        cypher = graph_cypher(response)
        cited = set(response.get("evidence_ids") or ())
        keys = stringified_order_keys(cypher)
        rows.append({
            **row,
            "failed": False,
            "recall": len(cited & gold) / len(gold),
            "exact": cited == gold,
            "string_ordered": bool(keys),
            "stringified_order_keys": keys,
            "cited": sorted(cited),
            "cypher": " ".join(cypher.split()),
        })
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [row for row in rows if not row["failed"]]
    return {
        "runs": len(rows),
        "answer_failures": sum(1 for row in rows if row["failed"]),
        "mean_recall": round(fmean(row["recall"] for row in rows), 4) if rows else None,
        "exact_rate": (
            round(fmean(float(row["exact"]) for row in scored), 4) if scored else None
        ),
        "string_ordered_runs": sum(1 for row in scored if row["string_ordered"]),
        "string_ordered_rate": (
            round(fmean(float(row["string_ordered"]) for row in scored), 4)
            if scored else None
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="집계 순위 질문의 정렬 오류를 측정합니다.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--family", default="topcited")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")

    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = [case for case in dataset["cases"] if case["category"] == args.family]
    if not cases:
        parser.error(f"{args.family} 유형의 케이스가 없습니다")

    import chatbot

    rows: list[dict[str, Any]] = []
    for case in cases:
        rows.extend(run_case(chatbot, case, args.repeats))

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "family": args.family,
        "repeats": args.repeats,
        "cases": [case["id"] for case in cases],
        "summary": summarize(rows),
        "by_case": {
            case["id"]: summarize([row for row in rows if row["id"] == case["id"]])
            for case in cases
        },
        "string_ordered_examples": [
            {"id": row["id"], "attempt": row["attempt"], "cited": row["cited"],
             "keys": row["stringified_order_keys"], "cypher": row["cypher"][:400]}
            for row in rows if row.get("string_ordered")
        ][:5],
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {"label": args.label, "summary": report["summary"], "by_case": report["by_case"]},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
