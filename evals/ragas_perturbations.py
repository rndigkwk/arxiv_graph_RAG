"""일부러 틀린 답을 만들어, 지표가 **떨어지는지** 확인합니다.

    uv run python -m evals.ragas_perturbations --report reports/ragas_input_families.json

RAGAS 1차 측정에서 faithfulness가 5건 전부 1.000이었습니다. 판정 모델을 바꿔도 전부 1.000
이었는데, 그것은 "두 판정이 일치했다"가 아니라 **"불일치할 자리가 없었다"** 는 뜻입니다.
천장에 붙은 표본으로는 지표가 나쁜 답을 걸러내는지 알 수 없습니다.

그래서 **알려진 결함을 심은 답변**을 만듭니다. 결함의 종류를 정해 두면 Day 59 교안 7절의
"결함과 떨어지는 지표 짝짓기"를 그대로 확인할 수 있습니다.

| 결함 | 어떤 답변인가 | 떨어져야 하는 지표 |
|---|---|---|
| `fabricated_id` | 문맥에 없는 arXiv ID로 바꿔치기 | Faithfulness |
| `contradiction` | 행이 있는데 "해당 논문이 없다"고 답 | Faithfulness |
| `off_topic` | 질문과 무관한 사실을 답 | Answer Relevancy, Faithfulness |
| `truncated` | 다섯 편 중 한 편만 답 | **아무것도 떨어지지 않아야 함** |

마지막 줄이 이 측정의 또 다른 목적입니다. `truncated`는 **말한 것은 다 맞지만 빠뜨린** 답변
입니다. 우리가 고른 네 지표는 완전성을 보지 않으므로 점수가 유지돼야 하고, 그러면 "이 네 개로는
누락을 못 잡는다"가 측정으로 남습니다 — Context Recall과 Answer Correctness가 왜 있는지의 답이고,
우리가 그 둘을 **골드 ID의 `recall_at_k`로 대신하는** 이유입니다.

변형은 **LLM을 쓰지 않고 문자열 규칙으로** 만듭니다. 생성하면 변형 자체가 변동을 끌어옵니다.
문맥은 손대지 않습니다 — 바꾸는 것은 답변뿐이어야 원인이 답변에 있습니다.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTPUT = Path("reports/ragas_perturbed_input.json")
ARXIV_ID = re.compile(r"\b\d{4}\.\d{4,5}\b")
# 코퍼스에 있을 수 없는 ID입니다(1999년 12월). 문맥에 없다는 것을 아래에서 확인합니다.
FABRICATED_ID = "9912.99999"
OFF_TOPIC_ANSWER = (
    "The transformer architecture was introduced in 2017 and replaced recurrence with "
    "self-attention, which made training parallelizable across sequence positions."
)
CONTRADICTION_ANSWER = (
    "No papers in this collection satisfy the question, so there is nothing to report."
)


def fabricated_id(answer: str, contexts: list[str]) -> str | None:
    """Swap the first arXiv id for one that cannot appear in the contexts."""
    found = ARXIV_ID.search(answer)
    if not found:
        return None
    if any(FABRICATED_ID in context for context in contexts):
        raise AssertionError("심으려는 ID가 이미 문맥에 있습니다. 다른 값을 쓰세요.")
    return answer[: found.start()] + FABRICATED_ID + answer[found.end() :]


def truncated(answer: str) -> str | None:
    """Keep the table header and a single data row, dropping the rest.

    말한 것은 전부 문맥에 있습니다. 빠진 것만 생깁니다.
    """
    lines = answer.splitlines()
    table = [index for index, line in enumerate(lines) if line.strip().startswith("|")]
    if len(table) < 4:  # 헤더·구분선·데이터 두 줄 이상이어야 자를 것이 있습니다.
        return None
    keep = set(table[:3])  # 헤더, 구분선, 데이터 한 줄
    return "\n".join(
        line for index, line in enumerate(lines) if index not in set(table) or index in keep
    )


def perturb(row: dict[str, Any]) -> dict[str, str]:
    """Build every perturbation that applies to this answer."""
    answer, contexts = row["answer"], row.get("contexts") or []
    variants = {
        "contradiction": CONTRADICTION_ANSWER,
        "off_topic": OFF_TOPIC_ANSWER,
    }
    if (swapped := fabricated_id(answer, contexts)) is not None:
        variants["fabricated_id"] = swapped
    if (shortened := truncated(answer)) is not None:
        variants["truncated"] = shortened
    return variants


def build(
    report: dict[str, Any], questions: dict[str, str] | None = None
) -> tuple[dict[str, Any], list[str]]:
    """Return a report of perturbed cases, in the same shape the study already reads.

    질문 문장을 각 사례에 **직접 적습니다.** 변형 사례의 id는 `citing-01::off_topic`이라서
    케이스 파일의 id와 맞지 않고, 채점기가 질문을 못 찾으면 전부 건너뜁니다(실제로 그렇게
    한 번 멈췄습니다). 보고서가 스스로 설명되게 만드는 것이 builder의 몫입니다.
    """
    questions = questions or {}
    results, notes = [], []
    for result in report.get("results", []):
        row = result.get("graph_rag", result)
        if row.get("failed") or not (row.get("answer") or "").strip():
            notes.append(f"{result.get('id')}: 원본에 답변이 없어 변형하지 않았습니다")
            continue
        variants = perturb(row)
        for defect, answer in sorted(variants.items()):
            perturbed = copy.deepcopy(result)
            target = perturbed.get("graph_rag", perturbed)
            target["answer"] = answer
            target["id"] = f"{result['id']}::{defect}"
            perturbed["id"] = target["id"]
            perturbed["defect"] = defect
            perturbed["base_case"] = result["id"]
            question = (
                result.get("question") or row.get("question") or questions.get(result["id"], "")
            )
            if not question:
                notes.append(f"{result['id']}: 질문 문장을 찾지 못했습니다")
            perturbed["question"] = question
            target["question"] = question
            results.append(perturbed)
        missing = {"fabricated_id", "truncated"} - set(variants)
        if missing:
            notes.append(f"{result['id']}: {sorted(missing)} 변형은 적용되지 않았습니다")
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": report.get("mode"),
        "note": (
            "일부러 심은 결함입니다. 문맥은 원본 그대로이고 답변만 바꿨습니다. "
            "변형은 문자열 규칙으로 만들어 LLM 변동이 섞이지 않습니다."
        ),
        "source_report": report.get("source_report") or "",
        "results": results,
    }, notes


def main() -> int:
    parser = argparse.ArgumentParser(description="알려진 결함을 심은 평가 보고서를 만듭니다.")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases_graph.json"),
                        help="질문 문장을 가져올 케이스 파일입니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    questions = {}
    if args.cases.exists():
        cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))
        for case in cases["cases"] if isinstance(cases, dict) else cases:
            questions[case["id"]] = case.get("question", "")
    built, notes = build(report, questions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(built, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    counts: dict[str, int] = {}
    for result in built["results"]:
        counts[result["defect"]] = counts.get(result["defect"], 0) + 1
    print(json.dumps({
        "output": str(args.output),
        "cases": len(built["results"]),
        "by_defect": counts,
        "notes": notes,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
