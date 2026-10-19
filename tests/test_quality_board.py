"""품질 게이트 화면이 **읽기만 하는지** 검증합니다.

이 화면의 값어치는 "측정이 기록으로 남는다"를 보이게 하는 것입니다. 그런데 화면이 숫자를
**만들기** 시작하면 그 숫자의 출처가 화면 코드가 되고, 되짚을 수 없게 됩니다. 그래서
고정할 것이 셋입니다.

1. **기준선 파일의 값을 그대로 옮깁니다.** 반올림·재계산 없이.
2. **여유와 변동폭을 나란히 둡니다.** 여유가 변동폭보다 좁은 하한은 품질이 아니라 운을
   재는 것이고([ADR-004](../docs/adr/ADR-004-quality-gate-thresholds.md)) 화면이 그것을
   세어 보여 줘야 합니다.
3. **파일이 없어도 죽지 않습니다.** 보고서가 빠진 체크아웃에서도 떠야 합니다.

`quality_board.py`는 Streamlit을 가져오지 않습니다 - 그래서 이 테스트가 접속도 화면도 없이
돕니다(`abstain.py`와 같은 선).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import quality_board

BASELINE = Path("baselines/eval_baseline.json")


@pytest.fixture(scope="module")
def baseline():
    if not BASELINE.exists():
        pytest.skip(f"기준선이 없습니다: {BASELINE}")
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_board_does_not_import_streamlit():
    """화면과 읽기를 나눠 둬야 접속 없이 테스트할 수 있습니다."""
    import re

    source = Path("quality_board.py").read_text(encoding="utf-8")

    assert not re.search(r"^\s*import streamlit", source, re.MULTILINE)
    assert not re.search(r"^\s*from streamlit", source, re.MULTILINE)


def test_every_gated_metric_appears_with_its_floor(baseline):
    rows = {row["metric"]: row for row in quality_board.gate_rows(baseline)}

    for name, floor in baseline["absolute_floor"].items():
        assert name in rows, name
        assert rows[name]["floor"] == floor, name
        assert rows[name]["gated"], name
    # 하한이 없는 지표도 숨기지 않습니다. 재고 있는데 게이트하지 않는 것도 사실입니다.
    for name in baseline["metrics"]:
        assert name in rows, name


def test_the_numbers_are_copied_not_recomputed(baseline):
    """반올림이나 재계산이 끼면 화면의 숫자가 기준선과 달라집니다."""
    rows = {row["metric"]: row for row in quality_board.gate_rows(baseline)}

    for name, value in baseline["metrics"].items():
        assert rows[name]["measured"] == value, name


def test_headroom_follows_the_direction_of_the_metric():
    """실패율은 낮을수록 좋습니다. 방향을 뒤집지 않으면 여유가 음수로 보입니다."""
    assert quality_board.headroom("answer_evidence_recall", 1.0, 0.99) == pytest.approx(0.01)
    assert quality_board.headroom("answer_failure_rate", 0.0, 0.1333) == pytest.approx(0.1333)
    assert quality_board.lower_is_better("answer_failure_rate")
    assert not quality_board.lower_is_better("recall_at_k")


def test_a_floor_tighter_than_the_measured_spread_is_surfaced():
    """게이트가 변동으로 터지는 상태를 화면이 **세어** 보여 줘야 합니다(ADR-004)."""
    tight = {
        "metrics": {"m": 0.90},
        "absolute_floor": {"m": 0.89},
        "source": {"observed_spread": {"m": 0.05}},
    }
    assert quality_board.tight_floors(quality_board.gate_rows(tight)) == ["m"]

    roomy = {
        "metrics": {"m": 0.90},
        "absolute_floor": {"m": 0.50},
        "source": {"observed_spread": {"m": 0.05}},
    }
    assert quality_board.tight_floors(quality_board.gate_rows(roomy)) == []


def test_the_current_baseline_has_no_floor_tighter_than_its_spread(baseline):
    """지금 상태가 그렇다는 것을 고정합니다. 깨지면 하한이나 측정 중 하나가 바뀐 것입니다."""
    assert quality_board.tight_floors(quality_board.gate_rows(baseline)) == []


def test_the_board_survives_missing_reports(tmp_path):
    assert quality_board.load(tmp_path / "없는파일.json") is None
    assert quality_board.variant_rows(None) == []
    assert quality_board.degree_rows(None) == []
    assert quality_board.degree_rows({"by_degree_slice": {}}) == []

    broken = tmp_path / "broken.json"
    broken.write_text("{ 이건 JSON이 아닙니다", encoding="utf-8")
    assert quality_board.load(broken) is None


def test_the_slots_outside_the_harness_are_shown(baseline):
    """`related_slot`과 `model_registry`는 게이트가 보지 않습니다. 화면은 봐야 합니다."""
    rows = {row["key"]: row for row in quality_board.slot_rows(baseline)}

    assert "related_slot" in rows
    assert rows["related_slot"]["floor"] == baseline["related_slot"]["floor"]
    assert rows["related_slot"]["variant"] == baseline["related_slot"]["variant"]
    assert "model_registry" in rows
    assert rows["model_registry"]["measured"] == (
        baseline["model_registry"]["measured"]["ranking_mrr"]
    )


def test_the_rejected_models_are_listed_with_where_they_lost():
    """기각을 숨기면 이 저장소의 내용이 절반이 됩니다."""
    assert len(quality_board.REJECTED) >= 6
    for name, slot, result, record in quality_board.REJECTED:
        assert name and slot and result
        assert record.startswith("ADR-") or record.startswith("EXPERIMENTS")
