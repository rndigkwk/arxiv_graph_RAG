"""생성된 Cypher가 문자열로 변환된 값을 정렬하는지 검사합니다.

이 오류가 위험한 이유는 질의가 **성공**한다는 점입니다. `toString(n) AS c ... ORDER BY c`는
사전 순서로 정렬하므로 내림차순에서 피인용 9회가 88회보다 위로 올라오는데, 오류가 없어서
아래쪽 어디에서도 순위가 틀어진 것을 알 수 없습니다. 조용히 틀린 행보다 오류가 낫습니다.

프롬프트 규칙만으로는 빈도가 47.1%에서 5.9%로 줄었을 뿐 0이 되지 않았습니다. 그래서 검사로
막습니다. 표시용으로 문자열을 만들면서 정렬은 숫자로 하는 경우를 통과시키는 것이 핵심입니다.
"""

import pytest

from cypher_validation import check_order_by, stringified_order_keys

AGGREGATE = (
    'MATCH (p:ArxivPaper) WHERE p.primary_category = "cs.CV" '
    "WITH p, COUNT { (p)<-[:REFERENCES]-() } AS citations "
    "RETURN p.arxiv_id AS answer_value, [p.arxiv_id] AS evidence_ids, "
)


def test_sorting_a_stringified_count_is_flagged():
    cypher = (
        AGGREGATE
        + "toString(citations) AS citation_count ORDER BY citation_count DESC LIMIT 3"
    )

    assert stringified_order_keys(cypher) == ["citation_count"]
    with pytest.raises(ValueError, match="문자열로 변환된 값"):
        check_order_by(cypher)


def test_sorting_the_numeric_alias_passes():
    cypher = AGGREGATE + "citations AS citation_count ORDER BY citation_count DESC LIMIT 3"

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_stringifying_for_display_while_sorting_numerically_passes():
    # 표시용으로 문자열을 만드는 것은 정상입니다. 정렬 키가 숫자면 순위가 맞습니다.
    cypher = (
        AGGREGATE + "toString(citations) AS shown, citations AS n ORDER BY n DESC LIMIT 3"
    )

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_a_stringified_key_in_a_multi_key_sort_is_flagged():
    cypher = (
        AGGREGATE
        + "toString(citations) AS citation_count, p.title AS title "
        "ORDER BY title ASC, citation_count DESC LIMIT 3"
    )

    assert stringified_order_keys(cypher) == ["citation_count"]
    with pytest.raises(ValueError):
        check_order_by(cypher)


def test_a_stringified_alias_that_is_never_sorted_passes():
    cypher = AGGREGATE + "toString(p.published) AS published ORDER BY citations DESC LIMIT 3"

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_the_published_timestamp_may_still_be_stringified_for_display():
    # 기존 질의가 toString(p.published)를 쓰고 있으므로 이것이 막히면 안 됩니다.
    cypher = (
        "MATCH (p:ArxivPaper) RETURN p.arxiv_id AS answer_value, "
        "toString(p.published) AS published ORDER BY p.published DESC LIMIT 5"
    )

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_case_and_spacing_do_not_hide_the_problem():
    cypher = AGGREGATE + "TOSTRING( citations )  AS  cc  order by  cc  desc  limit 3"

    assert stringified_order_keys(cypher) == ["cc"]


def test_a_limit_after_the_sort_does_not_swallow_the_key():
    assert stringified_order_keys(
        AGGREGATE + "toString(citations) AS cc ORDER BY cc DESC SKIP 1 LIMIT 3"
    ) == ["cc"]


@pytest.mark.parametrize("cypher", ["", "MATCH (p) RETURN p.arxiv_id AS answer_value"])
def test_queries_without_ordering_pass(cypher):
    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)
