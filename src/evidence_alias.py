"""모양만 어긋난 근거 별칭을 도구 경계에서 고쳐 받습니다(ADR-122, ADR-019의 일부를 뒤집음).

ADR-019는 `search_graph`의 행이 `evidence_ids`를 arxiv_id 문자열 리스트로 갖지 않으면 rows 대신
error를 돌려줍니다. 모델은 그 메시지를 읽고 조회를 다시 씁니다 - 모델 턴 하나(2~4초)가 더 듭니다.
2026-11-10 로컬에서 그래프 평가 문항 15개 중 2개가 그 길로 갔고, 두 모양은 ADR-019가 관측한 것과
같았습니다.

| 모양 | 예 |
|---|---|
| 문자열 하나 | `p.arxiv_id AS evidence_ids` → `"2303.14382"` |
| 단수 별칭 | `[p.arxiv_id] AS evidence_id` |

**값이 arXiv ID 모양일 때만 고칩니다.** 문자열 하나가 arXiv ID와 통째로 맞으면 논문 한 편입니다 -
ADR-019가 걱정한 "한 편인지 여러 편인지"가 갈립니다. 단수 별칭은 그 값이 같은 규칙을 지킬 때만
`evidence_ids`로 옮깁니다. ID는 여전히 그래프가 돌려준 값이고, 답의 인용은 `validate_answer`가
그 행과 대조합니다.

**별칭이 아예 없는 행은 고치지 않습니다.** 논문 행인지 집계 행인지 알 수 없습니다. 그런 행은
지금처럼 error가 됩니다.
"""

from __future__ import annotations

import re
from typing import Any

# 새 형식(1706.03762, 2410.01234v2)과 옛 형식(cs/0101001, adap-org/9708006, math.AG/0101001).
# 그래프에는 옛 형식이 160편 있습니다(2026-11-10, VM).
ARXIV_ID = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z][a-z-]*(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and bool(ARXIV_ID.match(value))


def _ids(value: Any) -> list[str] | None:
    """arXiv ID 하나 또는 ID 리스트면 리스트, 아니면 None."""
    if _is_id(value):
        return [value]
    if isinstance(value, list) and all(_is_id(item) for item in value):
        return list(value)
    return None


def repair(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`evidence_ids`가 ID 문자열이거나 `evidence_id`로 왔으면 리스트 `evidence_ids`로 바꿉니다.

    고칠 수 없는 행은 그대로 둡니다 - 뒤의 `check_evidence_alias`가 지금처럼 error로 돌려보냅니다.
    """
    fixed = []
    for row in rows:
        if "evidence_ids" in row:
            value = row["evidence_ids"]
            if isinstance(value, str) and (ids := _ids(value)):
                row = {**row, "evidence_ids": ids}
        elif "evidence_id" in row and (ids := _ids(row["evidence_id"])) is not None:
            row = {key: value for key, value in row.items() if key != "evidence_id"}
            row["evidence_ids"] = ids
        fixed.append(row)
    return fixed
