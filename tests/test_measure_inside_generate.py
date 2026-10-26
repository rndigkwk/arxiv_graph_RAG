"""`generate` 안쪽 요약이 보고서와 맞는지, 그리고 차가운 첫 호출을 따로 세는지 봅니다."""

import json
from pathlib import Path

import pytest

from scripts import measure_inside_generate as measure


def report(rows):
    return {"metadata": {"model": "m"}, "results": rows}


def row(case, calls, generate=10.0, model=7.0, tools=3.0, retries=None):
    out = {"id": case, "graph_trace": {"total_seconds": generate + 1},
           "generate_trace": {"generate_seconds": generate, "model_seconds": model,
                              "tools_seconds": tools, "other_seconds": generate - model - tools,
                              "adds_up": True},
           "tool_seconds": [{"name": n, "seconds": s} for n, s in calls]}
    if retries is not None:
        out["sdk_retries"] = retries
    return out


def test_the_first_related_call_is_kept_apart_as_the_cold_load():
    summary = measure.summarize(report([
        row("a", [("rank_related_papers", 20.0)], tools=20.0, model=0.0, generate=20.0),
        row("b", [("rank_related_papers", 0.5), ("search_graph", 0.3)], tools=0.8, model=9.2),
    ]))
    assert summary["cold_first_related_call"] == {"case": "a", "seconds": 20.0}
    assert summary["tools"]["rank_related_papers"]["calls"] == 1
    assert summary["generate"]["tools_share_warm"] == pytest.approx(0.8 / 30, abs=1e-3)


def test_retries_are_summed_by_endpoint_and_missing_keys_are_not_counted():
    summary = measure.summarize(report([
        row("a", [], retries={"responses": 1}),
        row("b", [], retries={}),
        row("c", []),
    ]))
    assert summary["sdk_retries"] == {"recorded_cases": 2, "total": 1,
                                      "by_endpoint": {"responses": 1}}


def test_the_stored_summary_matches_the_stored_report():
    stored = json.loads(Path("reports/inside_generate.json").read_text(encoding="utf-8"))
    fresh = measure.summarize(json.loads(Path(stored["source"]).read_text(encoding="utf-8")))
    fresh["source"] = stored["source"]
    assert fresh == stored
