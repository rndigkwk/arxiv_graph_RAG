"""범위 밖 질문은 앱이 먼저 가립니다 — 연구 단서가 없고, 컬렉션에 가까운 논문도 없을 때(ADR-095).

레드팀 셋(ADR-094)에서 범위 밖 질문 5개 중 2개에 관계없는 논문이 근거로 붙었습니다. 김치찌개
끓이는 법에는 음식 이미지 논문이, 2022 월드컵 우승국에는 "Argentina won"이라는 **모델 지식의
답**과 2026 결승을 다룬 논문이 붙었습니다. 에이전트는 범위 밖 질문에도 `search_papers`를 부르고,
벡터 검색은 언제나 무언가를 돌려줍니다. 판정은 근거 논문이 질문의 조건에 맞는지를 보지,
답의 문장이 근거에서 나왔는지는 보지 않습니다.

**지시문을 바꾸지 않습니다.** 지시문에 한 줄을 더하면 모든 질문의 턴이 느려집니다(ADR-090). 그래서
라우터 자리에서 코드로 가립니다.

1. **연구 단서가 있으면 범위 안입니다.** 논문·저자·인용·분류 같은 낱말이나 연구 용어가 하나라도
   있으면 이 단계는 아무것도 하지 않습니다 - 평가 질문 150개가 모두 여기서 끝납니다. 벡터 검색도
   부르지 않으므로 평소 질문의 지연은 그대로입니다.
2. **단서가 없으면 벡터 검색 1위의 점수를 봅니다.** 날씨·요리·코드·맛집·농담 같은 질문은 0.62~0.67,
   짧은 주제 질문("What is RLHF?", "mixture of experts")은 0.74~0.88이었습니다(10개씩).
   - `DECLINE_BELOW`보다 낮으면 **에이전트를 부르지 않고** 정해진 문장으로 답합니다. 근거는 비어
     있습니다.
   - 그 이상이면 에이전트에 그대로 맡깁니다. 처음에는 "연구 질문이 아니면 근거를 비우라"는
     메시지를 붙였는데, VM에서 "What is RLHF?"(0.74)까지 거절해서 뺐습니다(ADR-095 후속).

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

import re
from typing import Any

from src import routing

TOOL = "search_papers"
TOP_K = 1
# 범위 밖 9개의 최고점(0.669)과 짧은 주제 질문 10개의 최저점(0.739) 사이입니다.
DECLINE_BELOW = 0.70

# 질문이 연구·논문을 묻는다는 단서. **넓게 둡니다** - 범위 안 질문을 범위 밖으로 보는 쪽이
# 범위 밖 질문을 놓치는 쪽보다 비쌉니다(놓치면 지금처럼 에이전트가 답할 뿐입니다).
DOMAIN_CUES = re.compile(
    r"\bpapers?\b|\barxiv\b|\bpreprints?\b|\bauthors?\b|\bresearch\w*\b|\bstud(?:y|ies)\b"
    r"|\bsurveys?\b|\bbenchmarks?\b|\bdatasets?\b|\bmodel(?:s|ing|ling)?\b|\blearning\b"
    r"|\bneural\b|\bnetworks?\b|\balgorithms?\b|\btransformers?\b|\bgraphs?\b|\bagents?\b"
    r"|\bllms?\b|\bai\b|\bml\b|\bnlp\b|\bvision\b|\brobot\w*\b|\bretrieval\b|\bembedding\w*\b"
    r"|\boptimi[sz]\w*\b|\binference\b|\btraining\b|\bfine-?tun\w*\b|\bdiffusion\b|\bcs\.[a-z]{2}\b"
    # 저자를 묻는 다른 말투("X has written", "in your database")와 그래프 자체를 묻는 말.
    r"|\bwr(?:ote|itten)\b|\bpublish\w*\b|\bcollection\b|\bdatabase\b|\bcypher\b"
    r"|논문|연구|저자|인용|분류|모델|학습|신경망|알고리즘|데이터셋|벤치마크|에이전트|그래프|인공지능",
    re.IGNORECASE,
)

DECLINE_ANSWER = (
    "This question is outside what this collection covers. It holds arXiv computer-science "
    "research papers - ask about papers, authors, citations, categories or research topics."
)


# 대문자가 둘 이상인 짧은 낱말 - RLHF·MoE·GANs·LLMs. "What is RLHF?"는 연구 용어가 없고 점수도
# 메시지 쪽(0.74)이라, VM에서 모델이 메시지를 따라 거절했습니다(ADR-095 후속). "FIFA"도 여기에
# 걸려 월드컵 질문은 에이전트가 그대로 받습니다 - 범위 안 질문을 막는 쪽이 더 비쌉니다.
ACRONYM = re.compile(r"\b[A-Z][A-Za-z]{0,5}[A-Z]s?\b")


def has_domain_cue(*texts: str | None) -> bool:
    """연구 단서가 하나라도 있으면 True. 라우터의 관계 단서와 약어도 단서입니다."""
    for text in texts:
        if ACRONYM.search(text or ""):
            return True
        lowered = (text or "").lower()
        if DOMAIN_CUES.search(lowered) or any(
            re.search(pattern, lowered) for _, pattern in routing.RELATION_CUES
        ):
            return True
    return False


# 대문자로 시작하는 두세 낱말 - 사람 이름일 수 있는 구간입니다. 이름만 적은 질문
# ("What has Yann LeCun done?")은 단서가 없고 벡터 점수도 낮아, 그래프에 그 저자가 있는지
# 먼저 봅니다(`chatbot.scope_first`). "LeCun"처럼 안에 대문자가 있는 성도 받습니다.
_WORD = r"[A-Z][a-z]+(?:[A-Z][a-z]+)?(?:[-'][A-Z]?[a-z]+)?"
NAME = re.compile(rf"\b{_WORD}(?:\s+{_WORD}){{1,2}}\b")


def name_candidates(*texts: str | None) -> list[str]:
    """질문에서 저자 이름일 수 있는 구간. 세 낱말이면 앞뒤 두 낱말씩도 넣습니다."""
    names: list[str] = []
    for text in texts:
        for match in NAME.finditer(text or ""):
            words = match.group(0).split()
            names.append(" ".join(words))
            if len(words) == 3:
                names += [" ".join(words[:2]), " ".join(words[1:])]
    return list(dict.fromkeys(names))


def top_score(output: Any) -> float | None:
    papers = output.get("papers") if isinstance(output, dict) else None
    if not papers:
        return None
    score = papers[0].get("score")
    return float(score) if isinstance(score, (int, float)) else None


def decide(output: Any) -> str:
    """`decline`(앱이 답함) 또는 `pass`(에이전트에 그대로 맡김)."""
    score = top_score(output)
    # 점수를 모르면 거절하지 않습니다 - 범위 안 질문을 막는 쪽이 비쌉니다.
    return "decline" if score is not None and score < DECLINE_BELOW else "pass"


def call_record(query: str, output: Any, seconds: float) -> dict[str, Any]:
    """앱이 부른 범위 확인 검색을 `search_papers` 호출 기록 모양으로."""
    return {
        "id": f"app:{TOOL}:scope", "name": TOOL, "by": "app",
        "args": {"query": query, "top_k": TOP_K, "purpose": "scope"},
        "status": "success",
        "output": output,
        "seconds": round(seconds, 3),
    }
