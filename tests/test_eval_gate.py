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
    RAGAS_FAITHFULNESS,
    RAGAS_STABLE_PRECISION,
    check,
    compares_aggregates,
    normalize,
    ragas_metrics,
    transient_failures,
)

FIXTURES = sorted((Path(__file__).parent / "fixtures" / "eval_gate").iterdir())
RULES = ("cases", "scoring_error", "floor", "drop", "stable_case", "judge_model")


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
    """손으로 정한 키는 **하나도** 사라지지 않아야 합니다.

    전 판은 "들고 올 키 목록"으로 `carried`를 만들고 같은 목록이 남았는지 확인해서,
    **목록에서 빠뜨린 키는 영원히 잡지 못했습니다.** 실제로 `splits`·`related_slot`·
    `model_registry`가 차례로 조용히 사라졌습니다. 이제 기준선 파일에 있는 키를 기준으로
    봅니다 - `make_baseline`이 스스로 계산하는 키만 빼고 전부 남아야 합니다.
    """
    from evals.gate import DERIVED_KEYS, carried_over, make_baseline

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
        carried=carried_over(existing),
    )
    for key, value in existing.items():
        if key in DERIVED_KEYS:
            continue
        assert rewritten.get(key) == value, f"{key}가 재작성에서 사라졌거나 바뀌었습니다"
    # 계산하는 키를 들고 오면 기준선이 **옛 측정값**을 새 측정값으로 적습니다. 그쪽도 막습니다.
    assert not (DERIVED_KEYS & set(carried_over(existing)))


def test_a_case_that_failed_in_any_run_is_not_called_stable():
    """두 실행 중 한 번이라도 멈춘 사례는 "늘 통과하던 사례"가 아닙니다.

    전에는 `author-04`·`coauthor-03`이 안정 목록에서 빠져 있다는 것으로 확인했습니다. 그건
    2026-10-16 두 실행에서 그 둘이 멈췄다는 **당시 사실**이고, 기준선을 다시 쓰면 성립하지
    않습니다(2026-10-18 라우팅 뒤 재측정에서 둘 다 깨끗했습니다). 고정해야 하는 것은 어느
    사례였는지가 아니라 `merge`+`make_baseline`의 **속성**이므로 합성 데이터로 봅니다.
    """
    from evals.gate import make_baseline, merge

    runs = [
        {"metrics": {"m": 1.0}, "cases": {
            "ok": {"failed": False, "metrics": {"m": 1.0}},
            "stopped-once": {"failed": False, "metrics": {"m": 1.0}},
            "dipped-once": {"failed": False, "metrics": {"m": 1.0}},
        }},
        {"metrics": {"m": 1.0}, "cases": {
            "ok": {"failed": False, "metrics": {"m": 1.0}},
            # 한 실행에서만 멈췄습니다.
            "stopped-once": {"failed": True, "metrics": {}},
            # 멈추지는 않았지만 만점을 잃었습니다.
            "dipped-once": {"failed": False, "metrics": {"m": 0.5}},
        }},
    ]
    baseline = make_baseline(
        merge(runs), primary_metric="m", floors={}, drops=None, note="테스트",
    )

    assert baseline["stable_cases"] == ["ok"]


def test_the_live_baseline_was_built_from_repeated_runs():
    """한 번 돌린 결과로 기준선을 쓰면 그 실행의 잡음이 기준이 됩니다."""
    baseline = load(BASELINE)

    assert baseline["source"]["runs"] >= 2
    assert set(baseline["stable_cases"]) <= set(baseline["case_ids"])


def test_the_allowed_drop_is_not_tighter_than_the_measured_spread():
    """허용폭이 관측 변동폭보다 좁으면 품질 변화 없이 실패합니다.

    처음 추측한 0.05가 순위 지표의 관측 변동폭 0.0667보다 작았습니다.
    """
    baseline = load(BASELINE)
    spread = baseline["source"]["observed_spread"]
    for name, allowed in baseline["allowed_drop"].items():
        if name in spread:
            assert allowed >= spread[name], (name, allowed, spread[name])


# --- 품질과 실패를 분리해 집계합니다 --------------------------------------------------


