"""심판이 무너지면 게이트가 무엇을 잡는지 결함을 심어 잽니다(ADR-060).

    uv run python -m evals.judge_collapse --output reports/judge_collapse.json

[ADR-059](../docs/adr/ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md)가 조건
판정 심판이 **한 문장의 기울기 위에** 있다는 것을 쟀습니다. 그리고 게이트의 `prompt_version`
규칙은 **본문이 바뀌면** 막지만 **같은 본문이 다르게 행동하면** 못 잡습니다. 그래서 남은
질문이 "심판이 무너지면 게이트가 잡는가"입니다.

**LLM을 쓰지 않습니다.** 심판이 무너질 때 일어나는 일이 코드로 이어져 있기 때문입니다.

| 단계 | 어디 |
|---|---|
| 심판이 `requires_content=True`를 잘못 돌려줍니다 | `candidate_validation.judge_content` |
| 내용 조건이 걸린 논문이 걸러지고 `ValueError` | `answer_validation.validate_mixed_candidates` |
| 앱에서 그것은 "답하지 못함" | `evals/run.py`의 `except ValueError` |
| 그 케이스가 **0점 + `failed: True`** | `run.evaluate_case(case, {}, mode="agent")` |

마지막 줄이 이 측정을 싸게 만듭니다. 실패 행은 파이프라인이 `evaluate_case`를 **빈 응답으로**
부른 결과이므로, 변형이 그 함수를 그대로 부르면 손으로 지은 숫자가 하나도 없습니다. 집계도
`run.summarize_comparison`을 그대로 씁니다.

`evals/ragas_perturbations.py`가 같은 방법을 RAGAS에서 이미 썼습니다 — "생성하면 변형 자체가
변동을 끌어옵니다".

**이 거짓 양성 모드는 상상한 결함이 아닙니다.** v2 프롬프트에서 `citing`·`coauthor`·
`reference` 16건이 실제로 그 방향으로 틀렸습니다(ADR-059).
"""

from __future__ import annotations

import argparse
import itertools
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import gate, run
from pipeline.console import use_utf8_stdout

BASELINE = Path("baselines/eval_baseline.json")
# 대조군은 **실제 실행**의 사례별 숫자입니다. 합성한 숫자로 대조군을 만들면 그 뒤의 용량이
# 무엇에 대한 용량인지 말할 수 없습니다.
CONTROL = Path("reports/rag_eval_graph_compare_final.json")
CASES = Path("evals/cases_graph.json")
OUTPUT = Path("reports/judge_collapse.json")
SPLIT = "full"
# 각 용량에서 부분집합을 몇 개까지 볼지입니다. 전수는 조합이 폭발합니다(30건에서 N=4면
# 27,405개). 가족을 고르게 섞은 묶음과 가족을 뭉친 묶음을 함께 봅니다 - 뭉친 쪽이 집계를
# 덜 흔들 수 있어 **구멍이 있다면 거기**입니다.
SUBSET_LIMIT = 40
# 어디까지 올려 볼지입니다. 예측대로 1에서 잡히면 1에서 끝나지만, 안 잡히면 구멍의 크기를
# 알아야 하므로 넉넉히 둡니다.
MAX_DOSE = 8


def failed_row(case: dict[str, Any], top_k: int) -> dict[str, Any]:
    """파이프라인이 `ValueError`를 만났을 때 쓰는 그 행입니다.

    숫자를 손으로 적지 않습니다 - `run.evaluate_case`를 빈 응답으로 부르는 것이
    `evals/run.py`의 `except ValueError` 분기가 하는 일 그대로입니다.
    """
    row = run.evaluate_case(case, {}, mode="agent", top_k=top_k)
    row.update({
        "failed": True,
        "error": (
            "ValueError: 내용 조건을 확인하지 못한 논문: "
            "[심은 결함 - 심판이 requires_content=True를 잘못 돌려줬습니다]"
        ),
        "elapsed_seconds": 0.0,
    })
    return row


