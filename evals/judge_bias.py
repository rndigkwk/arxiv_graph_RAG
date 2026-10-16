"""심판의 판정이 **입력 순서**에 흔들리는지 재고, 그것을 그냥 변동과 구별합니다.

    uv run python -m evals.judge_bias --limit 12
    uv run python -m evals.judge_bias --output reports/judge_bias.json

Day 60 교안 5절의 "순서를 바꿔 다시 묻기"를 이 프로젝트의 심판에 맞게 옮긴 것입니다. 교안은 두
답을 비교하는 Pairwise 판사의 위치 편향을 다루는데, 우리 심판은 두 답을 비교하지 않습니다 —
질문 하나와 **논문 목록**을 받아 논문마다 내용 일치를 판정합니다. 그래서 순서를 바꿀 자리는
답이 아니라 **논문 목록**입니다.

[`judge_study`](judge_study.py)는 질문당 한 번만 불러 정확도를 봅니다. 한 번만 부르면 판정이
바뀌는 것을 볼 수 없습니다.

**핵심은 세 번째 호출입니다.** 순서를 바꿔 판정이 달라졌다고 바로 "위치 편향"이라고 적으면
안 됩니다. 같은 순서로 두 번 불러도 달라질 수 있기 때문입니다. 그래서 질문마다 세 번 부릅니다.

| 호출 | 입력 |
|---|---|
| `a1` | 원래 순서 |
| `a2` | **같은** 순서 (자기 일관성) |
| `b` | 뒤집은 순서 (위치 편향) |

`a1`≠`a2`는 변동이고, `a1`=`a2`인데 `b`만 다르면 순서가 원인입니다. 둘을 나누지 않으면
`coauthor-03`에서 했던 실수를 반복합니다 — 그럴듯한 설명에 수치를 붙이지 않고 믿는 것.

논문별 판정은 **arxiv_id로 맞춰** 비교합니다. 위치로 비교하면 순서를 바꾼 것 자체가 차이로
잡혀서 아무것도 재지 못합니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.judge_study import labelled_questions

OUTPUT = Path("reports/judge_bias.json")
# 순서를 뒤집는 것이 뜻을 가지려면 논문이 둘 이상이어야 합니다.
MIN_PAPERS = 2


def verdict_shape(verdict: dict[str, Any]) -> dict[str, Any]:
    """The parts of a verdict that two calls can be compared on."""
    return {
        "requires_content": verdict["requires_content"],
        "requires_graph": verdict["requires_graph"],
        # 조건 문장은 표현이 조금씩 달라지므로 개수만 봅니다. 문장 비교는 변동을 과대평가합니다.
        "graph_condition_count": len(verdict["graph_conditions"]),
        "content_matches": dict(verdict["content_matches"]),
    }


def disagreements(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    """Which parts of two verdicts differ, keyed by arxiv_id for the per-paper part."""
    differing = [
        key
        for key in ("requires_content", "requires_graph", "graph_condition_count")
        if left[key] != right[key]
    ]
    differing += [
        f"content_matches[{paper_id}]"
        for paper_id in sorted(set(left["content_matches"]) | set(right["content_matches"]))
        if left["content_matches"].get(paper_id) != right["content_matches"].get(paper_id)
    ]
    return differing


def run(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Judge each question three times: same order twice, then reversed."""
    import candidate_validation
    import chatbot

    def fetch(ids: list[str]) -> list[dict[str, Any]]:
        rows = chatbot.run_read(
            "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids "
            "RETURN p.arxiv_id AS arxiv_id, p.title AS title, p.abstract AS abstract",
            {"ids": ids},
        )
        # Cypher의 IN은 순서를 보장하지 않습니다. 순서가 변수이므로 직접 맞춥니다.
        by_id = {row["arxiv_id"]: row for row in rows}
        return [by_id[paper_id] for paper_id in ids if paper_id in by_id]

    rows = []
    for item in questions:
        papers = fetch(item["paper_ids"])
        if len(papers) < MIN_PAPERS:
            continue  # 뒤집을 것이 없으면 이 측정의 대상이 아닙니다.
        usage: dict[str, int] = {}

        def judge(ordered: list[dict[str, Any]], *, question: str = item["question"]) -> dict:
            return verdict_shape(
                candidate_validation.judge_content(
                    question, ordered, chatbot.judging_model(usage)
                )
            )

        first = judge(papers)
        repeat = judge(papers)
        reversed_order = judge(list(reversed(papers)))

        noise = disagreements(first, repeat)
        order = disagreements(first, reversed_order)
        rows.append({
            **{key: item[key] for key in ("id", "family", "question")},
            "paper_ids": [paper["arxiv_id"] for paper in papers],
            "same_order": [first, repeat],
            "reversed_order": reversed_order,
            "noise_disagreements": noise,
            "order_disagreements": order,
            "stable_under_repeat": not noise,
            # 같은 순서로는 흔들리지 않았는데 뒤집으니 달라진 것만 순서 탓입니다.
            "order_only": not noise and bool(order),
            "tokens": dict(usage),
        })
    return rows


