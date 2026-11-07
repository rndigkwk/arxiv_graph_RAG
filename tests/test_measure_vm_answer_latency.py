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


def test_the_share_within_ten_seconds_names_the_slow_ones_without_gating():
    rows = [row("a-1", 8.0), row("a-2", 12.0), row("a-3", 9.5), row("a-4", 10.0),
            row("b-1", 15.5), row("c-1", 0, outcome="abstain")]
    inside = m.summarize(rows, {})["within_target"]
    # 10초 정각은 안입니다. 답하지 못한 문항은 세지 않습니다.
    assert inside == {"count": 3, "of": 5, "share": 0.6,
                      "over": [{"id": "b-1", "seconds": 15.5}, {"id": "a-2", "seconds": 12.0}]}
    # 게이트는 그대로 p50입니다.
    assert m.summarize(rows, {})["meets_target"] is True
    assert m.summarize([], {})["within_target"] == {"count": 0, "of": 0, "share": None,
                                                    "over": []}


def test_cases_are_picked_per_family_in_file_order():
    cases = [{"id": f"{c}-{i}", "category": c} for c in ("author", "citing") for i in range(5)]
    picked = [c["id"] for c in m.pick_cases(cases, 2)]
    assert picked == ["author-0", "author-1", "citing-0", "citing-1"]


def test_the_judgement_and_the_last_turn_are_summarised_when_the_server_records_them():
    rows = [row("a-1", 8.0), row("a-2", 12.0), row("a-3", 9.0)]
    for each, (judge, ahead) in zip(rows, [(4.0, 2.0), (3.0, 3.0), (5.0, 1.0)]):
        each["condition_timing"] = {"early": True, "judge_seconds": judge,
                                    "ahead_seconds": ahead, "waited_seconds": judge - ahead}
        each["generate_trace"]["turns"] = [{"seconds": 1.5, "output_tokens": 30},
                                           {"seconds": 3.5, "output_tokens": 200}]
    rows.append(row("a-4", 10.0))  # 옛 서버 - 기록이 없으면 세지 않습니다

    summary = m.summarize(rows, {})
    assert summary["judge"] == {"early": 3, "timed": 3, "judge_seconds_p50": 4.0,
                                "ahead_seconds_p50": 2.0, "waited_seconds_p50": 2.0}
    assert summary["last_turn"] == {"traced": 3, "last_seconds_p50": 3.5,
                                    "earlier_seconds_p50": 1.5, "last_output_tokens_p50": 200}


def test_recent_cases_are_the_six_probe_questions_without_importing_the_app():
    cases = m.recent_cases()
    assert [case["id"] for case in cases] == [
        "gnn-en", "gnn-ko", "diffusion-en", "rlhf-ko", "agents-en", "retrieval-ko"]
    assert {case["category"] for case in cases} == {"recent"}


def test_korean_questions_are_timed_until_the_translation_arrives():
    """화면은 한국어 질문의 답을 받자마자 번역합니다(ADR-108).

    그 몫을 따로 적되 게이트는 그대로입니다.
    """
    korean = {**row("e-1", 6.0), "translate_seconds": 2.0}  # 받는 쪽 6.4초 + 번역 2초
    slow = {**row("e-2", 9.0), "translate_seconds": 3.0}  # 9.4 + 3 = 12.4초
    summary = m.summarize([korean, slow, row("a-1", 7.0)], {})
    assert summary["translation"]["translated"] == 2 and summary["translation"]["of"] == 3
    assert summary["translation"]["korean_ready_seconds"]["max"] == 12.4
    assert summary["translation"]["korean_ready_within_target"] == 1
    assert summary["meets_target"] is True  # 서버 p50 7초 - 번역은 게이트에 들어가지 않습니다


def test_examples_are_read_from_the_question_screen_and_all_get_translated():
    cases = m.example_cases()
    assert len(cases) >= 5
    assert all(m.needs_translation(case["question"]) for case in cases)
    assert not m.needs_translation("Which papers cite ReAct?")
