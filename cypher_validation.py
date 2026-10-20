"""Reject generated Cypher whose ORDER BY sorts a value that was converted to a string.

`toString(n) AS c ... ORDER BY c` sorts lexicographically, so descending order puts a count of
9 above a count of 88. The query succeeds and returns the wrong rows, which is worse than an
error: nothing downstream can tell the ranking is broken.

프롬프트에 규칙을 넣어 빈도를 47.1%에서 5.9%로 줄였지만 0이 되지는 않았습니다. 프롬프트는
권고이고 검사는 강제이므로, 남은 경우는 여기서 막고 에이전트에게 고치게 합니다.
"""

from __future__ import annotations

import re

# ── 조회 전용 판정 ──────────────────────────────────────────────────────────
#
# 모델이 쓴 Cypher를 실행하는 자리의 두 번째 겹입니다. 첫 겹은 세션을 `READ_ACCESS`로
# 열어 **서버가** 쓰기를 거부하게 하는 것이고, 이것은 실행 **전에** Neo4j 자신의 실행
# 계획에게 유형을 물어 거부하는 쪽입니다.
#
# 판정을 여기 둔 이유는 하나입니다 — `chatbot.run_read`와 가드를 때리는 탐침
# (`scripts/probe_cypher_guard.py`)이 **같은 규칙을 봐야** 합니다. 탐침이 자기 사본을
# 들고 있으면 "가드가 막는다"는 결과가 가드에 대한 사실이 아니게 됩니다.
#
# Neo4j의 유형 문자: r=읽기, w=쓰기, rw=읽기+쓰기, s=스키마.
READ_ONLY_QUERY_TYPE = "r"


# ── 외부 입출력 ────────────────────────────────────────────────────────────
#
# **유형 검사가 막지 못하는 자리입니다.** 2026-10-20에 가드를 때려 보고 알았습니다
# (ADR-048, `reports/cypher_guard_probe.json`).
#
# `LOAD CSV FROM 'http://...'`와 `apoc.load.json('http://...')`은 실행 계획 유형이 **'r'**
# 입니다. 그래프를 쓰지 않으니 맞는 분류이고, 그래서 `refuse_unless_read_only`가 통과시킵니다.
# 그런데 그 질의는 **DB가 우리 대신 바깥으로 요청을 보내게** 합니다. 질의를 쓰는 주체가
# 언어 모델이므로, URL도 모델이 정합니다.
#
# 그리고 이 인스턴스에 그 프로시저가 **실제로 있습니다** — `SHOW PROCEDURES`로 확인한
# `apoc.load.json`·`apoc.load.jsonArray`·`apoc.load.xml`(APOC 159개 중). 서버가 막아 줄
# 것이라고 가정할 수 없습니다.
#
# 그래서 **허용 목록**입니다. 거부 목록은 다음 위험한 프로시저를 알아야 하지만, 허용 목록은
# 우리가 쓰는 것만 알면 됩니다. 앱이 쓰는 것은 둘입니다.
ALLOWED_PROCEDURES = frozenset({
    "db.index.fulltext.querynodes",
    "db.index.vector.querynodes",
})

# `CALL name.space(` 만 잡습니다. `CALL { ... }` 서브쿼리와 `CALL (n) { ... }`는 이름이
# 없으므로 걸리지 않습니다 — 그 둘은 읽기 서브쿼리이고 정상 질의가 씁니다.
PROCEDURE_CALL = re.compile(r"\bCALL\s+([A-Za-z_][\w.]*)\s*\(", re.IGNORECASE)
LOAD_CSV = re.compile(r"\bLOAD\s+CSV\b", re.IGNORECASE)
_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")


def _code_only(cypher: str) -> str:
    """Drop comments and string literals so a mention is not mistaken for a call.

    문자열 안의 `LOAD CSV`는 실행되지 않습니다. 지우지 않으면 제목에 그 글자가 들어간
    정상 질의를 거부하게 됩니다.
    """
    return _STRING.sub("''", _COMMENT.sub(" ", cypher or ""))


def check_external_io(cypher: str) -> None:
    """Raise when the query would make the database read something outside the graph.

    유형 검사보다 **먼저** 돕니다. 정적 검사라 왕복이 필요 없고, 막을 것이면 DB에 보낼
    이유도 없습니다.
    """
    code = _code_only(cypher)
    if LOAD_CSV.search(code):
        raise ValueError(
            "LOAD CSV는 실행하지 않습니다. 실행 계획 유형이 '읽기'로 잡히지만 데이터베이스가 "
            "바깥으로 요청을 보내게 됩니다(그래프 조회에는 필요하지 않습니다)."
        )
    for name in PROCEDURE_CALL.findall(code):
        if name.lower() not in ALLOWED_PROCEDURES:
            allowed = ", ".join(sorted(ALLOWED_PROCEDURES))
            raise ValueError(
                f"허용하지 않은 프로시저입니다: {name}. 조회에 쓸 수 있는 것은 {allowed} "
                "입니다. 나머지는 MATCH로 쓰세요."
            )


def refuse_unless_read_only(query_type: str | None) -> None:
    """Raise unless Neo4j's own plan says this query only reads.

    **유형이 'r'이라고 안전한 것은 아닙니다.** 외부 입출력을 하는 읽기(`LOAD CSV`,
    `apoc.load.*`)나 자원을 소진하는 읽기는 'r'로 잡힙니다. 그 둘은 이 판정이 아니라
    질의 타임아웃과 서버가 허용하는 프로시저 목록이 막습니다 — 무엇이 무엇을 막는지는
    `reports/cypher_guard_probe.json`에 쟀습니다(ADR-048).
    """
    if query_type != READ_ONLY_QUERY_TYPE:
        raise ValueError(
            f"조회 전용 Cypher만 실행합니다(실행 계획 유형 {query_type!r})."
        )

# `toString(x) AS alias` 로 별칭이 문자열이 된 경우를 찾습니다.
STRINGIFIED_ALIAS = re.compile(r"toString\s*\(\s*([\w.]+)\s*\)\s+AS\s+(\w+)", re.IGNORECASE)
ORDER_BY_CLAUSE = re.compile(
    r"ORDER\s+BY\s+(.+?)(?=\s+SKIP\b|\s+LIMIT\b|\s+RETURN\b|$)", re.IGNORECASE | re.DOTALL
)


def stringified_order_keys(cypher: str) -> list[str]:
    """Return ORDER BY keys whose value the same query turned into a string.

    Only flags an alias that both comes from `toString(...)` and is used to sort.
    Stringifying a value for display while sorting by the numeric alias is fine.
    """
    if not cypher:
        return []
    aliases = {alias.lower() for _, alias in STRINGIFIED_ALIAS.findall(cypher)}
    if not aliases:
        return []
    flagged = []
    for clause in ORDER_BY_CLAUSE.findall(cypher):
        for term in clause.split(","):
            stripped = term.strip()
            if not stripped:
                continue
            key = stripped.split()[0].strip("`").lower()
            if key in aliases and key not in flagged:
                flagged.append(key)
    return flagged


def check_order_by(cypher: str) -> None:
    """Raise when the query would sort a stringified value, naming the aliases at fault."""
    flagged = stringified_order_keys(cypher)
    if flagged:
        raise ValueError(
            f"ORDER BY가 문자열로 변환된 값을 정렬합니다: {', '.join(flagged)}. "
            "toString()을 벗기고 숫자 별칭으로 정렬하세요. 문자열 순서에서는 9가 88보다 "
            "크게 평가돼 순위가 틀어집니다."
        )