def inject(report: dict[str, Any], cases: dict[str, dict[str, Any]],
           broken: set[str], top_k: int) -> dict[str, Any]:
    """`broken`의 사례를 실패로 바꾸고 집계를 **파이프라인 함수로** 다시 셉니다."""
    rows = []
    for row in report["results"]:
        row = json.loads(json.dumps(row))  # 원본을 건드리지 않습니다.
        if row["id"] in broken:
            row["graph_rag"] = failed_row(cases[row["id"]], top_k)
            row["graph_rag"]["id"] = row["id"]
        rows.append(row)
    shaped = dict(report)
    shaped["results"] = rows
    # 사례만 바꾸고 집계를 그대로 두면 **존재할 수 없는 보고서**를 시험하게 됩니다.
    shaped["summary"] = run.summarize_comparison(rows)
    return shaped


def subsets(case_ids: list[str], categories: dict[str, str],
            dose: int, limit: int) -> list[tuple[str, ...]]:
    """용량 `dose`의 부분집합을 고릅니다. 가족을 뭉친 묶음을 먼저 봅니다.

    어떤 1건은 잡고 어떤 1건은 놓치면 **놓치는 쪽이 구멍**입니다. 그래서 하나만 보지
    않습니다. 가족을 뭉치면 집계가 덜 흔들릴 수 있어, 구멍이 있다면 거기입니다.
    """
    if dose <= 0:
        return []
    picked: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()

    def add(combo: tuple[str, ...]) -> None:
        key = tuple(sorted(combo))
        if len(key) == dose and key not in seen:
            seen.add(key)
            picked.append(key)

    # 1) 가족별로 뭉친 묶음
    by_family: dict[str, list[str]] = {}
    for case_id in case_ids:
        by_family.setdefault(categories[case_id], []).append(case_id)
    for members in by_family.values():
        if len(members) >= dose:
            add(tuple(members[:dose]))
    # 2) 가족을 고르게 섞은 묶음
    mixed = [
        case_id for group in itertools.zip_longest(*by_family.values())
        for case_id in group if case_id
    ]
    for start in range(0, max(1, len(mixed) - dose + 1)):
        if len(picked) >= limit:
            break
        add(tuple(mixed[start:start + dose]))
    # 3) 남은 자리는 사전순 조합으로 채웁니다.
    for combo in itertools.combinations(case_ids, dose):
        if len(picked) >= limit:
            break
        add(combo)
    return picked[:limit]


def failures_after(control: dict[str, Any], broken: set[str]) -> int:
    """결함을 심은 뒤의 **총** 실패 건수입니다.

    게이트의 `answer_failure_rate`는 용량이 아니라 총계를 봅니다. 대조군에 이미 실패한
    사례가 있으면 그것을 다시 심어도 총계가 늘지 않습니다.
    """
    return sum(
        1 for row in control["results"]
        if row["id"] in broken or row.get("graph_rag", {}).get("failed")
    )


