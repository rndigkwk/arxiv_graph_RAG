"""`search_graph`가 모델에 돌려주는 논문 목록을 10행으로 줄입니다(ADR-120).

지시문(`prompts.AGENT_SYSTEM`)은 "LIMIT 25 이하"이고, 논문을 나열하면 한 편이 한 행인 표를 쓰라고
합니다. 그래서 인용이 많은 논문("Attention Is All You Need를 인용한 논문" - 그래프에 403편)은
모델이 25행 표와 근거 ID 25개를 한 글자씩 씁니다. 마지막 턴이 약 1,000토큰, 7~8초였고 화면 예시
7문항 중 두 측정 모두 이 문항만 10초를 넘겼습니다(2026-11-09, 3ee8abb·4e51cca).

지시문을 바꾸면 모든 질문의 턴이 그만큼 더 읽습니다(ADR-090). 그래서 도구가 돌려주는 행만
줄이고, 몇 행을 뺐는지(`rows_omitted`)를 함께 돌려줘 모델이 "더 있다"고 말할 수 있게 합니다.

**논문 행만 줄입니다.** `evidence_ids`가 빈 행(분류별 편수 같은 순수 집계)은 그대로 둡니다 -
집계 행은 짧고, 빼면 합계가 틀립니다.
"""

from __future__ import annotations

from typing import Any

MAX_PAPER_ROWS = 10


def cap_paper_rows(rows: list[dict[str, Any]],
                   limit: int = MAX_PAPER_ROWS) -> tuple[list[dict[str, Any]], int]:
    """논문 행(`evidence_ids`가 비지 않은 행)이 `limit`을 넘으면 앞의 `limit`개만 남깁니다.

    순서는 Cypher의 ORDER BY 그대로입니다. 두 번째 값은 뺀 행 수입니다.
    """
    if sum(1 for row in rows if row.get("evidence_ids")) <= limit:
        return rows, 0
    kept: list[dict[str, Any]] = []
    papers = 0
    for row in rows:
        if row.get("evidence_ids"):
            papers += 1
            if papers > limit:
                continue
        kept.append(row)
    return kept, len(rows) - len(kept)
