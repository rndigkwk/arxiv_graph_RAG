"""ADR-090: "최근"은 발표일 조건이고, 날짜는 의미 검색 **안에서** 거릅니다."""

import datetime as dt

import pytest

try:
    from src import chatbot
except Exception as exc:  # CI의 `test` job에는 `.env`가 없습니다.
    pytest.skip(f"chatbot을 가져올 수 없습니다: {type(exc).__name__}", allow_module_level=True)

from src import prompts

NOW = dt.datetime(2026, 10, 28, tzinfo=dt.UTC)


def test_no_months_means_no_date_filter():
    assert chatbot.published_since(0, NOW) is None
    assert chatbot.published_since(None, NOW) is None
    assert chatbot.published_since(-3, NOW) is None


def test_twelve_months_stays_inside_the_cypher_check():
    """검색이 찾은 논문은 지시문의 `duration("P12M")` 조회를 모두 통과해야 합니다."""
    since = chatbot.published_since(12, NOW)
    assert since == NOW - dt.timedelta(days=360)
    assert since > NOW.replace(year=NOW.year - 1)


def test_months_are_capped():
    assert chatbot.published_since(10_000, NOW) == NOW - dt.timedelta(days=3600)


def test_search_filters_inside_the_vector_search(monkeypatch):
    seen = {}

    class Retriever:
        def search(self, **kwargs):
            seen.update(kwargs)
            return type("R", (), {"items": []})()

    monkeypatch.setattr(chatbot, "vector_retriever", Retriever())
    chatbot.search_papers.invoke({"query": "rlhf", "published_within_months": 12})
    assert set(seen["filters"]) == {"published"}
    assert "$gte" in seen["filters"]["published"]
    # 대조: 개월을 주지 않으면 거르지 않습니다. 주제 질문 전부가 좁아지면 안 됩니다.
    chatbot.search_papers.invoke({"query": "rlhf"})
    assert seen["filters"] is None


def test_results_carry_the_publication_date():
    record = {"score": 0.8, "node": {
        "arxiv_id": "2602.02482", "title": "t", "abstract": "a", "primary_category": "cs.LG",
        "categories": ["cs.LG"], "pdf_url": "u",
        "published": dt.datetime(2026, 2, 2, tzinfo=dt.UTC)}}
    assert chatbot.to_item(record).metadata["published"] == "2026-02-02"
    # 발표일이 없는 논문이 있습니다(약 2%).
    record["node"]["published"] = None
    assert chatbot.to_item(record).metadata["published"] is None


def test_the_rule_names_the_same_window_in_both_tools():
    rule = prompts.AGENT_SYSTEM
    assert "published_within_months=12" in rule
    assert 'duration("P12M")' in rule