def test_quality_metrics_skip_the_cases_that_never_produced_an_answer():
    """멈춘 사례를 0점으로 섞으면 근거 recall이 품질이 아니라 실패율을 잽니다.

    2026-10-16 세 번째 full 실행이 이것으로 실패했습니다. 전체 평균으로는 0.9667 -> 0.9000이지만
    답을 만든 사례만 보면 **세 실행 모두 1.0000**이었습니다. 떨어뜨린 것은 더 나쁜 답이 아니라
    멈춘 사례 셋이었고, 그것은 `answer_failure_rate`가 자기 규칙으로 셉니다.
    """
    report = normalize({
        "mode": "compare",
        "summary": {"graph_rag": {}, "graph_rag_failures": 1},
        "results": [
            {"id": "ok-1", "graph_rag": {"id": "ok-1", "answer_evidence_recall": 1.0,
                                         "failed": False}},
            {"id": "ok-2", "graph_rag": {"id": "ok-2", "answer_evidence_recall": 1.0,
                                         "failed": False}},
            {"id": "dead", "graph_rag": {"id": "dead", "answer_evidence_recall": 0.0,
                                         "failed": True}},
        ],
    })
    # 멈춘 사례를 섞으면 0.667이 됩니다. 답을 만든 둘만 보면 1.0입니다.
    assert report["metrics"]["answer_evidence_recall"] == 1.0
    assert report["metrics"]["answer_failure_rate"] == pytest.approx(1 / 3)
    assert report["metrics"]["completed_cases"] == 2.0


def test_a_real_quality_regression_is_still_caught_after_the_split():
    """실패를 떼어 냈다고 품질 규칙이 느슨해지지 않아야 합니다."""
    baseline = load(BASELINE)
    stable = baseline["stable_cases"]
    primary = baseline["primary_metric"]

    def report_with(first_value: float) -> dict:
        return {
            "metrics": {
                primary: (first_value + (len(stable) - 1)) / len(stable),
                "answer_failure_rate": 0.0,
            },
            "cases": {
                case_id: {
                    "failed": False,
                    "metrics": {primary: first_value if index == 0 else 1.0},
                }
                for index, case_id in enumerate(stable)
            },
            "scoring_errors": 0,
        }

    # 한 사례가 0점이면 평균이 허용폭을 넘습니다.
    assert "drop" in {v.rule for v in check(baseline, report_with(0.0))}
    # 반만 맞히면 평균 변화는 작지만 사례별 회귀가 잡습니다.
    assert "stable_case" in {v.rule for v in check(baseline, report_with(0.5))}


def test_no_floor_can_be_breached_by_the_measured_spread_alone():
    """하한이 관측 범위 안에 있으면 품질 변화 없이 실패합니다.

    전에는 "순위 지표의 변동폭은 0.1보다 크다"처럼 2026-10-16의 **측정값**을 요구사항으로
    적어 뒀습니다. 라우팅(ADR-024)이 그 변동폭을 없애자 테스트가 깨졌습니다 — 좋아진
    것에 테스트가 걸린 것이므로, 고정해야 할 것은 수치가 아니라 **관계**입니다.

    이 규칙이 2026-10-18에 실제로 일을 했습니다. 라우팅 뒤 근거 precision이 0.9963,
    변동폭 0.0111이 됐는데 하한이 0.99였습니다. 여유가 0.0063뿐이라 변동 한 번으로
    터지고, 실제로 기준선을 만든 3회 중 한 번이 0.9889로 걸렸습니다. 하한을 0.95로
    넓혔습니다.
    """
    from evals.gate import _lower_is_better

    baseline = load(BASELINE)
    assert baseline["source"]["runs"] >= 3
    spread = baseline["source"]["observed_spread"]

    for name, floor in baseline["absolute_floor"].items():
        measured = baseline["metrics"].get(name)
        if measured is None or name not in spread:
            continue
        # 관측된 최악값이 하한을 넘지 않아야 합니다. 실패율처럼 작을수록 좋은 지표는
        # 방향이 반대입니다.
        worst = measured + spread[name] if _lower_is_better(name) else measured - spread[name]
        if _lower_is_better(name):
            assert worst < floor, (name, worst, floor)
        else:
            assert worst > floor, (name, worst, floor)



# --- RAGAS 지표 ---------------------------------------------------------------------


def _ragas_report(rows):
    return {"results": [{"id": f"c{i}", **row} for i, row in enumerate(rows)]}


def test_the_stable_family_precision_ignores_the_unstable_families():
    """전체 평균의 변동폭 0.1342를 `coauthor`·`reference`가 전부 만듭니다(11-12절)."""
    report = _ragas_report([
        {"family": "author", "faithfulness": 1.0, "context_precision": 1.0},
        {"family": "citing", "faithfulness": 1.0, "context_precision": 1.0},
        {"family": "coauthor", "faithfulness": 1.0, "context_precision": 0.0},
    ])
    metrics = ragas_metrics(report, ("author", "citing", "topcited"))

    # 불안정한 가족의 0.0이 섞이면 0.667이 됩니다. 섞지 않습니다.
    assert metrics[RAGAS_STABLE_PRECISION] == 1.0
    # faithfulness는 전체를 봅니다.
    assert metrics[RAGAS_FAITHFULNESS] == 1.0


