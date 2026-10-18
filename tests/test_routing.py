"""경로 판정이 **보수적인 쪽으로** 틀리는지 검증합니다.

틀리는 방향이 둘 다 비용이지만 값이 다릅니다. 좁히지 않아야 할 질문을 좁히면 **답을 못
찾습니다**. 좁혀도 되는 질문을 안 좁히면 **토큰 9천 개**를 더 씁니다. 그래서 의미 신호가
하나라도 있으면 넓은 경로로 둬야 하고, 여기서 고정하는 것이 그것입니다.

평가 세트 30건은 전부 저장된 관계만 묻는 질문이므로 전부 좁혀야 합니다. 그것도 함께 봅니다 —
분류기가 한 가족이라도 놓치면 그 가족의 측정이 섞입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from routing import ANY_ROUTE, GRAPH_ROUTE, classify

RELATION_QUESTIONS = [
    "Which papers in this collection list an author named Yuquan Deng?",
    "Which papers in this collection cite the paper titled The Alignment Problem?",
    "Which other papers in this collection share an author with Hyperbolic Neural Networks?",
    "Among the papers cited by PCBWorld, which ones have primary category cs.LG?",
    "Within primary category cs.CL, which 3 papers are cited most often?",
    "How many papers were published after 2026-06-01?",
]

TOPIC_QUESTIONS = [
    "Which papers are about graph neural networks?",
    "Find papers similar to Attention Is All You Need",
    "Summarize the Transformer paper",
    "What is a diffusion model?",
    "확산 모델에 관한 논문을 찾아줘",
]


@pytest.mark.parametrize("question", RELATION_QUESTIONS)
def test_stored_relationship_questions_are_narrowed(question):
    assert classify(question).name == GRAPH_ROUTE, question


@pytest.mark.parametrize("question", TOPIC_QUESTIONS)
def test_questions_that_need_meaning_are_left_alone(question):
    assert classify(question).name == ANY_ROUTE, question


def test_a_topic_signal_vetoes_even_when_a_relationship_signal_is_present():
    """이것이 좁히면 안 되는 경로입니다 - 두 신호가 **같이** 오는 혼용 질문입니다."""
    route = classify(
        "확산 모델에 관한 논문 중 Hyperbolic Neural Networks와 공저자가 겹치는 것은?"
    )

    assert route.name == ANY_ROUTE
    # 관계 신호를 못 본 것이 아니라, 의미 신호가 이겼습니다. 사유가 남아야 판정을 되짚습니다.
    assert route.cues
    assert route.vetoes


def test_an_unrecognised_question_is_left_alone():
    """모르는 질문을 좁히면 모르는 질문에서 답을 못 찾습니다."""
    for question in ("", "hello", "2601.00001"):
        assert classify(question).name == ANY_ROUTE, question


def test_the_whole_evaluation_set_is_narrowed():
    """30건이 전부 관계 질문입니다. 한 가족이라도 놓치면 그 가족 측정이 섞입니다."""
    cases = json.loads(Path("evals/cases_graph.json").read_text(encoding="utf-8"))["cases"]

    missed = [c["id"] for c in cases if classify(c["question"]).name != GRAPH_ROUTE]

    assert missed == []
    assert len(cases) == 30


def test_the_hint_names_the_tool_to_avoid_and_the_one_to_use():
    """힌트가 "벡터 쓰지 마"만 말하면 모델은 제목을 풀 방법이 없습니다."""
    hint = classify(RELATION_QUESTIONS[2]).hint

    assert "Do not call search_papers" in hint
    # 제목 해석 방법을 지시로 적지 않고 **도구를 가리킵니다**. 지시로 적었던 때
    # ("따로 조회해서 정확히 한 편인지 확인하라")는 조회가 둘로 쪼개져 `citing`의
    # Context Precision이 1.0000에서 0.5046으로 깎였습니다.
    assert "resolve_paper" in hint
    assert "CONTAINS yourself" in hint
    # 아끼려는 토큰에서 나가는 비용입니다. 한계는 **아끼는 양**으로 정합니다 - 좁히는 질문에서
    # 줄어드는 입력이 약 9,000 토큰이므로, 힌트가 그 1%를 넘기면 다시 생각해야 합니다.
    assert len(hint.split()) < 120


def test_the_hint_no_longer_spells_out_the_title_lookup():
    """중의성 확인을 **지시에서 도구로** 옮겼습니다.

    1차 측정에서 `CONTAINS "when to plan"`이 두 편을 잡고 25편을 인용했습니다(근거 precision
    1.000 → 0.040). 지시로 고쳤더니 조회가 둘로 쪼개져 Context Precision이 깎였습니다.
    `resolve_paper`가 전문 검색 인덱스로 그 일을 한 번에 하므로 지시가 필요 없습니다.
    """
    hint = classify(RELATION_QUESTIONS[2]).hint

    assert "exactly one paper" not in hint
    assert "narrow the phrase" not in hint
    # 힌트가 짧아진 것도 이득입니다. 여기 쓰는 토큰이 아끼려는 토큰에서 나갑니다.
    assert len(hint.split()) < 70


def test_a_broad_route_carries_no_hint():
    assert classify(TOPIC_QUESTIONS[0]).hint == ""


def test_the_decision_records_why():
    route = classify(RELATION_QUESTIONS[0])

    assert route.as_dict() == {"name": GRAPH_ROUTE, "cues": ["author_named"], "vetoes": []}
