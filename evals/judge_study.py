"""조건 판정 심판만 따로 재는 하네스입니다.

    uv run python -m evals.judge_study
    uv run python -m evals.judge_study --output reports/judge_study_after.json

`candidate_validation.judge_content`의 프롬프트를 고칠 때는 양쪽을 함께 봐야 합니다. 2026-10-13에
`requires_graph` 오탐을 잡으려고 한 문장을 넣었다가 `requires_content` 정확도가 21/21에서
11/21로 떨어진 적이 있습니다. 두 판정이 한 호출에 묶여 있어서 한쪽 설명이 다른 쪽으로 번집니다.

그래서 에이전트 전체를 돌리지 않고 심판 호출만 잽니다. 질문당 구조화 호출 한 번이라 싸고,
혼동 행렬이 바로 나옵니다. 라벨은 질문 문장에서 기계적으로 정합니다.

- 제목·주제만 묻는 질문: 내용 조건 있음, 그래프 조건 없음
- 저자·인용·분류·집계만 묻는 질문: 내용 조건 없음, 그래프 조건 있음

두 번째 묶음이 핵심입니다. "X를 인용한 논문은?"에서 제목 X는 **인용 대상**을 가리키며, 돌려줄
논문이 X에 관한 내용이어야 한다는 뜻이 아닙니다.
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTPUT = Path("reports/judge_study.json")
SAMPLE_PAPERS = 2


def labelled_questions(graph_cases: Path, base_cases: Path) -> list[dict[str, Any]]:
    """Build the labelled set from the existing case files; nothing is hand-written here."""
    questions = []
    graph = json.loads(graph_cases.read_text(encoding="utf-8"))["cases"]
    for case in graph:
        questions.append({
            "id": case["id"],
            "family": case["category"],
            "question": case["question"],
            "paper_ids": case["expected_ids"][:SAMPLE_PAPERS],
            "expect_content": False,
            "expect_graph": True,
            "why": (
                "질문의 제목·분류는 관계의 상대편을 가리킵니다. 돌려줄 논문의 주제 조건이 "
                "아닙니다."
            ),
        })
    base = json.loads(base_cases.read_text(encoding="utf-8-sig"))
    for case in base["cases"] if isinstance(base, dict) else base:
        questions.append({
            "id": case["id"],
            "family": case["category"],
            "question": case["question"],
            "paper_ids": case["expected_ids"][:SAMPLE_PAPERS],
            "expect_content": True,
            "expect_graph": False,
            "why": "주제나 정확한 제목만 묻습니다. 그래프 조건이 없습니다.",
        })
    return questions


def run(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Call the judge once per question with real papers attached."""
    import candidate_validation
    import chatbot

    def fetch(ids: list[str]) -> list[dict[str, Any]]:
        return chatbot.run_read(
            "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids "
            "RETURN p.arxiv_id AS arxiv_id, p.title AS title, p.abstract AS abstract",
            {"ids": ids},
        )

    rows = []
    for item in questions:
        papers = fetch(item["paper_ids"])
        usage: dict[str, int] = {}
        verdict = candidate_validation.judge_content(
            item["question"], papers, chatbot.judging_model(usage)
        )
        rows.append({
            **{key: item[key] for key in ("id", "family", "question")},
            "expect_content": item["expect_content"],
            "expect_graph": item["expect_graph"],
            "got_content": verdict["requires_content"],
            "got_graph": verdict["requires_graph"],
            "graph_conditions": verdict["graph_conditions"],
            "content_correct": verdict["requires_content"] == item["expect_content"],
            "graph_correct": verdict["requires_graph"] == item["expect_graph"],
            "tokens": dict(usage),
        })
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Report accuracy per judgment and per family, plus the two error directions."""
    def accuracy(selected: list[dict[str, Any]], key: str) -> float | None:
        if not selected:
            return None
        return round(sum(row[key] for row in selected) / len(selected), 4)

    families = sorted({row["family"] for row in rows})
    return {
        "questions": len(rows),
        "content_accuracy": accuracy(rows, "content_correct"),
        "graph_accuracy": accuracy(rows, "graph_correct"),
        "both_correct": accuracy(
            [{"ok": row["content_correct"] and row["graph_correct"]} for row in rows], "ok"
        ),
        "graph_false_positive": sum(
            1 for row in rows if row["got_graph"] and not row["expect_graph"]
        ),
        "graph_false_negative": sum(
            1 for row in rows if not row["got_graph"] and row["expect_graph"]
        ),
        "content_false_positive": sum(
            1 for row in rows if row["got_content"] and not row["expect_content"]
        ),
        "content_false_negative": sum(
            1 for row in rows if not row["got_content"] and row["expect_content"]
        ),
        "by_family": {
            family: {
                "questions": sum(1 for row in rows if row["family"] == family),
                "content_accuracy": accuracy(
                    [row for row in rows if row["family"] == family], "content_correct"
                ),
                "graph_accuracy": accuracy(
                    [row for row in rows if row["family"] == family], "graph_correct"
                ),
            }
            for family in families
        },
        "tokens": {
            "input": sum(row["tokens"].get("input_tokens", 0) for row in rows),
            "output": sum(row["tokens"].get("output_tokens", 0) for row in rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="조건 판정 심판의 정확도를 측정합니다.")
    parser.add_argument("--graph-cases", type=Path, default=Path("evals/cases_graph.json"))
    parser.add_argument("--base-cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부 돌립니다.")
    parser.add_argument("--label", default="", help="보고서에 남길 이 실행의 이름입니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    questions = labelled_questions(args.graph_cases, args.base_cases)
    if args.limit:
        questions = questions[: args.limit]
    rows = run(questions)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "summary": summarize(rows),
        "mistakes": [
            {
                key: row[key]
                for key in ("id", "family", "question", "expect_content", "got_content",
                            "expect_graph", "got_graph", "graph_conditions")
            }
            for row in rows
            if not (row["content_correct"] and row["graph_correct"])
        ],
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"틀린 질문 {len(report['mistakes'])}건: "
          + json.dumps(collections.Counter(
              row["family"] for row in report["mistakes"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
