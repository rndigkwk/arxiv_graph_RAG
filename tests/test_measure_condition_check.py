"""조건 판정의 무게를 세는 쪽의 시험입니다.

이 스크립트의 결론은 "게이트가 몇 번 막았는가" 하나에 달려 있습니다. 그 수를 틀리면
결론이 조용히 뒤집히므로, 막은 자리를 가리는 함수에 **대조군**을 둡니다 - 형식 검사가
막은 실행을 조건 판정으로 세면 시험이 터져야 합니다.
"""

import json

import pytest

from scripts.measure_condition_check import (
    gate_effect,
    load_runs,
    measure,
    node_cost,
    rejecting_gate,
    report_paths,
)

CLEAN = {"route": 1, "generate": 1, "collect": 1, "check_format": 1,
         "check_conditions": 1, "accept": 1}
# 형식 검사가 막은 실행입니다. 조건 판정은 두 번째 시도에서 한 번만 돕니다.
FORMAT_RETRY = {"route": 1, "generate": 2, "collect": 2, "check_format": 2,
                "check_conditions": 1, "revise": 1, "accept": 1}
# 조건 판정이 막은 실행입니다. 첫 시도에서 조건 판정까지 갔으므로 두 번 돕니다.
CONDITION_RETRY = {"route": 1, "generate": 2, "collect": 2, "check_format": 2,
                   "check_conditions": 2, "revise": 1, "accept": 1}


def run_row(visits, rejections=None, seconds=None):
    return {"report": "r.json", "id": "q-1", "category": "coauthor", "visits": visits,
            "seconds": seconds or {"generate": 10.0, "check_conditions": 4.0},
            "total_seconds": 14.0, "rejections": rejections}


def test_a_run_that_passed_once_has_no_rejecting_gate():
    assert rejecting_gate(run_row(CLEAN)) == (None, "inferred")


def test_a_condition_rejection_is_inferred_from_the_extra_visit():
    assert rejecting_gate(run_row(CONDITION_RETRY)) == ("check_conditions", "inferred")


def test_a_format_rejection_is_not_counted_as_a_condition_rejection():
    """대조군입니다. 이것이 통과하지 않으면 재시도를 전부 조건 판정으로 몰아 세고 있습니다."""
    assert rejecting_gate(run_row(FORMAT_RETRY)) == ("check_format", "inferred")


def test_the_two_retry_shapes_are_not_distinguishable_by_one_gate_alone():
    """추론이 왜 필요했는지입니다 - `check_format`의 방문 횟수는 두 경우에 같습니다."""
    assert FORMAT_RETRY["check_format"] == CONDITION_RETRY["check_format"]


def test_a_recorded_rejection_wins_over_the_inference():
    """리포트가 적어 둔 값이 있으면 추론하지 않습니다."""
    row = run_row(CONDITION_RETRY, rejections=[
        {"node": "check_format", "stage": "answer_format_citation", "attempt": 1}])

    assert rejecting_gate(row) == ("check_format", "recorded")


def test_an_empty_recorded_list_means_the_answer_passed_once():
    """빈 목록은 "모른다"가 아니라 "막히지 않았다"입니다."""
    assert rejecting_gate(run_row(CLEAN, rejections=[])) == (None, "recorded")


def test_the_effect_counts_runs_gates_and_families():
    runs = [run_row(CLEAN), run_row(CONDITION_RETRY), run_row(FORMAT_RETRY)]

    effect = gate_effect(runs)

    assert effect["runs"] == 3
    assert effect["runs_rejected_once"] == 2
    assert effect["by_gate"]["check_conditions"]["rejections"] == 1
    assert effect["by_gate"]["check_format"]["rejections"] == 1
    assert effect["by_gate"]["check_conditions"]["categories"] == {"coauthor": 1}


def test_read_and_inferred_counts_are_reported_apart():
    """같은 표에 섞으면, 추론한 값을 측정한 값으로 읽게 됩니다."""
    runs = [run_row(CLEAN), run_row(CLEAN, rejections=[])]

    assert gate_effect(runs)["attribution"] == {"inferred": 1, "recorded": 1}


def test_the_share_is_of_the_measured_total_not_of_the_node_sum():
    runs = [run_row(CLEAN, seconds={"generate": 9.0, "check_conditions": 3.0})]
    runs[0]["total_seconds"] = 12.0

    cost = node_cost(runs)

    assert cost["total_seconds"] == 12.0
    assert cost["nodes"]["check_conditions"]["share_percent"] == 25.0


def test_rescored_reports_are_left_out(tmp_path, monkeypatch):
    """같은 실행을 다시 채점한 파일을 같이 읽으면 한 실행을 두 번 셉니다."""
    reports = tmp_path / "reports"
    reports.mkdir()
    for name in ("baseline_routed_run1.json", "baseline_routed_run1_ragas.json",
                 "baseline_routed_run1_setmetrics.json"):
        (reports / name).write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert [path.replace("\\", "/") for path in report_paths("reports/baseline_*_run?.json")] == [
        "reports/baseline_routed_run1.json"
    ]


def test_a_missing_trace_is_loud(tmp_path, monkeypatch):
    """추적이 없는 리포트를 조용히 0으로 세면 게이트가 공짜로 보입니다."""
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "baseline_routed_run1.json").write_text(json.dumps(
        {"results": [{"id": "q-1", "category": "coauthor", "graph_rag": {}}]}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(KeyError):
        load_runs(["reports/baseline_routed_run1.json"])


def test_no_reports_is_an_error_not_an_empty_table(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit):
        measure("reports/baseline_*_run?.json")
