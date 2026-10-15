"""Reject generated Cypher whose ORDER BY sorts a value that was converted to a string.

`toString(n) AS c ... ORDER BY c` sorts lexicographically, so descending order puts a count of
9 above a count of 88. The query succeeds and returns the wrong rows, which is worse than an
error: nothing downstream can tell the ranking is broken.

프롬프트에 규칙을 넣어 빈도를 47.1%에서 5.9%로 줄였지만 0이 되지는 않았습니다. 프롬프트는
권고이고 검사는 강제이므로, 남은 경우는 여기서 막고 에이전트에게 고치게 합니다.
"""

from __future__ import annotations

import re

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
