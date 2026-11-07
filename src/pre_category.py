"""분류 코드와 주제를 함께 묻는 질문은 앱이 먼저 그 분류 안에서 찾습니다(ADR-113).

"대규모 언어 모델 에이전트를 다루는 cs.CL 논문을 알려 줘"에서 모델은 턴을 2~7개 썼습니다
(2026-11-07, VM). 주제 검색(`search_papers`)에는 분류를 거르는 인자가 없습니다. 그래서 모델은
벡터 검색을 한 뒤 `toLower(p.abstract) CONTAINS ...` Cypher를 낱말과 분류 조건
(`IN p.categories`·`p.primary_category`)을 바꿔 가며 다시 썼습니다. 같은 질문이 한 번은
2턴(10.9초), 한 번은 5턴(16.6초), 측정에서는 7턴(19.9초)이었습니다.

**지시문과 도구를 바꾸지 않습니다.** `search_papers`에 인자를 더하면 모든 질문의 턴이
느려집니다(ADR-090에서 기간 인자로 쟀습니다). "최근" 질문(`pre_recent`)과 같은 자리에 둡니다 -
분류 코드가 있고 주제가 남는 질문인지는 코드가 가리고, 그 질문에만 메시지를 붙입니다.

**대표 분류로 거릅니다.** 지시문의 Cypher 규칙이 "대표 분류만 볼 때는 p.primary_category"라고 하고,
2턴으로 끝난 실행도 그 조건을 썼습니다.

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

import re
from typing import Any

TOOL = "search_papers"
TOP_K = 5

# arXiv 분류 코드입니다. 대문자 두 글자 하위 분류를 쓰는 묶음만 봅니다(cs.CL, stat.ML, math.OC ...).
CODE = re.compile(r"(?<![\w.])(cs|stat|math|eess|econ|q-bio|q-fin)\.([A-Za-z]{2})(?![\w])",
                  re.IGNORECASE)

# 분류 코드를 지운 영어 질문에서 이것까지 지우고 낱말이 남아야 주제가 있는 질문입니다.
# 순위·집계·저자·날짜를 묻는 말은 주제가 아닙니다 - 그런 질문은 `search_graph`가 셉니다.
_FILLER = re.compile(
    r"\b(?:the|a|an|and|or|of|in|on|about|for|to|from|with|that|which|what|who|whose|are|is|"
    r"there|any|some|me|please|find|show|list|give|tell|provide|recommend|search|papers?|works?|"
    r"articles?|research|stud(?:y|ies)|category|categories|field|area|primary|published|"
    r"written|authored|by|top|most|highest|many|more|number|count|how|cited|citations?|"
    r"citing|cite|references?|referenced|influential|popular|important|best|ranked|ranking|"
    r"rank|highly|frequently|often|widely|heavily|much|spanning|across|both|multiple|"
    r"authors?|year|years|month|months|recent|recently|latest|newest|new|between|since|"
    r"after|before|during|than|do|does|did|have|has|use|using|uses|deal|deals|dealing|address|"
    r"addresses|addressing|related|relevant|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"twenty|\d+(?:st|nd|rd|th)?)\b",
    re.IGNORECASE,
)
# "by Yoshua Bengio"처럼 저자를 적은 질문은 주제 검색이 아닙니다.
_BY_NAME = re.compile(r"\bby\s+[A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)+")


def code(question: str, english: str | None = None) -> str | None:
    """질문에 적힌 분류 코드 하나. 없거나 둘 이상이면 None입니다(둘이면 모델이 정합니다)."""
    found = {f"{m.group(1).lower()}.{m.group(2).upper()}"
             for text in (question or "", english or "") for m in CODE.finditer(text)}
    return found.pop() if len(found) == 1 else None


def topic(english: str) -> str:
    """분류 코드와 순위·집계 낱말을 지우고 남은 주제 낱말입니다. 비면 주제가 없는 질문입니다."""
    rest = CODE.sub(" ", english or "")
    rest = _FILLER.sub(" ", rest)
    words = re.sub(r"[^\w\s-]", " ", rest).split()
    return " ".join(w for w in words if re.search(r"[^\W_]", w))


def asks(question: str, english: str) -> str | None:
    """분류 안의 주제를 묻는 질문이면 그 분류 코드, 아니면 None입니다."""
    found = code(question, english)
    if found is None or _BY_NAME.search(english or ""):
        return None
    return found if topic(english) else None


HINT = (
    "The question asks for {code} papers on a topic. The app already searched the papers whose "
    "primary category is {code} for the question's topic: {papers}. Run one search_graph query "
    "on these arxiv_ids with p.primary_category = \"{code}\" to check the category, and cite "
    "only those whose title or abstract fits the question's topic. Do not call search_papers "
    "unless none of them fit. If the question does not ask for {code} papers on a topic, "
    "ignore this message."
)


def usable(output: Any) -> bool:
    return isinstance(output, dict) and bool(output.get("papers"))


def hint(output: dict[str, Any], category: str) -> str:
    listed = "; ".join(
        f"{paper['arxiv_id']} \"{str(paper.get('title') or '').replace(chr(34), chr(39))}\""
        for paper in output["papers"]
    )
    return HINT.format(code=category, papers=listed)


def call_record(query: str, category: str, output: Any, seconds: float) -> dict[str, Any]:
    """앱이 부른 분류 안 검색을 `search_papers` 호출 기록 모양으로."""
    return {
        "id": f"app:{TOOL}:category", "name": TOOL, "by": "app",
        "args": {"query": query, "top_k": TOP_K, "primary_category": category},
        "status": "success",
        "output": output,
        "seconds": round(seconds, 3),
    }
