"""경로 판정을 **모델을 부르기 전에** 적는 줄을 지킵니다.

재검토 문서의 제안은 "`routing.py`가 가족을 정하니 'author 질문으로 봅니다'를 적자"였습니다.
**routing은 가족을 정하지 않습니다.** `classify`는 `graph`냐 `any`냐와 그 근거가 된 단서만
돌려줍니다 - author·citing·coauthor 같은 가족 이름은 평가 데이터셋의 `category`이고 질의
시점에는 없습니다. ADR-069에서 "가족별로 조건 판정을 건너뛰자"를 기각한 이유가 같습니다.

그래서 이 파일이 지키는 것은 **적을 수 있는 것만 적는지**입니다. 경로와 단서는 있고, 가족은
없습니다.
"""

import json
import re
from pathlib import Path

import app
from src import routing

FAMILIES = ("author", "citing", "coauthor", "reference", "topcited")


def test_a_narrowed_question_says_graph_and_why():
    line = app.route_line(routing.classify(
        "Which papers share an author with 2501.00001?").as_dict())

    assert "그래프 경로" in line
    assert "단서:" in line


def test_a_topic_question_says_the_broad_route():
    line = app.route_line(routing.classify(
        "Find papers about graph neural networks for reasoning").as_dict())

    assert "넓은 경로" in line
    assert "단서:" not in line


def test_a_vetoed_question_says_why_it_was_not_narrowed():
    """단서가 있는데도 넓게 간 경우입니다. 이 줄이 없으면 판정이 이유 없이 보입니다."""
    route = routing.classify("Explain the papers citing 2501.00001")

    assert route.name == routing.ANY_ROUTE
    assert route.cues and route.vetoes
    assert "좁히지 않은 이유:" in app.route_line(route.as_dict())


def test_the_line_never_claims_a_question_family():
    """데이터셋 질문 전부를 넣어 봅니다. 하나라도 가족 이름을 적으면 못 지키는 약속입니다."""
    cases = json.loads(Path("evals/cases_graph.json").read_text(encoding="utf-8"))
    questions = [case["question"] for case in cases["cases"]]

    assert len(questions) >= 30
    for question in questions:
        line = app.route_line(routing.classify(question).as_dict())
        for family in FAMILIES:
            assert f"{family} 질문" not in line


def test_an_empty_route_does_not_crash_the_caption():
    """추적이나 응답에 경로가 비어 올 수 있습니다. 화면이 터지는 것보다 덜 적는 것이 낫습니다."""
    assert "넓은 경로" in app.route_line({})


def test_the_route_is_written_before_the_model_is_called():
    """끝난 뒤 적으면 기다리는 동안 보이지 않습니다 - 이 줄의 목적이 그 기다림입니다."""
    source = Path("app.py").read_text(encoding="utf-8")
    body = source[source.index("def answer_question("):]

    assert body.index("route_line(") < body.index("chatbot.ask(")


def test_deciding_the_route_needs_no_model():
    """0원이라는 말이 지켜지는지 - routing이 모델이나 자격증명을 끌고 오면 거짓입니다."""
    source = Path("src/routing.py").read_text(encoding="utf-8")

    assert not re.search(r"^(import|from)\s+(openai|langchain|neo4j)", source, re.M)
    assert "chatbot" not in source


def test_the_after_the_fact_caption_is_a_different_line():
    """`route_marks`는 쓰인 도구를, `route_line`은 보낼 경로를 적습니다.

    섞으면 둘 다 거짓이 됩니다.
    """
    before = app.route_line(routing.classify("papers citing 2501.00001").as_dict())

    assert "Text2Cypher" not in before
    assert "선택 안 됨" not in before
