import pytest

from answer_validation import (
    direct_evidence_ids,
    validate_evidence_ids,
    validate_mixed_candidates,
)


def test_accepts_vector_and_graph_ids_as_direct_evidence():
    validate_evidence_ids(["vector-id", "graph-id"], {"vector-id", "graph-id"})


def test_rejects_ids_available_only_in_related_papers():
    with pytest.raises(ValueError, match="직접 검색된 근거"):
        validate_evidence_ids(["related-only-id"], {"vector-id", "graph-id"})


def test_related_papers_are_excluded_when_direct_evidence_is_collected():
    response = {
        "rows": [{"evidence_ids": ["graph-id"]}],
        "papers": [{"arxiv_id": "vector-id"}],
        "related": [{"arxiv_id": "related-only-id"}],
    }

    assert direct_evidence_ids(response) == {"graph-id", "vector-id"}


@pytest.mark.parametrize("evidence_ids", [["paper-id", "paper-id"], [""], [None]])
def test_rejects_duplicate_blank_and_non_string_evidence_ids(evidence_ids):
    with pytest.raises(ValueError):
        validate_evidence_ids(evidence_ids, {"paper-id"})


def test_mixed_answer_rejects_vector_only_paper_even_when_it_is_on_topic():
    response = {
        "evidence_ids": ["vector-only"],
        "rows": [{"evidence_ids": ["graph-match"]}],
    }
    assessment = {
        "requires_content": True,
        "requires_graph": True,
        "graph_conditions_covered": True,
        "content_matches": {"vector-only": True},
    }
    with pytest.raises(ValueError, match="그래프 조건"):
        validate_mixed_candidates(response, assessment)


def test_mixed_answer_accepts_graph_only_paper_when_content_is_verified():
    response = {"evidence_ids": ["graph-only"], "rows": [{"evidence_ids": ["graph-only"]}]}
    assessment = {
        "requires_content": True,
        "requires_graph": True,
        "graph_conditions_covered": True,
        "content_matches": {"graph-only": True},
    }
    validate_mixed_candidates(response, assessment)


def test_mixed_answer_rejects_graph_paper_without_content_support():
    response = {"evidence_ids": ["wrong-topic"], "rows": [{"evidence_ids": ["wrong-topic"]}]}
    assessment = {
        "requires_content": True,
        "requires_graph": True,
        "graph_conditions_covered": True,
        "content_matches": {"wrong-topic": False},
    }
    with pytest.raises(ValueError, match="내용 조건"):
        validate_mixed_candidates(response, assessment)


def test_mixed_answer_rejects_graph_query_that_omits_requested_condition():
    response = {"evidence_ids": ["p1"], "rows": [{"evidence_ids": ["p1"]}]}
    assessment = {
        "requires_content": True,
        "requires_graph": True,
        "graph_conditions_covered": False,
        "content_matches": {"p1": True},
    }
    with pytest.raises(ValueError, match="그래프 검색 조건"):
        validate_mixed_candidates(response, assessment)
