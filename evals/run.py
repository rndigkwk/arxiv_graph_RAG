from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

from evals.metrics import score_answer, score_related, score_retrieval, summarize
from evals.telemetry import (
    build_run_metadata,
    count_embedding_tokens,
    estimate_api_cost,
    pricing_from_env,
    summarize_latency,
)


def evaluate_case(
    case: dict[str, Any], response: dict[str, Any], *, mode: str, top_k: int
) -> dict[str, Any]:
    retrieved = [paper["arxiv_id"] for paper in response.get("papers", []) if paper.get("arxiv_id")]
    for row in response.get("rows", []):
        retrieved.extend(row.get("evidence_ids", []))
    related_ids = list(
        dict.fromkeys(
            paper["arxiv_id"] for paper in response.get("related", []) if paper.get("arxiv_id")
        )
    )
    retrieved = list(dict.fromkeys(retrieved))
    row = {"id": case["id"], "category": case["category"], "retrieved_ids": retrieved}
    row.update(score_retrieval(case["expected_ids"], retrieved, k=top_k))
    if mode == "agent":
        evidence = response.get("evidence_ids", [])
        row.update(
            score_answer(
                response.get("answer", ""),
                expected_answer_contains=case.get("expected_answer_contains", []),
                expected_ids=case["expected_ids"],
                evidence_ids=evidence,
            )
        )
        row.update(score_related(related_ids, case.get("expected_related_ids")))
        row["related_ids"] = related_ids
        row["evidence_ids"] = evidence
        row["answer"] = response.get("answer", "")
    return row


def load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value["cases"] if isinstance(value, dict) else value


def _token_totals(messages: Any) -> tuple[int | None, int | None]:
    """Sum provider-reported chat tokens without guessing when metadata is absent."""
    input_tokens = output_tokens = 0
    saw_input = saw_output = False
    if not isinstance(messages, (list, tuple)):
        messages = [messages]
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not usage and isinstance(message, dict):
            usage = message.get("usage_metadata")
        if not usage:
            metadata = getattr(message, "response_metadata", {}) or {}
            usage = metadata.get("token_usage", {}) if isinstance(metadata, dict) else {}
        if isinstance(usage, dict):
            input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
            output_value = usage.get("output_tokens", usage.get("completion_tokens"))
            if input_value is not None:
                input_tokens += int(input_value)
                saw_input = True
            if output_value is not None:
                output_tokens += int(output_value)
                saw_output = True
    return input_tokens if saw_input else None, output_tokens if saw_output else None


def _answer_from_vector_context(chatbot: Any, case: dict[str, Any], papers: list[dict[str, Any]]):
    context = "\n\n".join(
        f"arXiv ID: {paper.get('arxiv_id', '')}\nTitle: {paper.get('title', '')}\n"
        f"Abstract: {paper.get('abstract', '')}"
        for paper in papers
    )
    prompt = (
        "Answer the question using only the retrieved papers. Return a concise English answer. "
        "Include only arXiv IDs from the context in evidence_ids; if none support the answer, "
        "say it cannot be confirmed and use an empty evidence_ids list.\n\n"
        f"Question: {case['question']}\n\nRetrieved papers:\n{context}"
    )
    return chatbot.llm.with_structured_output(chatbot.GroundedAnswer, include_raw=True).invoke(
        [("user", prompt)]
    )


def compare_case(
    chatbot: Any,
    case: dict[str, Any],
    *,
    top_k: int,
    rates: dict[str, float | str] | None = None,
) -> dict[str, Any]:
    """Run a vector-context answer baseline and the full graph agent for one case."""
    start = perf_counter()
    retrieval = chatbot.search_papers.invoke({"query": case["search_query"], "top_k": top_k})
    vector_response = _answer_from_vector_context(chatbot, case, retrieval.get("papers", []))
    vector_seconds = perf_counter() - start
    vector_answer = vector_response.get("parsed")
    if vector_answer is None:
        raise ValueError("Vector-only baseline did not return a structured answer")
    vector_row = evaluate_case(
        case,
        {
            "papers": retrieval.get("papers", []),
            "answer": vector_answer.answer,
            "evidence_ids": vector_answer.evidence_ids,
        },
        mode="agent",
        top_k=top_k,
    )
    vector_raw = vector_response.get("raw")
    vector_input, vector_output = _token_totals(vector_raw)
    embedding_model = getattr(chatbot, "EMBEDDING_MODEL", "text-embedding-3-large")
    vector_embedding_tokens = count_embedding_tokens([case["search_query"]], embedding_model)
    vector_cost = estimate_api_cost(vector_input, vector_output, vector_embedding_tokens, rates)
    vector_row.update(
        {
            "elapsed_seconds": vector_seconds,
            "chat_input_tokens": vector_input,
            "chat_output_tokens": vector_output,
            "embedding_tokens_estimate": vector_embedding_tokens,
            "estimated_api_cost_usd": vector_cost,
        }
    )

    start = perf_counter()
    graph_response, graph_messages = chatbot.ask(case["question"], [])
    graph_seconds = perf_counter() - start
    graph_row = evaluate_case(case, graph_response, mode="agent", top_k=top_k)
    graph_input, graph_output = _token_totals(graph_messages)
    graph_queries = [
        call.get("args", {}).get("query", "")
        for call in graph_response.get("tool_calls", [])
        if call.get("name") == "search_papers"
        and isinstance(call.get("args"), dict)
        and isinstance(call.get("args", {}).get("query"), str)
    ]
    graph_embedding_tokens = count_embedding_tokens(graph_queries, embedding_model)
    graph_cost = estimate_api_cost(graph_input, graph_output, graph_embedding_tokens, rates)
    graph_row.update(
        {
            "elapsed_seconds": graph_seconds,
            "chat_input_tokens": graph_input,
            "chat_output_tokens": graph_output,
            "embedding_tokens_estimate": graph_embedding_tokens,
            "estimated_api_cost_usd": graph_cost,
        }
    )
    return {
        "id": case["id"],
        "category": case["category"],
        "vector_only": vector_row,
        "graph_rag": graph_row,
    }