def test_the_stable_family_list_comes_from_the_baseline_not_the_code():
    """어느 가족이 안정적인지는 측정으로 정해진 값입니다. 데이터가 바뀌면 다시 재야 합니다."""
    baseline = load(BASELINE)

    # 2026-10-18에 citing을 뺐습니다. citing-06(4회차)과 citing-02(5회차)가 0.1667로 무너져
    # 변동폭이 0.0000 -> 0.0463 -> 0.0926으로 계속 넓어졌습니다. author·topcited는 5회 전부
    # 1.0000입니다. **다시 좁히지 않습니다** - 또 무너지면 지표를 게이트에서 뺍니다.
    assert baseline["ragas_stable_families"] == ["author", "topcited"]
    report = _ragas_report([{"family": "author", "context_precision": 1.0}])
    assert RAGAS_STABLE_PRECISION not in ragas_metrics(report, ())


def test_a_ragas_regression_trips_the_floor():
    baseline = load(BASELINE)
    report = {
        "metrics": {
            **baseline["metrics"],
            # 결함을 심은 20건에서 관측된 수준입니다(11-10절: 모순·주제 이탈에서 0.0000).
            RAGAS_FAITHFULNESS: 0.4100,
        },
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["case_ids"]
        },
        "scoring_errors": 0,
    }
    broken = [v for v in check(baseline, report) if v.rule == "floor"]

    assert any(RAGAS_FAITHFULNESS in str(v) for v in broken), broken


def test_one_collapsed_case_in_a_stable_family_does_not_trip_the_floor():
    """이 단정은 2026-10-18에 뒤집혔습니다.

    3회 관측에서 안정 세 가족이 전부 1.0000이어서 "한 건이 무너지면 걸린다"로 하한을 0.95에
    뒀습니다. 4회차에서 `citing-06`이 0.1667로 무너져 0.9537이 됐고, 하한까지 여유가 0.0037뿐
    이었습니다. 변동폭 0은 3회의 착시였습니다.

    그래서 하한은 **파국만** 잡습니다. 한 건의 뒤집힘으로 터지면, 터지는 게이트는 꺼집니다.
    """
    baseline = load(BASELINE)
    report = {
        "metrics": {**baseline["metrics"], RAGAS_STABLE_PRECISION: 1.0 - 1 / 18},
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["case_ids"]
        },
        "scoring_errors": 0,
        "ragas_judge_model": baseline["ragas_judge_model"],
    }
    broken = [v for v in check(baseline, report) if v.rule == "floor"]

    assert not any(RAGAS_STABLE_PRECISION in str(v) for v in broken), broken
    # 그래도 여러 건이 무너지면 걸립니다.
    report["metrics"][RAGAS_STABLE_PRECISION] = 1.0 - 4 / 18
    broken = [v for v in check(baseline, report) if v.rule == "floor"]
    assert any(RAGAS_STABLE_PRECISION in str(v) for v in broken), broken


def test_a_full_run_without_ragas_fails_loudly():
    """full 판정에 RAGAS가 빠지면 조용히 통과해서는 안 됩니다.

    EVAL_GATE_REQUIRED와 같은 이유입니다 - 재지 못한 것을 통과로 적으면 게이트가 아닙니다.
    """
    baseline = load(BASELINE)
    report = {
        "metrics": {
            name: value for name, value in baseline["metrics"].items()
            if not name.startswith("ragas_")
        },
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["case_ids"]
        },
        "scoring_errors": 0,
    }
    broken = [str(v) for v in check(baseline, report) if v.rule == "floor"]

    assert any("ragas_faithfulness" in text and "없습니다" in text for text in broken), broken


def test_the_smoke_split_does_not_require_ragas():
    """smoke는 집계 규칙을 아예 적용하지 않으므로 RAGAS를 돌리지 않아도 됩니다(비용)."""
    baseline = load(BASELINE)
    report = {
        "metrics": {},
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["splits"]["smoke"]
        },
        "scoring_errors": 0,
    }

    assert not check(baseline, report, split="smoke")


def test_no_drop_or_per_case_rule_is_attached_to_the_ragas_metrics():
    """사례별 변동폭이 최악 1.0000입니다(`coauthor-02`). 붙이면 매번 터집니다."""
    baseline = load(BASELINE)

    for name in (RAGAS_FAITHFULNESS, RAGAS_STABLE_PRECISION):
        assert name in baseline["absolute_floor"]
        assert name not in (baseline["allowed_drop"] or {})


