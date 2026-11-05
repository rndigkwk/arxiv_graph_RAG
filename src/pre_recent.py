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


# 날짜 낱말 말고 조건 문구에 남아도 되는 말입니다.
# 이것까지 지우고 아무것도 안 남아야 "최근"뿐입니다.
FILLER = re.compile(
    r"\b(?:the|most|papers?|works?|research|stud(?:y|ies)|published|ones?|in|on|of)\b"
    r"|논문|연구|나온|발표된|것|에|의",
    re.IGNORECASE,
)


def recency_only(conditions: list[str]) -> bool:
    """판정이 적은 그래프 조건이 모두 "최근" 같은 날짜 낱말뿐인지(ADR-103).

    "2024년 이후", "cited by X", "cs.CL"처럼 다른 것이 하나라도 남으면 False입니다.
    """
    if not conditions:
        return False
    for text in conditions:
        rest = FILLER.sub(" ", CUES.sub(" ", text or ""))
        if not CUES.search(text or "") or re.sub(r"[\W_]+", "", rest):
            return False
    return True


def _published(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def covers(question: str, conditions: list[str], papers: list[dict[str, Any]],
           now: dt.datetime | None = None) -> bool:
    """"최근" 조건을 앱이 직접 확인합니다(ADR-103).

    "최근 = 12개월"은 앱이 정한 뜻입니다(ADR-090). 판정은 그 뜻을 몰라, 같은 12개월 날짜 조회를
    보고도 실행마다 충족·미충족을 다르게 냈습니다(EXPERIMENTS 11-60).

    질문이 "최근"을 묻고, 판정의 그래프 조건이 날짜 낱말뿐이고, 인용한 논문이 **모두** 기간 안에
    발표됐을 때만 True입니다. 발표일을 읽지 못한 논문이 하나라도 있으면 False - 판정의 결론을
    그대로 둡니다.
    """
    if not (asks_recent(question) and recency_only(conditions) and papers):
        return False
    start = since(now=now)
    dates = [_published(paper.get("published")) for paper in papers]
    return all(date is not None and date >= start for date in dates)


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
