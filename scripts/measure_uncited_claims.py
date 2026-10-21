"""인용 없이 주장하는 답변이 몇 건인지 셉니다 — 검사를 붙이기 전에.

    uv run python -m scripts.measure_uncited_claims

2026-10-21에 `coauthor-01`의 답변이 이랬습니다.

> "The collection lists four other papers sharing the author Han Lu with …"

**네 편이 있다고 말하면서 한 편도 인용하지 않았고**(`evidence_ids == []`) 보류되지 않았습니다.
[ADR-019](../docs/adr/ADR-019-validate-evidence-alias-at-the-tool.md)의 검증은 **인용된** ID가
근거에 있는지를 봅니다 — 아무것도 인용하지 않은 주장은 막지 않습니다. 읽는 사람이 확인할 수
없는 답변인데 통과합니다.

[ADR-055](../docs/adr/ADR-055-the-stable-case-rule-is-not-resolved.md)가 이것을 "별 결정"으로
남기며 **"30건 중 몇 건이 그런 모양인지 세는 것이 먼저"** 라고 적었습니다. 이 스크립트가 그
셈입니다. API를 쓰지 않습니다 — 커밋된 보고서를 읽습니다.

## 세려는 것과 셀 수 있는 것이 다릅니다

**`evidence_ids == []`를 결함으로 세면 틀립니다.** 답변이 비어 있을 수 있는 두 이유가
전혀 다릅니다.

| 답변 | 인용 | 판정 |
|---|---|---|
| "I cannot confirm any paper that…" | 없음 | **정직한 무주장** — 결함이 아닙니다 |
| "The collection lists four other papers…" | 없음 | **인용 없는 주장** — 결함입니다 |

그래서 이 스크립트는 둘을 **나눠** 셉니다. 나누는 방법은 **부정 표현 목록**이고, 그것은
언어에 매달린 어림입니다 — 어림이라고 적습니다. 목록이 놓치면 **결함으로 세는 쪽으로**
틀립니다(거절을 주장으로 봄). 과소보고보다 과대보고가 안전한 방향입니다.

## 지문 없는 실행은 버립니다

ADR-018 이전 보고서는 `prompt_versions`가 없어 **어느 코드 상태인지 말할 수 없습니다.**
2026-10-15의 수정 전 실행이 그런 경우이고, ADR-055가 그것을 섞어 판정이 한 번 뒤집혔습니다.
같은 선을 여기서도 지킵니다 — 다만 **버린 것도 따로 적습니다**, 거기 있던 것이 이 질문의
답에 쓸모가 있기 때문입니다.
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTPUT = Path("reports/uncited_claims.json")

# 답변이 "확인할 수 없다"고 말하는 표현들. 이것이 있으면 **무주장**으로 봅니다.
# 목록이 놓치면 거절을 주장으로 세므로 **과대보고** 쪽으로 틀립니다 - 안전한 방향입니다.
REFUSAL_MARKERS = (
    "cannot confirm",
    "could not confirm",
    "can not confirm",
    "could be confirmed",
    "cannot be confirmed",
    "no papers",
    "not provide",
    "no information",
    "확인할 수 없",
    "확인되지 않",
    "찾을 수 없",
)


def is_refusal(answer: str) -> bool:
    """답변이 "확인할 수 없다"고 말하는가. **어림이고, 어림이라고 적습니다.**"""
    low = answer.lower()
    return any(marker.lower() in low for marker in REFUSAL_MARKERS)


def runs(directory: Path) -> list[dict[str, Any]]:
    """`evals.run`이 만든 보고서만. `mode`가 없으면 파생물입니다(ADR-055의 장부와 같은 선)."""
    found = []
    for path in sorted(directory.glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not report.get("mode") or not isinstance(report.get("results"), list):
            continue
        rows = [row.get("graph_rag") or row for row in report["results"]]
        if not rows or "evidence_ids" not in rows[0]:
            continue
        metadata = report.get("metadata") or {}
        found.append({
            "path": path.name,
            "fingerprinted": bool(metadata.get("prompt_versions")),
            "generated_at": (report.get("generated_at") or "")[:19],
            "mode": report["mode"],
            "rows": rows,
        })
    return found


def inspect(run: dict[str, Any]) -> dict[str, Any]:
    """한 실행에서 인용 없는 답변을 거절과 주장으로 나눕니다."""
    refusals, claims, answered = [], [], 0
    for row in run["rows"]:
        if row.get("failed"):
            continue
        answer = (row.get("answer") or "").strip()
        if not answer:
            continue
        answered += 1
        if row.get("evidence_ids"):
            continue
        shaped = {"case": row.get("id"), "answer": answer[:160]}
        (refusals if is_refusal(answer) else claims).append(shaped)
    return {
        "path": run["path"],
        "generated_at": run["generated_at"],
        "mode": run["mode"],
        "answered": answered,
        "refusals": refusals,
        "uncited_claims": claims,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="인용 없이 주장하는 답변을 셉니다(검사를 붙이기 전에)."
    )
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    # 한 번만 읽습니다. 세 번 부르면 같은 디스크를 세 번 긁고, 그 사이 바뀌면 묶음이 갈라집니다.
    found = runs(args.reports)
    kept = [inspect(run) for run in found if run["fingerprinted"]]
    dropped = [inspect(run) for run in found if not run["fingerprinted"]]

    def totals(group: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "runs": len(group),
            "answered": sum(g["answered"] for g in group),
            "refusals": sum(len(g["refusals"]) for g in group),
            "uncited_claims": sum(len(g["uncited_claims"]) for g in group),
        }

    kept_totals, dropped_totals = totals(kept), totals(dropped)
    families = collections.Counter(
        item["case"].rsplit("-", 1)[0]
        for group in (kept, dropped) for g in group for item in g["uncited_claims"]
    )
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "fingerprinted": kept_totals,
        "dropped_no_fingerprint": dropped_totals,
        "families_of_claims": dict(families),
        # 사례 id만 적습니다. 답변 본문은 화면에만 보이고 보고서에 넣지 않습니다 -
        # 원본 보고서에 이미 있으므로 두 벌로 둘 이유가 없습니다.
        "claim_cases": sorted({
            item["case"] for group in (kept, dropped)
            for g in group for item in g["uncited_claims"]
        }),
        "refusal_cases": sorted({
            item["case"] for group in (kept, dropped)
            for g in group for item in g["refusals"]
        }),
        "refusal_markers": list(REFUSAL_MARKERS),
        "note": (
            "evidence_ids가 빈 것을 전부 결함으로 세면 틀립니다 - 거절은 정직한 무주장입니다. "
            "둘을 나누는 방법은 부정 표현 목록이고 언어에 매달린 어림입니다. 목록이 놓치면 "
            "거절을 주장으로 세므로 과대보고 쪽으로 틀립니다. 지문 없는 실행은 어느 코드 "
            "상태인지 말할 수 없어 따로 셉니다(ADR-055와 같은 선)."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    for label, group, numbers in (
        ("지문 있음", kept, kept_totals),
        ("지문 없음(버림)", dropped, dropped_totals),
    ):
        print(f"\n== {label}: 실행 {numbers['runs']}개, 답변 {numbers['answered']}건")
        print(f"   인용 없음 = 거절 {numbers['refusals']} + **주장 {numbers['uncited_claims']}**")
        for shaped in group:
            for item in shaped["uncited_claims"]:
                print(f"   [주장] {shaped['path']} {item['case']}: {item['answer'][:110]}")
            for item in shaped["refusals"]:
                print(f"   [거절] {shaped['path']} {item['case']}: {item['answer'][:110]}")
    if families:
        print(f"\n주장이 나온 가족: {dict(families)}")
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