def test_a_report_scored_by_another_judge_is_not_compared():
    """2026-10-17에 CI가 OPENAI_MODEL을 넘기지 않아 기준선과 다른 모델로 채점했습니다.

    그 값을 같은 표에 올려놓고 비교했습니다. 모수가 다르면 비교하지 않는다는 `metrics_split`과
    같은 규칙이고, 그래서 게이트가 막습니다.
    """
    baseline = load(BASELINE)
    report = {
        "metrics": dict(baseline["metrics"]),
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["case_ids"]
        },
        "scoring_errors": 0,
        "ragas_judge_model": "gpt-4o-mini",
    }
    broken = [v for v in check(baseline, report) if v.rule == "judge_model"]

    assert broken and "gpt-6-luna" in str(broken[0])


def test_the_matching_judge_passes():
    baseline = load(BASELINE)
    report = {
        "metrics": dict(baseline["metrics"]),
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["case_ids"]
        },
        "scoring_errors": 0,
        "ragas_judge_model": baseline["ragas_judge_model"],
    }

    assert not [v for v in check(baseline, report) if v.rule == "judge_model"]


def test_the_smoke_split_does_not_check_the_judge():
    """smoke는 RAGAS를 돌리지 않습니다. 판정 모델을 요구하면 매 PR이 터집니다."""
    baseline = load(BASELINE)
    report = {
        "metrics": {},
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["splits"]["smoke"]
        },
        "scoring_errors": 0,
    }

    assert not [v for v in check(baseline, report, split="smoke") if v.rule == "judge_model"]


def test_runs_scored_by_different_judges_cannot_be_merged_into_one_baseline():
    """여러 실행을 합칠 때 판정 모델이 섞이면 하나로 적을 수 없습니다."""
    from evals.gate import merge

    runs = [
        {"metrics": {"a": 1.0}, "cases": {}, "ragas_judge_model": "gpt-6-luna"},
        {"metrics": {"a": 1.0}, "cases": {}, "ragas_judge_model": "gpt-4o-mini"},
    ]

    # 비워 두면 게이트가 불일치로 막습니다. 아무 하나를 골라 적지 않습니다.
    assert merge(runs)["ragas_judge_model"] == ""


def test_the_ragas_floors_catch_a_catastrophe_not_a_single_case_flip():
    """사례별 판정이 같은 답변에서도 0과 1을 왕복하므로 작은 회귀는 구별할 수 없습니다.

    하한의 일은 파국을 잡는 것입니다. 심은 결함에서 faithfulness가 0.0000~0.6100이었고
    (11-10절), 정상 실행은 4회 관측 0.9000~0.9833입니다. 하한이 그 사이에 있어야 합니다.
    """
    baseline = load(BASELINE)
    floor = baseline["absolute_floor"][RAGAS_FAITHFULNESS]

    # 심은 결함 최악(0.0000)과 ID 바꿔치기(0.6100)보다 위
    assert floor > 0.6100
    # 관측 최소(0.9000)보다 아래. 사례 몇 건이 뒤집혀도 터지지 않아야 합니다.
    assert floor < 0.9000
    # 0점 사례가 몇 건까지 허용되는지를 수치로 고정합니다(1건 = 1/30).
    allowed_zero_cases = int(round((1.0 - floor) * 30))
    assert allowed_zero_cases == 6, allowed_zero_cases


def test_a_catastrophic_ragas_drop_still_fails():
    baseline = load(BASELINE)
    report = {
        "metrics": {
            **baseline["metrics"],
            # 11-10절에서 모순·주제 이탈 답변이 받은 값입니다.
            RAGAS_FAITHFULNESS: 0.0,
            RAGAS_STABLE_PRECISION: 0.0,
        },
        "cases": {
            case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
            for case_id in baseline["case_ids"]
        },
        "scoring_errors": 0,
        "ragas_judge_model": baseline["ragas_judge_model"],
    }
    broken = {v.rule for v in check(baseline, report)}

    assert "floor" in broken


def test_the_family_set_is_not_narrowed_a_third_time():
    """하한을 관측에 맞춰 반복해 내리면 게이트가 장식이 됩니다.

    가족 집합은 한 번(3개 -> 2개) 좁혔고, 그 약속을 기준선의 근거에 적어 뒀습니다. 이 테스트는
    약속이 문서에서 사라지지 않게 고정합니다.
    """
    baseline = load(BASELINE)
    rationale = baseline["floor_rationale"]["ragas_context_precision_stable_families"]

    assert "다시 좁히지 않습니다" in rationale
    assert "게이트에서 뺍니다" in rationale
    assert len(baseline["ragas_stable_families"]) == 2


