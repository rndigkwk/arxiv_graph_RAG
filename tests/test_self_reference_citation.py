"""질문이 제외를 요구한 논문을 인용하지 못하게 막는지 봅니다.

"Which other papers share an author with X"는 X를 **빼라고** 지목한 질문입니다. 그런데 근거
쪽 검사만으로는 X를 걸러낼 수 없습니다. 에이전트가 제목을 ID로 바꾸는 조회를 먼저 돌려야 하고,
그 조회 결과에 X가 정당하게 들어가므로 "그래프에서 나왔는가" 검사는 전부 통과합니다. 제외 요구는
행이 아니라 질문 문장에만 있습니다.

그래서 **질문이 명시적으로 제외를 말한 경우만** 봅니다. 그냥 논문을 지목한 질문("X의 대표 분류는
무엇인가")은 X 자신이 답이므로 절대 거부해서는 안 됩니다. 아래 테스트의 절반이 그 경계입니다.
"""

import pytest

from answer_validation import (
    log_validation_failure,
    self_reference_citations,
    validate_self_reference,
)

ANCHOR_TITLE = "When Does Delegation Beat Majority? A Delegation-Based Aggregator"
ANCHOR = {"arxiv_id": "2606.08098", "title": ANCHOR_TITLE, "abstract": "..."}
OTHER = {"arxiv_id": "2504.13641", "title": "A completely unrelated paper title", "abstract": "."}
OTHER_QUESTION = f"Which other papers in this collection share an author with {ANCHOR_TITLE}?"


def test_citing_the_excluded_paper_is_rejected():
    response = {"evidence_ids": ["2504.13641", "2606.08098"]}

    assert self_reference_citations(OTHER_QUESTION, [ANCHOR, OTHER], response["evidence_ids"]) == {
        "2606.08098"
    }
    with pytest.raises(ValueError, match="2606.08098"):
        validate_self_reference(OTHER_QUESTION, [ANCHOR, OTHER], response)


def test_citing_only_the_other_papers_passes():
    response = {"evidence_ids": ["2504.13641"]}

    validate_self_reference(OTHER_QUESTION, [OTHER], response)
    assert self_reference_citations(OTHER_QUESTION, [OTHER], ["2504.13641"]) == set()


def test_a_question_that_names_a_paper_without_excluding_it_is_untouched():
    # 이 질문의 답은 그 논문 자신입니다. 거부하면 멀쩡한 답을 떨어뜨립니다.
    question = f"What is the primary category of the paper titled {ANCHOR_TITLE}?"

    assert self_reference_citations(question, [ANCHOR], ["2606.08098"]) == set()
    validate_self_reference(question, [ANCHOR], {"evidence_ids": ["2606.08098"]})


def test_an_exact_title_question_is_untouched():
    question = f"Summarize the paper titled {ANCHOR_TITLE}."

    validate_self_reference(question, [ANCHOR], {"evidence_ids": ["2606.08098"]})


def test_a_citation_question_naming_the_cited_paper_is_untouched_without_other():
    # "X를 인용한 논문은?"에는 제외 표현이 없으므로 이 규칙이 개입하지 않습니다.
    # X가 인용되면 그것은 별개의 문제이며, 여기서 추론으로 막지 않습니다.
    question = f"Which papers cite the paper titled {ANCHOR_TITLE}?"

    assert self_reference_citations(question, [ANCHOR], ["2606.08098"]) == set()


def test_a_short_title_is_not_matched_by_accident():
    # 짧은 제목은 질문 문장에 우연히 들어갈 수 있습니다.
    short = {"arxiv_id": "p1", "title": "Attention", "abstract": "."}
    question = "Which other papers use attention in this collection?"

    assert self_reference_citations(question, [short], ["p1"]) == set()


def test_matching_ignores_case_and_extra_whitespace():
    messy = {"arxiv_id": "2606.08098", "title": f"  {ANCHOR_TITLE.upper()}  ", "abstract": "."}

    assert self_reference_citations(OTHER_QUESTION, [messy], ["2606.08098"]) == {"2606.08098"}


def test_an_uncited_paper_is_never_reported():
    # 질문이 제목을 인용했더라도 답변이 그 논문을 인용하지 않았다면 문제가 없습니다.
    assert self_reference_citations(OTHER_QUESTION, [ANCHOR, OTHER], ["2504.13641"]) == set()


@pytest.mark.parametrize(
    "phrase",
    ["other papers", "other paper", "papers other than", "papers besides", "papers excluding"],
)
def test_each_explicit_exclusion_phrase_triggers_the_rule(phrase):
    question = f"List {phrase} related to {ANCHOR_TITLE}"

    assert self_reference_citations(question, [ANCHOR], ["2606.08098"]) == {"2606.08098"}


def test_the_failure_reason_is_logged_under_its_own_code(caplog):
    error = ValueError("질문이 제외를 요구한 논문을 인용했습니다: ['2606.08098']")

    with caplog.at_level("WARNING"):
        log_validation_failure("req", 1, "self_reference", error)

    assert "reason=self_reference_citation" in caplog.text
    # 논문 ID 말고 질문이나 본문이 로그에 남지 않아야 합니다.
    assert ANCHOR_TITLE.casefold() not in caplog.text.casefold()
