"""최신 답변만 세 칸 작업대로 펼치고, 지난 답변은 접습니다(ADR-076).

화면은 다시 그릴 때마다 대화 기록을 전부 다시 그립니다. 지난 답변까지 펼치면 답변마다 그래프·
인용 경로 조회가 반복되고 스크롤이 답변 수만큼 길어집니다.
"""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

import app

SOURCE = Path("app.py").read_text(encoding="utf-8")


def conversation(pairs):
    messages = []
    for number in range(pairs):
        messages += [{"role": "user", "content": f"질문 {number}"},
                     {"role": "assistant", "response": {"answer": f"답 {number}"}}]
    return messages


def test_the_latest_answer_is_the_workbench_by_default():
    assert app.workbench_index(conversation(3), None) == 5


def test_a_chosen_past_answer_takes_the_workbench():
    assert app.workbench_index(conversation(3), 1) == 1


@pytest.mark.parametrize("focus", [0, 2, 99, "1"])
def test_a_focus_that_is_not_an_answer_falls_back_to_the_latest(focus):
    """질문 칸이나 지운 대화의 위치를 고른 채로 남아 있어도 화면이 비지 않습니다."""
    assert app.workbench_index(conversation(2), focus) == 3


def test_an_empty_conversation_has_no_workbench():
    assert app.workbench_index([], None) is None


def test_a_folded_answer_is_titled_with_its_question():
    assert app.question_before(conversation(3), 3) == "질문 1"


@pytest.fixture
def drawn(monkeypatch):
    """화면을 실제로 돌려, 어느 답변이 어떻게 그려졌는지 기록합니다."""
    calls = []
    monkeypatch.setattr(app, "show_response", lambda c, r, i: calls.append(("full", i)))
    monkeypatch.setattr(app, "show_past_response",
                        lambda q, r, i: calls.append(("past", i, q)))
    monkeypatch.setattr(app, "show_saved_papers", lambda chatbot: None)
    monkeypatch.setattr(app, "citation_warmup", lambda chatbot: SimpleNamespace(
        state=lambda: {"status": app.warmup.DONE, "seconds": 1.0}))
    return calls


def page(app_module, messages, focus):
    import streamlit as st

    if "messages" not in st.session_state:
        st.session_state.messages = messages
        st.session_state.history, st.session_state.korean = [], {}
        st.session_state.pending, st.session_state.focus = None, focus
    app_module.chat(object())


def test_only_one_answer_is_drawn_in_full(drawn):
    AppTest.from_function(page, args=(app, conversation(3), None)).run()
    assert drawn == [("past", 1, "질문 0"), ("past", 3, "질문 1"), ("full", 5)]


def test_the_chosen_answer_is_drawn_in_full_and_the_latest_is_folded(drawn):
    AppTest.from_function(page, args=(app, conversation(3), 1)).run()
    assert drawn == [("full", 1), ("past", 3, "질문 1"), ("past", 5, "질문 2")]


def test_a_new_answer_and_clearing_return_the_workbench_to_the_latest():
    for name in ("answer_question", "clear_chat"):
        body = ast.get_source_segment(SOURCE, next(
            node for node in ast.parse(SOURCE).body
            if isinstance(node, ast.FunctionDef) and node.name == name))
        assert "st.session_state.focus = None" in body, name
        if name == "answer_question":
            # 다시 그리지 않으면 이 실행에서는 앞 답변이 접히지 않습니다.
            assert body.rstrip().endswith("st.rerun()")


def test_a_folded_answer_does_not_reach_the_database():
    """접힌 패널도 안쪽은 실행됩니다. 그래서 안쪽에서 조회하면 접은 의미가 없습니다."""
    body = ast.get_source_segment(SOURCE, next(
        node for node in ast.parse(SOURCE).body
        if isinstance(node, ast.FunctionDef) and node.name == "show_past_response"))
    for reaches in ("subgraph_of", "chatbot.", "show_response", "show_answer_column"):
        assert reaches not in body, reaches


def test_the_workbench_has_three_columns_and_the_cypher_is_not_folded():
    body = ast.get_source_segment(SOURCE, next(
        node for node in ast.parse(SOURCE).body
        if isinstance(node, ast.FunctionDef) and node.name == "show_evidence_column"))
    assert len(app.WORKBENCH) == 3
    assert body.index('st.code(response["cypher"]') < body.index("st.expander(")


def test_unlinked_papers_are_stacked_in_a_grid_not_one_row():
    """인용으로 이어지지 않은 논문이 한 줄로 늘어서면 가운데 칸에서 글자를 읽을 수 없었습니다."""
    papers = [{"arxiv_id": f"2601.0000{n}", "title": f"Paper {n}", "primary_category": "cs.AI",
               "authors": [], "author_count": 0} for n in range(8)]
    dot = app.make_graph({"papers": papers, "references": []})
    assert "pack=true;" in dot and 'packmode="array_3";' in dot
