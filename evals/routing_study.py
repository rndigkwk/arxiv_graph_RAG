"""라우팅 전후를 같은 사례에서 비교합니다.

    uv run python -m evals.routing_study \
        --before reports/regression_full_20261016.json reports/regression_full_20261016b.json \
        --after reports/routing_after2.json \
        --families coauthor reference

[ADR-024](../docs/adr/ADR-024-route-by-question-type.md)의 주장이 둘입니다 — 토큰이 줄고,
답변 정확도는 유지된다. 둘 다 **같은 사례**에서 전후를 봐야 성립합니다.

보고서 두 모양을 모두 읽습니다. `compare` 모드는 사례마다 `vector_only`/`graph_rag`로 나뉘고
`agent` 모드는 행이 곧 답변입니다. 전후 비교에 쓰는 것은 양쪽 다 **그래프 에이전트 쪽**입니다.

**모델이 같은지 먼저 확인하고, 다르면 멈춥니다.** 2026-10-17에 판사 모델이 다른 표본을
같은 분포로 읽어 잘못된 결론을 냈습니다. 같은 실수를 숫자가 좋아 보일 때 다시 하지 않도록
비교 자체가 거부하게 합니다.

세는 지표를 나눠 둡니다.

- **정확도**: `answer_evidence_precision`·`answer_evidence_recall`. 답변이 맞았는지입니다.
- **순위**: `hit_at_k`. 이 값은 `retrieved_ids`의 **순서**에 걸려 있어서, 벡터 결과가 앞에
  붙으면 맞은 답도 0이 됩니다(ADR-024의 맥락). 그래서 정확도와 따로 봅니다.
- **비용**: `chat_input_tokens`, `elapsed_seconds`, `search_papers` 호출률.
"""

from __future__ import annotations

import argparse
import json
import statistics as st
from pathlib import Path
from typing import Any

OUTPUT = Path("reports/routing_study.json")
# 정확도와 순위를 섞어 적으면 "좋아졌다"가 어느 쪽인지 알 수 없습니다.
ACCURACY = ("answer_evidence_precision", "answer_evidence_recall")
RANKING = ("hit_at_k", "recall_at_k", "reciprocal_rank")
COST = ("chat_input_tokens", "chat_output_tokens", "elapsed_seconds")


def answers(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the graph-agent rows out of either report shape."""
    rows = []
    for row in report.get("results", []):
        # compare 모드는 한 사례에 두 시스템이 들어 있습니다. 비교에 쓰는 것은 에이전트입니다.
        rows.append(row["graph_rag"] if "graph_rag" in row else row)
    return rows


def called_search_papers(row: dict[str, Any]) -> bool | None:
    """Whether the vector tool was used, or None when the run did not record it.

    `tool_names`가 있으면 그것이 정확합니다. 없으면 임베딩 토큰으로 **추정**합니다 - 예전
    보고서에는 도구 이름이 없고, 그때는 임베딩이 곧 `search_papers`였습니다. 둘 다 없으면
    모른다고 답합니다. 0으로 적으면 "부르지 않았다"로 읽혀 결론이 뒤집힙니다.
    """
    if "tool_names" in row:
        return "search_papers" in (row["tool_names"] or [])
    tokens = row.get("embedding_tokens_estimate")
    return None if tokens is None else tokens > 0


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"cases": len(rows)}
    for key in (*ACCURACY, *RANKING, *COST):
        values = [row[key] for row in rows if row.get(key) is not None]
        out[key] = round(st.mean(values), 4) if values else None
        # 12건에서 한 건이 평균을 끌고 갑니다 - 2차 측정의 `coauthor-03`이 다른 건의 세 배인
        # 36,735 토큰이었습니다. 비용은 중앙값도 같이 적어야 읽는 사람이 속지 않습니다.
        if key in COST:
            out[f"{key}_p50"] = round(st.median(values), 4) if values else None
    flags = [called_search_papers(row) for row in rows]
    known = [flag for flag in flags if flag is not None]
    out["search_papers_rate"] = round(st.mean(known), 4) if known else None
    out["search_papers_measured"] = len(known)
    out["failed"] = sum(1 for row in rows if row.get("failed"))
    return out


def models(reports: list[dict[str, Any]]) -> set[str]:
    return {report.get("metadata", {}).get("model", "") for report in reports}


def load(paths: list[Path], families: tuple[str, ...]) -> list[dict[str, Any]]:
    rows = []
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        for row in answers(report):
            if not families or row.get("category") in families:
                rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="라우팅 전후를 같은 사례에서 비교합니다.")
    parser.add_argument("--before", type=Path, nargs="+", required=True)
    parser.add_argument("--after", type=Path, nargs="+", required=True)
    parser.add_argument("--families", nargs="*", default=[])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    families = tuple(args.families)
    before_reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.before]
    after_reports = [json.loads(p.read_text(encoding="utf-8")) for p in args.after]
    seen = models(before_reports) | models(after_reports)
    if len(seen) != 1:
        print(f"모델이 다릅니다: {sorted(seen)}. 같은 모델끼리만 비교할 수 있습니다.")
        return 1

    before = summarize(load(args.before, families))
    after = summarize(load(args.after, families))
    keys = (*ACCURACY, *RANKING, *COST, *(f"{k}_p50" for k in COST), "search_papers_rate")
    deltas = {
        key: round(after[key] - before[key], 4)
        for key in keys
        if before.get(key) is not None and after.get(key) is not None
    }
    report = {
        "model": seen.pop(),
        "families": list(families),
        "before_files": [str(p).replace("\\", "/") for p in args.before],
        "after_files": [str(p).replace("\\", "/") for p in args.after],
        "before": before,
        "after": after,
        "deltas": deltas,
        "note": (
            "정확도(answer_evidence_*)와 순위(hit_at_k)를 따로 읽어야 합니다. hit_at_k는 "
            "retrieved_ids의 순서에 걸려 있어서 벡터 결과가 앞에 붙으면 맞은 답도 0이 됩니다."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
