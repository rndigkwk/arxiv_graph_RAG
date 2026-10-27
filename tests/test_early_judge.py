"""조건 검사를 답 쓰기와 겹치기(`src/early_judge.py`, ADR-085).

- 근거 ID는 `answer`가 시작된 뒤에만 확정으로 읽습니다. 그 전에는 목록이 아직 늘어날 수 있습니다.
- 앞당긴 판정은 **입력(근거 ID·Cypher)이 같을 때만** 쓰입니다. 다르면 새로 판정합니다.
- 판정은 답 쓰기와 **동시에** 돕니다 - 그래프가 검사 노드에 닿기 전에 이미 시작해 있습니다.
- 앱 배선은 소스 모양으로 확인합니다. `chatbot`은 임포트만으로 Aura·OpenAI에 붙습니다.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from langchain_core.utils.json import parse_partial_json

from src import early_judge


def prefixes(text):
    return [text[:end] for end in range(1, len(text) + 1)]


def first_final(text):
    """스트림을 한 글자씩 받을 때 근거 ID가 처음 확정되는 위치와 그 값."""
    for prefix in prefixes(text):
        try:
            ids = early_judge.final_evidence(parse_partial_json(prefix))
        except ValueError:
            continue
        if ids is not None:
            return len(prefix), ids
    return None


def test_evidence_is_final_only_once_the_answer_starts():
    text = '{"evidence_ids": ["2401.00001", "2401.00002"], "answer": "Two papers cite it."}'
    at, ids = first_final(text)
    assert ids == ["2401.00001", "2401.00002"]
    # 답 글 대부분이 남아 있을 때 확정됩니다. 그 남은 시간이 판정과 겹치는 몫입니다.
    assert at <= text.index("Two")


def test_with_the_answer_first_the_evidence_comes_too_late_to_overlap():
    # 옛 필드 순서입니다. 근거가 끝에 오면 확정되는 때가 답이 다 써진 뒤라 겹칠 것이 없습니다.
    text = '{"answer": "Two papers cite it.", "evidence_ids": ["2401.00001"]}'
    at, _ = first_final(text)
    assert at > text.index("it.")


def test_evidence_that_is_not_a_list_of_strings_is_not_offered():
    assert early_judge.final_evidence({"evidence_ids": [1], "answer": ""}) is None
    assert early_judge.final_evidence({"evidence_ids": ["a"]}) is None
    assert early_judge.final_evidence(None) is None


def test_only_successful_graph_queries_are_passed_to_the_judge():
    calls = [
        {"name": "search_graph", "status": "success", "args": {"cypher": "A"}, "output": {}},
        {"name": "search_graph", "status": "success", "args": {"cypher": "B"},
         "output": {"error": "bad"}},
        {"name": "search_graph", "status": "error", "args": {"cypher": "C"}},
        {"name": "search_graph", "args": {"cypher": "D"}},  # 결과가 돌아오지 않은 호출
        {"name": "search_papers", "status": "success", "args": {"query": "x"}, "output": {}},
    ]
    assert early_judge.graph_queries(calls) == ["A"]


def test_the_early_result_is_used_only_for_the_same_inputs():
    seen = []
    judge = early_judge.EarlyJudge(lambda ids, queries: seen.append((ids, queries)) or ("p", "v"))
    assert judge.offer(["a"], ["Q"]) is True
    assert judge.offer(["a"], ["Q"]) is False  # 같은 입력은 두 번 돌리지 않습니다.
    assert judge.take(["a", "b"], ["Q"]) is None  # 근거가 바뀌었으면 쓰지 않습니다.
    assert judge.take(["a"], ["Q2"]) is None  # Cypher가 바뀌어도.
    future = judge.take(["a"], ["Q"])
    assert future.result() == ("p", "v")
    assert judge.take(["a"], ["Q"]) is None  # 한 번 넘기면 비웁니다.
    assert seen == [(["a"], ["Q"])]


def test_no_evidence_means_no_early_judgement():
    judge = early_judge.EarlyJudge(lambda ids, queries: pytest.fail("판정하면 안 됩니다"))
    assert judge.offer([], ["Q"]) is False
    assert judge.take([], ["Q"]) is None


def test_the_judgement_runs_while_the_answer_is_still_being_written():
    started, release = threading.Event(), threading.Event()

    def work(ids, queries):
        started.set()
        release.wait(2)
        return "papers", "verdict"

    judge = early_judge.EarlyJudge(work)
    judge.offer(["a"], [])
    # 그래프가 검사 노드에 닿기 전(= 답 글을 쓰는 중)에 이미 돌고 있습니다.
    assert started.wait(1)
    release.set()
    assert judge.take(["a"], []).result(timeout=2) == ("papers", "verdict")


def test_overlap_saves_the_shorter_of_the_two():
    judge = early_judge.EarlyJudge(lambda ids, queries: time.sleep(0.3) or ("p", "v"))
    begin = time.perf_counter()
    judge.offer(["a"], [])
    time.sleep(0.2)  # 답 글을 쓰는 시간
    judge.take(["a"], []).result()
    # 차례로 돌면 0.5초입니다. 겹치면 둘 중 긴 쪽(0.3초)에 가깝습니다.
    assert time.perf_counter() - begin < 0.45


def test_the_judgement_reports_how_much_of_it_was_hidden_behind_the_answer():
    """기다린 시간만으로는 "판정이 느렸나, 겹친 시간이 짧았나"를 가를 수 없었습니다(ADR-087)."""
    judge = early_judge.EarlyJudge(lambda ids, queries: time.sleep(0.3) or ("p", "v"))
    judge.offer(["a"], [])
    time.sleep(0.1)  # 답 글을 쓰는 시간
    ahead = judge.take(["a"], [])
    check_started = time.perf_counter()
    ahead.exception()
    timing = early_judge.timing(ahead, check_started, time.perf_counter() - check_started)

    assert timing["early"] is True
    assert 0.28 <= timing["judge_seconds"] < 0.45
    assert 0.08 <= timing["ahead_seconds"] < 0.2
    # 겹친 몫과 기다린 몫을 더하면 판정 시간입니다.
    assert abs(timing["ahead_seconds"] + timing["waited_seconds"] - timing["judge_seconds"]) < 0.03


def test_a_judgement_that_finished_before_the_check_waits_for_nothing():
    judge = early_judge.EarlyJudge(lambda ids, queries: ("p", "v"))
    judge.offer(["a"], [])
    ahead = judge.take(["a"], [])
    ahead.result()
    time.sleep(0.05)
    timing = early_judge.timing(ahead, time.perf_counter(), 0.0)
    # 다 숨었습니다 - 겹친 몫이 판정 시간 전부입니다(검사 노드가 늦게 온 시간까지 세지 않습니다).
    assert timing["ahead_seconds"] == timing["judge_seconds"]
    assert timing["waited_seconds"] == 0.0


def test_a_failed_judgement_is_raised_where_the_check_would_raise_it():
    with pytest.raises(ValueError, match="불완전"):
        early_judge.unpack_verdict(ValueError("인용 논문의 내용 판정 결과가 불완전합니다."))
    assert early_judge.unpack_verdict({"ok": True}) == {"ok": True}


# ── 배선: 앱이 실제로 쓰는 모양 ───────────────────────────────────────────────

SOURCE = Path("src/chatbot.py").read_text(encoding="utf-8")


def test_the_answer_schema_puts_the_evidence_first():
    """이 순서가 뒤집히면 시험은 전부 통과하고 겹치기만 조용히 사라집니다."""
    body = re.search(r"class GroundedAnswer\(BaseModel\):(.*?)\n\n\n", SOURCE, re.S).group(1)
    assert body.index("evidence_ids:") < body.index("answer:")


def test_the_app_offers_and_takes_the_early_judgement():
    assert "on_evidence=offer_early," in SOURCE
    assert "early.offer(ids, early_judge.graph_queries(calls))" in SOURCE
    assert "early.take(response[\"evidence_ids\"], queries)" in SOURCE
    # 앞당긴 쪽과 최종 응답이 같은 함수로 도구 기록을 읽어야 열쇠가 맞습니다.
    assert "calls = tool_calls_of(result[\"messages\"])" in SOURCE
    assert "on_evidence(ids, tool_calls_of(result.get(\"messages\", [])))" in SOURCE


def test_the_app_records_the_judgement_timing_on_the_answer():
    assert "response[\"condition_timing\"] = early_judge.timing(" in SOURCE
    assert "response[\"condition_timing\"] = {\"early\": False}" in SOURCE
