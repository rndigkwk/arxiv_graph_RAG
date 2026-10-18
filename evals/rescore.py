"""이미 받아 둔 보고서를 **다시 돌리지 않고** 재채점합니다.

    uv run python -m evals.rescore --report reports/baseline_routed_run1.json

[ADR-024](../docs/adr/ADR-024-route-by-question-type.md)의 세 결함은 뿌리가 하나였습니다 —
제목을 arxiv_id로 바꾸는 **중간 조회**의 행이 답변 근거와 섞여 세어졌습니다. 그 분리는 앱이
아니라 **평가 쪽** 변경입니다(`evals/run.py`의 `anchor_ids`·`is_lookup`).

그래서 다시 돌리면 안 됩니다. 모델이 비결정적이라 재실행하면 답변 자체가 달라지고, 그러면
"지표를 고쳤더니 무엇이 변했는가"를 분리할 수 없습니다. **같은 답변에 새 채점을 적용**합니다.

할 수 있는 것과 못 하는 것이 나뉩니다.

- **검색 지표**(`hit_at_k`·`recall_at_k`·`reciprocal_rank`)는 저장된 `retrieved_ids`에서 기준
  논문을 빼고 다시 셉니다. 순수한 집합·순서 연산이라 정확히 재현됩니다.
- **근거 지표**(`answer_evidence_*`)는 **건드리지 않습니다.** 질문이 지칭한 논문을 답변이
  인용한 것은 측정 결함이 아니라 **답변 결함**이고, 지표가 그것을 잡은 것이 맞습니다.
- **RAGAS 문맥은 건드리지 않습니다.** 2026-10-18에 제목 해석 문맥을 빼 봤더니 Context
  Precision은 `citing` 0.5046 -> 1.0000으로 회복됐지만 **faithfulness가 1.0000 -> 0.2296으로
  무너졌습니다.** 답변이 주제 논문을 제목으로 언급하고 그 주장의 근거가 바로 그 조회라서,
  순수한 중간 단계가 아니었습니다. 두 지표가 충돌할 때는 답변 정확도에 묶인 faithfulness를
  따릅니다. `context_is_lookup`은 그 측정을 재현할 때만 남겨 둡니다.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from evals.metrics import score_retrieval, summarize
from evals.run import anchor_ids, load_cases

# 저장된 문맥에서 행을 찾기 위한 것입니다. 문맥은 "Cypher: ..." 다음에 `key=value | ...` 줄이
# 붙은 형태로 굳어 있습니다(`generator_contexts`).
EVIDENCE_IN_CONTEXT = re.compile(r"evidence_ids=\[([^\]]*)\]")
QUOTED = re.compile(r"['\"]([^'\"]+)['\"]")


def context_is_lookup(context: str, anchors: set[str]) -> bool:
    """Whether a stored context is only the question's own paper being resolved.

    원본 행이 아니라 **굳어 버린 문자열**을 봅니다. 재실행하지 않고 과거 보고서를 재채점하는
    유일한 길이어서 그렇습니다. `evals/run.py`의 `is_lookup`과 판정 기준은 같습니다 — 그
    문맥에 적힌 `evidence_ids`가 전부 기준 논문일 때만 해석 조회입니다.
    """
    if not anchors or not context.startswith("Cypher:"):
        return False
    found: set[str] = set()
    for match in EVIDENCE_IN_CONTEXT.finditer(context):
        found.update(QUOTED.findall(match.group(1)))
    return bool(found) and found <= anchors


def rescore_row(row: dict[str, Any], case: dict[str, Any], top_k: int) -> dict[str, Any]:
    anchors = anchor_ids(case)
    kept = [arxiv_id for arxiv_id in row.get("retrieved_ids", []) if arxiv_id not in anchors]
    updated = dict(row)
    updated["retrieved_ids"] = kept
    if anchors:
        updated["anchor_ids"] = sorted(anchors)
    updated.update(score_retrieval(case["expected_ids"], kept, k=top_k))
    # **문맥은 건드리지 않습니다.** 한 번 걸러 봤고 faithfulness가 무너졌습니다
    # (`citing` 1.0000 -> 0.2296) - 답변이 주제 논문을 제목으로 언급하고 그 근거가 바로
    # 제목 해석 조회입니다. `context_is_lookup`은 그 측정을 재현할 때만 씁니다.
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description="저장된 보고서를 재채점합니다(재실행 없음).")
    parser.add_argument("--report", type=Path, nargs="+", required=True)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases_graph.json"))
    parser.add_argument("--suffix", default="_rescored")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    cases = {case["id"]: case for case in load_cases(args.cases)}

    for path in args.report:
        report = json.loads(path.read_text(encoding="utf-8"))
        top_k = report.get("top_k", 5)
        moved = 0
        for result in report.get("results", []):
            case = cases.get(result["id"])
            if case is None:
                continue
            for side in ("graph_rag", "vector_only"):
                if side not in result:
                    continue
                before = result[side].get("reciprocal_rank")
                result[side] = rescore_row(result[side], case, top_k)
                if result[side].get("reciprocal_rank") != before:
                    moved += 1
            if "graph_rag" not in result and "vector_only" not in result:
                before = result.get("reciprocal_rank")
                result.update(rescore_row(result, case, top_k))
                if result.get("reciprocal_rank") != before:
                    moved += 1

        # 요약을 다시 계산합니다. 안 하면 보고서의 집계가 행과 어긋납니다.
        rows = [r.get("graph_rag", r) for r in report.get("results", [])]
        if "graph_rag" in (report.get("summary") or {}):
            report["summary"]["graph_rag"] = summarize(rows)
            report["summary"]["vector_only"] = summarize(
                [r["vector_only"] for r in report["results"] if "vector_only" in r]
            )
        else:
            report["summary"] = summarize(rows)
        report["rescored"] = {
            "reason": (
                "기준 논문(질문이 지칭한 논문)을 retrieved_ids에서만 분리(ADR-024). "
                "문맥은 그대로 - 빼니 faithfulness가 무너졌습니다"
            ),
            "rows_whose_rank_changed": moved,
            "answer_metrics_untouched": True,
            "contexts_untouched": True,
        }
        out = path.with_name(path.stem + args.suffix + path.suffix)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{path.name}: 순위가 바뀐 행 {moved}건 -> {out.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
