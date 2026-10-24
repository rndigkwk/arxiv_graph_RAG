"""생성된 Cypher가 문자열로 변환된 값을 정렬하는지 검사합니다.

이 오류가 위험한 이유는 질의가 **성공**한다는 점입니다. `toString(n) AS c ... ORDER BY c`는
사전 순서로 정렬하므로 내림차순에서 피인용 9회가 88회보다 위로 올라오는데, 오류가 없어서
아래쪽 어디에서도 순위가 틀어진 것을 알 수 없습니다. 조용히 틀린 행보다 오류가 낫습니다.

프롬프트 규칙만으로는 빈도가 47.1%에서 5.9%로 줄었을 뿐 0이 되지 않았습니다. 그래서 검사로
막습니다. 표시용으로 문자열을 만들면서 정렬은 숫자로 하는 경우를 통과시키는 것이 핵심입니다.
"""

import pytest

from src.cypher_validation import check_order_by, stringified_order_keys

AGGREGATE = (
    'MATCH (p:ArxivPaper) WHERE p.primary_category = "cs.CV" '
    "WITH p, COUNT { (p)<-[:REFERENCES]-() } AS citations "
    "RETURN p.arxiv_id AS answer_value, [p.arxiv_id] AS evidence_ids, "
)


def test_sorting_a_stringified_count_is_flagged():
    cypher = (
        AGGREGATE
        + "toString(citations) AS citation_count ORDER BY citation_count DESC LIMIT 3"
    )

    assert stringified_order_keys(cypher) == ["citation_count"]
    with pytest.raises(ValueError, match="문자열로 변환된 값"):
        check_order_by(cypher)


def test_sorting_the_numeric_alias_passes():
    cypher = AGGREGATE + "citations AS citation_count ORDER BY citation_count DESC LIMIT 3"

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_stringifying_for_display_while_sorting_numerically_passes():
    # 표시용으로 문자열을 만드는 것은 정상입니다. 정렬 키가 숫자면 순위가 맞습니다.
    cypher = (
        AGGREGATE + "toString(citations) AS shown, citations AS n ORDER BY n DESC LIMIT 3"
    )

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_a_stringified_key_in_a_multi_key_sort_is_flagged():
    cypher = (
        AGGREGATE
        + "toString(citations) AS citation_count, p.title AS title "
        "ORDER BY title ASC, citation_count DESC LIMIT 3"
    )

    assert stringified_order_keys(cypher) == ["citation_count"]
    with pytest.raises(ValueError):
        check_order_by(cypher)


def test_a_stringified_alias_that_is_never_sorted_passes():
    cypher = AGGREGATE + "toString(p.published) AS published ORDER BY citations DESC LIMIT 3"

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_the_published_timestamp_may_still_be_stringified_for_display():
    # 기존 질의가 toString(p.published)를 쓰고 있으므로 이것이 막히면 안 됩니다.
    cypher = (
        "MATCH (p:ArxivPaper) RETURN p.arxiv_id AS answer_value, "
        "toString(p.published) AS published ORDER BY p.published DESC LIMIT 5"
    )

    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


def test_case_and_spacing_do_not_hide_the_problem():
    cypher = AGGREGATE + "TOSTRING( citations )  AS  cc  order by  cc  desc  limit 3"

    assert stringified_order_keys(cypher) == ["cc"]


def test_a_limit_after_the_sort_does_not_swallow_the_key():
    assert stringified_order_keys(
        AGGREGATE + "toString(citations) AS cc ORDER BY cc DESC SKIP 1 LIMIT 3"
    ) == ["cc"]


@pytest.mark.parametrize("cypher", ["", "MATCH (p) RETURN p.arxiv_id AS answer_value"])
def test_queries_without_ordering_pass(cypher):
    assert stringified_order_keys(cypher) == []
    check_order_by(cypher)


# --- 외부 입출력과 조회 전용 판정 (ADR-048) -------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

from src.cypher_validation import (  # noqa: E402
    ALLOWED_PROCEDURES,
    READ_ONLY_QUERY_TYPE,
    check_external_io,
    refuse_unless_read_only,
)

PROBE_REPORT = Path("reports/cypher_guard_probe.json")


def test_load_csv_is_refused_even_though_its_plan_is_read_only():
    """2026-10-20에 가드를 때려서 알았습니다 — 유형이 'r'이라 유형 검사는 통과시킵니다."""
    with pytest.raises(ValueError, match="LOAD CSV"):
        check_external_io(
            "LOAD CSV FROM 'http://169.254.169.254/latest/meta-data/' AS row RETURN row"
        )
    # 대문자·공백을 바꿔도 같습니다.
    with pytest.raises(ValueError):
        check_external_io("load  csv  from 'http://x/' AS r RETURN r")


def test_the_apoc_load_procedures_this_instance_actually_has_are_refused():
    """`SHOW PROCEDURES`로 확인한 것들입니다. 서버가 막아 줄 것이라 가정하지 않습니다."""
    for name in ("apoc.load.json", "apoc.load.jsonArray", "apoc.load.xml"):
        with pytest.raises(ValueError, match="허용하지 않은 프로시저"):
            check_external_io(f"CALL {name}('http://169.254.169.254/') YIELD value RETURN value")


