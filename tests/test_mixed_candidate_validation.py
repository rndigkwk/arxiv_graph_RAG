import pytest

from candidate_validation import (
    CandidateVerdict,
    QuestionVerdict,
    assess_and_validate,
    judge_content,
)


def test_rejects_mixed_answer_with_vector_only_citation():
    response = {
        "evidence_ids": ["vector-only"],
        "rows": [{"evidence_ids": ["graph-match"]}],
    }
    fetched = [{"arxiv_id": "vector-only", "title": "A topic paper", "abstract": "Topic"}]

    with pytest.raises(ValueError, match="그래프 조건"):
        assess_and_validate(
            "topic papers with a graph condition", response,
            lambda ids: fetched,
            lambda question, papers: {
                "requires_content": True, "requires_graph": True,
                "graph_conditions_covered": True,
                "content_matches": {"vector-only": True},
            },
        )


def test_graph_only_candidate_can_pass_when_its_abstract_supports_topic():
    response = {"evidence_ids": ["graph-only"], "rows": [{"evidence_ids": ["graph-only"]}]}
    fetched = [{"arxiv_id": "graph-only", "title": "Shared Memory", "abstract": "Agents"}]
    seen = []

    def assess(question, papers):
        seen.extend(papers)
        return {"requires_content": True, "requires_graph": True,
                "graph_conditions_covered": True,
                "content_matches": {"graph-only": True}}

    result = assess_and_validate("shared memory papers citing AutoGen", response,
                                 lambda ids: fetched, assess)
    assert seen == fetched
    assert result["content_matches"] == {"graph-only": True}


def test_missing_paper_details_cannot_be_treated_as_verified():
    response = {"evidence_ids": ["missing"], "rows": [{"evidence_ids": ["missing"]}]}
    with pytest.raises(ValueError, match="논문 정보"):
        assess_and_validate("topic with graph condition", response,
                            lambda ids: [], lambda question, papers: {})


def test_empty_answer_does_not_call_the_topic_judge():
    response = {"evidence_ids": [], "rows": []}

    def unexpected(*args):
        raise AssertionError("No candidate should be checked")

    assess_and_validate("no matches", response, unexpected, unexpected)


def test_topic_judge_returns_one_verdict_per_cited_paper():
    papers = [{"arxiv_id": "p1", "title": "Shared Memory", "abstract": "For LLM agents"}]

    def invoke(messages):
        assert "p1" in messages[1][1]
        assert "REFERENCES" in messages[1][1]
        return QuestionVerdict(
            requires_content=True, requires_graph=True, graph_conditions_covered=True,
            candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)],
        )

    assert judge_content(
        "shared memory papers citing AutoGen", papers, invoke,
        graph_queries=["MATCH (p)-[:REFERENCES]->(autogen) RETURN p"],
    ) == {
        "requires_content": True, "requires_graph": True,
        "graph_conditions_covered": True,
        "content_matches": {"p1": True},
    }


def test_topic_judge_rejects_missing_candidate_verdict():
    papers = [{"arxiv_id": "p1", "title": "Shared Memory", "abstract": "For LLM agents"}]
    verdict = QuestionVerdict(requires_content=True, requires_graph=True,
                              graph_conditions_covered=True, candidates=[])
    with pytest.raises(ValueError, match="판정 결과"):
        judge_content("shared memory papers citing AutoGen", papers, lambda messages: verdict)
