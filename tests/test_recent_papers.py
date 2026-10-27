""""최근" 논문은 앱이 먼저 기간 안에서 찾습니다(`src/pre_recent.py`, `recent` 노드, ADR-090).

- "최근"인지는 코드가 가립니다. 그 질문에만 메시지를 붙이고, 지시문과 도구 스키마는 그대로입니다.
- 날짜는 검색 **안에서** 거릅니다. 앱 배선은 소스 모양으로 확인합니다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import replace
from pathlib import Path

import pytest

from src import pre_recent, pre_resolve, pre_translate, prompts
from tests.test_agent_graph import make_deps, nodes, run

NOW = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)
FOUND = {"query": "rlhf", "papers": [
    {"arxiv_id": "2602.02482", "title": 'A "quoted" title', "published": "2026-02-02"},
    {"arxiv_id": "2511.12796", "title": "Another", "published": None},
]}


@pytest.mark.parametrize("question", [
    "Recommend recent papers on molecular property prediction",
    "What are the latest papers on diffusion models?",
    "Show me new work on LLM agents",
    "최근에 나온 RLHF 관련 논문 알려줘",
    "RAG에 관한 최신 연구는?",
])
def test_recent_questions_are_recognised(question):
    assert pre_recent.asks_recent(question)


@pytest.mark.parametrize("question", [
    "Which papers cite the paper titled X?",
    "Find papers about graph neural networks",
    "그래프 신경망으로 분자 특성을 예측하는 논문 추천해줘",
    # "new"만으로는 걸지 않습니다. "a new method"는 기간이 아닙니다.
    "Papers proposing a new method for retrieval",
])
def test_other_questions_are_left_alone(question):
    assert not pre_recent.asks_recent(question)


def test_the_window_stays_inside_the_cypher_check():
    """앱이 찾은 논문은 힌트의 `duration("P12M")` 조회를 모두 통과해야 합니다."""
    start = pre_recent.since(now=NOW)
    assert start == NOW - dt.timedelta(days=360)
    assert start > NOW.replace(year=NOW.year - 1)


def test_the_hint_lists_the_papers_and_the_same_window():
    hint = pre_recent.hint(FOUND)
    assert '2602.02482 "A \'quoted\' title" (2026-02-02)' in hint
    assert "(no date)" in hint
    assert 'duration("P12M")' in hint and "last 12 months" in hint


def test_an_empty_search_is_not_used():
    assert not pre_recent.usable({"query": "q", "papers": []})
    assert not pre_recent.usable(None)
    assert pre_recent.usable(FOUND)


def test_the_record_reads_like_the_tool_call_it_replaces():
    record = pre_recent.call_record("rlhf", FOUND, 1.23456)
    assert record["name"] == "search_papers" and record["by"] == "app"
    assert record["args"]["published_within_months"] == 12
    assert record["seconds"] == 1.235


# ── 그래프 노드 ─────────────────────────────────────────────────────────────


def with_recent(deps, result):
    calls = []

    def recent(question, english):
        calls.append((question, english))
        return result

    return replace(deps, recent=recent), calls


def test_the_found_papers_are_handed_to_the_agent():
    deps, _, generated = make_deps()
    record = pre_recent.call_record("q", FOUND, 1.0)
    deps, calls = with_recent(deps, {"record": record, "hint": "RECENT"})
    state = run(deps, question="q")

    assert calls == [("q", "q")]
    assert nodes(state)[:2] == ["recent", "route"]
    assert ("user", "RECENT") in generated[0]
    assert state["response"]["tool_calls"][0] is record


def test_the_search_uses_the_english_translation():
    deps, _, _ = make_deps()
    translated = pre_translate.call_record("최근 질문", {"source_text": "최근 질문",
                                                     "english": "recent question"}, 1.0)
    deps = replace(deps, translate=lambda question: {"record": translated, "hint": "EN"})
    deps, calls = with_recent(deps, None)
    run(deps, question="최근 질문")
    assert calls == [("최근 질문", "recent question")]


def test_a_question_naming_a_paper_skips_the_search():
    """제목 해석이 찾았으면 "최근"은 제목의 낱말이거나 그 논문의 관계에 걸린 조건입니다."""
    deps, _, _ = make_deps()
    resolved = pre_resolve.call_record("q", {"arxiv_id": "2303.11366", "ambiguous": False,
                                             "candidates": []}, 0.1)
    deps = replace(deps, resolve=lambda question: {"record": resolved, "hint": "ID"})
    deps, calls = with_recent(deps, {"record": {}, "hint": "RECENT"})
    state = run(deps, question="q")
    assert calls == []
    assert nodes(state)[:3] == ["resolve", "recent", "route"]


def test_the_records_keep_the_order_things_happened():
    deps, _, _ = make_deps()
    translated = pre_translate.call_record("질문", {"source_text": "질문", "english": "q"}, 1.0)
    found = pre_recent.call_record("q", FOUND, 1.0)
    deps = replace(deps, translate=lambda question: {"record": translated, "hint": "EN"},
                   resolve=lambda question: None,
                   recent=lambda question, english: {"record": found, "hint": "RECENT"})
    state = run(deps, question="질문")
    assert nodes(state)[:4] == ["translate", "resolve", "recent", "route"]
    assert state["response"]["tool_calls"][:2] == [translated, found]


def test_without_the_step_the_graph_is_the_same_as_before():
    deps, _, _ = make_deps()
    assert "recent" not in nodes(run(deps))


# ── 배선 ───────────────────────────────────────────────────────────────────

SOURCE = Path("src/chatbot.py").read_text(encoding="utf-8")


def test_the_app_filters_inside_the_vector_search():
    assert "recent=recent_first," in SOURCE
    assert 'filters={"published": {"$gte": pre_recent.since()}}' in SOURCE


def test_the_model_sees_the_same_prompt_and_tool_schema():
    """인자 하나와 지시문 다섯 줄이 모든 질문의 턴을 0.5~1초 느리게 했습니다(ADR-090)."""
    assert "recent" not in prompts.AGENT_SYSTEM.lower()
    assert "def search_papers(query: str, top_k: int = 5) -> dict:" in SOURCE
    assert "published_within_months" not in SOURCE[
        SOURCE.index("def search_papers"):SOURCE.index("# ── 최종 출력 형식")]


def test_results_carry_the_publication_date():
    assert '"published": published_date(node.get("published"))' in SOURCE
    assert '"published", "pdf_url"]' in SOURCE
