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
AGENT_NODES = ("translate", "resolve", "route", "generate", "collect", "check_format",
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


# ── ADR-066: 안쪽을 잰 뒤 ───────────────────────────────────────────────────


def inside(**kwargs):
    """`generate_trace`의 기본 모양. 앞 시도까지 합친 안쪽입니다."""
    return {"generate_seconds": 35.0, "tools_seconds": 2.0, "tool_calls": 3,
            "model_seconds": 28.0, "model_turns": 4, "unattributed_seconds": 0.0,
            "other_seconds": 5.0, "adds_up": True, "stray_ends": 0, "attached": 3,
            **kwargs}


def test_the_split_replaces_the_disclaimer_when_the_inside_was_measured():
    """**이 테스트가 ADR-066의 값입니다.** 괄호가 고백에서 분해로 바뀝니다."""
    response = {**trace({"generate": 35.0}), "generate_trace": inside()}

    line = app.latency_line(response)

    assert "모델 28.0초 (4턴)" in line
    assert "도구 2.0초 (3회)" in line
    assert "그 외 5.0초" in line
    assert app.GENERATE_CONTAINS not in line


def test_the_disclaimer_comes_back_when_nothing_was_measured():
    """옛 보고서와 콜백이 걸리지 않은 실행입니다. 빈칸보다 고백이 맞습니다."""
    assert app.GENERATE_CONTAINS in app.latency_line(trace(FAST))
    assert app.generate_split({}) == ""


def test_a_decomposition_that_does_not_add_up_shows_no_numbers():
    """잰 안쪽이 노드 시간보다 크면 두 번 센 것입니다. 수를 보여 주면 측정값처럼 읽힙니다."""
    doubled = inside(model_seconds=35.0, other_seconds=-2.0, adds_up=False)

    assert app.generate_split({"generate_trace": doubled}) == ""
    assert app.GENERATE_CONTAINS in app.latency_line(
        {**trace({"generate": 35.0}), "generate_trace": doubled})


def test_a_call_whose_name_was_lost_is_not_folded_into_the_remainder():
    """오류로 끝난 호출의 시간입니다. 숨기면 `그 외`가 그만큼 부풀어 보입니다."""
    split = app.generate_split({"generate_trace": inside(unattributed_seconds=1.5,
                                                         other_seconds=3.5)})

    assert "이름 못 붙임 1.5초" in split
    assert "그 외 3.5초" in split


def test_the_node_table_carries_the_split_too():
    rows = app.latency_rows({**trace({"generate": 35.0, "collect": 0.0}),
                             "generate_trace": inside()})
    generate = next(row for row in rows if row["노드"] == "답변 생성")

    assert "모델 28.0초" in generate["비고"]


def test_the_tool_table_leaves_an_unmeasured_call_blank():
    """0초를 적으면 재지 못한 것을 공짜라고 말하게 됩니다."""
    rows = app.tool_rows({"tool_calls": [
        {"id": "a", "name": "search_graph", "status": "success", "seconds": 1.234},
        {"id": "b", "name": "search_papers", "status": "success"},
    ]})

    assert [row["초"] for row in rows] == [1.23, None]
    assert rows[0]["도구"] == "Text2Cypher 관계 조회"


def test_the_tool_table_stays_empty_when_no_call_was_measured():
    """옛 실행에서 초가 전부 빈 표를 띄우면 "재었는데 0"으로 보입니다."""
    assert app.tool_rows({"tool_calls": [{"id": "a", "name": "search_graph"}]}) == []
    assert app.tool_rows({}) == []


def test_every_tool_the_agent_can_call_has_a_label():
    """도구가 늘었는데 표찰이 없으면 화면에 함수 이름이 나옵니다."""
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    listed = source[source.index("tools=[") + len("tools=["):]
    names = [name.strip() for name in listed[:listed.index("]")].split(",")]

    # 셋입니다. 관련 논문(ADR-086)과 번역(ADR-087)은 앱이 부릅니다.
    # 그 표찰은 앱이 붙인 기록이 씁니다.
    assert len(names) >= 3, names
    assert "rank_related_papers" in app.TOOL_LABEL
    assert "translate_to_english" in app.TOOL_LABEL
    for name in names:
        assert name in app.TOOL_LABEL, name
