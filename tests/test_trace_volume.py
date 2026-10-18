"""발생량을 **세어서** 적는지 검증합니다.

[ADR-023](../docs/adr/ADR-023-what-to-trace.md)의 결정은 "한 달에 쓸 수 있는 유한한 수를
어디에 쓸 것인가"입니다. 그 수를 추정으로 적으면 결정이 근거를 잃으므로, 여기서 고정하는
것은 둘입니다 - 그래프를 실제로 돌려 센 값인가, 그리고 예산이 평가 한 번을 덮는가.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from pipeline.tracing import DEFAULT_BUDGET

_spec = importlib.util.spec_from_file_location(
    "measure_trace_volume", Path("scripts/measure_trace_volume.py")
)
measure = importlib.util.module_from_spec(_spec)
sys.modules["measure_trace_volume"] = measure
_spec.loader.exec_module(measure)

REPORT = Path("reports/trace_volume.json")


def test_the_counts_come_from_running_the_graph():
    """세 경로를 실제로 돌려 노드 수를 봅니다. 손으로 적은 수가 아닙니다."""
    counted = measure.paths()

    assert set(counted) == {"clean", "revise_once", "abstain"}
    # observation = 노드 수 + 루트 하나. `send_trace()`가 만드는 구조입니다.
    for label, entry in counted.items():
        assert entry["observations"] == entry["nodes"] + 1, label
    # 수정이 끼면 더 비쌉니다. 그 순서가 뒤집히면 세는 쪽이 틀린 것입니다.
    assert counted["clean"]["observations"] < counted["revise_once"]["observations"]


def test_the_report_matches_what_the_graph_does_now():
    """보고서가 늙으면 ADR-023의 표도 같이 늙습니다."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["per_path"] == measure.paths()
    cases = len(json.loads(Path(measure.CASES_FILE).read_text(encoding="utf-8"))["cases"])
    assert report["cases"] == cases
    assert report["per_eval_run"]["best"] == cases * report["per_path"]["clean"]["observations"]


def test_the_default_budget_covers_one_full_evaluation():
    """예산이 평가 한 번보다 작으면 켜는 순간 잘린 추적을 보게 됩니다."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    # 예산은 trace 단위(답변 하나 = trace 하나)이므로 사례 수와 비교합니다.
    assert DEFAULT_BUDGET >= report["cases"]
    assert report["default_budget_traces"] == DEFAULT_BUDGET


def test_the_measurement_needs_no_network():
    """`chatbot`을 가져오면 Aura와 OpenAI에 붙습니다. 세는 데 그것이 필요하면 안 됩니다."""
    source = Path("scripts/measure_trace_volume.py").read_text(encoding="utf-8")

    assert "import chatbot" not in source
