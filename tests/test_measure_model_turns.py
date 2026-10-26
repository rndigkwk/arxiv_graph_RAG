"""줄일 수 있는 턴을 세는 규칙이 순서가 필요한 문항을 세지 않는지 봅니다."""

import json
from pathlib import Path

from scripts import measure_model_turns as measure


def row(case, names, turns):
    return {"id": case, "tool_names": names,
            "generate_trace": {"tool_calls": len(names), "model_turns": turns}}


def test_a_resolved_paper_lets_the_pair_share_a_turn():
    assert measure.separate_pair(
        row("citing-01", ["resolve_paper", "search_graph", "rank_related_papers"], 4))


def test_the_pair_already_in_one_turn_is_not_counted_again():
    """citing-02: 도구 셋, 턴 셋 - 이미 한 턴에 둘을 불렀습니다."""
    assert not measure.separate_pair(
        row("citing-02", ["resolve_paper", "search_graph", "rank_related_papers"], 3))


def test_without_resolve_paper_the_order_is_needed():
    """저자 질문은 search_graph가 찾은 논문을 rank_related_papers에 넣습니다."""
    assert not measure.separate_pair(
        row("author-02", ["translate_to_english", "search_graph", "rank_related_papers"], 4))


def test_the_saved_summary_matches_its_report():
    saved = json.loads(Path("reports/model_turns.json").read_text(encoding="utf-8"))
    report = json.loads(Path(saved["source"]).read_text(encoding="utf-8"))
    fresh = measure.summarize(report)
    assert fresh == saved
    assert saved["separate_pair"]["cases"] == 17
