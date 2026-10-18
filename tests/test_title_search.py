"""제목 해석 규칙을 **접속 없이** 전수 검사합니다.

`resolve_paper`는 두 조회를 보내고 무엇을 돌려줄지는 `title_search.choose`가 정합니다. 그
판정이 200편 × 질의 4형태 측정에서 고른 규칙이므로
([`reports/title_resolution.json`](../reports/title_resolution.json)) 규칙이 조용히 바뀌지
않도록 여기서 고정합니다.

고정하는 것은 넷입니다.

1. **부분문자열이 정확히 한 편이면 그것을 믿습니다.** 짧은 구절에서 전문 검색을 이깁니다
   (0.800 대 0.780).
2. **아니면 전문 검색 1위입니다.** 낱말이 빠지거나 순서가 섞이면 `CONTAINS`는 94~99%가
   0건입니다.
3. **1·2위 점수 차가 0.10 미만이면 애매하다고 말합니다.** 틀린 것의 95.5%를 잡고 헛경보는
   2.9%입니다.
4. **못 찾으면 못 찾았다고 합니다.** 조용히 빈 값을 돌려주면 모델이 지어냅니다.
"""

from __future__ import annotations

import pytest

from title_search import (
    AMBIGUITY_GAP,
    LUCENE_SPECIAL,
    MIN_COVERAGE,
    choose,
    coverage,
    escape_lucene,
    relative_gap,
)


def ranked(*pairs):
    """후보 목록. 제목은 적중률이 1.0이 되도록 질의와 같게 둡니다."""
    return [{"arxiv_id": a, "title": QUERY, "score": s} for a, s in pairs]


# 적중률이 1.0이 되도록 후보 제목과 같은 문자열을 질의로 씁니다. 적중률 자체는 아래
# 전용 테스트에서 따로 봅니다.
QUERY = "Hyperbolic Neural Networks"


def test_a_unique_substring_match_wins():
    """짧은 구절에서 전문 검색보다 낫습니다. 고유 부분문자열이기 때문입니다."""
    exact = [{"arxiv_id": "2601.00001", "title": "When to Plan"}]

    picked = choose(exact, ranked(("2601.00009", 9.0), ("2601.00001", 8.9)), query=QUERY)

    assert picked.arxiv_id == "2601.00001"
    assert picked.method == "exact_substring"
    assert picked.ambiguous is False


def test_several_substring_matches_fall_through_to_the_ranking():
    """`coauthor-04`를 깨뜨린 경우입니다 - "when to plan"이 두 편에 들어 있었습니다."""
    exact = [{"arxiv_id": "2601.00001"}, {"arxiv_id": "2601.00009"}]

    picked = choose(exact, ranked(("2601.00001", 9.0), ("2601.00009", 4.0)), query=QUERY)

    assert picked.method == "fulltext"
    assert picked.arxiv_id == "2601.00001"


def test_a_narrow_score_gap_is_reported_as_ambiguous():
    """맞을 때 차이가 0.39~0.54, 틀릴 때 0.038이었습니다. 0.10이 그 사이입니다."""
    picked = choose([], ranked(("a", 10.0), ("b", 9.7)), query=QUERY)

    assert picked.ambiguous is True
    assert picked.score_gap == pytest.approx(0.03, abs=0.001)
    assert "후보" in picked.note


def test_a_wide_score_gap_is_not_ambiguous():
    picked = choose([], ranked(("a", 10.0), ("b", 5.0)), query=QUERY)

    assert picked.ambiguous is False
    assert picked.score_gap == pytest.approx(0.5)


def test_the_threshold_is_the_measured_one():
    """임계값을 올리면 헛경보가 늘고 내리면 놓칩니다. 측정으로 고른 값입니다."""
    assert AMBIGUITY_GAP == 0.10


def test_a_single_candidate_has_no_gap_and_is_not_ambiguous():
    picked = choose([], ranked(("a", 10.0)), query=QUERY)

    assert picked.score_gap is None
    assert picked.ambiguous is False
    assert picked.arxiv_id == "a"


