import pytest

from evals.metrics import (
    score_answer,
    score_ceiling,
    score_related,
    score_retrieval,
    summarize,
)


def test_retrieval_reports_hit_recall_and_reciprocal_rank():
    result = score_retrieval(["a", "b"], ["x", "b", "a"], k=2)

    assert result == {
        "hit_at_k": 1.0,
        "recall_at_k": 0.5,
        "reciprocal_rank": 0.5,
        # 집합 지표는 **자르지 않습니다**: 3편 중 2편이 골드이고 골드 2편을 다 찾았습니다.
        "retrieval_precision": pytest.approx(2 / 3),
        "retrieval_recall": 1.0,
    }


def test_retrieval_returns_zero_for_empty_results():
    assert score_retrieval(["a"], [], k=5) == {
        "hit_at_k": 0.0,
        "recall_at_k": 0.0,
        "reciprocal_rank": 0.0,
        # 빈 목록의 precision은 None이 아니라 0.0입니다. None이면 `summarize`가 건너뛰어
        # **전면 실패가 집계에서 사라집니다**(`score_related`와 다른 이유).
        "retrieval_precision": 0.0,
        "retrieval_recall": 0.0,
    }


def test_set_metrics_separate_not_found_from_buried_below_k():
    """`recall_at_k` 하나로는 "못 찾았다"와 "너무 많이 찾았다"가 구별되지 않습니다.

    2026-10-16 `coauthor` 가족이 그 모양이었습니다 - hit@5 0.1667인데 집합 recall은 1.0000,
    평균 7.3편을 돌려주고 골드가 알파벳순 상위 5개 밖으로 밀렸습니다(ADR-033).
    """
    buried = score_retrieval(["a", "b"], ["c", "d", "e", "f", "g", "a", "b"], k=5)

    assert buried["recall_at_k"] == 0.0        # 상위 5개에 골드가 없습니다
    assert buried["hit_at_k"] == 0.0
    assert buried["retrieval_recall"] == 1.0   # 그러나 **둘 다 찾았습니다**
    assert buried["retrieval_precision"] == pytest.approx(2 / 7)

    missing = score_retrieval(["a", "b"], ["c", "d"], k=5)

    assert missing["recall_at_k"] == 0.0       # 여기서는 같은 0.0이지만
    assert missing["retrieval_recall"] == 0.0  # 집합 지표가 두 상태를 가릅니다
    assert missing["retrieval_precision"] == 0.0


def test_set_metrics_do_not_depend_on_order():
    """집합 지표를 더한 이유 자체입니다 - 이 하네스의 그래프 경로는 순위를 만들지 않습니다.

    답변 조회의 `ORDER BY`는 30건 중 24건이 식별자 문자열순(`title`·`arxiv_id`)입니다.
    순서를 섞어도 집합 지표는 움직이지 않아야 하고, 순위 지표는 움직입니다.
    """
    forward = score_retrieval(["a", "b"], ["x", "a", "b"], k=2)
    backward = score_retrieval(["a", "b"], ["b", "a", "x"], k=2)

    for metric in ("retrieval_precision", "retrieval_recall"):
        assert forward[metric] == backward[metric], metric
    assert forward["reciprocal_rank"] != backward["reciprocal_rank"]


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


def test_ceiling_separates_pool_membership_from_rank_position():
    # The gold paper sits at rank 9, outside k=5: retrieval found it, ranking did not surface it.
    pool = [f"p{n}" for n in range(1, 21)]
    scores = score_ceiling(["p9"], pool, k=5)
    assert scores["hit_in_pool"] == 1.0
    assert scores["ceiling_recall_in_pool"] == 1.0
    assert score_retrieval(["p9"], pool, k=5)["recall_at_k"] == 0.0


def test_ceiling_by_k_binds_when_a_case_lists_more_relevant_papers_than_k():
    gold = [f"g{n}" for n in range(100)]
    scores = score_ceiling(gold, gold[:20], k=5)
    # Twenty are in the pool, but only five can be reported, so k is the binding limit.
    assert scores["found_in_pool"] == 20.0
    assert scores["ceiling_recall_in_pool"] == 0.05
    assert scores["ceiling_recall_by_k"] == 0.05


def test_ceiling_reports_a_miss_without_pretending_it_is_zero_recall():
    scores = score_ceiling(["a"], ["b", "c"], k=5)
    assert scores["hit_in_pool"] == 0.0
    assert scores["found_in_pool"] == 0.0
    assert scores["ceiling_recall_in_pool"] == 0.0
    assert scores["ceiling_recall_by_k"] == 1.0


@pytest.mark.parametrize("k", [0, -1])
def test_ceiling_rejects_a_nonpositive_k(k):
    with pytest.raises(ValueError, match="at least 1"):
        score_ceiling(["a"], ["a"], k=k)
