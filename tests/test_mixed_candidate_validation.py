import pytest

from src.answer_validation import validate_mixed_candidates
from src.candidate_validation import (
    CandidateVerdict,
    QuestionVerdict,
    assess_and_validate,
    judge_content,
    structured_judge,
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
            requires_content=True, graph_conditions=["citing AutoGen"],
            graph_conditions_covered=True,
            candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)],
        )

    assert judge_content(
        "shared memory papers citing AutoGen", papers, invoke,
        graph_queries=["MATCH (p)-[:REFERENCES]->(autogen) RETURN p"],
    ) == {
        "requires_content": True, "requires_graph": True,
        "graph_conditions": ["citing AutoGen"],
        "graph_conditions_covered": True,
        "content_matches": {"p1": True},
    }


def test_topic_judge_rejects_missing_candidate_verdict():
    papers = [{"arxiv_id": "p1", "title": "Shared Memory", "abstract": "For LLM agents"}]
    verdict = QuestionVerdict(requires_content=True, graph_conditions=["citing AutoGen"],
                              graph_conditions_covered=True, candidates=[])
    with pytest.raises(ValueError, match="판정 결과"):
        judge_content("shared memory papers citing AutoGen", papers, lambda messages: verdict)


def test_a_broken_verdict_is_asked_again_before_the_answer_is_sent_back():
    """11-02 정기 평가: 판정이 한 번 깨지자 답을 고쳐 쓰게 했고, 고친 답이 정답 논문을 뺐습니다."""
    papers = [{"arxiv_id": "p1", "title": "Shared Memory", "abstract": "For LLM agents"}]
    good = QuestionVerdict(requires_content=False, graph_conditions=["cite X"],
                           graph_conditions_covered=True,
                           candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)])
    replies = [QuestionVerdict(requires_content=False, graph_conditions=["cite X"],
                               graph_conditions_covered=True, candidates=[]), good]
    calls = []

    def invoke(messages):
        calls.append(messages)
        return replies[len(calls) - 1]

    assessment = judge_content("Which papers cite X?", papers, invoke)
    assert len(calls) == 2
    assert assessment["content_matches"] == {"p1": True}


def test_an_unreadable_verdict_is_asked_again_too():
    papers = [{"arxiv_id": "p1", "title": "Shared Memory", "abstract": "For LLM agents"}]
    usage = {}
    raw = [{"raw": None, "parsed": None},
           {"raw": None, "parsed": QuestionVerdict(
               requires_content=False, graph_conditions=[], graph_conditions_covered=True,
               candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)])}]

    class Structured:
        def invoke(self, messages):
            return raw.pop(0)

    assessment = judge_content("Which papers cite X?", papers,
                               structured_judge(Structured(), usage))
    assert assessment["content_matches"] == {"p1": True}
    assert raw == []


def test_other_judge_errors_are_not_retried():
    papers = [{"arxiv_id": "p1", "title": "Shared Memory", "abstract": "For LLM agents"}]
    calls = []

    def invoke(messages):
        calls.append(messages)
        raise TimeoutError("provider stalled")

    with pytest.raises(TimeoutError):
        judge_content("Which papers cite X?", papers, invoke)
    assert len(calls) == 1


def test_unnamed_graph_condition_does_not_trigger_the_coverage_check():
    """A bare boolean used to invent a condition and force a retry on content-only questions."""
    papers = [{"arxiv_id": "p1", "title": "Semantic Compression Trees", "abstract": "Retrieval"}]

    def invoke(messages):
        return QuestionVerdict(
            requires_content=True, graph_conditions=[], graph_conditions_covered=False,
            candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)],
        )

    assessment = judge_content("Find the paper titled: Semantic Compression Trees", papers,
                               invoke, graph_queries=[])

    assert assessment["requires_graph"] is False
    assert assessment["graph_conditions"] == []
    # Nothing to enforce, so the coverage flag must not stay false and fail validation.
    assert assessment["graph_conditions_covered"] is True
    validate_mixed_candidates(
        {"rows": [], "evidence_ids": ["p1"]}, assessment
    )


def test_blank_condition_strings_do_not_count_as_a_graph_condition():
    papers = [{"arxiv_id": "p1", "title": "T", "abstract": "A"}]

    def invoke(messages):
        return QuestionVerdict(
            requires_content=True, graph_conditions=["", "   "], graph_conditions_covered=False,
            candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)],
        )

    assessment = judge_content("Find a paper", papers, invoke, graph_queries=[])
    assert assessment["requires_graph"] is False
    assert assessment["graph_conditions"] == []


def test_named_graph_condition_without_covering_cypher_still_fails():
    papers = [{"arxiv_id": "p1", "title": "T", "abstract": "A"}]

    def invoke(messages):
        return QuestionVerdict(
            requires_content=True, graph_conditions=["published in 2024"],
            graph_conditions_covered=False,
            candidates=[CandidateVerdict(arxiv_id="p1", content_matches=True)],
        )

    assessment = judge_content("2024 papers about agents", papers, invoke, graph_queries=[])
    assert assessment["requires_graph"] is True
    with pytest.raises(ValueError, match="그래프 검색 조건"):
        validate_mixed_candidates({"rows": [], "evidence_ids": ["p1"]}, assessment)
