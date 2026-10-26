"""품질 게이트 탭의 분야별 표가 기준선의 세 실행과 맞고, 표본 크기를 감추지 않는지 봅니다."""

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from src import family_board

STABLE = json.loads(Path("baselines/eval_baseline.json").read_text(encoding="utf-8"))[
    "ragas_stable_families"]


def test_every_family_has_six_cases_and_three_runs():
    rows = family_board.load_rows(STABLE)
    assert [row["family"] for row in rows] == [
        "author", "citing", "coauthor", "reference", "topcited"]
    assert {(row["cases"], row["runs"]) for row in rows} == {(6, 3)}


def test_the_exact_metrics_are_at_the_ceiling_and_the_board_says_so():
    """천장에 붙은 것을 '분야별로 다 좋다'로 읽히게 두지 않습니다."""
    rows = family_board.load_rows(STABLE)
    assert family_board.at_ceiling(rows)
    rows[0]["hit_at_k"] = 0.8333
    assert not family_board.at_ceiling(rows)


def test_only_the_stable_families_are_marked_as_gated():
    rows = {row["family"]: row for row in family_board.load_rows(STABLE)}
    assert {name for name, row in rows.items() if row["gated_context_precision"]} == set(STABLE)


def test_a_judged_metric_keeps_the_spread_of_its_runs():
    run = {"results": [{"id": "a-1", "category": "a", "graph_rag": {"hit_at_k": 1.0}}]}
    ragas = [{"summary": {"by_family": {"a": {"faithfulness": value}}}} for value in (0.9, 1.0)]
    row = family_board.family_rows([run, run], ragas, [])[0]
    assert row["faithfulness"] == 0.95
    assert row["faithfulness_range"] == (0.9, 1.0)
    assert row["context_precision"] is None


def test_missing_reports_draw_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(family_board, "RUNS", (tmp_path / "none.json",))
    assert family_board.load_rows(STABLE) is None


def test_the_quality_tab_draws_the_family_table():
    def script():
        import app

        app.show_quality_board()

    at = AppTest.from_function(script, default_timeout=60).run()
    assert not at.exception
    assert any("분야별" in md.value for md in at.markdown)
    assert any("다섯 분야 모두 1.0" in caption.value for caption in at.caption)