def test_every_floor_names_a_metric_the_harness_produces():
    """하한에 적힌 이름이 평가 보고서에 없으면 **게이트가 매번 실패합니다.**

    2026-10-19에 관련 논문 점수(`related_recall_at_k`)를 `absolute_floor`에 넣었습니다. 그
    지표는 다른 하네스가 따로 내므로 평가 보고서에 없고, 게이트는 "이번 결과에 없습니다"로
    위반을 올립니다. 로컬과 단위 CI는 `reports/eval_gate.json`이 없어 이 층을 건너뛰므로
    초록이었고, 다음 주간 크론에서야 터졌을 것입니다.

    그래서 실제로 기록된 실행 보고서로 확인합니다 - 하한의 모든 이름이 거기 있어야 합니다.
    """
    baseline = load(BASELINE)
    # **기준선을 쓴 보고서 세대**로 봐야 합니다. 더 옛 세대에는 그 뒤에 생긴 지표가 없고,
    # 그것을 "하한이 잘못됐다"로 읽으면 지표를 늘릴 수 없게 됩니다. 새것부터 찾습니다.
    candidates = (
        Path("reports/baseline_resolve_run1_setmetrics.json"),
        Path("reports/baseline_resolve_run1.json"),
        Path("reports/baseline_routed_run1_rescored.json"),
    )
    recorded = next((path for path in candidates if path.exists()), None)
    if recorded is None:
        pytest.skip(f"기록된 실행 보고서가 없습니다: {candidates[0]}")
    produced = set(normalize(load(recorded))["metrics"])
    # RAGAS 지표는 별도 보고서에서 합쳐집니다. 그 두 개는 이름으로 인정합니다.
    produced |= {RAGAS_FAITHFULNESS, RAGAS_STABLE_PRECISION}

    missing = sorted(set(baseline["absolute_floor"]) - produced)

    assert missing == [], missing


def _clean_cases(baseline: dict) -> dict:
    """Every case at full marks, so only the aggregate rules can fire."""
    return {
        case_id: {"failed": False, "metrics": {baseline["primary_metric"]: 1.0}}
        for case_id in baseline["case_ids"]
    }


def test_over_retrieval_is_caught_only_by_the_set_metrics():
    """순위 지표가 전부 만점인데 쓰레기를 함께 돌려주는 상태를 잡는 규칙은 하나뿐입니다.

    `evals/retrieval_metric_study.py`가 실제 보고서에 쓰레기를 심어 재 본 모양입니다 -
    30건 전부가 무관한 논문 5편을 함께 돌려주면 `hit_at_k`는 0.9000이고 `recall_at_k`는
    0.5844인데, **둘 다 자기 하한(0.5 / 0.48) 안입니다.** `retrieval_precision` 0.3474만
    하한을 깹니다(ADR-033).
    """
    baseline = load(BASELINE)
    report = _report(_clean_cases(baseline), {
        **baseline["metrics"],
        "hit_at_k": 0.9,
        "recall_at_k": 0.5844,
        "reciprocal_rank": 0.6822,
        "retrieval_recall": 1.0,
        "retrieval_precision": 0.3474,
    })
    violations = check(baseline, report)
    assert [v.rule for v in violations if "retrieval_precision" in v.detail] == ["floor", "drop"]
    # 순위 지표는 같은 상태에서 조용합니다. 그것이 집합 지표를 더한 이유입니다.
    assert not [v for v in violations if "hit_at_k" in v.detail or "recall_at_k" in v.detail]


def test_finding_the_gold_but_burying_it_is_not_called_a_recall_failure():
    """"못 찾음"과 "묻힘"을 가르는 것이 집합 recall의 일입니다.

    2026-10-16 `coauthor` 가족이 hit@5 0.1667이었는데 집합 recall은 1.0000이었습니다. 그때
    "검색 품질 문제"로 적었고 틀렸습니다 - 다 찾아 놓고 알파벳순에 밀린 것입니다. 그 상태에서
    `retrieval_recall`의 하한은 **울리지 않아야** 합니다.
    """
    baseline = load(BASELINE)
    report = _report(_clean_cases(baseline), {
        **baseline["metrics"],
        "hit_at_k": 0.7333,
        "recall_at_k": 0.7111,
        "reciprocal_rank": 0.7167,
        "retrieval_recall": 0.9667,
        "retrieval_precision": 0.7908,
    })
    violations = [v for v in check(baseline, report) if v.rule == "floor"]

    assert not [v for v in violations if "retrieval_recall" in v.detail]
    assert not [v for v in violations if "retrieval_precision" in v.detail]
