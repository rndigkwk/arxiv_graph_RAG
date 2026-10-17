"""RAGAS 표준 지표로 답변과 문맥의 품질을 재고, 고장 위치를 진단합니다.

    uv run --group dev python -m evals.ragas_study --limit 6 \
        --report reports/rag_eval_graph_compare_final.json

Day 59 교안의 지표입니다. 현재 `answer_correct`는 **문구 포함 검사**라서 완전성도 사실성도
판정하지 못합니다. 표준 지표로 바꾸면 "검색이 문제인가 생성이 문제인가"를 나눠 말할 수 있습니다.

**여섯 개 중 네 개만 씁니다.** 나머지 둘은 못 하는 것이 아니라 **쓰면 더 나빠지는 쪽**입니다.

- `Faithfulness` ✅ 답변의 주장이 문맥에 있는지. 질문·답변·문맥이 다 있습니다.
- `AnswerRelevancy` ✅ 답변이 질문에 맞는지. 정답 답변이 필요 없습니다.
- `ContextPrecisionWithoutReference` ✅ 쓸모 있는 문맥이 위에 있는지.
- `ContextRelevance` ✅ 가져온 문맥이 질문과 관련 있는지.
- `ContextRecall` ❌ 정답 **답변**을 주장으로 쪼개 세는 지표입니다. 우리에겐 정답 **ID**가
  있어서 `recall_at_k`로 **정확히** 셉니다. LLM 추정으로 바꾸면 더 나쁜 측정이 됩니다.
- `AnswerCorrectness` ❌ 정답 답변이 필요합니다. 우리 골드는 ID 목록이고, 답변 원문을 쓰는
  일은 사람이 해야 합니다(ROADMAP 3c).

**문맥은 보고서에 적힌 것을 그대로 씁니다.** ID로 초록을 되찾아 문맥이라고 넘기면 안 됩니다 —
그래프 조건 질문에서 모델이 본 것은 초록이 아니라 Cypher가 돌려준 행입니다. 그래서
`evals.run`이 `contexts`를 보고서에 남기고, 이 스크립트는 그것만 읽습니다. `contexts`가 없는
옛 보고서는 **건너뛰고 그 사실을 적습니다.** 되찾은 초록으로 점수를 내면 모델이 아니라 우리
입력 실수를 재게 됩니다.

판정 모델은 보고서에 적습니다. 교안 02가 "판사 모델에 따라 RAGAS 점수가 달라진다"를 지적하므로,
모델 이름 없는 점수는 비교할 수 없습니다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from typing import Any

OUTPUT = Path("reports/ragas_study.json")
DEFAULT_MODEL = "gpt-4o-mini"
EMBEDDING_MODEL = "text-embedding-3-large"
# 검색 쪽 지표와 생성 쪽 지표를 나눠 읽기 위한 분류입니다. 고장 위치 진단의 축입니다.
RETRIEVAL_METRICS = ("context_precision", "context_relevance")
GENERATION_METRICS = ("faithfulness", "answer_relevancy")


def samples(report: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Pull the (question, answer, contexts) triples a RAGAS metric needs.

    `--mode compare` 보고서는 사례를 `graph_rag`와 `vector_only`로 나눠 담습니다. 제품 경로는
    `graph_rag`입니다. 멈춘 사례는 답변이 없으므로 채점 대상이 아니고, 건너뛴 이유를 함께
    돌려줍니다 — 조용히 빠지면 평균이 무엇의 평균인지 알 수 없습니다.
    """
    rows, skipped = [], []
    cases = {case["id"]: case for case in report.get("cases", [])}
    for result in report.get("results", []):
        row = result.get("graph_rag", result)
        case_id = result.get("id") or row.get("id")
        if row.get("failed"):
            skipped.append(f"{case_id}: 답변을 만들지 못했습니다")
            continue
        answer = (row.get("answer") or "").strip()
        contexts = [text for text in row.get("contexts") or [] if text.strip()]
        if not answer:
            skipped.append(f"{case_id}: 답변이 비어 있습니다")
            continue
        if not contexts:
            skipped.append(f"{case_id}: contexts가 없습니다(옛 보고서)")
            continue
        question = (
            result.get("question")
            or row.get("question")
            or cases.get(case_id, {}).get("question", "")
        )
        if not question:
            skipped.append(f"{case_id}: 질문 문장이 보고서에 없습니다")
            continue
        rows.append({
            "id": case_id,
            "family": result.get("category") or row.get("category", ""),
            "question": question,
            "answer": answer,
            "contexts": contexts,
            # 우리 골드 ID로 이미 정확히 센 검색 지표입니다. 진단 표에서 나란히 읽습니다.
            "recall_at_k": row.get("recall_at_k"),
            "hit_at_k": row.get("hit_at_k"),
            "answer_evidence_recall": row.get("answer_evidence_recall"),
        })
    return rows, skipped


def build_metrics(model: str) -> dict[str, Any]:
    """Create the four metrics with our own judge model.

    ragas 0.4는 클라이언트를 직접 받습니다("Text-only mode has been removed"). 그래서 키를
    읽는 책임이 우리에게 있고, `load_env()`가 먼저 돌아야 합니다.

    **비동기 클라이언트여야 합니다.** 이 지표들의 `ascore()`는 내부에서 `agenerate()`를 부르고,
    동기 클라이언트를 주면 네 지표가 전부 `TypeError`로 죽습니다.
    """
    from openai import AsyncOpenAI
    from ragas.embeddings.base import embedding_factory
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecisionWithoutReference,
        ContextRelevance,
        Faithfulness,
    )

    client = AsyncOpenAI()
    llm = llm_factory(model, client=client)
    embeddings = embedding_factory(provider="openai", model=EMBEDDING_MODEL, client=client)
    return {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": ContextPrecisionWithoutReference(llm=llm),
        "context_relevance": ContextRelevance(llm=llm),
    }