def summarize_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def summarize_rows(comparison_rows: list[dict[str, Any]]) -> dict[str, Any]:
        result_summary: dict[str, Any] = {}
        for name in ("vector_only", "graph_rag"):
            results = [row[name] for row in comparison_rows]
            if not results:
                continue
            metrics = [
                key
                for key in results[0]
                if key not in {"id", "category", "retrieved_ids", "evidence_ids", "answer"}
            ]
            result_summary[name] = {
                key: fmean(values)
                for key in metrics
                if (
                    values := [
                        result[key]
                        for result in results
                        if isinstance(result.get(key), (int, float))
                    ]
                )
            }
            input_values = [result.get("chat_input_tokens") for result in results]
            output_values = [result.get("chat_output_tokens") for result in results]
            result_summary[name]["chat_tokens_total"] = (
                sum(input_values) + sum(output_values)
                if all(value is not None for value in (*input_values, *output_values))
                else None
            )
            embedding_values = [result.get("embedding_tokens_estimate") for result in results]
            result_summary[name]["embedding_tokens_total_estimate"] = (
                sum(embedding_values)
                if all(value is not None for value in embedding_values)
                else None
            )
            result_summary[name]["latency_seconds"] = summarize_latency(
                [
                    result["elapsed_seconds"]
                    for result in results
                    if result.get("elapsed_seconds") is not None
                ]
            )
            cost_values = [result.get("estimated_api_cost_usd") for result in results]
            result_summary[name]["estimated_api_cost_usd_total"] = (
                sum(cost_values) if all(value is not None for value in cost_values) else None
            )
        return result_summary

    summary = {"case_count": len(rows), **summarize_rows(rows)}
    summary["by_category"] = {
        category: summarize_rows([row for row in rows if row["category"] == category])
        for category in sorted({row["category"] for row in rows})
    }
    costs = [
        summary.get(name, {}).get("estimated_api_cost_usd_total")
        for name in ("vector_only", "graph_rag")
    ]
    summary["cost_usd"] = (
        sum(costs) if costs and all(value is not None for value in costs) else None
    )
    summary["cost_note"] = (
        "Includes configured chat and estimated embedding API charges only; excludes Neo4j costs. "
        "Null means usage or sourced pricing is unavailable."
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a small arXiv GraphRAG retrieval/answer evaluation."
    )
    parser.add_argument("--mode", choices=("retrieval", "agent", "compare"), default="retrieval")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--case-id", help="Run one named case, for example title-01.")
    parser.add_argument("--output", type=Path, default=Path("reports/rag_eval.json"))
    args = parser.parse_args()
    if args.limit < 1 or args.top_k < 1:
        parser.error("--limit and --top-k must be positive")
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [case for case in cases if case["id"] == args.case_id]
    cases = cases[: args.limit]
    if not cases:
        parser.error("no evaluation cases found")
    import chatbot

    rates = pricing_from_env(os.environ)

    rows = []
    for case in cases:
        if args.mode == "compare":
            rows.append(compare_case(chatbot, case, top_k=args.top_k, rates=rates))
            continue
        if args.mode == "retrieval":
            response = chatbot.search_papers.invoke(
                {"query": case["search_query"], "top_k": args.top_k}
            )
        else:
            response, _ = chatbot.ask(case["question"], [])
        rows.append(evaluate_case(case, response, mode=args.mode, top_k=args.top_k))
    metadata = build_run_metadata(
        cases_path=args.cases,
        case_ids=[case["id"] for case in cases],
        mode=args.mode,
        top_k=args.top_k,
        model=os.environ.get("OPENAI_MODEL", "gpt-5.6-luna"),
        embedding_model=getattr(chatbot, "EMBEDDING_MODEL", "text-embedding-3-large"),
    )
    report = {
        "schema_version": 2,
        "generated_at": metadata["timestamp_utc"],
        "mode": args.mode,
        "top_k": args.top_k,
        "dataset": args.cases.name,
        "metadata": metadata,
        "pricing": rates,
        "results": rows,
        "summary": summarize_comparison(rows) if args.mode == "compare" else summarize(rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