FIELDS = ("requires_content", "requires_graph", "graph_condition_count", "content_matches")


def _field_counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    """How many questions moved on each part of the verdict."""
    return {
        field: sum(1 for row in rows if any(d.startswith(field) for d in row[key]))
        for field in FIELDS
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Separate what the order changed from what the judge changes on its own."""
    if not rows:
        return {"questions": 0, "note": "비교할 질문이 없습니다."}

    def rate(count: int) -> float:
        return round(count / len(rows), 4)

    noisy = [row for row in rows if row["noise_disagreements"]]
    order_only = [row for row in rows if row["order_only"]]
    families = sorted({row["family"] for row in rows})
    return {
        "questions": len(rows),
        "calls_per_question": 3,
        # 같은 입력으로 두 번 불렀을 때 판정이 달라진 비율입니다. 이것이 바닥 소음입니다.
        "repeat_flip_rate": rate(len(noisy)),
        # 소음이 없는데 순서만으로 달라진 비율입니다. 이것만 위치 편향입니다.
        "order_only_flip_rate": rate(len(order_only)),
        "both_flipped": rate(
            sum(1 for row in rows if row["noise_disagreements"] and row["order_disagreements"])
        ),
        "stable_everywhere": rate(
            sum(
                1
                for row in rows
                if not row["noise_disagreements"] and not row["order_disagreements"]
            )
        ),
        # 어느 항목이 움직였는지는 두 쪽을 함께 세야 합니다. 순서 쪽만 세면 "내용 판정은
        # 한 번도 안 뒤집혔다"처럼 읽히는데, 같은 순서로 뒤집힌 것이 있을 수 있습니다.
        "order_flipped_fields": _field_counts(rows, "order_disagreements"),
        "repeat_flipped_fields": _field_counts(rows, "noise_disagreements"),
        "by_family": {
            family: {
                "questions": sum(1 for row in rows if row["family"] == family),
                "order_only": sum(
                    1 for row in rows if row["family"] == family and row["order_only"]
                ),
                "repeat_flips": sum(
                    1
                    for row in rows
                    if row["family"] == family and row["noise_disagreements"]
                ),
            }
            for family in families
        },
        "reading": (
            "order_only_flip_rate만 위치 편향입니다. repeat_flip_rate가 그보다 크거나 비슷하면 "
            "순서가 아니라 심판 자체의 변동이며, 그때 고칠 것은 프롬프트가 아니라 "
            "판정을 여러 번 모으는 설계입니다."
        ),
        "tokens": {
            "input": sum(row["tokens"].get("input_tokens", 0) for row in rows),
            "output": sum(row["tokens"].get("output_tokens", 0) for row in rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="심판의 위치 편향을 변동과 구별해 측정합니다.")
    parser.add_argument("--graph-cases", type=Path, default=Path("evals/cases_graph.json"))
    parser.add_argument("--base-cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부 돌립니다.")
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    questions = labelled_questions(args.graph_cases, args.base_cases)
    if args.limit:
        questions = questions[: args.limit]
    rows = run(questions)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "summary": summarize(rows),
        "flipped": [
            {
                key: row[key]
                for key in ("id", "family", "question", "paper_ids",
                            "noise_disagreements", "order_disagreements", "order_only")
            }
            for row in rows
            if row["noise_disagreements"] or row["order_disagreements"]
        ],
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