def test_an_unknown_procedure_is_refused_by_default():
    """거부 목록이 아니라 허용 목록입니다 — 다음 위험한 프로시저를 몰라도 막힙니다."""
    for cypher in (
        "CALL dbms.security.listUsers() YIELD username RETURN username",
        "CALL apoc.periodic.iterate('MATCH (p) RETURN p', 'SET p.x = 1', {}) YIELD b RETURN b",
        "CALL db.labels() YIELD label RETURN label",
    ):
        with pytest.raises(ValueError):
            check_external_io(cypher)


def test_the_two_procedures_the_app_needs_are_allowed():
    """막는 것만 세면 과하게 잡는 쪽을 못 봅니다. 이 둘이 막히면 앱이 답을 못 합니다."""
    check_external_io(
        "CALL db.index.fulltext.queryNodes($index, $needle, {limit: 5}) "
        "YIELD node, score RETURN node.arxiv_id AS id"
    )
    check_external_io(
        "CALL db.index.vector.queryNodes($index, $limit, $vector) YIELD node RETURN node"
    )
    assert ALLOWED_PROCEDURES == {
        "db.index.fulltext.querynodes", "db.index.vector.querynodes",
    }


def test_a_call_subquery_is_not_a_procedure_call():
    """`CALL { ... }`는 읽기 서브쿼리이고 정상 질의가 씁니다."""
    check_external_io("CALL { MATCH (p:ArxivPaper) RETURN p LIMIT 1 } RETURN p")
    check_external_io("MATCH (p:ArxivPaper) CALL (p) { RETURN p AS q } RETURN q")


def test_the_words_inside_a_string_or_comment_are_not_a_call():
    """제목에 그 글자가 든 정상 질의를 거부하면 안 됩니다."""
    check_external_io(
        "MATCH (p:ArxivPaper) WHERE p.title CONTAINS 'LOAD CSV' RETURN p.arxiv_id AS id"
    )
    check_external_io("MATCH (p:ArxivPaper) RETURN p // CALL apoc.load.json('x')")
    check_external_io("/* CALL dbms.security.listUsers() */ MATCH (p:ArxivPaper) RETURN p")


def test_plain_reads_pass_the_static_check():
    for cypher in (
        "MATCH (p:ArxivPaper) RETURN p.arxiv_id AS id LIMIT 1",
        "MATCH (a:ArxivAuthorName)-[:AUTHORED]->(p) RETURN a.name AS name LIMIT 1",
        "UNWIND [1, 2] AS n RETURN n",
        "",
    ):
        check_external_io(cypher)


def test_only_a_read_plan_is_allowed_to_run():
    refuse_unless_read_only(READ_ONLY_QUERY_TYPE)
    for query_type in ("rw", "w", "s", None, ""):
        with pytest.raises(ValueError):
            refuse_unless_read_only(query_type)


def test_the_app_runs_the_static_check_before_it_talks_to_the_database():
    """막을 질의를 보낼 이유가 없고, 유형 검사는 외부 입출력을 막지 못합니다."""
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    body = source.split("def run_read(")[1].split("\ndef ")[0]

    assert "check_external_io(cypher)" in body
    assert "refuse_unless_read_only(" in body
    assert body.index("check_external_io(cypher)") < body.index("EXPLAIN ")


def test_the_probe_never_executes_what_it_probes():
    """이 스크립트의 안전 불변 조건입니다. 통과한 질의도 실행하지 않습니다."""
    source = Path("scripts/probe_cypher_guard.py").read_text(encoding="utf-8")

    # EXPLAIN을 붙이지 않은 session.run 호출이 있으면 안 됩니다.
    for line in source.splitlines():
        if "session.run(" in line:
            assert "EXPLAIN" in line, line.strip()
    assert "graph.write(" not in source


def test_the_recorded_probe_shows_the_guard_holding():
    """깨지면 가드가 뚫린 것이거나 탐침이 늘어난 것입니다. 둘 다 봐야 합니다."""
    if not PROBE_REPORT.exists():
        pytest.skip(f"탐침 보고서가 없습니다: {PROBE_REPORT}")
    report = json.loads(PROBE_REPORT.read_text(encoding="utf-8"))
    summary = report["summary"]

    assert summary["baseline_passed"] == summary["baseline_total"], "정상 질의가 막혔습니다"
    assert summary["writes_blocked"] == summary["writes_total"], "쓰기가 통과했습니다"
    assert summary["external_io_reached_server"] == [], "외부 입출력이 서버에 닿았습니다"
    assert summary["unexpected"] == []
    # 겹이 셋 다 일하고 있어야 합니다. 하나가 0이 되면 그 겹이 사라진 것입니다.
    blocked_by = summary["blocked_by"]
    assert blocked_by.get("blocked_by_the_static_check", 0) > 0
    assert blocked_by.get("blocked_by_the_type_check", 0) > 0
    assert report["safety"].startswith("EXPLAIN만")
