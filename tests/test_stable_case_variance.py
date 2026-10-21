"""`stable_case` 변동 측정의 **통계부**를 검증합니다.

묶는 규칙은 여기서 보지 않습니다 — [`evals/ledger.py`](../evals/ledger.py)에 한 벌로 있고
[`tests/test_eval_ledger.py`](test_eval_ledger.py)가 고정합니다. 처음에는 이 스크립트가
직접 묶었는데 **장부와 두 벌이 되어 바로 갈라졌습니다**(날짜·모드·최소 실행 수가 전부
달랐습니다). 이 저장소에서 손으로 유지하는 두 벌이 갈라진 자리를 여덟 번 봤으므로 합쳤습니다.

여기서 고정하는 것은 하나입니다 — **0건을 "비율 0"으로 읽지 않는가.** 240쌍이든 366쌍이든
사건이 0이면 비율은 0이 아니고, 95% 상한이 약 `3/n`입니다. 그 상한으로 `P`를 내야 "바를
통과했다"를 말할 수 있고, 상한이 바를 넘으면 답은 **"측정되지 않았다"** 입니다.
"""

import json
from pathlib import Path

import pytest

from scripts.measure_stable_case_variance import (
    POPULATIONS,
    TOLERABLE_RED_RATE,
    pairs_needed,
    red_rate,
    upper95,
    verdict,
)

REPORT = Path("reports/stable_case_variance.json")


# ── 통계 ────────────────────────────────────────────────────────────────────

def test_the_red_rate_grows_with_the_population():
    """사례당 비율이 작아도 30건을 곱하면 커집니다 - 그게 이 질문의 요지입니다."""
    assert red_rate(0.0, 30) == 0.0
    # 반올림한 값을 넣지 않습니다 - 0.0082는 3/366을 자른 값이고 결과가 한 자리 어긋납니다.
    assert red_rate(3 / 366, 5) < red_rate(3 / 366, 30)
    assert red_rate(3 / 366, 30) == pytest.approx(0.2188, abs=1e-4)


def test_zero_events_gets_an_upper_bound_not_a_rate_of_zero():
    """**이 테스트가 결론을 지탱합니다.** rule of three: 사건 0이면 상한이 약 3/n입니다."""
    assert upper95(366, 0) == pytest.approx(3 / 366)
    assert upper95(240, 0) == pytest.approx(3 / 240)
    # 사건이 있으면 상한을 쓰지 않습니다 - 관측 비율 자체를 씁니다.
    assert upper95(366, 1) is None
    assert upper95(0, 0) is None


def test_a_bigger_sample_gives_a_tighter_bound():
    assert upper95(405, 0) < upper95(366, 0) < upper95(240, 0)


def test_the_verdict_has_three_branches():
    """상한이 바를 넘으면 고치는 것도 그대로 두는 것도 근거가 없습니다."""
    # 관측 비율만으로 바를 넘음.
    assert verdict(0.05, None) == "rule_fires_on_variance"
    # 사건 0인데 상한이 바를 넘음 - 지금 상태입니다.
    assert verdict(0.0, 3 / 366) == "not_resolved"
    # 표본이 충분해 상한도 바 아래.
    assert verdict(0.0, 3 / pairs_needed()) == "within_tolerance"


def test_the_needed_sample_actually_clears_the_bar():
    """"더 재야 한다"로 끝내지 않습니다. 얼마나 더인지 적고, 그 수가 맞는지 확인합니다."""
    needed = pairs_needed()

    assert red_rate(3.0 / needed, max(POPULATIONS)) < TOLERABLE_RED_RATE
    assert red_rate(3.0 / (needed - 20), max(POPULATIONS)) >= TOLERABLE_RED_RATE


# ── 실제 측정 결과 ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def measured():
    if not REPORT.exists():
        pytest.skip(f"{REPORT}가 없습니다. uv run python -m scripts.measure_stable_case_variance")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_it_reads_the_ledger_not_the_reports(measured):
    """보고서를 긁으면 묶는 규칙이 두 벌이 됩니다."""
    assert measured["source"].endswith("case_ledger.jsonl")
    assert measured["ledger_entries"] > 0


def test_only_the_gate_mode_is_pooled(measured):
    """`agent` 실행은 게이트가 돌리는 것이 아닙니다 - 표에 보이되 합에 들어가지 않습니다."""
    assert measured["for_mode"] == "compare"
    assert measured["groups_counted"] < measured["groups_seen"]
    counted = [g for g in measured["groups"].values() if g["mode"] == "compare"]
    assert sum(g["eligible_pairs"] for g in counted) == measured["pooled_eligible_pairs"]


def test_no_drop_was_seen_in_the_gate_mode(measured):
    assert measured["pooled_drops"] == 0
    assert measured["pooled_eligible_pairs"] >= 366


def test_the_question_is_still_not_resolved(measured):
    """상한이 바를 넘습니다. **규칙을 고치지 않는 근거입니다.**"""
    upper = measured["red_rate_upper95_by_population"]

    assert upper[str(max(POPULATIONS))] > TOLERABLE_RED_RATE
    assert upper[str(min(POPULATIONS))] < TOLERABLE_RED_RATE
    assert measured["verdict"] == "not_resolved"
    assert measured["pairs_short"] > 0


def test_every_group_has_at_least_two_runs(measured):
    """한 번만 돌린 묶음은 분모만 늘립니다."""
    assert all(group["runs"] >= 2 for group in measured["groups"].values())
    assert measured["min_runs"] == 2
