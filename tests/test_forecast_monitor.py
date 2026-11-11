"""배포된 공동인용 예측 모델의 월간 감시 시험입니다(ADR-124).

감시가 지키는 약속은 둘입니다.

- **학습 평가와 같은 과제를 잰다.** 피처 그래프와 라벨을 `dataset`과 같은 정의로 만들고,
  판정은 승격과 같은 `model_registry.judge`입니다.
- **나빠졌는데 ok라고 하지 않는다.** 바 아래, 실패, 멈춘 타이머를 각각 문제로 셉니다.
"""

import collections
import datetime as dt
import json
import random

import numpy as np
import pytest

from models.link_prediction import dataset, monitor
from pipeline.snapshot import month_label
from scripts.visit_deployed_app import forecast_problem
from src import forecast_monitor_status

NOW = dt.datetime(2026, 11, 11, 5, 0, tzinfo=dt.UTC)


def random_references(seed=7, citers=60, papers=80):
    rng = random.Random(seed)
    references = collections.defaultdict(set)
    for index in range(citers):
        citer = f"26{rng.randint(5, 10):02d}.{index:05d}"
        count = rng.randint(0, 12)
        references[citer] = {f"2601.{rng.randrange(papers):05d}" for _ in range(count)}
    # 참고문헌이 150편을 넘는 논문은 공동인용에서 뺍니다. 감시도 같아야 합니다.
    references["2609.99999"] = {f"2601.{i:05d}" for i in range(151)}
    return references


def test_feature_adjacency_matches_the_training_definition():
    references = random_references()
    citers = set(references)
    expected = dataset.adjacency_of(dataset.cocitation_pairs(references, citers))
    assert monitor.feature_adjacency(references, citers) == expected


def test_new_labels_are_pairs_the_feature_graph_does_not_have_yet():
    references = random_references()
    before = {c for c in references if c < "2609"}
    during = {c for c in references if c.startswith("2609")}
    feature = monitor.feature_adjacency(references, before)
    expected = (dataset.cocitation_pairs(references, during)
                - dataset.cocitation_pairs(references, before))
    assert monitor.new_labels(references, during, feature) == expected


@pytest.mark.parametrize(("now", "label"), [
    (dt.datetime(2026, 11, 3, tzinfo=dt.UTC), "2026-10"),
    (dt.datetime(2027, 1, 3, tzinfo=dt.UTC), "2026-12"),
])
def test_the_default_month_is_the_last_complete_one(now, label):
    assert month_label(monitor.previous_month(now)) == label


def row(name, kind, mrr, interval=None):
    result = {"model": name, "detail": {"kind": kind}, "ranking_hard_negatives": {"mrr": mrr}}
    if interval is not None:
        result["mrr_vs_best_heuristic"] = {"baseline": "adamic-adar", **interval}
    return result


def test_the_verdict_is_the_promotion_judgment():
    passing = {"counts": {"groups": 3000}, "results": [
        row("adamic-adar", "heuristic", 0.63),
        row("graph-text-gbm", "gbm", 0.66,
            {"mean": 0.03, "low": 0.02, "high": 0.04, "excludes_zero": True}),
    ]}
    assert monitor.verdict(passing, {})["status"] == "ok"
    # 하한 0.60 아래면 구간이 0을 빼도 바 아래입니다.
    low = {"counts": {"groups": 3000}, "results": [
        row("adamic-adar", "heuristic", 0.50),
        row("graph-text-gbm", "gbm", 0.55,
            {"mean": 0.05, "low": 0.03, "high": 0.07, "excludes_zero": True}),
    ]}
    judged = monitor.verdict(low, {})
    assert judged["status"] == "below_bar" and any("하한" in r for r in judged["reasons"])
    # 그룹이 모자라면 판정하지 않습니다.
    short = monitor.verdict({"counts": {"groups": 12}, "results": []}, {})
    assert short["status"] == "insufficient"


