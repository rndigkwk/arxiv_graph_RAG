"""번역은 앱이 정합니다(`src/pre_translate.py`, `agent_graph`의 `translate` 노드, ADR-087).

- 영어가 아닌 글자가 있는지는 코드가 셉니다. 영어 질문은 번역하지 않습니다.
- 번역은 이번 턴의 메시지로 붙고, 질문 원문은 그대로입니다(판정과 기록은 원문을 봅니다).
- 결과는 도구 호출 기록 모양으로 붙어, 화면의 "한국어→영어 번역" 줄이 그대로 읽습니다.
- 앱 배선은 소스 모양으로 확인합니다. `chatbot`은 임포트만으로 Aura·OpenAI에 붙습니다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src import pre_translate, prompts
from tests.test_agent_graph import make_deps, nodes, run


@pytest.mark.parametrize("text", [
    "Which papers in this collection list an author named Evgenia Nitishinskaya?",
    "Which papers cite 2303.11366 in cs.AI since 2026-06-01?",
    "Papers by Jürgen Schmidhuber or Gaël Varoquaux",  # 악센트 붙은 라틴 문자
    "",
])
def test_an_english_question_is_not_translated(text):
    assert pre_translate.needs_translation(text) is False


@pytest.mark.parametrize("text", [
    "Hyperbolic Neural Networks와 저자를 공유하는 논문은?",
    "グラフニューラルネットワークの論文",
    "статьи о графах",
    "图神经网络",
])
def test_any_non_latin_letter_asks_for_a_translation(text):
    assert pre_translate.needs_translation(text) is True


def test_the_record_reads_like_the_tool_call_it_replaces():
    output = {"source_text": "질문", "english": "question"}
    record = pre_translate.call_record("질문", output, 1.23456)
    assert record["name"] == "translate_to_english" and record["by"] == "app"
    assert record["args"] == {"text": "질문"} and record["output"] is output
    assert record["status"] == "success" and record["seconds"] == 1.235
    assert "question" in pre_translate.hint(output)


def test_a_failed_translation_is_an_error_record_not_a_failed_answer():
    record = pre_translate.call_record("질문", RuntimeError("timeout"), 0.5)
    assert record["status"] == "error" and record["output"] == {"error": "timeout"}


# ── 그래프 노드 ─────────────────────────────────────────────────────────────


def with_translation(deps, result):
    calls = []

    def translate(question):
        calls.append(question)
        return result

    return replace(deps, translate=translate), calls


def test_the_translation_is_added_for_this_turn_before_the_answer_is_written():
    deps, _, generated = make_deps()
    record = pre_translate.call_record("질문", {"source_text": "질문", "english": "question"}, 1.0)
    deps, calls = with_translation(deps, {"record": record, "hint": "HINT"})
    state = run(deps, question="질문")

    assert calls == ["질문"]
    assert nodes(state)[:2] == ["translate", "route"]
    # 에이전트가 처음 받는 메시지에 번역이 있고, 질문 원문은 그대로입니다.
    assert ("user", "HINT") in generated[0] and ("user", "질문") in generated[0]
    assert state["question"] == "질문"
    response = state["response"]
    assert response["tool_calls"][0] is record


def test_an_english_question_passes_through_the_node_untouched():
    deps, _, generated = make_deps()
    deps, calls = with_translation(deps, None)
    state = run(deps, question="q")

    assert calls == ["q"]
    assert generated[0] == [("user", "q")]
    assert state["response"]["tool_calls"] == []


def test_without_a_translator_the_graph_is_the_same_as_before():
    deps, _, _ = make_deps()
    assert "translate" not in nodes(run(deps))


# ── 배선 ───────────────────────────────────────────────────────────────────

SOURCE = Path("src/chatbot.py").read_text(encoding="utf-8")


def test_the_model_can_no_longer_call_the_translator():
    listed = SOURCE[SOURCE.index("tools=[") + len("tools=["):]
    assert "translate_to_english" not in listed[:listed.index("]")]
    assert "call translate_to_english" not in prompts.AGENT_SYSTEM
    assert "translate=translate_first," in SOURCE
    assert "pre_translate.needs_translation(asked)" in SOURCE