def test_nothing_found_says_so_instead_of_returning_nothing():
    """조용히 빈 값을 돌려주면 모델이 지어냅니다."""
    picked = choose([], [], query=QUERY)

    assert picked.arxiv_id is None
    assert picked.method == "none"
    assert "찾은 논문이 없습니다" in picked.note


def test_a_zero_top_score_does_not_divide_by_zero():
    assert relative_gap(ranked(("a", 0.0), ("b", 0.0))) is None


@pytest.mark.parametrize("raw", [
    "Attention Is All You Need",
    "When Does Delegation Beat Majority? A Delegation-Based Aggregator",
    "Fuzzy K-Means Clustering without Cluster Centroids",
    "A (Very) Short Note: on [brackets] and +plus",
])
def test_lucene_operators_are_neutralised(raw):
    """논문 제목에 콜론·물음표·괄호가 흔합니다. 그대로 보내면 질의가 깨집니다."""
    escaped = escape_lucene(raw)

    for char in LUCENE_SPECIAL:
        # 특수문자가 남아 있다면 반드시 바로 앞에 역슬래시가 있어야 합니다.
        for position, found in enumerate(escaped):
            if found == char and char != "\\":
                assert position > 0 and escaped[position - 1] == "\\", (raw, char)


def test_the_resolution_carries_its_candidates_for_the_model_to_check():
    """애매하다고만 말하고 후보를 안 주면 모델이 확인할 방법이 없습니다."""
    picked = choose([], ranked(("a", 10.0), ("b", 9.8), ("c", 1.0)), query=QUERY)

    assert picked.ambiguous is True
    assert [c["arxiv_id"] for c in picked.as_dict()["candidates"]] == ["a", "b", "c"]


def test_a_weak_match_refuses_to_name_a_paper():
    """**점수 차로는 못 막는 실패입니다.**

    가짜 제목 질의가 점수 차 0.4315로 "자신 있게" 무관한 논문을 받았습니다. 가짜 제목 100건과
    양성 800건으로 재서 적중률 0.8을 골랐습니다 - 음성 94%를 막고 맞은 양성은 0% 잃습니다.
    """
    candidates = [
        {"arxiv_id": "2209.14935", "title": "Does Zero-Shot Reinforcement Learning Exist?",
         "score": 9.0},
        {"arxiv_id": "2209.00001", "title": "Something Else Entirely", "score": 5.0},
    ]

    picked = choose([], candidates, query="a paper that certainly does not exist here xyzzy")

    assert picked.arxiv_id is None
    assert picked.method == "weak_match"
    # 후보는 돌려줍니다. 모델이 직접 볼 수 있어야 합니다.
    assert picked.candidates == candidates
    assert picked.coverage is not None and picked.coverage < MIN_COVERAGE
    assert "없는 논문일 수 있습니다" in picked.note


def test_extra_words_in_the_question_do_not_cause_a_weak_match():
    """질의 쪽으로만 세면 "the paper titled X"가 0.500이 되어 정당한 질의가 거부됩니다."""
    assert coverage("the paper titled Hyperbolic Neural Networks",
                    "Hyperbolic Neural Networks") == 1.0

    picked = choose([], ranked(("a", 10.0), ("b", 2.0)),
                    query="the paper titled Hyperbolic Neural Networks")

    assert picked.arxiv_id == "a"
    assert picked.method == "fulltext"


def test_a_short_prefix_of_a_long_title_is_not_a_weak_match():
    """제목 쪽으로만 세면 `prefix` 질의의 맞은 것들이 중앙값 0.375로 거부됩니다."""
    long_title = "When to Plan: Learning to Select Between Reactive Control and Planning"

    assert coverage("when to plan", long_title) == 1.0


def test_the_coverage_threshold_is_the_measured_one():
    assert MIN_COVERAGE == 0.8