class Constant:
    """일부러 쓸모없는 모델: 모든 후보에 같은 점수를 줍니다. 동점은 나쁜 쪽 순위입니다."""

    name = "graph-text-gbm"

    def describe(self):
        return {"kind": "gbm", "fitted": True}

    def score(self, features):
        return np.zeros(len(features))


def test_a_useless_model_falls_below_the_bar(monkeypatch):
    rng = random.Random(3)
    references = collections.defaultdict(set)
    papers = [f"2601.{i:05d}" for i in range(400)]
    for index in range(900):
        month = "2609" if index < 600 else "2610"
        references[f"{month}.{index:05d}"] = set(rng.sample(papers, 6))
    table = monitor.LiveTable(
        {"arxiv_id": p, "primary_category": "cs.AI", "categories": ["cs.AI"]} for p in papers
    )
    monkeypatch.setattr(monitor, "MIN_GROUPS", 50)

    def vectors(ids):
        return ((arxiv_id, [rng.random() for _ in range(4)]) for arxiv_id in ids)

    scored = monitor.score(references, table, 2026 * 12 + 9, vectors, Constant())
    assert scored["counts"]["groups"] >= 50
    names = [r["model"] for r in scored["results"]]
    assert names == ["common-neighbors", "adamic-adar", "graph-text-gbm"]
    gbm = scored["results"][-1]
    # 모든 후보가 동점이면 진짜 상대는 10위입니다 - MRR 0.1.
    assert gbm["ranking_hard_negatives"]["mrr"] == pytest.approx(0.1)
    assert monitor.verdict(scored, {})["status"] == "below_bar"


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def status(tmp_path, *, last=None, report=None):
    reports = tmp_path / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    last_path = tmp_path / "last.json"
    if report is not None:
        write(reports / "r.json", report)
    if last is not None:
        last.setdefault("report", str(reports / "r.json"))
        write(last_path, last)
    return forecast_monitor_status.summary(now=NOW, last_run_path=last_path, reports=reports)


REPORT = {
    "month": "2026-10", "status": "ok", "model": {"rung": "graph-text-gbm"},
    "counts": {"groups": 3000},
    "results": [row("graph-text-gbm", "gbm", 0.6614,
                    {"mean": 0.0312, "low": 0.0215, "high": 0.0406, "excludes_zero": True})],
}
DONE = "2026-11-03T04:20:00Z"


def test_never_run_is_unknown_not_a_problem(tmp_path):
    result = status(tmp_path)
    assert result["status"] == "unknown"
    assert forecast_problem({"forecast_monitor": result}) is None


def test_a_passing_month_is_ok(tmp_path):
    result = status(tmp_path, last={"finished_at": DONE, "exit_code": 0}, report=REPORT)
    assert result["status"] == "ok"
    assert result["report"]["mrr"] == 0.6614
    assert result["report"]["interval"] == [0.0215, 0.0406]


@pytest.mark.parametrize(("last", "report", "problem"), [
    ({"finished_at": DONE, "exit_code": 0}, {**REPORT, "status": "below_bar"}, "below_bar"),
    ({"finished_at": DONE, "exit_code": 1}, None, "monitor_failed"),
    ({"finished_at": "2026-09-03T04:20:00Z", "exit_code": 0}, REPORT, "monitor_stale"),
    ({"finished_at": DONE, "exit_code": 0}, None, "no_report"),
])
def test_each_failure_is_a_problem(tmp_path, last, report, problem):
    result = status(tmp_path, last=last, report=report)
    assert result["status"] == "problem" and problem in result["problems"]
    assert forecast_problem({"forecast_monitor": result}).startswith("공동인용 예측 모델 감시")


def test_a_report_outside_the_reports_folder_is_not_read(tmp_path):
    outside = tmp_path / "elsewhere.json"
    write(outside, REPORT)
    result = status(tmp_path, last={"finished_at": DONE, "exit_code": 0, "report": str(outside)})
    assert "no_report" in result["problems"]
