"""용량 여유를 **편수**로 말하게 지킵니다(ADR-045의 2026-10-24 덧붙임).

ADR-045가 자리를 4,657편으로 맞게 셌는데 그것을 **스냅샷 말뭉치의 증가분**(1,364편/월)으로
나눠 "3.4개월"을 적었습니다. 파이프라인이 실제로 받는 양은 그 3배여서 같은 자리가 **약
1.1개월**입니다.

두 수를 같은 이름("월 생산량")으로 부른 것이 어긋남의 자리입니다. 이 파일이 지키는 것은
**그 둘이 다르다는 사실이 기록에 남아 있는지**입니다 - 한쪽이 조용히 지워지면 "3.4개월"이
다시 planning 수치로 읽힙니다.
"""

import json
from pathlib import Path

import pytest

ADR = Path("docs/adr/ADR-045-what-to-delete-before-adding-a-month.md")
CAPACITY = Path("reports/capacity.json")
WEEKLY = Path("reports/ingest_weekly_window.json")


@pytest.fixture(scope="module")
def reports():
    for path in (CAPACITY, WEEKLY):
        if not path.exists():
            pytest.skip(f"보고서를 읽을 수 없습니다: {path}")
    return (
        json.loads(CAPACITY.read_text(encoding="utf-8")),
        json.loads(WEEKLY.read_text(encoding="utf-8")),
    )


def measured_rate(weekly):
    """파이프라인이 받는 월 환산 편수입니다. 창 길이로 나눠 30일로 맞춥니다."""
    from datetime import date

    window = weekly["window"]
    days = (date.fromisoformat(window["until"]) - date.fromisoformat(window["from"])).days
    return weekly["embeddings"]["new_papers"] * 30 / days


def test_the_two_rates_really_differ(reports):
    """다르지 않다면 이 기록 전체가 필요 없습니다. 먼저 그것을 확인합니다."""
    capacity, weekly = reports
    snapshot_rate = capacity["rate"]["papers_per_month"]

    assert measured_rate(weekly) > snapshot_rate * 2, (
        measured_rate(weekly), snapshot_rate
    )


def test_the_seat_count_is_the_number_both_agree_on(reports):
    """자리(편수)는 어긋나지 않습니다. 어긋나는 것은 그것을 나누는 값입니다."""
    capacity, _ = reports
    seats = capacity["headroom"]["within_safety_ceiling"]["papers_that_fit"]

    assert seats == 4657, seats
    assert "4,657편" in ADR.read_text(encoding="utf-8")


def test_the_adr_records_the_months_both_ways(reports):
    """**이 테스트가 이 파일의 이유입니다.** 한쪽만 남으면 거짓이 됩니다."""
    capacity, weekly = reports
    seats = capacity["headroom"]["within_safety_ceiling"]["papers_that_fit"]
    text = ADR.read_text(encoding="utf-8")

    # 스냅샷 기준(원래 적힌 값)과 실측 기준이 **둘 다** 있어야 합니다.
    assert "3.4개월" in text
    assert f"약 {seats / measured_rate(weekly):.1f}개월" in text


def test_the_adr_does_not_erase_the_wrong_number(reports):
    """기각한 결정을 지우지 않고 뒤집힘으로 표시하는 것이 이 저장소의 선입니다."""
    text = ADR.read_text(encoding="utf-8")

    assert "쓸 수 없는 수입니다" in text
    # 그리고 무엇이 맞았는지도 적혀 있어야 합니다 - 결정 1번은 뒤집히지 않았습니다.
    assert "결정 1번은 그대로 맞습니다" in text


def test_the_density_estimate_erred_the_safe_way(reports):
    """ADR-045가 걱정한 축(밀도)은 안전한 쪽으로 틀렸습니다. 그 사실도 기록입니다."""
    capacity, weekly = reports
    per_paper = capacity["rate"]["relationships_per_paper"]
    cap = weekly["capacity"]
    measured = (
        cap["projected_relationships"] - cap["current_relationships"]
    ) / weekly["embeddings"]["new_papers"]

    assert measured < per_paper, (measured, per_paper)
    assert "안전한 쪽으로 틀렸고" in ADR.read_text(encoding="utf-8")


def test_the_deletion_decision_moved_earlier(reports):
    """AUTHORED 접기를 재야 하는 때가 네 번째 달에서 두 번째 달로 당겨졌습니다."""
    # **공백을 모읍니다.** 문장이 줄바꿈과 인용 표시(`>`)를 넘나들어, 그대로 찾으면
    # 줄폭을 바꾸는 것만으로 테스트가 깨집니다.
    flat = " ".join(ADR.read_text(encoding="utf-8").replace(">", " ").split())

    assert "네 번째 달이 아니라 두 번째 달" in flat
