"""회귀 게이트가 규칙마다 제대로 실패하는지, 그리고 이번 평가가 기준선을 지키는지 봅니다.

두 층입니다.

1. **게이트 자체 검증** — `tests/fixtures/eval_gate/`의 가짜 기준선·결과 쌍으로 규칙마다
   걸리는지 확인합니다. 통과만 시키는 게이트는 게이트가 아니므로, 이 층이 없으면 아래 층의
   "통과"가 아무것도 뜻하지 않습니다.
2. **이번 평가 판정** — `reports/eval_gate.json`이 있으면 규칙별로 판정합니다. 없으면 건너뜁니다.
   단 CI는 `EVAL_GATE_REQUIRED=1`을 켜서, 평가 단계가 파일을 쓰지 못한 경우가 **건너뛰기가 아니라
   실패**가 되게 합니다. 그러지 않으면 평가가 깨진 날 게이트가 초록색이 됩니다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from evals.gate import (
    BASELINE,
    GATE_REPORT,
    check,
    compares_aggregates,
    normalize,
    transient_failures,
)

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "eval_gate").iterdir())
RULES = ("cases", "scoring_error", "floor", "drop", "stable_case")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- 1층: 게이트 자체 --------------------------------------------------------------


@pytest.mark.parametrize("fixture", FIXTURES, ids=[path.name for path in FIXTURES])
def test_the_gate_reports_exactly_the_rules_each_fixture_breaks(fixture: Path):
    expected = set(load(fixture / "expected.json")["violated_rules"])
    violations = check(load(fixture / "baseline.json"), normalize(load(fixture / "results.json")))
    assert {violation.rule for violation in violations} == expected, [
        str(violation) for violation in violations
    ]


def test_every_rule_is_covered_by_some_fixture():
    covered = set()
    for fixture in FIXTURES:
        covered |= set(load(fixture / "expected.json")["violated_rules"])
    # 규칙을 더하면 그 규칙이 실제로 실패하는 fixture도 함께 더해야 합니다.
    assert covered == set(RULES)


def test_at_least_one_fixture_passes_cleanly():
    # 아무것도 통과시키지 못하는 게이트는 배포를 영구히 막습니다.
    passing = [
        fixture
        for fixture in FIXTURES
        if not load(fixture / "expected.json")["violated_rules"]
    ]
    assert passing


def test_a_metric_sitting_exactly_on_the_floor_passes():
    fixture = next(path for path in FIXTURES if path.name == "02_pass_boundary")
    baseline = load(fixture / "baseline.json")
    report = normalize(load(fixture / "results.json"))
    floor = baseline["absolute_floor"]["answer_evidence_recall"]
    assert report["metrics"]["answer_evidence_recall"] == pytest.approx(floor)
    assert not check(baseline, report)


def test_a_failure_rate_is_judged_in_the_other_direction():
    """실패율은 낮은 쪽이 좋습니다. 하한을 그대로 적용하면 부호가 뒤집힙니다."""
    baseline = {
        "primary_metric": "answer_evidence_recall",
        "metrics": {"answer_failure_rate": 0.03},
        "absolute_floor": {"answer_failure_rate": 0.10},
        "allowed_drop": {"answer_failure_rate": 0.05},
        "case_ids": [],
        "stable_cases": [],
    }
    good = {"metrics": {"answer_failure_rate": 0.0}, "cases": {}, "scoring_errors": 0}
    bad = {"metrics": {"answer_failure_rate": 0.20}, "cases": {}, "scoring_errors": 0}
    assert not check(baseline, good)
    assert {violation.rule for violation in check(baseline, bad)} == {"floor", "drop"}


def test_a_case_not_evaluated_this_run_is_not_treated_as_a_regression():
    """smoke 분할은 일부만 돕니다. 돌지 않은 사례를 깨진 것으로 세면 분할을 쓸 수 없습니다."""
    baseline = {
        "primary_metric": "answer_evidence_recall",
        "metrics": {},
        "absolute_floor": {},
        "allowed_drop": None,
        "case_ids": ["c1", "c2"],
        "stable_cases": ["c1", "c2"],
    }
    partial = {
        "metrics": {},
        "cases": {"c1": {"failed": False, "metrics": {"answer_evidence_recall": 1.0}}},
        "scoring_errors": 0,
    }
    # 빠진 사례는 `cases` 규칙이 말하고, `stable_case`는 중복으로 말하지 않습니다.
    assert {violation.rule for violation in check(baseline, partial)} == {"cases"}


def test_normalize_watches_the_product_path_not_the_contrast():
    """`--mode compare`는 vector_only와 graph_rag를 함께 적습니다. 게이트가 보는 것은 후자입니다."""
    report = {
        "mode": "compare",
        "summary": {
            "vector_only": {"answer_evidence_recall": 0.0},
            "graph_rag": {"answer_evidence_recall": 1.0},
            "graph_rag_failures": 0,
        },
        "results": [
            {
                "id": "c1",
                "vector_only": {"id": "c1", "answer_evidence_recall": 0.0},
                "graph_rag": {"id": "c1", "answer_evidence_recall": 1.0, "failed": False},
            }
        ],
    }
    normalized = normalize(report)
    assert normalized["metrics"]["answer_evidence_recall"] == 1.0
    assert normalized["cases"]["c1"]["metrics"]["answer_evidence_recall"] == 1.0


def test_cost_and_latency_are_not_gated():
    """지연과 토큰은 품질이 아니고 실행 환경에 따라 흔들립니다. 게이트가 보면 매번 터집니다."""
    report = {
        "summary": {"recall_at_k": 1.0, "elapsed_seconds": 99.0, "chat_input_tokens": 12345},
        "results": [{"id": "c1", "recall_at_k": 1.0, "elapsed_seconds": 99.0, "failed": False}],
    }
    metrics = normalize(report)["metrics"]
    assert "recall_at_k" in metrics
    assert "elapsed_seconds" not in metrics
    assert "chat_input_tokens" not in metrics


# --- 2층: 이번 평가 ----------------------------------------------------------------


def _this_run() -> tuple[dict, dict]:
    if not BASELINE.exists():
        pytest.fail(f"기준선이 없습니다: {BASELINE}")
    if not GATE_REPORT.exists():
        message = f"이번 평가 결과가 없습니다: {GATE_REPORT}"
        if os.environ.get("EVAL_GATE_REQUIRED") == "1":
            pytest.fail(f"{message} (EVAL_GATE_REQUIRED=1)")
        pytest.skip(f"{message} (로컬에서는 평가를 돌리지 않으므로 건너뜁니다)")
    return load(BASELINE), load(GATE_REPORT)


def _split_of(report: dict) -> str:
    """Which split produced this report.

    2026-10-16 첫 CI 실행이 여기서 실패했습니다. 게이트 CLI는 분할을 알고 "위반 0건"이라고
    적었는데, 판정하는 쪽이 그것을 읽지 않아 5건 실행을 30건 기준선으로 봤습니다. 분할을
    추측하면 안 됩니다 — `full`로 가정하면 smoke가 매번 거짓 실패하고, `smoke`로 가정하면
    full이 거짓 통과합니다.
    """
    split = report.get("split")
    assert split, f"{GATE_REPORT}에 split이 없습니다. evals.gate가 쓴 파일이 아닙니다."
    return split


@pytest.mark.parametrize("rule", RULES)
def test_this_run_does_not_break_the_rule(rule: str):
    baseline, report = _this_run()
    violations = check(baseline, report, split=_split_of(report))
    broken = [str(violation) for violation in violations if violation.rule == rule]
    assert not broken, "\n".join(broken)


def test_the_baseline_states_why_each_floor_is_where_it_is():
    """하한을 이번 측정값에 붙이면 게이트가 '어제와 같은가'만 검사합니다 (ADR-004)."""
    baseline = load(BASELINE)
    assert baseline["note"].strip(), "기준선에 근거가 적혀 있어야 합니다"
    assert baseline["absolute_floor"], "하한이 비어 있으면 게이트가 아무것도 막지 않습니다"
    for name, floor in baseline["absolute_floor"].items():
        measured = baseline["metrics"].get(name)
        if measured is None:
            continue
        # 하한이 측정값과 같으면 변동 한 번에 터집니다. 여유가 있어야 합니다.
        assert floor != measured, f"{name}의 하한이 측정값과 같습니다"


# --- 분할 ---------------------------------------------------------------------------


def test_the_smoke_split_covers_every_family():
    """가족이 승자를 정하는 과제입니다(ADR-008). 앞에서 n건을 자르면 한 가족만 봅니다."""
    baseline = load(BASELINE)
    smoke = baseline["splits"]["smoke"]
    families = {case_id.rsplit("-", 1)[0] for case_id in baseline["splits"]["full"]}
    assert {case_id.rsplit("-", 1)[0] for case_id in smoke} == families
    assert len(smoke) == len(families), "가족마다 한 건씩입니다"


def test_a_smoke_run_is_judged_against_the_smoke_split_only():
    baseline = load(BASELINE)
    smoke = baseline["splits"]["smoke"]
    report = {
        "metrics": {},
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in smoke
        },
        "scoring_errors": 0,
    }
    assert not [v for v in check(baseline, report, split="smoke") if v.rule == "cases"]
    # 같은 결과를 full로 판정하면 25건이 빠진 것으로 걸려야 합니다.
    assert [v for v in check(baseline, report, split="full") if v.rule == "cases"]


def test_an_unknown_split_falls_back_to_the_full_case_set():
    from evals.gate import expected_cases

    baseline = load(BASELINE)
    assert expected_cases(baseline, "typo") == set(baseline["case_ids"])


def test_a_smoke_run_is_not_judged_against_full_set_averages():
    """30건 평균과 5건 평균은 다른 모수의 값입니다.

    실제로 smoke가 고르는 `-01` 사례들은 전체보다 쉬워서 평균이 1.0으로 나왔습니다. 그 1.0은
    "좋아졌다"가 아니라 "다른 것을 쟀다"입니다. 반대 방향도 같은 이유로 위험합니다 — 어려운
    5건을 골랐다면 품질이 그대로여도 하한 아래로 떨어집니다.
    """
    baseline = load(BASELINE)
    assert baseline["metrics_split"] == "full"
    assert compares_aggregates(baseline, "full")
    assert not compares_aggregates(baseline, "smoke")

    # smoke 평균이 하한을 한참 밑돌아도 집계 규칙으로는 걸리지 않아야 합니다.
    terrible_average = {
        "metrics": {name: 0.0 for name in baseline["absolute_floor"]},
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["splits"]["smoke"]
        },
        "scoring_errors": 0,
    }
    rules = {v.rule for v in check(baseline, terrible_average, split="smoke")}
    assert rules == set(), rules
    # 같은 결과를 full로 보면 하한이 걸립니다 — 규칙이 사라진 것이 아닙니다.
    assert "floor" in {v.rule for v in check(baseline, terrible_average, split="full")}


def test_a_smoke_run_still_catches_a_broken_case():
    """모수와 무관한 규칙은 5건으로도 답할 수 있는 질문입니다."""
    baseline = load(BASELINE)
    smoke = baseline["splits"]["smoke"]
    broken = {
        "metrics": {},
        "cases": {
            case_id: {
                "failed": False,
                "metrics": {baseline["primary_metric"]: 0.0 if index == 0 else 1.0},
            }
            for index, case_id in enumerate(smoke)
        },
        "scoring_errors": 0,
    }
    rules = {v.rule for v in check(baseline, broken, split="smoke")}
    assert rules == {"stable_case"}, rules


# --- 일시 실패와 품질 회귀의 구별 -----------------------------------------------------


def _report(cases: dict, metrics: dict | None = None) -> dict:
    return {"metrics": metrics or {}, "cases": cases, "scoring_errors": 0}


def test_a_stable_case_whose_agent_raised_is_not_counted_as_a_regression():
    """에이전트 예외는 측정된 변동이고 실패율 상한이 이미 셉니다.

    2026-10-16 첫 full 실행이 이것으로 실패했습니다. 집계 지표는 전부 기준선과 같거나
    좋았는데(hit@k 0.6667 -> 0.7333) `author-04` 한 건이 멈췄다는 이유로 빨갛게 됐습니다.
    기준선의 실패도 한 건이었고(`coauthor-03`) 실패율은 양쪽 다 0.0333이었습니다 —
    같은 비율에서 **어느 사례가 멈췄는지만** 달랐습니다.
    """
    baseline = load(BASELINE)
    stable = baseline["stable_cases"]
    cases = {
        case_id: {"failed": index == 0, "metrics": {} if index == 0
                  else {baseline["primary_metric"]: 1.0}}
        for index, case_id in enumerate(stable)
    }
    report = _report(cases, {"answer_failure_rate": 1 / len(stable)})
    assert not [v for v in check(baseline, report) if v.rule == "stable_case"]
    # 신호를 버리지 않습니다. 어느 사례였는지는 정보로 남습니다.
    assert transient_failures(baseline, report) == [stable[0]]


def test_a_stable_case_that_ran_and_scored_worse_is_still_a_regression():
    """채점까지 된 사례가 만점을 잃은 것은 변동이 아니라 더 나쁜 답입니다."""
    baseline = load(BASELINE)
    stable = baseline["stable_cases"]
    cases = {
        case_id: {
            "failed": False,
            "metrics": {baseline["primary_metric"]: 0.5 if index == 0 else 1.0},
        }
        for index, case_id in enumerate(stable)
    }
    violations = [v for v in check(baseline, _report(cases)) if v.rule == "stable_case"]
    assert len(violations) == 1
    assert stable[0] in violations[0].detail
    assert transient_failures(baseline, _report(cases)) == []


def test_counting_a_transient_twice_would_make_the_gate_red_most_days():
    """왜 두 번 세지 않는가를 수치로 고정합니다.

    사례당 실패율은 5.6~8.3%로 측정됐습니다(EXPERIMENTS.md 10절). 안정 사례가 29건이면
    적어도 한 건이 멈출 확률이 그 범위에서 아래와 같습니다. 터지는 게이트는 꺼집니다.
    """
    stable = len(load(BASELINE)["stable_cases"])
    for rate in (0.056, 0.083):
        at_least_one = 1 - (1 - rate) ** stable
        assert at_least_one > 0.7, (rate, at_least_one)


def test_many_failures_are_still_caught_by_the_aggregate_ceiling():
    """일시 실패를 사례별로 세지 않아도 계통적 고장은 막힙니다."""
    baseline = load(BASELINE)
    stable = baseline["stable_cases"]
    cases = {
        case_id: {"failed": True, "metrics": {}} for case_id in stable
    }
    report = _report(cases, {"answer_failure_rate": 1.0})
    rules = {v.rule for v in check(baseline, report)}
    assert "floor" in rules and "drop" in rules


def test_rewriting_the_baseline_keeps_the_values_a_person_set():
    """`splits`가 한 번 조용히 사라졌습니다. 그러면 smoke가 "25건 빠졌다"로 거짓 실패합니다."""
    from evals.gate import CARRIED_OVER, make_baseline

    existing = load(BASELINE)
    merged = {
        "mode": "compare",
        "dataset": "cases_graph.json",
        "runs": 2,
        "observed_spread": {},
        "metrics": {"answer_evidence_recall": 1.0},
        "cases": {
            case_id: {"failed": False, "metrics": {"answer_evidence_recall": 1.0}}
            for case_id in existing["case_ids"]
        },
    }
    rewritten = make_baseline(
        merged,
        primary_metric="answer_evidence_recall",
        floors=existing["absolute_floor"],
        drops=existing["allowed_drop"],
        note="테스트",
        carried={key: existing[key] for key in CARRIED_OVER if key in existing},
    )
    for key in CARRIED_OVER:
        assert rewritten.get(key) == existing.get(key), key


def test_a_case_that_failed_in_any_run_is_not_called_stable():
    """두 실행 중 한 번이라도 멈춘 사례는 "늘 통과하던 사례"가 아닙니다."""
    baseline = load(BASELINE)
    assert baseline["source"]["runs"] >= 2
    # 2026-10-16의 두 full 실행에서 각각 한 건이 멈췄고, 둘 다 안정 목록에서 빠졌습니다.
    assert set(baseline["case_ids"]) - set(baseline["stable_cases"]) >= {
        "author-04",
        "coauthor-03",
    }


def test_the_allowed_drop_is_not_tighter_than_the_measured_spread():
    """허용폭이 관측 변동폭보다 좁으면 품질 변화 없이 실패합니다.

    처음 추측한 0.05가 순위 지표의 관측 변동폭 0.0667보다 작았습니다.
    """
    baseline = load(BASELINE)
    spread = baseline["source"]["observed_spread"]
    for name, allowed in baseline["allowed_drop"].items():
        if name in spread:
            assert allowed >= spread[name], (name, allowed, spread[name])