async def score_one(metrics: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    """Score one case, recording an error per metric instead of losing the whole run."""
    scores: dict[str, Any] = {}
    for name, metric in metrics.items():
        try:
            if name == "answer_relevancy":
                result = await metric.ascore(user_input=row["question"], response=row["answer"])
            elif name == "context_relevance":
                result = await metric.ascore(
                    user_input=row["question"], retrieved_contexts=row["contexts"]
                )
            else:
                result = await metric.ascore(
                    user_input=row["question"],
                    response=row["answer"],
                    retrieved_contexts=row["contexts"],
                )
            value = float(result.value)
            # NaN은 JSON 표준이 아니고 평균에 섞이면 조용히 전체를 NaN으로 만듭니다.
            scores[name] = None if math.isnan(value) else value
            if scores[name] is None:
                scores[f"{name}_error"] = "판정이 NaN을 돌려줬습니다"
        except Exception as exc:  # noqa: BLE001 - 한 지표의 실패가 나머지를 막지 않습니다.
            scores[name] = None
            scores[f"{name}_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return scores


async def run(rows: list[dict[str, Any]], model: str) -> list[dict[str, Any]]:
    metrics = build_metrics(model)
    scored = []
    for index, row in enumerate(rows, start=1):
        scores = await score_one(metrics, row)
        scored.append({**row, **scores})
        done = [name for name in metrics if scores.get(name) is not None]
        print(f"  [{index}/{len(rows)}] {row['id']}: {len(done)}/{len(metrics)} 지표")
    return scored


def mean_of(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), (int, float))]
    return round(fmean(values), 4) if values else None


def summarize(rows: list[dict[str, Any]], skipped: list[str], model: str) -> dict[str, Any]:
    """Report the four metrics, and pair them with our exact retrieval numbers."""
    names = (*GENERATION_METRICS, *RETRIEVAL_METRICS)
    families = sorted({row["family"] for row in rows if row["family"]})
    scored = {name: mean_of(rows, name) for name in names}
    errors = {
        name: sum(1 for row in rows if row.get(f"{name}_error")) for name in names
    }
    return {
        "cases_scored": len(rows),
        "cases_skipped": len(skipped),
        "skipped": skipped,
        "judge_model": model,
        "metrics": scored,
        "metric_errors": {name: count for name, count in errors.items() if count},
        # 교안 02 6-4절의 진단 축입니다. 검색 쪽과 생성 쪽을 나눠 봐야 어디를 고칠지 압니다.
        "diagnosis": {
            "retrieval_side": {name: scored[name] for name in RETRIEVAL_METRICS},
            "generation_side": {name: scored[name] for name in GENERATION_METRICS},
            "exact_retrieval": {
                "recall_at_k": mean_of(rows, "recall_at_k"),
                "hit_at_k": mean_of(rows, "hit_at_k"),
                "answer_evidence_recall": mean_of(rows, "answer_evidence_recall"),
            },
            "reading": (
                "검색 쪽이 낮고 생성 쪽이 높으면 검색기를, 반대면 생성기를 고칩니다. "
                "`exact_retrieval`은 골드 ID로 정확히 센 값이므로 LLM 추정과 어긋나면 "
                "지표가 아니라 판정 모델을 의심합니다."
            ),
        },
        "by_family": {
            family: {
                "cases": sum(1 for row in rows if row["family"] == family),
                **{
                    name: mean_of([row for row in rows if row["family"] == family], name)
                    for name in names
                },
            }
            for family in families
        },
        "not_measured": {
            "context_recall": (
                "정답 답변을 주장으로 쪼개 세는 지표입니다. 우리는 골드 ID로 recall_at_k를 "
                "정확히 세므로 LLM 추정으로 바꾸면 더 나쁜 측정이 됩니다."
            ),
            "answer_correctness": (
                "정답 답변이 필요합니다. 우리 골드는 ID 목록이고 답변 원문 작성은 사람 몫입니다."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RAGAS 지표로 답변·문맥 품질을 측정합니다.")
    parser.add_argument("--report", type=Path, required=True,
                        help="contexts가 담긴 평가 보고서(evals.run --mode compare/agent)")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases_graph.json"),
                        help="질문 문장을 보고서에서 못 찾을 때 참조합니다.")
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부 채점합니다.")
    # 기본값을 여기서 환경변수로 정하면 `load_env()`보다 먼저 평가돼 .env가 무시됩니다.
    parser.add_argument("--model", default="")
    parser.add_argument("--label", default="")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout
    from pipeline.tracking import load_env

    use_utf8_stdout()
    load_env()
    model = args.model or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL

    report = json.loads(args.report.read_text(encoding="utf-8"))
    if args.cases.exists() and "cases" not in report:
        cases = json.loads(args.cases.read_text(encoding="utf-8-sig"))
        report["cases"] = cases["cases"] if isinstance(cases, dict) else cases
    rows, skipped = samples(report)
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print(json.dumps({"cases_scored": 0, "skipped": skipped}, ensure_ascii=False, indent=2))
        return 1

    print(f"{len(rows)}건 채점 (판정 모델 {model})")
    scored = asyncio.run(run(rows, model))
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        "source_report": str(args.report),
        "summary": summarize(scored, skipped, model),
        "results": scored,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(out["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
