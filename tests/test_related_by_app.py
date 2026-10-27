"""관련 논문은 앱이 부릅니다(`src/related_by_app.py`, ADR-086).

- 시드는 지시문이 모델에게 시키던 규칙과 같습니다: 제목으로 확정한 논문, 없으면 근거 앞 3편.
- 근거가 없으면(순수 집계) 부르지 않습니다.
- 결과는 도구 호출 기록 모양으로 붙어, 화면의 캡션이 "관련 논문"을 그대로 읽습니다.
- 앱 배선은 소스 모양으로 확인합니다. `chatbot`은 임포트만으로 Aura·OpenAI에 붙습니다.
"""

from __future__ import annotations

from pathlib import Path

from src import prompts, related_by_app


def resolve(arxiv_id, *, ambiguous=False, status="success"):
    return {"name": "resolve_paper", "status": status,
            "output": {"arxiv_id": arxiv_id, "ambiguous": ambiguous}}


def test_the_named_paper_is_the_seed():
    calls = [resolve("2303.11366"), {"name": "search_graph", "status": "success", "output": {}}]
    # 인용하는 논문들이 근거여도, 질문이 지칭한 논문이 시드입니다.
    assert related_by_app.seeds(calls, ["2401.1", "2401.2"]) == ["2303.11366"]


def test_without_a_named_paper_the_first_three_pieces_of_evidence_seed_it():
    ids = ["a", "b", "a", "c", "d"]
    assert related_by_app.seeds([], ids) == ["a", "b", "c"]


def test_a_paper_the_model_had_to_pick_is_not_taken_on_trust():
    """후보가 비슷하면 모델이 골랐습니다. 1위를 시드로 쓰면 다른 논문일 수 있습니다."""
    calls = [resolve("2303.11366", ambiguous=True)]
    assert related_by_app.seeds(calls, ["x"]) == ["x"]


def test_an_unresolved_or_failed_title_is_not_a_seed():
    calls = [resolve(None), resolve("2303.11366", status="error")]
    assert related_by_app.seeds(calls, ["x"]) == ["x"]


def test_a_pure_aggregate_gets_no_related_call():
    assert related_by_app.seeds([], []) == []


def test_the_result_reads_like_a_tool_call():
    output = {"seed_ids": ["a"], "unknown_ids": [], "related": [{"arxiv_id": "b"}]}
    record = related_by_app.call_record(["a"], output, 0.81234)
    assert record["name"] == "rank_related_papers" and record["by"] == "app"
    assert record["status"] == "success" and record["output"] is output
    assert record["args"] == {"arxiv_ids": ["a"]} and record["seconds"] == 0.812


def test_a_failed_ranking_leaves_an_empty_table_not_a_failed_answer():
    record = related_by_app.call_record(["a"], RuntimeError("neo4j down"), 0.1)
    assert record["status"] == "error"
    assert record["output"] == {"error": "neo4j down", "related": []}


# ── 배선 ───────────────────────────────────────────────────────────────────

SOURCE = Path("src/chatbot.py").read_text(encoding="utf-8")


def test_the_model_can_no_longer_call_the_related_tool():
    listed = SOURCE[SOURCE.index("tools=[") + len("tools=["):]
    assert "rank_related_papers" not in listed[:listed.index("]")]
    assert "call rank_related_papers" not in prompts.AGENT_SYSTEM


def test_the_app_ranks_related_papers_with_the_judgement_and_attaches_them():
    assert "early_related.offer(related_by_app.seeds(calls, ids), ())" in SOURCE
    assert 'related_by_app.seeds(response["tool_calls"], response["evidence_ids"])' in SOURCE
    assert 'response["tool_calls"].append(record)' in SOURCE
    # 보류한 답에는 붙이지 않습니다 - 붙이는 자리가 보류 예외 뒤입니다.
    assert SOURCE.index("raise AnswerValidationError(\n            \"답변의 형식") < SOURCE.index(
        'response["tool_calls"].append(record)')
