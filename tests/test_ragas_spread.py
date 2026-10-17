"""변동폭 집계가 두 종류의 변동을 구별하는지 검증합니다.

ADR-017에서 2회 실행으로 정한 허용폭이 3회차에서 깨졌습니다. 그 교훈은 "표본을 늘려라"만이
아니라 **무엇의 변동인지 말하라**는 것이었습니다 — 같은 답변을 판정만 다시 해서 생긴 변동과,
에이전트가 새로 답해서 생긴 변동은 고칠 곳이 다릅니다.
"""

from evals.ragas_spread import by_family, kind_of, per_case, spread, summarize


def run(path, source, metrics, cases, judge="gpt-6-luna", families=None):
    return {
        "path": path,
        "label": "",
        "source_report": source,
        "judge_model": judge,
        "metrics": metrics,
        "by_family": families or {},
        "cases": cases,
    }


def case(**metrics):
    return {"faithfulness": 1.0, "answer_relevancy": 0.8, "context_precision": 1.0, **metrics}


def test_spread_reports_the_range_not_only_the_mean():
    result = spread([0.9, 1.0, 0.95])

    assert result["runs"] == 3
    assert result["min"] == 0.9
    assert result["max"] == 1.0
    assert result["spread"] == 0.1


def test_runs_from_one_input_report_are_judge_only_variation():
    """같은 답변을 판정만 다시 한 변동입니다. 고칠 곳은 앱이 아니라 판정 설계입니다."""
    runs = [
        run("a.json", "reports/input.json", {"faithfulness": 1.0}, {"x": case()}),
        run("b.json", "reports/input.json", {"faithfulness": 0.9}, {"x": case(faithfulness=0.9)}),
    ]

    assert kind_of(runs) == "judge_only"
    assert "판정 모델만" in summarize(runs)["kind_note"]


def test_runs_from_different_input_reports_are_end_to_end_variation():
    runs = [
        run("a.json", "reports/input1.json", {"faithfulness": 1.0}, {"x": case()}),
        run("b.json", "reports/input2.json", {"faithfulness": 0.9}, {"x": case(faithfulness=0.9)}),
    ]

    assert kind_of(runs) == "end_to_end"
    assert "합한" in summarize(runs)["kind_note"]


def test_a_single_run_is_not_called_judge_only():
    # 입력이 하나뿐이라는 것만으로 판정 변동이라고 적으면 안 됩니다. 비교할 쌍이 필요합니다.
    runs = [run("a.json", "", {"faithfulness": 1.0}, {"x": case()})]

    assert kind_of(runs) == "end_to_end"


def test_the_worst_case_is_reported_per_metric():
    """실행 평균이 안정적이어도 사례 하나가 크게 흔들릴 수 있습니다."""
    runs = [
        run("a.json", "i1", {}, {"calm": case(), "wild": case(faithfulness=1.0)}),
        run("b.json", "i2", {}, {"calm": case(), "wild": case(faithfulness=0.0)}),
    ]
    worst = per_case(runs)["worst_case_per_metric"]

    assert worst["faithfulness"]["case"] == "wild"
    assert worst["faithfulness"]["spread"] == 1.0


def test_cases_missing_from_a_run_are_named_not_dropped_silently():
    """실패로 빠진 사례를 조용히 빼면 평균이 무엇의 평균인지 알 수 없습니다."""
    runs = [
        run("a.json", "i1", {}, {"both": case(), "only_here": case()}),
        run("b.json", "i2", {}, {"both": case()}),
    ]
    result = per_case(runs)

    assert result["cases_compared"] == 1
    assert result["cases_only_in_some_runs"] == ["only_here"]


def test_families_are_compared_separately():
    """가족마다 분포가 완전히 다르므로(1.0000 대 0.2183) 전체 평균으로는 못 봅니다."""
    runs = [
        run("a.json", "i1", {}, {}, families={"citing": {"context_precision": 1.0}}),
        run("b.json", "i2", {}, {}, families={"citing": {"context_precision": 0.8}}),
    ]
    result = by_family(runs)

    assert result["citing"]["context_precision"]["spread"] == 0.2


def test_a_metric_measured_in_only_one_run_is_skipped():
    """끈 지표를 한 쪽에만 있는 값으로 비교하면 변동폭이 0으로 보입니다."""
    runs = [
        run("a.json", "i1", {"context_relevance": 1.0}, {"x": case()}),
        run("b.json", "i2", {}, {"x": case()}),
    ]

    assert "context_relevance" not in summarize(runs)["overall"]
