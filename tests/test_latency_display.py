"""화면의 단계별 시간이 **잰 것만** 말하는지 지킵니다(ADR-065, 11-46절).

지연 분해의 결론은 두 가지였습니다.

1. 두 노드가 전부입니다 — `generate` 72.8%, `check_conditions` 27.2%. 나머지는 0.00초.
2. **`generate` 안쪽은 재지 못했습니다.** 번역·Text2Cypher·벡터·모델 턴이 모두 그 안에
   있고 도구 호출에 소요 시간이 기록되지 않습니다.

그래서 화면이 "답변 생성 35.1초"만 적으면 **생성이 느리다고 읽힙니다.** 사실은 "그 안의
무엇이 느린지 모른다"입니다. 이 파일이 지키는 것은 그 구별입니다.

그리고 캡션에 적는 기준선(p50·최대)이 보고서와 **같은 수인지** 봅니다. 화면에 박힌 수가
조용히 늙는 것은 ADR-016에서 한 번 일어났습니다(259MB vs 266.5MB).
"""

import json
from pathlib import Path

import app

LATENCY = Path("reports/answer_latency.json")
AGENT_NODES = ("route", "generate", "collect", "check_format",
               "check_conditions", "revise", "accept", "abstain")


def trace(seconds, *, attempts=1, visits=None):
    return {"graph_trace": {
        "seconds": seconds,
        "visits": visits or {node: 1 for node in seconds},
        "attempts": attempts,
        "total_seconds": round(sum(seconds.values()), 3),
    }}


FAST = {"route": 0.003, "generate": 6.7, "collect": 0.0,
        "check_format": 0.0, "check_conditions": 3.1, "accept": 0.0}


def test_no_trace_shows_nothing_rather_than_zero():
    """추적이 없을 때 "0.0초"를 적으면 빠른 답변으로 보입니다."""
    assert app.latency_line({}) == ""
    assert app.latency_line({"graph_trace": {}}) == ""
    assert app.latency_rows({}) == []


def test_the_line_leads_with_the_total():
    line = app.latency_line(trace(FAST))

    assert line.startswith("지연 합계 9.8초")


def test_generate_says_what_it_contains():
    """**이 테스트가 이 파일의 이유입니다.** 이 말이 없으면 생성이 느리다고 읽힙니다."""
    line = app.latency_line(trace(FAST))

    assert "답변 생성" in line
    assert app.GENERATE_CONTAINS in line
    assert "Cypher" in app.GENERATE_CONTAINS


def test_only_the_two_heaviest_nodes_reach_the_caption():
    """0.00초 노드를 캡션에 적으면 줄만 길어지고 아무것도 더 알려 주지 않습니다."""
    line = app.latency_line(trace(FAST))

    assert "조건 검사" in line
    assert "결과 수집" not in line
    assert "경로 판정" not in line


def test_a_retry_is_not_hidden():
    """재시도가 느린 답변의 흔한 이유입니다. 숨기면 합계만 보고 모델을 의심하게 됩니다."""
    once = app.latency_line(trace(FAST))
    twice = app.latency_line(trace(FAST, attempts=2))

    assert "시도" not in once
    assert "시도 2회" in twice


def test_the_table_keeps_the_free_nodes():
    """0.00초라는 것이 분해의 결론입니다. 지우면 그 결론이 화면에서 사라집니다."""
    rows = app.latency_rows(trace(FAST))
    labels = [row["노드"] for row in rows]

    assert "결과 수집" in labels
    assert "경로 판정" in labels
    assert any(row["초"] == 0.0 for row in rows)


def test_the_table_is_sorted_and_totals_last():
    rows = app.latency_rows(trace(FAST))
    seconds = [row["초"] for row in rows[:-1]]

    assert seconds == sorted(seconds, reverse=True)
    assert rows[-1]["노드"] == "합계"
    assert rows[-1]["초"] == 9.8


def test_the_total_row_matches_the_trace():
    """합계를 다시 더하면 추적이 준 값과 같아야 합니다."""
    rows = app.latency_rows(trace(FAST))
    parts = sum(row["초"] for row in rows[:-1])

    assert abs(parts - rows[-1]["초"]) < 0.01


def test_every_graph_node_has_a_label():
    """StateGraph에 노드가 늘었는데 표찰이 없으면 화면에 영어 이름이 나옵니다."""
    for node in AGENT_NODES:
        assert app.NODE_LABEL.get(node), node


def test_an_unknown_node_falls_back_to_its_name():
    """표찰이 없어도 행이 사라지지는 않아야 합니다."""
    rows = app.latency_rows(trace({"brand_new_node": 1.5}))

    assert rows[0]["노드"] == "brand_new_node"


def test_the_baseline_on_screen_matches_the_report():
    """**화면에 박힌 수가 조용히 늙지 않게.** ADR-016에서 한 번 일어났습니다."""
    if not LATENCY.exists():
        return
    report = json.loads(LATENCY.read_text(encoding="utf-8"))
    routed = report["modes"]["routed"]

    assert round(routed["p50"], 1) == app.BASELINE_P50_SECONDS
    assert round(routed["max"], 1) == app.BASELINE_MAX_SECONDS


def test_the_related_tool_is_not_called_pagerank_in_the_process_panel():
    """캡션만 고치고 이 표를 놓치면 같은 거짓이 한 칸 아래 남습니다."""
    source = Path("app.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    assert '"rank_related_papers": "개인화 PageRank"' not in code
    assert "공동인용" in code
