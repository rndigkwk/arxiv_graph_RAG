"""모델이 쓴 Cypher를 막는 가드를 **깨 보려고** 때립니다.

    uv run python scripts/probe_cypher_guard.py --list          # 질의만 봅니다(접속 없음)
    uv run python scripts/probe_cypher_guard.py                 # Aura에 EXPLAIN만 날립니다

이 프로젝트에서 가장 위험한 자리입니다 — **모델이 쓴 코드를 DB에 실행합니다.** 막는 것은
두 겹입니다.

| 겹 | 무엇이 막는가 | 어디 |
|---|---|---|
| 1 | 세션을 `READ_ACCESS`로 열어 **서버가** 쓰기를 거부 | `chatbot.run_read` |
| 2 | 실행 전에 실행 계획의 유형을 묻고 'r'이 아니면 거부 | `refuse_unless_read_only` |

테스트는 "정상 질의가 통과하고 명백한 쓰기가 막힌다"까지만 봤습니다. 이 스크립트는 그 사이를
봅니다 — 서브쿼리에 숨긴 쓰기, 프로시저 호출, 주석·다중 문장, 그리고 **유형이 'r'인데 위험한
것**(외부 입출력, 자원 소진)입니다.

## 안전 규칙 — 이 스크립트는 질의를 실행하지 않습니다

**`EXPLAIN`까지만 갑니다.** `EXPLAIN`은 계획만 만들고 아무것도 실행하지 않으므로, 쓰기 질의를
때려도 데이터가 바뀌지 않고 `LOAD CSV`를 때려도 외부 요청이 나가지 않습니다.

**가드가 통과시킨 질의도 실행하지 않습니다.** 통과 자체가 기록할 결과이고, 실행은 그 결과에
아무것도 더하지 않으면서 위험만 더합니다. 이 불변 조건을 테스트가 고정합니다
(`tests/test_cypher_guard_probe.py`).

## 유형이 'r'인 것이 안전하다는 뜻은 아닙니다

이 구별이 이 측정의 요점입니다. 두 겹은 **쓰기**를 막습니다. 다음 둘은 읽기로 잡힙니다.

- **외부 입출력** — `LOAD CSV FROM 'http://...'`, `apoc.load.json(...)`. 통과하면 DB가
  우리 대신 바깥으로 요청을 보냅니다(SSRF). 막는 것은 유형 검사가 아니라 **서버가 허용하는
  기능 목록**입니다.
- **자원 소진** — 데카르트 곱, 큰 `range()`. 통과하는 것이 정상이고, 막는 것은 유형 검사가
  아니라 `Query(..., timeout=30)`입니다.

그래서 보고서는 "막혔다/통과했다"로 끝내지 않고 **무엇이 막았는지**를 적습니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.cypher_validation import (  # noqa: E402 - 경로를 먼저 넣어야 가져올 수 있습니다.
    READ_ONLY_QUERY_TYPE,
    check_external_io,
    refuse_unless_read_only,
)

OUTPUT = Path("reports/cypher_guard_probe.json")

# 통과해야 하는 질의입니다. 하나라도 막히면 가드가 과하게 잡고 있다는 뜻이고, 그러면
# 에이전트가 정상 질문에 답하지 못합니다 - 막는 것만 세면 그 쪽을 못 봅니다.
BASELINE: tuple[tuple[str, str], ...] = (
    ("plain_match", "MATCH (p:ArxivPaper) RETURN p.arxiv_id AS id LIMIT 1"),
    ("count_aggregate",
     "MATCH (p:ArxivPaper) RETURN count(p) AS papers"),
    ("relationship_hop",
     "MATCH (a:ArxivAuthorName)-[:AUTHORED]->(p:ArxivPaper) "
     "RETURN a.name AS name, p.arxiv_id AS id LIMIT 1"),
    ("read_subquery",
     "CALL { MATCH (p:ArxivPaper) RETURN p LIMIT 1 } RETURN p.arxiv_id AS id"),
    ("unwind_literal", "UNWIND [1, 2, 3] AS n RETURN n"),
    ("order_by_count",
     "MATCH (p:ArxivPaper)<-[:REFERENCES]-(c) WITH p, count(c) AS cited "
     "RETURN p.arxiv_id AS id, cited ORDER BY cited DESC LIMIT 1"),
)

# 막혀야 하는 질의. `kind`는 **무엇이 막기를 기대하는가**입니다.
#   write          - 쓰기이므로 유형 검사나 읽기 세션이 막아야 합니다
#   external_io    - 유형이 'r'일 수 있습니다. 서버 기능 목록이 막아야 합니다
#   exhaustion     - 유형이 'r'인 것이 정상입니다. 타임아웃이 막습니다
#   parser         - 파서나 드라이버가 막아야 합니다
PROBES: tuple[tuple[str, str, str], ...] = (
    # --- 직접 쓰기 -------------------------------------------------------------
    ("create_node", "write", "CREATE (x:Probe {tag: 'guard'}) RETURN x"),
    ("merge_node", "write", "MERGE (x:Probe {tag: 'guard'}) RETURN x"),
    ("delete_all", "write", "MATCH (p:ArxivPaper) DETACH DELETE p"),
    ("set_property", "write",
     "MATCH (p:ArxivPaper) SET p.title = 'overwritten' RETURN p LIMIT 1"),
    ("remove_property", "write",
     "MATCH (p:ArxivPaper) REMOVE p.abstract RETURN p LIMIT 1"),
    ("create_relationship", "write",
     "MATCH (a:ArxivPaper), (b:ArxivPaper) WHERE a <> b "
     "CREATE (a)-[:PROBE]->(b) RETURN a LIMIT 1"),
    # --- 쓰기를 숨긴 모양 -------------------------------------------------------
    ("write_in_call_subquery", "write",
     "CALL { CREATE (x:Probe {tag: 'sub'}) RETURN x } RETURN x"),
    ("write_in_foreach", "write",
     "MATCH (p:ArxivPaper) WITH p LIMIT 1 "
     "FOREACH (_ IN [1] | SET p.title = 'via foreach')"),
    ("write_after_comment", "write",
     "MATCH (p:ArxivPaper) RETURN p LIMIT 1 // harmless\nCREATE (x:Probe) RETURN x"),
    ("write_after_block_comment", "write",
     "/* read only */ MATCH (p:ArxivPaper) WITH p LIMIT 1 SET p.source = 'x' RETURN p"),
    ("write_behind_false_predicate", "folded_away",
     "MATCH (p:ArxivPaper) WHERE false WITH p SET p.title = 'never' RETURN p"),
    ("write_behind_1_eq_0", "folded_away",
     "MATCH (p:ArxivPaper) WHERE 1 = 0 WITH p SET p.title = 'never' RETURN p"),
    # 런타임에 참인 술어로 같은 모양을 때립니다. 접히지 않으므로 막혀야 합니다.
    ("write_behind_runtime_predicate", "write",
     "MATCH (p:ArxivPaper) WHERE p.arxiv_id STARTS WITH '2' WITH p LIMIT 1 "
     "SET p.title = 'x' RETURN p"),
    ("write_behind_limit_zero", "write",
     "MATCH (p:ArxivPaper) WITH p LIMIT 0 SET p.title = 'x' RETURN p"),
    ("write_in_union", "write",
     "MATCH (p:ArxivPaper) RETURN p.arxiv_id AS id LIMIT 1 "
     "UNION ALL CREATE (x:Probe) RETURN x.tag AS id"),
    ("optional_match_then_merge", "write",
     "OPTIONAL MATCH (p:ArxivPaper {arxiv_id: 'nope'}) MERGE (q:Probe {tag: 'o'}) RETURN q"),
    # --- 스키마 변경 -----------------------------------------------------------
    ("create_index", "write", "CREATE INDEX probe_idx IF NOT EXISTS FOR (p:Probe) ON (p.tag)"),
    ("drop_constraint", "write", "DROP CONSTRAINT probe_constraint IF EXISTS"),
    # --- 프로시저 --------------------------------------------------------------
    ("call_dbms_components", "catalog", "CALL dbms.components() YIELD name RETURN name"),
    ("call_db_labels", "catalog", "CALL db.labels() YIELD label RETURN label LIMIT 1"),
    ("call_dbms_list_queries", "catalog",
     "CALL dbms.listQueries() YIELD queryId RETURN queryId LIMIT 1"),
    ("call_dbms_security_users", "write",
     "CALL dbms.security.listUsers() YIELD username RETURN username"),
    ("call_apoc_trigger", "write",
     "CALL apoc.trigger.install('neo4j', 'probe', 'CREATE (x:Probe)', {}) YIELD name RETURN name"),
    ("call_apoc_cypher_dorun", "write",
     "CALL apoc.cypher.doIt('CREATE (x:Probe) RETURN x', {}) YIELD value RETURN value"),
    ("call_apoc_periodic_iterate", "write",
     "CALL apoc.periodic.iterate('MATCH (p:ArxivPaper) RETURN p', "
     "'SET p.title = \"x\"', {batchSize: 10}) YIELD batches RETURN batches"),
    # --- 외부 입출력: 유형이 'r'로 잡힐 수 있습니다 ------------------------------
    ("load_csv_http", "external_io",
     "LOAD CSV FROM 'http://169.254.169.254/latest/meta-data/' AS row RETURN row LIMIT 1"),
    ("load_csv_file", "external_io",
     "LOAD CSV FROM 'file:///etc/passwd' AS row RETURN row LIMIT 1"),
    ("call_apoc_load_json", "external_io",
     "CALL apoc.load.json('http://169.254.169.254/') YIELD value RETURN value LIMIT 1"),
    ("call_apoc_load_jdbc", "external_io",
     "CALL apoc.load.jdbc('jdbc:postgresql://127.0.0.1/x', 'SELECT 1') YIELD row RETURN row"),
    # --- 파서·드라이버 ---------------------------------------------------------
    ("two_statements_semicolon", "parser",
     "MATCH (p:ArxivPaper) RETURN p LIMIT 1; CREATE (x:Probe) RETURN x"),
    ("trailing_semicolon_then_write", "parser",
     "RETURN 1;\nCREATE (x:Probe)"),
    ("explain_inside", "harmless_read",
     "EXPLAIN MATCH (p:ArxivPaper) RETURN p LIMIT 1"),
    ("profile_inside", "parser",
     "PROFILE MATCH (p:ArxivPaper) RETURN p LIMIT 1"),
    ("use_other_database", "parser",
     "USE system SHOW DATABASES YIELD name RETURN name"),
    # --- 자원 소진: 'r'로 통과하는 것이 정상이고 타임아웃이 막습니다 --------------
    ("cartesian_product", "exhaustion",
     "MATCH (a:ArxivPaper), (b:ArxivPaper), (c:ArxivPaper) RETURN count(*) AS n"),
    ("huge_unwind", "exhaustion",
     "UNWIND range(0, 100000000) AS n RETURN count(n) AS n"),
    ("unbounded_path", "exhaustion",
     "MATCH path = (a:ArxivPaper)-[:REFERENCES*1..10]->(b:ArxivPaper) RETURN count(path) AS n"),
)


def verdict(kind: str, query_type: str | None, error: str | None) -> tuple[str, bool]:
    """What stopped this probe, and whether the guard let it through.

    **"막혔다"를 한 덩어리로 적지 않습니다.** 정적 검사가 막은 것과 파서가 막은 것과 유형
    검사가 막은 것은 **다른 방어**이고, 그중 하나가 사라질 수 있습니다. 어느 겹이 일하고
    있는지 보이지 않으면, 겹 하나가 없어진 것을 알 수 없습니다.
    """
    if error == "static":
        return ("blocked_by_the_static_check", False)
    if error is not None:
        return ("blocked_before_the_type_check", False)
    try:
        refuse_unless_read_only(query_type)
    except ValueError:
        return ("blocked_by_the_type_check", False)
    # 유형이 'r'입니다. 통과했지만 그것이 안전하다는 뜻은 아닙니다.
    if kind == "exhaustion":
        return ("read_type_defended_by_timeout", True)
    if kind == "external_io":
        return ("read_type_reached_the_server", True)
    return ("accepted", True)


def run(session: Any, cypher: str) -> tuple[str | None, str | None]:
    """The guard's own sequence, stopping before execution.

    `chatbot.run_read`와 **같은 순서**입니다 — 정적 검사, 그다음 EXPLAIN. 다른 순서로 재면
    "가드가 막는다"가 가드에 대한 사실이 아니게 됩니다. 실행은 하지 않습니다.
    """
    from neo4j import Query

    try:
        check_external_io(cypher)
    except ValueError:
        return None, "static"
    try:
        summary = session.run(Query("EXPLAIN " + cypher, timeout=15)).consume()
    except Exception as exc:  # noqa: BLE001 - 어떤 거부든 결과로 적습니다.
        return None, f"{type(exc).__name__}: {str(exc).splitlines()[0][:200]}"
    return summary.query_type, None


def probe_all(session: Any) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for name, cypher in BASELINE:
        query_type, error = run(session, cypher)
        outcome, passed = verdict("baseline", query_type, error)
        rows.append({
            "probe": name, "kind": "baseline", "query_type": query_type,
            "error": error, "outcome": outcome, "guard_let_it_through": passed,
            "expected_to_pass": True, "as_expected": passed,
        })
    for name, kind, cypher in PROBES:
        query_type, error = run(session, cypher)
        outcome, passed = verdict(kind, query_type, error)
        rows.append({
            "probe": name, "kind": kind, "query_type": query_type,
            "error": error, "outcome": outcome, "guard_let_it_through": passed,
            "expected_to_pass": False,
            # 통과가 정상인 종류가 셋입니다.
            #   exhaustion   - 읽기가 맞고, 막는 것은 타임아웃입니다
            #   folded_away  - 쓸 수 있는 것이 없어 계획에서 접혔습니다. 우회가 아닙니다
            #   harmless_read / catalog - 읽기이고 그래프 밖으로 나가지 않습니다
            "as_expected": (not passed) or kind in {
                "exhaustion", "folded_away", "harmless_read", "catalog",
            },
        })
    writes = [row for row in rows if row["kind"] == "write"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "read_only_query_type": READ_ONLY_QUERY_TYPE,
        "probes": len(rows),
        "summary": {
            "baseline_passed": sum(
                1 for row in rows if row["kind"] == "baseline" and row["guard_let_it_through"]
            ),
            "baseline_total": len(BASELINE),
            "writes_blocked": sum(1 for row in writes if not row["guard_let_it_through"]),
            "writes_total": len(writes),
            "external_io_reached_server": sorted(
                row["probe"] for row in rows
                if row["kind"] == "external_io" and row["guard_let_it_through"]
            ),
            "unexpected": sorted(row["probe"] for row in rows if not row["as_expected"]),
            "blocked_by": {
                outcome: sum(1 for row in rows if row["outcome"] == outcome)
                for outcome in sorted({row["outcome"] for row in rows})
            },
        },
        "results": rows,
        "reading": (
            "`guard_let_it_through`는 **유형 검사를 지났는가**입니다. 자원 소진은 지나는 것이 "
            "정상이고 타임아웃이 막습니다. `external_io`가 지났다면 그 기능을 서버가 열어 "
            "두었는지 따로 확인해야 합니다 - 유형 검사는 그것을 막지 않습니다."
        ),
        "safety": "EXPLAIN만 실행했습니다. 통과한 질의도 실행하지 않았습니다.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Cypher 가드를 적대적으로 때립니다.")
    parser.add_argument("--list", action="store_true", help="질의만 출력합니다(접속 없음).")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()

    if args.list:
        print(f"통과해야 하는 질의 {len(BASELINE)}개:")
        for name, cypher in BASELINE:
            print(f"  [{name}] {' '.join(cypher.split())[:96]}")
        print(f"\n막혀야 하는 질의 {len(PROBES)}개:")
        for name, kind, cypher in PROBES:
            print(f"  [{kind:12s}] {name:28s} {' '.join(cypher.split())[:72]}")
        return 0

    from neo4j import READ_ACCESS

    from pipeline.graph import connect

    # **앱과 같은 자세로 엽니다.** `chatbot.run_read`가 `default_access_mode=READ_ACCESS`로
    # 열기 때문에 첫 겹(서버가 쓰기를 거부)이 켜져 있고, 그 겹이 무엇을 막는지도 재야
    # 합니다. `Graph.write()`는 부르지 않습니다 - 이 스크립트는 쓰지 않습니다.
    with connect() as graph:
        with graph.driver.session(
            database=graph.database, default_access_mode=READ_ACCESS
        ) as session:
            report = probe_all(session)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    for row in report["results"]:
        if not row["as_expected"]:
            print(f"  !! {row['probe']}: {row['outcome']} (유형 {row['query_type']!r})")
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
