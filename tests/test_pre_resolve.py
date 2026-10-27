"""질문이 지칭한 논문은 앱이 먼저 찾습니다(`src/pre_resolve.py`, `resolve` 노드, ADR-088).

- 확실할 때만 씁니다. 못 찾음·약한 일치·애매함은 힌트를 붙이지 않고 모델에게 맡깁니다.
- 결과는 도구 호출 기록 모양으로 붙고, 근거 허용 목록(`resolved`)과 관련 논문 시드가 읽습니다.
- 앱 배선은 소스 모양으로 확인합니다. `chatbot`은 임포트만으로 DB·OpenAI에 붙습니다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src import pre_resolve, pre_translate, prompts, related_by_app
from src.answer_validation import direct_evidence_ids
from tests.test_agent_graph import make_deps, nodes, run

FOUND = {
    "query": "q", "arxiv_id": "2303.11366", "method": "exact_substring", "ambiguous": False,
    "candidates": [{"arxiv_id": "2303.11366", "title": 'Reflexion: "verbal" RL'}],
}


@pytest.mark.parametrize("output", [
    FOUND,
    {**FOUND, "method": "fulltext"},
])
def test_a_confident_resolution_is_used(output):
    assert pre_resolve.usable(output) is True


@pytest.mark.parametrize("output", [
    {**FOUND, "ambiguous": True},                     # 1·2위가 비슷합니다 - 모델이 고릅니다
    {**FOUND, "arxiv_id": None, "method": "weak_match"},  # 제목이 아닌 질문
    {**FOUND, "arxiv_id": None, "method": "none"},
    RuntimeError("neo4j down"),
    None,
])
def test_anything_less_than_confident_is_left_to_the_model(output):
    assert pre_resolve.usable(output) is False


def test_the_hint_names_the_id_and_the_title_without_breaking_quotes():
    hint = pre_resolve.hint(FOUND)
    assert "2303.11366" in hint and "do not call resolve_paper" in hint
    assert "'verbal'" in hint and '"verbal"' not in hint


def test_the_record_reads_like_the_tool_call_it_replaces():
    record = pre_resolve.call_record("q", FOUND, 0.12345)
    assert record["name"] == "resolve_paper" and record["by"] == "app"
    assert record["status"] == "success" and record["output"] is FOUND
    assert record["args"] == {"title": "q"} and record["seconds"] == 0.123
    # 관련 논문 시드가 이 기록을 모델의 호출과 똑같이 읽습니다.
    assert related_by_app.seeds([record], ["x", "y"]) == ["2303.11366"]


# ── 그래프 노드 ─────────────────────────────────────────────────────────────


def with_resolution(deps, result):
    calls = []

    def resolve(question):
        calls.append(question)
        return result

    return replace(deps, resolve=resolve), calls


def test_the_resolved_paper_is_handed_to_the_agent_and_kept_as_evidence():
    deps, _, generated = make_deps()
    record = pre_resolve.call_record("q", FOUND, 0.1)
    deps, calls = with_resolution(deps, {"record": record, "hint": "HINT"})
    state = run(deps, question="q")

    assert calls == ["q"]
    assert nodes(state)[:2] == ["resolve", "route"]
    assert ("user", "HINT") in generated[0] and ("user", "q") in generated[0]
    response = state["response"]
    assert response["tool_calls"][0] is record
    # 질문이 지칭한 논문은 근거로 인용할 수 있습니다 - 모델이 찾았을 때와 같습니다.
    assert response["resolved"][0] is FOUND
    assert "2303.11366" in direct_evidence_ids(response)


def test_an_unresolved_question_passes_through_untouched():
    deps, _, generated = make_deps()
    deps, calls = with_resolution(deps, None)
    state = run(deps, question="q")

    assert calls == ["q"]
    assert generated[0] == [("user", "q")]
    assert state["response"]["tool_calls"] == []


def test_translation_comes_first_and_the_records_keep_that_order():
    deps, _, generated = make_deps()
    translated = pre_translate.call_record("질문", {"source_text": "질문", "english": "q"}, 1.0)
    resolved = pre_resolve.call_record("질문", FOUND, 0.1)
    deps = replace(deps, translate=lambda question: {"record": translated, "hint": "EN"},
                   resolve=lambda question: {"record": resolved, "hint": "ID"})
    state = run(deps, question="질문")

    assert nodes(state)[:3] == ["translate", "resolve", "route"]
    assert generated[0][-2:] == [("user", "EN"), ("user", "ID")]
    assert state["response"]["tool_calls"][:2] == [translated, resolved]


def test_without_a_resolver_the_graph_is_the_same_as_before():
    deps, _, _ = make_deps()
    assert "resolve" not in nodes(run(deps))


# ── 배선 ───────────────────────────────────────────────────────────────────

SOURCE = Path("src/chatbot.py").read_text(encoding="utf-8")


def test_the_app_resolves_first_and_the_model_is_told_so():
    assert "resolve=resolve_first," in SOURCE
    assert 'resolve_paper.invoke({"title": asked})' in SOURCE
    assert "pre_resolve.usable(output)" in SOURCE
    assert "already resolved the paper the question names" in prompts.AGENT_SYSTEM
    # 확실하지 않을 때를 위해 모델의 도구로도 남습니다.
    listed = SOURCE[SOURCE.index("tools=[") + len("tools=["):]
    assert "resolve_paper" in listed[:listed.index("]")]