def measure(baseline: dict[str, Any], control: dict[str, Any],
            cases: dict[str, dict[str, Any]], *, top_k: int,
            max_dose: int, limit: int) -> dict[str, Any]:
    """용량을 올리며 **새로 생긴** 위반만 셉니다.

    대조군에서 이미 켜져 있는 위반은 결함을 심어도 켜져 있습니다. 규칙 이름으로 빼면
    너무 거칩니다 - `floor`는 RAGAS 때문에 상수로 켜져 있지만, 결함은 같은 `floor`에서
    `answer_evidence_recall`을 새로 터뜨립니다. 그래서 **(규칙, 사유) 쌍**으로 봅니다.
    """
    case_ids = [row["id"] for row in control["results"]]
    categories = {row["id"]: row["category"] for row in control["results"]}
    already = {
        (violation.rule, violation.detail)
        for violation in gate.check(baseline, gate.normalize(control), split=SPLIT)
    }
    rounds = []
    hole = 0
    for dose in range(1, max_dose + 1):
        slipped: list[list[str]] = []
        rules: dict[str, int] = {}
        details: dict[str, str] = {}
        trials = subsets(case_ids, categories, dose, limit)
        for combo in trials:
            shaped = inject(control, cases, set(combo), top_k)
            found = [
                violation
                for violation in gate.check(baseline, gate.normalize(shaped), split=SPLIT)
                if (violation.rule, violation.detail) not in already
            ]
            for violation in found:
                rules[violation.rule] = rules.get(violation.rule, 0) + 1
                details.setdefault(violation.rule, violation.detail)
            if not found:
                slipped.append(list(combo))
        rounds.append({
            "dose": dose,
            "subsets_tried": len(trials),
            "subsets_the_gate_did_not_newly_flag": len(slipped),
            "examples_that_slipped": slipped[:5],
            # **용량이 아니라 총 실패 건수가 규칙을 움직입니다.** 대조군에 이미 실패한
            # 사례가 있으면 그것을 다시 심어도 총계가 늘지 않습니다 - 2026-10-23에
            # `coauthor-03`(문서에 적힌 변덕스러운 사례)이 그래서 빠져나갔습니다.
            "total_failures_when_slipped": sorted({
                failures_after(control, set(combo)) for combo in slipped
            }),
            "new_rules_raised": dict(sorted(rules.items())),
            "one_detail_per_rule": details,
        })
        if slipped:
            hole = max(
                hole,
                max(failures_after(control, set(combo)) for combo in slipped),
            )
    return {"rounds": rounds, "largest_total_failures_that_slipped": hole}


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="심판 붕괴를 심어 게이트의 탐지 용량을 잽니다(ADR-060)."
    )
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--control", type=Path, default=CONTROL)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--max-dose", type=int, default=MAX_DOSE)
    parser.add_argument("--subset-limit", type=int, default=SUBSET_LIMIT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    control = json.loads(args.control.read_text(encoding="utf-8"))
    cases = {case["id"]: case for case in run.load_cases(args.cases)}
    top_k = (control.get("metadata") or {}).get("top_k") or control.get("top_k") or 5

    missing = sorted({row["id"] for row in control["results"]} - set(cases))
    if missing:
        raise SystemExit(f"대조군의 사례가 케이스 파일에 없습니다: {missing}")

    # **계측기가 성립하는지 먼저 봅니다.** 대조군이 통과하지 않으면 그 뒤의 모든 용량이
    # 뜻을 잃습니다. 통과하지 않는 이유를 그대로 적고 멈추지는 않습니다 - 어느 규칙이
    # 상수로 켜져 있는지가 결과를 읽는 데 필요합니다.
    control_violations = gate.check(baseline, gate.normalize(control), split=SPLIT)
    constant = sorted({violation.rule for violation in control_violations})

    found = measure(baseline, control, cases, top_k=top_k,
                    max_dose=args.max_dose, limit=args.subset_limit)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "baseline": str(args.baseline),
        "control": str(args.control),
        "split": SPLIT,
        "control_violations": [
            {"rule": violation.rule, "detail": violation.detail}
            for violation in control_violations
        ],
        "rules_already_on_in_the_control": constant,
        "note": (
            "대조군에서 이미 켜져 있는 규칙은 결함을 심어도 켜져 있습니다. 그 규칙이 잡은 "
            "것은 결함이 아니므로, 구멍은 **그 규칙을 뺀** 나머지로 읽어야 합니다."
        ),
        "control_failures": failures_after(control, set()),
        # 구멍의 크기를 **총 실패 건수**로 적습니다. 그 아래면 게이트가 새 위반을 올리지
        # 않습니다.
        "largest_total_failures_that_slipped": found["largest_total_failures_that_slipped"],
        "rounds": found["rounds"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"대조군에서 이미 켜진 규칙: {constant or '없음'}")
    for entry in found["rounds"]:
        print(f"  용량 {entry['dose']}: 부분집합 {entry['subsets_tried']}개 중 "
              f"{entry['subsets_the_gate_did_not_newly_flag']}개가 빠져나감 | "
              f"새 규칙 {list(entry['new_rules_raised'])}")
    print(f"대조군의 실패: {failures_after(control, set())}건")
    print("게이트가 새 위반을 올리지 않는 최대 총 실패: "
          f"{found['largest_total_failures_that_slipped']}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
