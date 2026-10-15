"""색인에 LLM을 쓰는 GraphRAG 변형의 비용 하한을 스냅샷에서 계산합니다.

    uv run --group dev python -m evals.index_cost
    uv run --group dev python -m evals.index_cost --subset-size 500

Day 57 비교 틀의 축 중 LightRAG와 Microsoft GraphRAG는 **문서마다 LLM을 호출해 그래프를
만듭니다.** 이 프로젝트는 그래프가 데이터에 이미 있어서 색인 LLM 비용이 0입니다. 그 차이를
말로 적는 대신 수치로 적습니다.

여기서 내는 값은 **하한**입니다. 근거를 분명히 해 둡니다.

- 셉니다: 제목+초록을 토크나이저로 센 입력 토큰, 추출 **1회 통과** 기준.
- 세지 않습니다: 프레임워크의 프롬프트 템플릿 길이, 출력 토큰, 청킹으로 늘어나는 중복,
  재시도, 그리고 Microsoft GraphRAG가 추가로 돌리는 커뮤니티 요약 통과.

따라서 실제 비용은 이 값보다 **크다**고만 말할 수 있습니다. 두 프레임워크의 내부 통과 수를
추정해 곱하지 않습니다 — 측정하지 않은 수를 곱하면 숫자가 아니라 주장이 됩니다.

요율은 `.env`의 `EVAL_CHAT_INPUT_USD_PER_1M` 등에서 읽습니다. 출처가 없으면 금액을 비웁니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.telemetry import pricing_from_env
from pipeline import snapshot as snapshot_module

OUTPUT = Path("reports/index_cost.json")
CASE_FILES = (Path("evals/cases_graph.json"), Path("evals/cases.json"))
SUBSET_SIZES = (500, 2000)
SUBSET_CATEGORY = "cs.AI"
SUBSET_SIZE = 500
EMBEDDING_MODEL = "text-embedding-3-large"


def document_tokens(snap: Any, model: str) -> dict[str, Any]:
    """Count title+abstract tokens per paper, for the whole corpus and one category subset."""
    import tiktoken

    encoding = tiktoken.encoding_for_model(model)
    totals: collections.Counter[str] = collections.Counter()
    subset: list[tuple[str, int]] = []
    papers = 0
    for row in snap.rows("papers"):
        text = f"{row.get('title') or ''}\n{row.get('abstract') or ''}"
        tokens = len(encoding.encode(text))
        papers += 1
        totals["all"] += tokens
        if row.get("primary_category") == SUBSET_CATEGORY:
            subset.append((row["arxiv_id"], tokens))
    subset.sort()
    return {
        "papers": papers,
        "tokens_all": totals["all"],
        "subset_category": SUBSET_CATEGORY,
        "subset_available": len(subset),
        "subset_tokens_by_id": subset,
    }


def gold_coverage(subset: set[str], case_files: tuple[Path, ...]) -> dict[str, Any]:
    """How much of each evaluation set a partial index could even reach.

    This decides whether a partial index can be compared at all. A subset that holds none of
    the gold papers scores zero for a reason that has nothing to do with the retrieval method,
    so the comparison would measure coverage and call it quality.
    """
    coverage = {}
    for path in case_files:
        if not path.exists():
            continue
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        cases = value["cases"] if isinstance(value, dict) else value
        gold = [paper_id for case in cases for paper_id in case["expected_ids"]]
        complete = sum(
            1 for case in cases if all(paper_id in subset for paper_id in case["expected_ids"])
        )
        inside = sum(1 for paper_id in gold if paper_id in subset)
        coverage[path.name] = {
            "cases": len(cases),
            "gold_ids": len(gold),
            "gold_ids_inside": inside,
            "gold_share_inside": round(inside / len(gold), 4) if gold else None,
            "cases_fully_covered": complete,
        }
    return coverage


def scope(name: str, papers: int, tokens: int, rates: dict[str, Any] | None) -> dict[str, Any]:
    """Price one indexing scope at the configured input rate, output tokens excluded."""
    row: dict[str, Any] = {
        "scope": name,
        "papers": papers,
        "input_tokens_one_pass": tokens,
        "mean_tokens_per_paper": round(tokens / papers, 1) if papers else 0.0,
    }
    rate = (rates or {}).get("chat_input_usd_per_1m")
    row["usd_one_pass_input_only"] = (
        round(tokens / 1_000_000 * float(rate), 4) if isinstance(rate, (int, float)) else None
    )
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="LLM 색인 비용의 하한을 계산합니다.")
    parser.add_argument("--version")
    parser.add_argument("--subset-size", type=int, default=SUBSET_SIZE)
    parser.add_argument("--model", default=EMBEDDING_MODEL,
                        help="토큰을 세는 데만 쓰는 토크나이저 이름입니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    rates = pricing_from_env(os.environ)
    counted = document_tokens(snap, args.model)
    subset = counted["subset_tokens_by_id"][: max(0, args.subset_size)]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "tokenizer": args.model,
        "pricing": rates,
        "counts_input_tokens_only": True,
        "note": (
            "추출 1회 통과의 입력 토큰만 센 하한입니다. 프롬프트 템플릿·출력 토큰·청킹 중복·"
            "재시도·커뮤니티 요약 통과는 포함하지 않았습니다."
        ),
        "scopes": [
            scope("this project (graph already in the data)", counted["papers"], 0, rates),
            scope(
                f"{SUBSET_CATEGORY} subset ({len(subset)} papers)",
                len(subset),
                sum(tokens for _, tokens in subset),
                rates,
            ),
            scope("whole corpus", counted["papers"], counted["tokens_all"], rates),
        ],
        "subset_available": counted["subset_available"],
        "partial_index_gold_coverage": {
            str(size): gold_coverage(
                {arxiv_id for arxiv_id, _ in counted["subset_tokens_by_id"][:size]},
                CASE_FILES,
            )
            for size in SUBSET_SIZES
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {"scopes": report["scopes"], "pricing": rates,
         "subset_available": report["subset_available"],
         "partial_index_gold_coverage": report["partial_index_gold_coverage"]},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
