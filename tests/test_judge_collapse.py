"""심판 붕괴에 대한 게이트의 탐지 용량을 지킵니다(ADR-060).

이 테스트들이 지키는 것은 **숫자가 아니라 그 숫자의 근거**입니다. 구멍 3건은 두 설계 결정이
곱해진 결과이고(실패 사례를 `stable_case`가 건너뛰는 것, 품질 집계에서 빼는 것), 그 둘 중
하나가 바뀌면 구멍의 크기가 바뀝니다. 그때 ADR을 함께 고치게 하려고 여기서 막습니다.
"""

import json
from pathlib import Path

import pytest

from evals import gate, run
from evals import judge_collapse as jc

ADR = Path("docs/adr/ADR-060-what-the-gate-catches-when-the-judge-collapses.md")
REPORT = Path("reports/judge_collapse.json")
CASES = Path("evals/cases_graph.json")


@pytest.fixture(scope="module")
def cases():
    return {case["id"]: case for case in run.load_cases(CASES)}


# ── 변형이 파이프라인 자신의 함수를 씁니다 ──────────────────────────────

def test_the_failed_row_is_built_by_the_pipeline_not_by_hand():
    """손으로 지은 숫자가 하나라도 있으면 파이프라인이 아니라 내 모델을 시험하게 됩니다."""
    source = Path("evals/judge_collapse.py").read_text(encoding="utf-8")
    assert "run.evaluate_case(case, {}, mode=\"agent\"" in source
    assert "run.summarize_comparison(rows)" in source


def test_the_failed_row_looks_like_the_pipelines_failure(cases):
    row = jc.failed_row(cases["author-01"], 5)
    assert row["failed"] is True
    assert row["error"].startswith("ValueError:")
    # 빈 응답으로 채점한 결과이므로 검색 지표가 0입니다.
    assert row["recall_at_k"] == 0.0
    assert row["hit_at_k"] == 0.0


def test_inject_does_not_mutate_the_control(cases):
    control = json.loads(
        Path("reports/rag_eval_graph_compare_final.json").read_text(encoding="utf-8")
    )
    before = json.dumps(control, sort_keys=True)
    jc.inject(control, cases, {"author-01"}, 5)
    assert json.dumps(control, sort_keys=True) == before


def test_inject_recomputes_the_summary(cases):
    """사례만 바꾸고 집계를 그대로 두면 **존재할 수 없는 보고서**를 시험하게 됩니다."""
    control = json.loads(
        Path("reports/rag_eval_graph_compare_final.json").read_text(encoding="utf-8")
    )
    shaped = jc.inject(control, cases, {"author-01", "author-02"}, 5)
    assert shaped["summary"]["graph_rag_failures"] > control["summary"][
        "graph_rag_failures"
    ]


def test_failures_are_counted_as_a_total_not_as_a_dose(cases):
    """이미 실패한 사례를 다시 심어도 총계가 늘지 않습니다 - 거기서 빠져나갑니다."""
    control = json.loads(
        Path("reports/rag_eval_graph_compare_final.json").read_text(encoding="utf-8")
    )
    already = [
        row["id"] for row in control["results"] if row.get("graph_rag", {}).get("failed")
    ]
    assert already, "대조군에 실패가 없으면 이 테스트가 뜻을 잃습니다"
    assert jc.failures_after(control, set()) == len(already)
    assert jc.failures_after(control, {already[0]}) == len(already)
    fresh = next(
        row["id"] for row in control["results"]
        if not row.get("graph_rag", {}).get("failed")
    )
    assert jc.failures_after(control, {fresh}) == len(already) + 1


# ── 구멍을 만드는 두 설계를 못 박습니다 ─────────────────────────────────

def test_stable_case_skips_a_failed_case_on_purpose():
    """근거는 `evals/gate.py`에 적혀 있습니다 - 같은 변동에 두 번 벌점을 주지 않습니다.

    이 동작이 바뀌면 구멍의 크기가 바뀝니다. 그때 ADR-060을 함께 고쳐야 합니다.
    """
    baseline = {
        "primary_metric": "answer_evidence_recall",
        "metrics": {}, "absolute_floor": {}, "allowed_drop": {},
        "case_ids": ["c1"], "stable_cases": ["c1"],
        "splits": {"full": ["c1"]}, "metrics_split": "full",
    }
    report = {
        "cases": {"c1": {"failed": True, "metrics": {"answer_evidence_recall": 0.0}}},
        "metrics": {}, "scoring_errors": 0, "prompt_versions": {},
    }
    assert [v.rule for v in gate.check(baseline, report, split="full")] == []
    # 실패가 아니라 점수가 낮으면 같은 사례가 잡힙니다.
    report["cases"]["c1"]["failed"] = False
    assert [v.rule for v in gate.check(baseline, report, split="full")] == ["stable_case"]


def test_quality_aggregates_leave_failed_cases_out_on_purpose(cases):
    """근거는 `gate.normalize`에 적혀 있습니다 - 섞으면 품질이 실패율을 재게 됩니다."""
    control = json.loads(
        Path("reports/rag_eval_graph_compare_final.json").read_text(encoding="utf-8")
    )
    fresh = [
        row["id"] for row in control["results"]
        if not row.get("graph_rag", {}).get("failed")
    ][:3]
    shaped = gate.normalize(jc.inject(control, cases, set(fresh), 5))
    clean = gate.normalize(control)
    # 실패를 셋 심어도 품질 지표가 움직이지 않습니다. 움직이는 것은 실패율뿐입니다.
    assert shaped["metrics"]["answer_evidence_recall"] == pytest.approx(
        clean["metrics"]["answer_evidence_recall"]
    )
    assert shaped["metrics"]["answer_failure_rate"] > clean["metrics"][
        "answer_failure_rate"
    ]


def test_only_newly_raised_violations_are_counted():
    """대조군에서 이미 켜진 위반을 결함이 잡은 것으로 세면 구멍이 0으로 보입니다."""
    source = Path("evals/judge_collapse.py").read_text(encoding="utf-8")
    assert "(violation.rule, violation.detail) not in already" in source


# ── 커밋된 측정 결과 ────────────────────────────────────────────────────

def test_the_measured_hole_matches_the_adr():
    """ADR의 숫자와 보고서가 갈라지면 둘 중 하나는 거짓말입니다."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    hole = report["largest_total_failures_that_slipped"]
    adr = ADR.read_text(encoding="utf-8")
    assert f"-->{hole}<!--/n-->" in adr
    assert f"총 실패 {hole + 1}건부터" in adr or f"{hole + 1}건부터" in adr


def test_the_report_says_which_rules_were_already_on():
    """상수로 켜진 규칙을 적지 않으면 결과를 잘못 읽습니다."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["rules_already_on_in_the_control"]
    assert "구멍은" in report["note"]


def test_the_study_records_that_stable_case_never_fired():
    """예측이 틀린 자리가 결과에 남아 있어야 합니다."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    raised = {
        rule
        for entry in report["rounds"]
        for rule in entry["new_rules_raised"]
    }
    assert "stable_case" not in raised
    assert raised == {"floor", "drop"}, raised


def test_the_gate_cases_do_not_cover_the_family_that_actually_broke():
    """ADR-059가 잰 붕괴는 게이트가 구조적으로 못 봅니다. 그 사실을 고정합니다."""
    baseline = json.loads(
        Path("baselines/eval_baseline.json").read_text(encoding="utf-8")
    )
    assert not [case for case in baseline["case_ids"] if case.startswith("title-")]
    assert "탐지율 0%" in ADR.read_text(encoding="utf-8")
