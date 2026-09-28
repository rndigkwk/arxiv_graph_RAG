from evals.run import compare_case, evaluate_case, summarize_comparison


def test_evaluate_case_retrieval_uses_ranked_paper_ids():
    case = {
        "id": "sample",
        "category": "topic",
        "expected_ids": ["a"],
        "expected_answer_contains": [],
    }
    response = {"papers": [{"arxiv_id": "a"}, {"arxiv_id": "b"}], "evidence_ids": []}

    row = evaluate_case(case, response, mode="retrieval", top_k=2)

    assert row["retrieved_ids"] == ["a", "b"]
    assert row["hit_at_k"] == 1.0
    assert "answer_correct" not in row


def test_evaluate_case_agent_scores_answer_and_citations():
    case = {
        "id": "title-01",
        "category": "exact-title",
        "expected_ids": ["a"],
        "expected_answer_contains": ["known paper"],
    }
    response = {
        "papers": [{"arxiv_id": "a"}],
        "evidence_ids": ["a", "other"],
        "answer": "A known paper.",
    }

    row = evaluate_case(case, response, mode="agent", top_k=1)

    assert row["answer_correct"] == 1.0
    assert row["answer_evidence_precision"] == 0.5
    assert row["answer_evidence_recall"] == 1.0


def test_evaluate_case_agent_keeps_graph_and_related_ids_separate():
    case = {
        "id": "graph-case",
        "category": "topic",
        "expected_ids": ["graph-hit"],
        "expected_related_ids": ["related-hit"],
    }
    response = {
        "papers": [{"arxiv_id": "vector-hit"}],
        "rows": [{"evidence_ids": ["graph-hit"]}],
        "related": [{"arxiv_id": "related-hit"}],
        "evidence_ids": ["graph-hit"],
        "answer": "Found graph-hit",
    }

    row = evaluate_case(case, response, mode="agent", top_k=3)

    assert row["retrieved_ids"] == ["vector-hit", "graph-hit"]
    assert row["related_ids"] == ["related-hit"]
    assert row["hit_at_k"] == 1.0
    assert row["answer_evidence_precision"] == 1.0
    assert row["related_retrieval_precision"] == 1.0
    assert row["related_retrieval_recall"] == 1.0


def test_compare_case_runs_vector_only_and_graph_agent_with_separate_metrics(monkeypatch):
    import evals.run

    monkeypatch.setattr(evals.run, "count_embedding_tokens", lambda queries, model: len(queries))
    case = {
        "id": "title-01",
        "category": "exact-title",
        "question": "Find a known paper",
        "search_query": "known paper",
        "expected_ids": ["a"],
        "expected_answer_contains": ["known paper"],
    }

    class FakeMessage:
        usage_metadata = {"input_tokens": 12, "output_tokens": 4}

    class FakeChatbot:
        GroundedAnswer = object

        class StructuredModel:
            def invoke(self, messages):
                return {
                    "raw": FakeMessage(),
                    "parsed": type(
                        "Answer", (), {"answer": "Known paper", "evidence_ids": ["a"]}
                    )(),
                }

        llm = type(
            "LLM",
            (),
            {"with_structured_output": lambda self, *a, **kw: FakeChatbot.StructuredModel()},
        )()
        search_papers = type(
            "Search", (), {"invoke": lambda self, values: {"papers": [{"arxiv_id": "a"}]}}
        )()

        @staticmethod
        def ask(question, history):
            return {
                "papers": [{"arxiv_id": "a"}],
                "answer": "Known paper",
                "evidence_ids": ["a"],
            }, [FakeMessage()]

    result = compare_case(FakeChatbot, case, top_k=1)

    assert result["vector_only"]["answer_correct"] == 1.0
    assert result["graph_rag"]["answer_evidence_precision"] == 1.0
    assert result["vector_only"]["chat_input_tokens"] == 12
    assert result["graph_rag"]["chat_output_tokens"] == 4
    assert result["vector_only"]["elapsed_seconds"] >= 0
    assert result["graph_rag"]["elapsed_seconds"] >= 0


def test_compare_summary_reports_per_category_and_unpriced_cost_explicitly():
    result = {
        "category": "exact-title",
        "vector_only": {"hit_at_k": 1.0, "chat_input_tokens": 10, "chat_output_tokens": 2},
        "graph_rag": {"hit_at_k": 0.0, "chat_input_tokens": 30, "chat_output_tokens": 4},
    }

    summary = summarize_comparison([result])

    assert summary["case_count"] == 1
    assert summary["by_category"]["exact-title"]["graph_rag"]["hit_at_k"] == 0.0
    assert summary["vector_only"]["chat_tokens_total"] == 12
    assert summary["cost_usd"] is None


def test_compare_summary_keeps_missing_usage_and_latency_visible_as_unknown():
    result = {
        "category": "topic",
        "vector_only": {
            "hit_at_k": 1.0,
            "chat_input_tokens": None,
            "chat_output_tokens": None,
            "embedding_tokens_estimate": 12,
            "elapsed_seconds": 2.0,
            "estimated_api_cost_usd": None,
        },
        "graph_rag": {
            "hit_at_k": 0.0,
            "chat_input_tokens": 3,
            "chat_output_tokens": 1,
            "embedding_tokens_estimate": 20,
            "elapsed_seconds": 4.0,
            "estimated_api_cost_usd": None,
        },
    }

    summary = summarize_comparison([result])

    assert summary["vector_only"]["chat_tokens_total"] is None
    assert summary["vector_only"]["embedding_tokens_total_estimate"] == 12
    assert summary["vector_only"]["latency_seconds"]["p95_seconds"] == 2.0
    assert summary["graph_rag"]["chat_tokens_total"] == 4
    assert summary["cost_usd"] is None
