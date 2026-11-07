"""분류 코드와 주제를 함께 묻는 질문은 앱이 먼저 그 분류 안에서 찾습니다(`src/pre_category.py`,
`category` 노드, ADR-113).

- 그 질문인지는 코드가 가립니다. 지시문과 도구 스키마는 그대로입니다.
- 순위·집계·저자 질문은 주제 검색이 아니므로 건너뜁니다.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src import pre_category, pre_recent, pre_resolve, pre_translate, prompts
from tests.test_agent_graph import make_deps, nodes, run

FOUND = {"query": "q", "papers": [
    {"arxiv_id": "2608.16114", "title": 'A "quoted" title'},
    {"arxiv_id": "2607.04235", "title": "Another"},
]}


@pytest.mark.parametrize(("question", "english", "code"), [
    ("대규모 언어 모델 에이전트를 다루는 cs.CL 논문을 알려 줘",
     "cs.CL papers on large language model agents", "cs.CL"),
    ("Which cs.LG papers study diffusion models?", "Which cs.LG papers study diffusion models?",
     "cs.LG"),
    ("stat.ml papers about Bayesian optimization", "stat.ml papers about Bayesian optimization",
     "stat.ML"),
])
def test_a_topic_in_a_category_is_searched_in_that_category(question, english, code):
    assert pre_category.asks(question, english) == code


@pytest.mark.parametrize(("question", "english"), [
    # 화면 예시의 순위 질문입니다. `search_graph`가 셉니다.
    ("cs.AI 논문 중에서 피인용이 가장 많은 논문 5편은?",
     "What are the 5 most-cited papers in cs.AI?"),
    ("2026년에 나온 cs.AI 논문 중 피인용이 많은 3편은?",
     "What are the three most cited cs.AI papers published in 2026?"),
    ("cs.CL 분야에서 많이 인용된 논문", "Highly cited papers in cs.CL"),
    ("How many papers are in cs.CV?", "How many papers are in cs.CV?"),
    ("cs.CL papers by Yoshua Bengio", "cs.CL papers by Yoshua Bengio"),
    # 분류가 둘이면 어느 쪽으로 거를지 모델이 정합니다.
    ("cs.AI와 cs.CL에 걸친 에이전트 논문", "Agent papers spanning cs.AI and cs.CL"),
    # 분류가 없는 주제 질문은 지금처럼 모델이 `search_papers`를 부릅니다.
    ("지식 그래프 추론에 그래프 신경망을 쓰는 논문을 찾아 줘",
     "Find papers that use graph neural networks for knowledge graph reasoning."),
])
def test_rankings_authors_and_plain_topics_are_left_to_the_model(question, english):
    assert pre_category.asks(question, english) is None


def test_the_hint_names_the_category_and_the_papers():
    text = pre_category.hint(FOUND, "cs.CL")
    assert 'p.primary_category = "cs.CL"' in text
    assert "2608.16114 \"A 'quoted' title\"" in text
    assert "Do not call search_papers unless none of them fit" in text


def test_the_record_looks_like_an_app_search():
    record = pre_category.call_record("q", "cs.CL", FOUND, 0.4321)
    assert record["name"] == "search_papers" and record["by"] == "app"
    assert record["args"] == {"query": "q", "top_k": 5, "primary_category": "cs.CL"}
    assert record["seconds"] == 0.432


# ── 그래프 노드 ─────────────────────────────────────────────────────────────


def with_category(deps, result):
    calls = []

    def category(question, english):
        calls.append((question, english))
        return result

    return replace(deps, category=category), calls


def test_the_found_papers_are_handed_to_the_agent():
    deps, _, generated = make_deps()
    record = pre_category.call_record("q", "cs.CL", FOUND, 1.0)
    deps, calls = with_category(deps, {"record": record, "hint": "CATEGORY"})
    state = run(deps, question="q")

    assert calls == [("q", "q")]
    assert nodes(state)[:2] == ["category", "route"]
    assert ("user", "CATEGORY") in generated[0]
    assert state["response"]["tool_calls"][0] is record


def test_the_search_uses_the_english_translation():
    deps, _, _ = make_deps()
    translated = pre_translate.call_record("질문", {"source_text": "질문", "english": "question"},
                                           1.0)
    deps = replace(deps, translate=lambda question: {"record": translated, "hint": "EN"})
    deps, calls = with_category(deps, None)
    run(deps, question="질문")
    assert calls == [("질문", "question")]


def test_a_named_paper_or_a_recent_search_skips_it():
    """메시지를 둘 붙이지 않습니다. 두 힌트가 서로 다른 조회를 시킵니다."""
    deps, _, _ = make_deps()
    resolved = pre_resolve.call_record("q", {"arxiv_id": "2303.11366", "ambiguous": False,
                                             "candidates": []}, 0.1)
    named = replace(deps, resolve=lambda question: {"record": resolved, "hint": "ID"})
    named, calls = with_category(named, {"record": {}, "hint": "CATEGORY"})
    run(named, question="q")
    assert calls == []

    recent = pre_recent.call_record("q", FOUND, 1.0)
    dated = replace(deps, recent=lambda q, en: {"record": recent, "hint": "RECENT"})
    dated, calls = with_category(dated, {"record": {}, "hint": "CATEGORY"})
    state = run(dated, question="q")
    assert calls == [] and nodes(state)[:3] == ["recent", "category", "route"]


def test_a_category_search_that_found_papers_skips_the_scope_check():
    deps, _, _ = make_deps()
    scoped = []
    record = pre_category.call_record("q", "cs.CL", FOUND, 1.0)
    deps = replace(deps, category=lambda q, en: {"record": record, "hint": "CATEGORY"},
                   scope=lambda q, en: scoped.append(q))
    run(deps, question="q")
    assert scoped == []


def test_the_prompt_and_the_tool_are_unchanged():
    """ADR-090: 인자 하나와 지시문 몇 줄이 모든 질문의 턴을 느리게 했습니다."""
    assert "primary_category" not in prompts.AGENT_SYSTEM.split("Cypher")[0]
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    assert "def search_papers(query: str, top_k: int = 5) -> dict:" in source
    assert 'filters={"primary_category": found}' in source
    assert "category=category_first," in source
