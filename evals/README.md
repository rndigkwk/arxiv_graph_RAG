# Retrieval and answer evaluation

`cases.json` contains 31 fixed cases tied to the bundled 6,467-paper seed snapshot (2026-06-15 to 2026-09-14). Ten topic cases use a transparent lexical rule (the phrase appears in title or abstract across the bundled seed and reference metadata); twenty-one exact-title cases use the source title and arXiv ID. These are a reproducible smoke benchmark, not expert relevance labels or a claim of general RAG quality.

## Run

```bash
uv sync --locked --group dev
uv run python -m evals.run --mode retrieval --limit 31 --top-k 5
uv run python -m evals.run --mode agent --limit 3 --top-k 5
# Run a single known-title case: add --case-id title-01
```

All modes require `.env` credentials and a reachable Neo4j Aura database with the `arxiv_paper_embedding_index` vector index online. Retrieval mode calls embedding/vector search per case. Agent mode additionally calls the chat model and is intentionally limited by default. The runner writes `reports/rag_eval.json`; set `--output` for another path. Reports use schema version 2. They distinguish direct answer evidence (`answer_evidence_precision/recall`) from PageRank related-paper retrieval (`related_retrieval_precision/recall`). `answer_correct` checks required phrases only, not factual completeness.

Compare results include end-to-end latency mean/p50/p95, provider-reported chat token usage, and estimated embedding input tokens using the configured model tokenizer. Token fields remain null when the provider does not report usage. USD estimates remain null unless all three per-million-token rates and a pricing source are configured in `.env`; set `EVAL_CHAT_INPUT_USD_PER_1M`, `EVAL_CHAT_OUTPUT_USD_PER_1M`, `EVAL_EMBEDDING_USD_PER_1M`, and `EVAL_PRICING_SOURCE`. Estimates exclude Neo4j costs. Reports include UTC timestamp, dataset SHA-256, selected case IDs, model names, Python/platform/package versions, and Git SHA/dirty state. They do not record credentials or other environment values.

The checked-in Aura quality report is a separate source-to-graph integrity audit. Its 100% conformity/precision figures do not measure retrieval or generated answer quality.

A one-case live agent smoke result is checked in at `reports/rag_agent_smoke.json`; it confirms the execution path and metrics, not general answer quality.

## Curated topic candidates

`cases_curated.json` adds ten source-backed topic candidates with a small gold ID set, source file/field/excerpt provenance, and a written label rationale. Dataset tests verify that every cited excerpt is present in the bundled source record.

Each candidate is now `assistant_reviewed`, recorded in `review`: 27 of the 30 gold IDs were confirmed mechanically by matching the query against the title and abstract, three were settled by judgment, and one is flagged for a human to check. **This is not human review**, so their scores still must not be presented as validated quality claims; `evals/curated_review.md` lists what a reviewer needs to look at to promote them to `human_reviewed`.

That review retired every `expected_answer_contains` phrase. Each was a five-to-ten word fragment lifted from one of the three gold abstracts, so requiring it measured whether the answer quoted that one paper rather than whether the answer was right, a failure already observed on the exact-title cases. The phrases are preserved under `review.retired_answer_phrase`; with the list empty, `answer_correct` reports null and scoring relies on the source-backed evidence IDs.

After reviewing the labels, run a subset with `uv run python -m evals.run --mode compare --cases evals/cases_curated.json --limit 10 --top-k 5 --output reports/rag_curated_compare.json`.
