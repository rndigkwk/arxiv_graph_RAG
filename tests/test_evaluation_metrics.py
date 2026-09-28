from evals.metrics import score_answer, score_related, score_retrieval, summarize


def test_retrieval_reports_hit_recall_and_reciprocal_rank():
    result = score_retrieval(["a", "b"], ["x", "b", "a"], k=2)

    assert result == {
        "hit_at_k": 1.0,
        "recall_at_k": 0.5,
        "reciprocal_rank": 0.5,
    }


def test_retrieval_returns_zero_for_empty_results():
    assert score_retrieval(["a"], [], k=5) == {
        "hit_at_k": 0.0,
        "recall_at_k": 0.0,
        "reciprocal_rank": 0.0,
    }


def test_answer_metrics_check_required_text_and_supported_citations():
    result = score_answer(
        "Attention Is All You Need (1706.03762)",
        expected_answer_contains=["attention is all you need"],
        expected_ids=["1706.03762"],
        evidence_ids=["1706.03762", "9999.99999"],
    )

    assert result == {
        "answer_correct": 1.0,
        "answer_evidence_precision": 0.5,
        "answer_evidence_recall": 1.0,
    }


def test_answer_metrics_handle_no_expected_text_or_citations():
    assert score_answer(
        "No relevant papers found.",
        expected_answer_contains=[],
        expected_ids=[],
        evidence_ids=[],
    ) == {
        "answer_correct": None,
        "answer_evidence_precision": None,
        "answer_evidence_recall": None,
    }


def test_related_metrics_are_separate_and_require_related_gold():
    assert score_related(["paper-a", "paper-b"], ["paper-a"]) == {
        "related_retrieval_precision": 0.5,
        "related_retrieval_recall": 1.0,
    }
    assert score_related(["paper-a"], None) == {
        "related_retrieval_precision": None,
        "related_retrieval_recall": None,
    }


def test_summary_averages_only_defined_answer_metrics():
    rows = [
        {
            "hit_at_k": 1.0,
            "recall_at_k": 0.5,
            "reciprocal_rank": 1.0,
            "answer_correct": 1.0,
            "answer_evidence_precision": 0.5,
            "answer_evidence_recall": 1.0,
        },
        {
            "hit_at_k": 0.0,
            "recall_at_k": 0.0,
            "reciprocal_rank": 0.0,
            "answer_correct": None,
            "answer_evidence_precision": None,
            "answer_evidence_recall": None,
        },
    ]

    assert summarize(rows) == {
        "case_count": 2,
        "hit_at_k": 0.5,
        "recall_at_k": 0.25,
        "reciprocal_rank": 0.5,
        "answer_correct": 1.0,
        "answer_evidence_precision": 0.5,
        "answer_evidence_recall": 1.0,
    }
