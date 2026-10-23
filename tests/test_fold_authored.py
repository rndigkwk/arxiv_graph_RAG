"""AUTHORED 접기 측정이 **재려던 것을 재는지** 지킵니다(ADR-063).

이 측정은 한 번 다른 것을 재고 있었습니다. 대리 질의가 21,183행을 돌려주는 동안 진짜 질의는
2~3행이어서, 18.2배·34.9배라는 수에 **행을 돌려주고 정렬하는 비용**이 섞였습니다 — 접은
뒤에는 일어나지 않는 일입니다. 행을 맞추자 1.12배·1.52배로 **판정이 뒤집혔습니다.**

그래서 이 파일이 지키는 것은 결론이 아니라 **비교가 성립하는 조건**입니다.

1. 두 질의가 **같은 행 수**를 돌려주는지 (어긋나면 주사 비용이 아닌 것을 잽니다)
2. 바 두 개가 코드와 ADR에서 **같은 수**인지
3. 리스트 길이 외삽이 **측정으로** 묶여 있는지
4. 통과를 **허가로 읽지 않는다**는 문장이 ADR에 남아 있는지
"""

import json
from pathlib import Path

import pytest

from scripts.measure_fold_authored import (
    FOLDED_LONG,
    FOLDED_PROXY,
    LONG_LIST,
    MAX_RATIO,
    MAX_SECONDS,
    NOW,
    access,
    pick_category,
    verdict,
)

ADR = Path("docs/adr/ADR-063-what-folding-authored-would-cost.md")
REPORT = Path("reports/fold_authored.json")
CAPACITY = Path("reports/capacity.json")


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {REPORT}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_bar_is_the_same_number_in_code_and_adr():
    """바가 두 곳에 적히면 어긋납니다. 어긋나면 보고서의 판정이 ADR의 판정이 아닙니다."""
    text = ADR.read_text(encoding="utf-8")

    assert MAX_RATIO == 5.0
    assert MAX_SECONDS == 1.0
    assert "5배 이내" in text
    assert "절대 1.0초 이내" in text


def test_both_conditions_are_required():
    """하나만 넘겨도 통과가 되면 바가 두 개인 의미가 없습니다."""
    inside = {"worst_seconds": 0.5}
    slow_but_cheap_ratio = {"worst_seconds": 1.4}  # 2.8배지만 절대 1.0초를 넘습니다
    fast_but_big_ratio = {"worst_seconds": 0.05}

    assert verdict({"worst_seconds": 0.5}, inside)["passes"]
    assert not verdict(inside, slow_but_cheap_ratio)["passes"]
    assert not verdict({"worst_seconds": 0.005}, fast_but_big_ratio)["passes"]


def test_the_rows_match_so_the_scan_is_what_was_measured(report):
    """**이 테스트가 이 파일의 이유입니다.** 행 수가 어긋나면 다른 것을 잰 것입니다."""
    for family, entry in report["families"].items():
        for case in entry["cases"]:
            assert case["now"]["rows"] == case["folded_proxy"]["rows"], (
                family, case["case"], case["now"]["rows"], case["folded_proxy"]["rows"]
            )


def test_the_frequent_category_run_is_kept_but_not_judged(report):
    """섞인 수를 지우지 않고 **판정에서 뺀 채로** 남깁니다."""
    for entry in report["families"].values():
        # 섞인 수가 실제로 훨씬 크므로, 판정에 넣으면 결론이 뒤집힙니다.
        assert entry["worst_wide_seconds"] > entry["worst_folded_seconds"] * 5
        assert entry["verdict"]["worst_ratio"] == pytest.approx(
            entry["worst_folded_seconds"] / entry["worst_now_seconds"], rel=0.01
        )


def test_the_list_length_extrapolation_is_measured_not_assumed(report):
    """`authors`가 `categories`보다 긴 것을 **선형 가정**으로 메우지 않습니다."""
    assert LONG_LIST == 7  # p.authors의 평균 7.1개를 비교 횟수로 덮습니다
    assert report["long_list"]["length"] == LONG_LIST
    for family, entry in report["families"].items():
        assert entry["long_list_within_bar"], family


def test_the_proxy_queries_scan_instead_of_seeking():
    """대리가 인덱스로 새면 접은 모양을 흉내 내지 못합니다."""
    for family, query in FOLDED_PROXY.items():
        assert "ArxivAuthorName" not in query, family
        assert "AUTHORED" not in query, family
    for family, query in NOW.items():
        assert "AUTHORED" in query, family


def test_the_long_variant_only_changes_the_list():
    """상한 변종이 질의 모양까지 바꾸면 상한이 아닙니다."""
    for family in FOLDED_LONG:
        assert "ArxivPaper" in FOLDED_LONG[family]
        assert "AUTHORED" not in FOLDED_LONG[family]
        assert "$many" in FOLDED_LONG[family]


def test_access_pulls_the_bottom_of_the_plan():
    """결정적인 연산자는 트리 아래쪽입니다. 위쪽 네 개를 찍으면 가려집니다."""
    plan = ["ProduceResults", "Sort", "Projection", "Filter", "NodeByLabelScan"]

    assert access(plan) == ["NodeByLabelScan"]
    assert access(["ProduceResults", "Sort", "Projection", "Filter"]) == []


def test_picking_the_category_prefers_the_exact_row_count():
    by_count = {2: "cs.NA", 3: "cond-mat.other", 4: "cond-mat"}

    assert pick_category(by_count, 3) == ("cond-mat.other", 3)
    assert pick_category(by_count, 99) == ("cond-mat", 4)


def test_the_index_loss_is_recorded_not_eyeballed(report):
    """시간은 흔들리지만 `Seek`가 `Scan`으로 바뀌는 것은 흔들리지 않습니다."""
    author = report["families"]["author"]

    assert author["index_lost"] is True
    assert any("Seek" in name for name in author["access_now"])
    assert not any("Seek" in name for name in author["access_folded"])
    assert "인덱스를 잃습니다" in ADR.read_text(encoding="utf-8")


def test_the_capacity_ceiling_closes_the_scaling_worry(report):
    """주사는 말뭉치에 비례하는데, **자리가 먼저 떨어집니다.** 추측이 아니라 센 수입니다."""
    if not CAPACITY.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {CAPACITY}")
    capacity = json.loads(CAPACITY.read_text(encoding="utf-8"))
    seats = capacity["headroom"]["within_safety_ceiling"]["papers_that_fit"]
    growth = (report["papers"] + seats) / report["papers"]

    for family, entry in report["families"].items():
        assert entry["worst_folded_seconds"] * growth <= MAX_SECONDS, (family, growth)


def test_the_adr_does_not_read_passing_as_permission():
    """통과를 허가로 읽으면 10개 모듈과 제약 하나가 조용히 따라옵니다."""
    text = ADR.read_text(encoding="utf-8")

    assert "필요조건이고 충분조건이 아닙니다" in text
    assert "지금 할 일은 없습니다" in text
    # 재지 않은 것이 무엇인지도 적혀 있어야 합니다.
    assert "별도 승인이 필요합니다" in text


def test_the_adr_keeps_the_prediction_that_was_wrong():
    """기각할 것이라 적었고 통과했습니다. 그 예측을 지우지 않습니다."""
    text = ADR.read_text(encoding="utf-8")

    assert "기각될 것으로 봅니다" in text
    assert "예측이 틀렸습니다" in text
    # 왜 틀렸는지가 수치로 남아 있어야 합니다.
    assert "21,183행" in text
