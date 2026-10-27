""""최근" 논문을 묻는 질문은 앱이 먼저 기간 안에서 찾습니다(ADR-090).

"최근 RLHF 논문"에서 모델은 `search_papers`만 부르고 답했다가 조건 판정에 거절당하고
(`graph_conditions_not_covered` - "최근"을 거르는 Cypher가 없음), 두 번째 시도에서 날짜 기준을
스스로 정하며 턴 5~7개를 썼습니다. 기준은 질문마다 달랐습니다(2024-01-01, 2026-07-14 ...).

**지시문과 도구를 바꾸지 않습니다.** 처음에는 `search_papers`에 기간 인자를 더하고 지시문에 규칙을
적었습니다. "최근" 질문은 고쳐졌지만, 인자 하나와 지시문 다섯 줄이 **모든 질문의** 모델 턴을
0.5~1초 느리게 했습니다(CI에서 main과 동시에 돌린 세 쌍, 로컬 에이전트 A/B 모두). 그래서 번역
(ADR-087)·제목 해석(ADR-088)과 같은 자리로 옮깁니다 - "최근"인지는 코드가 가리고, 그 질문에만
메시지를 붙입니다.

**날짜는 검색 안에서 거릅니다.** 가까운 10편을 받은 뒤 거르면 오래된 대표 논문(2017~2023)이 자리를
차지해 0편이 남았습니다(6문항 중 3개). 최근 12개월 안의 논문은 약 12,000편입니다.

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

TOOL = "search_papers"
WINDOW_MONTHS = 12
TOP_K = 5
# 날짜를 묻는 낱말입니다. 제목 안의 "Recent Advances"처럼 걸릴 수 있어서, 질문이 논문 하나를
# 지칭하면(제목 해석이 찾았으면) 이 단계를 건너뜁니다(`chatbot.recent_first`).
CUES = re.compile(
    r"\brecent(?:ly)?\b|\blatest\b|\bnewest\b|\bnew\s+(?:papers?|work|research|stud(?:y|ies))\b"
    r"|최근|최신|요즘",
    re.IGNORECASE,
)
HINT = (
    "The question asks for recent papers. The app already searched the papers published in the "
    "last {months} months for the question's topic: {papers}. Run one search_graph query on "
    "these arxiv_ids with p.published >= datetime() - duration(\"P{months}M\") to check the "
    "date, cite only those whose title or abstract fits the question's topic, and say that "
    "recent means published in the last {months} months. Do not call search_papers unless none "
    "of them fit. If the question does not ask for recent papers, ignore this message."
)


def asks_recent(question: str) -> bool:
    return bool(CUES.search(question or ""))


def since(months: int = WINDOW_MONTHS, now: dt.datetime | None = None) -> dt.datetime:
    """`months`개월 전 시각입니다.

    달을 30일로 셉니다. 12개월이면 360일이라 힌트의 Cypher(`duration("P12M")`)보다 며칠 짧습니다 -
    여기서 찾은 논문은 그 조회를 모두 통과합니다.
    """
    now = now or dt.datetime.now(dt.UTC)
    return now - dt.timedelta(days=30 * months)


def usable(output: Any) -> bool:
    return isinstance(output, dict) and bool(output.get("papers"))


def hint(output: dict[str, Any]) -> str:
    listed = "; ".join(
        f"{paper['arxiv_id']} \"{str(paper.get('title') or '').replace(chr(34), chr(39))}\" "
        f"({paper.get('published') or 'no date'})"
        for paper in output["papers"]
    )
    return HINT.format(months=WINDOW_MONTHS, papers=listed)


def call_record(query: str, output: Any, seconds: float) -> dict[str, Any]:
    """앱이 부른 기간 안 검색을 `search_papers` 호출 기록 모양으로."""
    return {
        "id": f"app:{TOOL}", "name": TOOL, "by": "app",
        "args": {"query": query, "top_k": TOP_K, "published_within_months": WINDOW_MONTHS},
        "status": "success",
        "output": output,
        "seconds": round(seconds, 3),
    }
