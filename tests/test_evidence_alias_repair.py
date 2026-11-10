"""모양만 어긋난 근거 별칭을 고쳐 받기(ADR-122). 모델 없이 돕니다."""

from __future__ import annotations

from src import evidence_alias as e


def test_a_single_id_string_becomes_a_list():
    """2026-11-10 coauthor-01에서 관측된 모양 - `p.arxiv_id AS evidence_ids`."""
    rows = e.repair([{"answer_value": "2303.14382", "evidence_ids": "2303.14382"}])
    assert rows == [{"answer_value": "2303.14382", "evidence_ids": ["2303.14382"]}]


def test_the_singular_alias_is_renamed_when_it_holds_ids():
    assert e.repair([{"evidence_id": "2606.08098", "title": "T"}]) == [
        {"title": "T", "evidence_ids": ["2606.08098"]}]
    assert e.repair([{"evidence_id": ["2601.00001", "2601.00002"]}]) == [
        {"evidence_ids": ["2601.00001", "2601.00002"]}]


def test_old_style_and_versioned_ids_count():
    for value in ("adap-org/9708006", "math.AG/0101001", "cs/0101001v2", "2410.01234v3"):
        assert e.repair([{"evidence_ids": value}]) == [{"evidence_ids": [value]}]


def test_values_that_are_not_ids_are_left_for_the_error():
    """한 편인지 여러 편인지 모르는 값은 고치지 않습니다(ADR-019의 걱정)."""
    for value in ("2601.00001, 2601.00002", "Attention Is All You Need", "", 3):
        assert e.repair([{"evidence_ids": value}]) == [{"evidence_ids": value}]
    assert e.repair([{"evidence_id": "Some title"}]) == [{"evidence_id": "Some title"}]


def test_a_missing_alias_is_not_guessed():
    """논문 행인지 집계 행인지 모릅니다. `arxiv_id`가 있어도 근거로 삼지 않습니다."""
    row = {"arxiv_id": "2601.1", "title": "T"}
    assert e.repair([row]) == [row]


def test_good_rows_are_untouched():
    rows = [{"evidence_ids": ["2601.00001"]}, {"answer_value": "37854", "evidence_ids": []}]
    assert e.repair(rows) == rows
