"""승격 판정이 **거부해야 할 때 거부하는지** 검증합니다.

[ADR-032](../docs/adr/ADR-032-close-the-model-lifecycle.md)의 주장은 "게이트를 통과한 것만
승격한다"입니다. 그 문장이 참이려면 판정이 세 경우에 거부해야 합니다 — 구간이 0을 포함할 때,
MRR이 하한 아래일 때, 그리고 **조건 자체가 없을 때**.

네트워크를 쓰지 않습니다. 판정은 보고서와 기준선만 읽으므로 접속 없이 전수 검사됩니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.model_registry import MODEL_NAME, RUNG, criterion, judge

BASELINE = Path("baselines/eval_baseline.json")
CLEAN = Path("reports/link_prediction.json")
SABOTAGED = Path("reports/link_prediction_sabotaged.json")


def report_with(mrr: float, interval: dict | None) -> dict:
    row = {
        "model": RUNG,
        "detail": {"fitted": True},
        "ranking_hard_negatives": {"mrr": mrr},
    }
    if interval is not None:
        row["mrr_vs_best_heuristic"] = interval
    return {"results": [row]}


def test_the_criterion_lives_in_the_baseline():
    """조건이 코드에 박혀 있으면 왜 그 값인지 적을 자리가 없습니다."""
    rules = criterion()

    assert rules["model_name"] == MODEL_NAME
    assert rules["rung"] == RUNG
    assert rules["ranking_mrr_floor"] > 0
    assert rules["rationale"].strip()


def test_the_measured_model_passes():
    if not CLEAN.exists():
        pytest.skip(f"학습 보고서가 없습니다: {CLEAN}")

    verdict = judge(path=CLEAN)

    assert verdict["passed"], verdict["reasons"]


def test_the_sabotaged_model_is_refused():
    """**라벨을 섞어 만든 모델입니다.** 이것이 통과하면 판정이 아무것도 막지 않습니다."""
    if not SABOTAGED.exists():
        pytest.skip(f"일부러 나쁜 보고서가 없습니다: {SABOTAGED}")

    verdict = judge(path=SABOTAGED)

    assert verdict["passed"] is False
    assert verdict["reasons"]
    # 사유가 둘입니다 - 휴리스틱보다 낮고, 하한 아래입니다. 하나만 잡아도 거부지만 둘 다
    # 적혀야 어느 조건이 걸렸는지 알 수 있습니다.
    assert any("휴리스틱" in reason for reason in verdict["reasons"])
    assert any("하한" in reason for reason in verdict["reasons"])


def test_an_interval_straddling_zero_is_refused():
    """점 추정이 양수여도 구간이 0을 포함하면 변동을 승격하는 것입니다."""
    floor = criterion()["ranking_mrr_floor"]
    straddles = {"baseline": "adamic-adar", "mean": 0.01,
                 "low": -0.02, "high": 0.04, "excludes_zero": False}

    verdict = judge(report_with(floor + 0.1, straddles))

    assert verdict["passed"] is False
    assert any("0을 포함" in reason for reason in verdict["reasons"])


def test_a_good_interval_with_a_low_mrr_is_refused():
    """구간만 보면 "조금 나은 나쁜 모델"이 올라갑니다."""
    floor = criterion()["ranking_mrr_floor"]
    solid = {"baseline": "adamic-adar", "mean": 0.02,
             "low": 0.01, "high": 0.03, "excludes_zero": True}

    verdict = judge(report_with(floor - 0.05, solid))

    assert verdict["passed"] is False
    assert any("하한" in reason for reason in verdict["reasons"])


def test_a_missing_interval_is_refused():
    verdict = judge(report_with(criterion()["ranking_mrr_floor"] + 0.1, None))

    assert verdict["passed"] is False


def test_a_missing_criterion_is_refused_not_waved_through(tmp_path, monkeypatch):
    """**조건이 없으면 통과가 아니라 실패입니다.**

    기준선 항목이 빠진 채 구간만 보고 올리면 "게이트를 통과한 것만 승격한다"가 빈 문장이
    됩니다. `EVAL_GATE_REQUIRED`가 평가 파일이 없을 때 실패로 두는 것과 같은 이유입니다.
    """
    empty = tmp_path / "baseline.json"
    empty.write_text(json.dumps({"metrics": {}}), encoding="utf-8")
    monkeypatch.setattr("pipeline.model_registry.BASELINE", empty)

    verdict = judge(report_with(0.99, {"mean": 0.5, "low": 0.4, "high": 0.6,
                                       "excludes_zero": True}))

    assert verdict["passed"] is False
    assert any("조건이 없습니다" in reason for reason in verdict["reasons"])


def test_the_floor_sits_above_the_best_heuristic():
    """하한이 휴리스틱 아래면 "휴리스틱으로 되돌아갔다"를 잡지 못합니다."""
    if not CLEAN.exists():
        pytest.skip(f"학습 보고서가 없습니다: {CLEAN}")
    data = json.loads(CLEAN.read_text(encoding="utf-8"))
    heuristics = [
        (row.get("ranking_hard_negatives") or {}).get("mrr", 0)
        for row in data["results"] if not row["detail"].get("fitted")
    ]

    assert criterion()["ranking_mrr_floor"] > max(heuristics)
