"""범위 확인 기준(`pre_scope.DECLINE_BELOW`)을 질문 100개로 잽니다(ADR-095).

    uv run --locked python -m evals.scope_threshold
    uv run --locked python -m evals.scope_threshold --output reports/scope_threshold.json

앱의 `scope_first`와 같은 순서로 봅니다 - 단서 → 저자 이름 → 벡터 1위 점수. 에이전트는 부르지
않습니다. 한국어 질문만 앱의 번역 도구를 한 번 부릅니다(질문당 0.1센트 아래). 제목 해석(ADR-088)이
논문을 찾으면 앱은 이 단계를 건너뛰지만, 여기서는 **건너뛰지 않습니다** - 기준만 따로 봅니다.

`out`은 거절돼야 하고 `in`은 거절되면 안 됩니다. 점수 분포와 기준값별 오거절·놓침을 함께 적어,
기준을 옮길 때 무엇을 잃는지 숫자로 남깁니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src import pre_scope, pre_translate

CASES = Path("evals/cases_scope.json")
OUTPUT = Path("reports/scope_threshold.json")
THRESHOLDS = (0.66, 0.68, 0.70, 0.72, 0.74)


def check(question: str, chatbot: Any) -> dict[str, Any]:
    english = question
    if pre_translate.needs_translation(question):
        output = chatbot.translate_to_english.invoke({"text": question})
        english = output.get("english") or question
    row: dict[str, Any] = {"question": question, "english": english}
    if pre_scope.has_domain_cue(question, english):
        return {**row, "path": "cue", "score": None}
    if names := pre_scope.name_candidates(question, english):
        found = chatbot.run_read("MATCH (a:ArxivAuthorName) WHERE a.name IN $names "
                                 "RETURN a.name AS name LIMIT 1", {"names": names})
        if found:
            return {**row, "path": "author", "score": None}
    result = chatbot.vector_retriever.search(query_text=english, top_k=pre_scope.TOP_K)
    top = result.items[0] if result.items else None
    output = {"papers": [{**top.metadata, "abstract": top.content}]} if top else {"papers": []}
    return {**row, "path": "score", "score": pre_scope.top_score(output),
            "title": (top.metadata.get("title") if top else None),
            "declined": pre_scope.decide(output) == "decline"}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def side(kind: str) -> dict[str, Any]:
        mine = [r for r in rows if r["kind"] == kind]
        scored = [r["score"] for r in mine if r["score"] is not None]
        return {
            "cases": len(mine),
            "by_path": {p: sum(r["path"] == p for r in mine) for p in ("cue", "author", "score")},
            "declined": sum(bool(r.get("declined")) for r in mine),
            "score_min": min(scored, default=None), "score_max": max(scored, default=None),
            "score_median": statistics.median(scored) if scored else None,
        }

    by_threshold = {}
    for t in THRESHOLDS:
        out_scored = [r for r in rows if r["kind"] == "out" and r["score"] is not None]
        in_scored = [r for r in rows if r["kind"] == "in" and r["score"] is not None]
        by_threshold[f"{t:.2f}"] = {
            # 범위 밖인데 거절하지 못함(에이전트로 감) - 단서로 빠진 질문도 놓친 것으로 셉니다.
            "out_missed": sum(1 for r in rows if r["kind"] == "out")
            - sum(r["score"] < t for r in out_scored),
            # 범위 안인데 거절함 - 이쪽이 비쌉니다.
            "in_refused": sum(r["score"] < t for r in in_scored),
        }
    return {"out": side("out"), "in": side("in"), "decline_below": pre_scope.DECLINE_BELOW,
            "by_threshold": by_threshold}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    from src import chatbot  # 접속이 필요한 모듈은 실행할 때만 가져옵니다.

    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    rows = []
    for kind in ("out", "in"):
        for question in cases[kind]:
            row = {"kind": kind, **check(question, chatbot)}
            rows.append(row)
            print(kind, row["path"], row["score"], question, flush=True)
    report = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
              "summary": summarize(rows), "rows": rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
