"""VM 지연 측정(`scripts/measure_vm_answer_latency.py`)의 요약. 네트워크 없이 돕니다."""

from __future__ import annotations

from scripts import measure_vm_answer_latency as m


def row(case_id, total, *, category="author", turns=3, model=6.0, outcome="final"):
    done = outcome == "final"
    return {
        "id": case_id, "category": category, "outcome": outcome,
        "client_seconds": total + 0.4, "first_partial_seconds": 2.0,
        "graph_trace": {"seconds": {"generate": total - 3, "check_conditions": 3.0, "route": 0.0},
                        "total_seconds": total} if done else None,
        "generate_trace": {"adds_up": True, "model_turns": turns, "model_seconds": model,
                           "tools_seconds": 1.0, "generate_seconds": total - 3} if done else None,
        "tools": [{"name": "search_graph", "seconds": 0.3}] if done else [],
    }


def test_the_target_is_the_server_p50_and_failures_are_named():
    rows = [row("a-1", 8.0), row("a-2", 12.0), row("a-3", 9.0), row("c-1", 0, outcome="abstain")]
    summary = m.summarize(rows, {"a-1": 14.0, "a-2": 16.0})
    assert summary["answered"] == 3
    assert summary["not_answered"] == [{"id": "c-1", "outcome": "abstain"}]
    assert summary["server_seconds"]["p50"] == 9.0
    assert summary["meets_target"] is True
    assert summary["nodes"][0]["node"] == "generate"
    assert summary["vs_local"] == {"pairs": 2, "local_p50": 16.0, "vm_p50": 12.0}


def test_a_slow_middle_misses_the_target():
    summary = m.summarize([row("a-1", 8.0), row("a-2", 12.0), row("a-3", 13.0)], {})
    assert summary["meets_target"] is False
    assert summary["vs_local"]["pairs"] == 0


def test_cases_are_picked_per_family_in_file_order():
    cases = [{"id": f"{c}-{i}", "category": c} for c in ("author", "citing") for i in range(5)]
    picked = [c["id"] for c in m.pick_cases(cases, 2)]
    assert picked == ["author-0", "author-1", "citing-0", "citing-1"]
