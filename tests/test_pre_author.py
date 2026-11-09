"""저자 이름이 그래프에 딱 맞게 없으면 앱이 후보를 먼저 알려 줍니다(`src/pre_author.py`,
`author` 노드, ADR-121).

- 저자를 묻는 질문인지, 이름이 무엇인지는 코드가 가립니다. 지시문과 도구 스키마는 그대로입니다.
- 딱 맞는 이름이 있으면 아무것도 하지 않습니다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src import pre_author, pre_resolve
from tests.test_agent_graph import make_deps, nodes, run

BENGIO = [{"name": "Samy Bengio", "papers": 21}, {"name": "Yoshua Bengio", "papers": 95},
          {"name": "Emmanuel Bengio", "papers": 4}]


@pytest.mark.parametrize(("question", "english"), [
    ("Bengio가 쓴 논문이 있어?", "Are there papers written by Bengio?"),
    ("Yang Liu와 가장 많이 함께 쓴 공저자는 누구야?",
     "Who has co-authored the most with Yang Liu?"),
    ("Papers by LeCun", "Papers by LeCun"),
])
def test_author_questions_are_seen(question, english):
    assert pre_author.asks_author(question, english)


@pytest.mark.parametrize(("question", "english"), [
    ("Attention Is All You Need를 인용한 논문을 알려 줘",
     "Tell me the papers that cite Attention Is All You Need."),
    ("cs.AI 논문 중에서 피인용이 가장 많은 논문 5편은?",
     "What are the 5 most-cited papers in cs.AI?"),
])
def test_other_questions_are_left_alone(question, english):
    assert not pre_author.asks_author(question, english)


def test_names_drop_sentence_words_and_survive_korean_particles():
    assert pre_author.names("Bengio가 쓴 논문이 있어?", "Are there papers written by Bengio?") \
        == ["Bengio"]
    assert pre_author.names("", "Papers by Yoshua Bengio") == ["Yoshua Bengio"]
    assert pre_author.names("", "Find papers by LeCun.") == ["LeCun"]


def test_an_exact_name_needs_no_hint():
    assert pre_author.unresolved("yoshua bengio", BENGIO) == []
    assert pre_author.unresolved("Nobody", []) == []


def test_partial_names_are_listed_by_paper_count():
    candidates = pre_author.unresolved("Bengio", BENGIO)
    assert [c["name"] for c in candidates] == ["Yoshua Bengio", "Samy Bengio", "Emmanuel Bengio"]
    text = pre_author.hint("Bengio", candidates)
    assert "Yoshua Bengio (95); Samy Bengio (21); Emmanuel Bengio (4)" in text
    assert 'query Yoshua Bengio with a.name = "Yoshua Bengio"' in text
    assert "Do not use CONTAINS" in text and "ignore this message" in text


def test_a_long_candidate_list_is_cut():
    many = [{"name": f"A{n} Lee", "papers": n} for n in range(8)]
    text = pre_author.hint("Lee", pre_author.unresolved("Lee", many))
    assert "A7 Lee (7); A6 Lee (6)" in text and "A2 Lee" not in text and "and 3 more" in text


def test_the_phrase_cannot_break_out_of_the_query():
    assert pre_author.lucene_phrase('Ben"gio\\') == '"Ben gio "'


# ── 그래프 노드 ─────────────────────────────────────────────────────────────


def with_author(deps, result):
    calls = []

    def author(question, english):
        calls.append((question, english))
        return result

    return replace(deps, author=author), calls


def test_the_candidates_are_handed_to_the_agent_and_skip_the_scope_check():
    deps, _, generated = make_deps()
    record = pre_author.call_record("Bengio", pre_author.unresolved("Bengio", BENGIO), 0.2)
    scoped = []
    deps = replace(deps, scope=lambda q, en: scoped.append(q))
    deps, calls = with_author(deps, {"record": record, "hint": "AUTHOR"})
    state = run(deps, question="q")
    assert calls == [("q", "q")]
    assert nodes(state)[:2] == ["author", "scope"]
    assert ("user", "AUTHOR") in generated[0]
    assert state["response"]["tool_calls"][0] is record
    assert scoped == []


def test_a_named_paper_skips_it():
    """제목 낱말이 저자 이름 후보로 잡히므로, 논문을 지칭한 질문은 보지 않습니다."""
    deps, _, _ = make_deps()
    resolved = pre_resolve.call_record("q", {"arxiv_id": "1706.03762", "ambiguous": False,
                                             "candidates": []}, 0.1)
    deps = replace(deps, resolve=lambda question: {"record": resolved, "hint": "ID"})
    deps, calls = with_author(deps, {"record": {}, "hint": "AUTHOR"})
    run(deps, question="q")
    assert calls == []
