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


def test_compare_case_counts_the_condition_validation_call(monkeypatch):
    """The judge runs outside the agent stream, so its tokens must be folded in."""
    import evals.run

    monkeypatch.setattr(evals.run, "count_embedding_tokens", lambda queries, model: 0)
    case = {
        "id": "title-01",
        "category": "exact-title",
        "question": "Find a known paper",
        "search_query": "known paper",
        "expected_ids": ["a"],
        "expected_answer_contains": ["known paper"],
    }

    class FakeMessage:
        usage_metadata = {"input_tokens": 100, "output_tokens": 10}

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
                "validation_usage": {"input_tokens": 400, "output_tokens": 20},
            }, [FakeMessage()]

    result = compare_case(FakeChatbot, case, top_k=1)

    assert result["graph_rag"]["chat_input_tokens"] == 500
    assert result["graph_rag"]["chat_output_tokens"] == 30
    # The vector-only baseline never calls the judge, so it stays at the stream total.
    assert result["vector_only"]["chat_input_tokens"] == 100


def test_compare_case_keeps_unreported_validation_usage_as_unknown(monkeypatch):
    import evals.run

    monkeypatch.setattr(evals.run, "count_embedding_tokens", lambda queries, model: 0)
    case = {
        "id": "title-02",
        "category": "exact-title",
        "question": "Find a known paper",
        "search_query": "known paper",
        "expected_ids": ["a"],
        "expected_answer_contains": [],
    }

    class Bare:
        pass

    class FakeChatbot:
        GroundedAnswer = object

        class StructuredModel:
            def invoke(self, messages):
                return {
                    "raw": Bare(),
                    "parsed": type("Answer", (), {"answer": "x", "evidence_ids": ["a"]})(),
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
                "answer": "x",
                "evidence_ids": ["a"],
                "validation_usage": None,
            }, [Bare()]

    result = compare_case(FakeChatbot, case, top_k=1)

    assert result["graph_rag"]["chat_input_tokens"] is None
    assert result["graph_rag"]["chat_output_tokens"] is None


def test_evaluate_case_records_what_the_query_sorted_by():
    """순위 지표의 "1위"가 무슨 뜻인지는 `ORDER BY`가 정합니다(ADR-033).

    보고서에 남지 않으면 `reciprocal_rank` 1.0000을 "순위가 좋다"로 잘못 읽습니다. 실제로
    30건 중 24건이 식별자 문자열순이었습니다.
    """
    case = {
        "id": "citing-01",
        "category": "citing",
        "expected_ids": ["a"],
        "expected_answer_contains": [],
    }
    response = {
        "papers": [],
        "rows": [{"evidence_ids": ["a"]}],
        "evidence_ids": ["a"],
        "answer": "one paper",
        "tool_calls": [
            {
                "name": "search_graph",
                "status": "success",
                "output": {
                    "cypher": "MATCH (p:ArxivPaper) RETURN p.title AS answer_value "
                              "ORDER BY answer_value LIMIT 25",
                    "rows": [{"evidence_ids": ["a"]}],
                },
            },
            {
                "name": "search_graph",
                "status": "success",
                "output": {"cypher": "MATCH (p) RETURN p ORDER BY p.citation_count DESC"},
            },
            # 실패한 조회와 다른 도구는 세지 않습니다.
            {"name": "search_graph", "status": "error", "output": {"cypher": "ORDER BY x"}},
            {"name": "resolve_paper", "status": "success", "output": {"arxiv_id": "a"}},
        ],
    }

    row = evaluate_case(case, response, mode="agent", top_k=5)

    assert row["ordered_by"] == ["answer_value", "p.citation_count DESC"]


def test_evaluate_case_omits_the_sort_key_when_no_query_declared_one():
    """정렬 기준이 없으면 빈 목록이 아니라 **키 자체가 없습니다.**

    설명용 필드이므로 조용히 빠지는 쪽이 맞습니다 - 빈 목록을 적으면 "ORDER BY가 없었다"와
    "Cypher를 못 읽었다"가 같은 모양이 됩니다.
    """
    case = {
        "id": "author-01",
        "category": "author",
        "expected_ids": ["a"],
        "expected_answer_contains": [],
    }
    response = {"papers": [{"arxiv_id": "a"}], "evidence_ids": ["a"], "answer": "x"}

    assert "ordered_by" not in evaluate_case(case, response, mode="agent", top_k=5)


def test_the_row_keeps_the_inside_of_generate():
    """보고서에 남기지 않으면 실행이 끝나는 순간 사라지고, 다시 보려면 돈을 또 씁니다."""
    inside = {"generate_seconds": 35.0, "tools_seconds": 2.0, "model_seconds": 28.0,
              "other_seconds": 5.0, "adds_up": True, "tool_calls": 3, "model_turns": 4}
    response = {"answer": "A", "evidence_ids": [], "rows": [], "papers": [],
                "graph_trace": {"visits": {}, "seconds": {"generate": 35.0},
                                "rejections": [], "attempts": 1, "total_seconds": 35.0},
                "generate_trace": inside}

    row = evaluate_case({"id": "x", "category": "author", "expected_ids": ["1111.1111"]},
                        response, mode="agent", top_k=5)

    assert row["generate_trace"] == inside


def test_an_old_run_without_the_inside_gets_no_empty_key():
    """빈 값을 넣으면 "재었는데 0"으로 읽힙니다. 없는 것은 없는 대로 둡니다."""
    response = {"answer": "A", "evidence_ids": [], "rows": [], "papers": []}

    row = evaluate_case({"id": "x", "category": "author", "expected_ids": ["1111.1111"]},
                        response, mode="agent", top_k=5)

    assert "generate_trace" not in row


def test_the_row_keeps_which_gate_rejected():
    """보고서가 이것을 버리면 어느 단계가 막았는지는 방문 횟수에서 추론해야 합니다(ADR-069)."""
    rejections = [{"node": "check_conditions", "stage": "mixed_candidate_conditions",
                   "attempt": 1}]
    response = {"answer": "A", "evidence_ids": [], "rows": [], "papers": [],
                "graph_trace": {"visits": {"check_conditions": 2}, "seconds": {},
                                "rejections": rejections, "attempts": 2,
                                "total_seconds": 1.0}}

    row = evaluate_case({"id": "x", "category": "coauthor", "expected_ids": ["1111.1111"]},
                        response, mode="agent", top_k=5)

    assert row["graph_trace"]["rejections"] == rejections
