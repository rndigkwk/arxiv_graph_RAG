"""긴 논문 목록을 10행으로 줄이기(ADR-120). 모델 없이 돕니다."""

from __future__ import annotations

from src import row_cap


def paper(n: int) -> dict:
    return {"title": f"T{n}", "evidence_ids": [f"2601.{n:05d}"]}


def test_a_long_paper_list_keeps_the_first_ten_in_order():
    rows, omitted = row_cap.cap_paper_rows([paper(n) for n in range(25)])
    assert [r["title"] for r in rows] == [f"T{n}" for n in range(10)]
    assert omitted == 15


def test_a_short_list_is_untouched():
    rows = [paper(n) for n in range(10)]
    assert row_cap.cap_paper_rows(rows) == (rows, 0)


def test_aggregate_rows_are_never_dropped():
    """분류별 편수 같은 집계 행은 짧고, 빼면 합계가 틀립니다."""
    counts = [{"answer_value": str(n), "evidence_ids": []} for n in range(30)]
    assert row_cap.cap_paper_rows(counts) == (counts, 0)
    mixed = counts[:2] + [paper(n) for n in range(12)]
    rows, omitted = row_cap.cap_paper_rows(mixed)
    assert rows[:2] == counts[:2] and len(rows) == 12 and omitted == 2
