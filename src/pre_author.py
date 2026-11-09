"""질문의 저자 이름이 그래프의 저자 이름과 딱 맞지 않으면 앱이 먼저 후보를 알려 줍니다(ADR-121).

"Bengio가 쓴 논문이 있어?"에서 모델은 `toLower(a.name) CONTAINS "bengio"`로 찾고 이름순으로 25행에서
끊었습니다. Emmanuel Bengio(4편)와 Samy Bengio(21편)가 25행을 채워, 답은 95편이 있는 Yoshua Bengio를
빼고 "Emmanuel과 Samy의 논문이 있다"고 했습니다(2026-11-09, 로컬).

**지시문과 도구를 바꾸지 않습니다.** 저자 이름 확인 도구를 더하면 모든 질문의 턴이 그 설명을
읽습니다(ADR-090). "최근"(`pre_recent`)·분류(`pre_category`)와 같은 자리에 두고, 저자를 묻는
질문에만 메시지를 붙입니다.

**딱 맞는 이름이 없을 때만 씁니다.** "Yoshua Bengio"처럼 그래프에 그대로 있는 이름이면 아무것도 하지
않습니다 - 모델은 지금도 `a.name = ...`으로 맞게 찾습니다. 같은 이름의 다른 사람(한 노드에 섞인
"Yang Liu")은 그래프만으로는 가를 수 없어 여기서 다루지 않습니다.

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

import re
from typing import Any

TOOL = "find_author"
INDEX = "arxiv_author_name_fulltext"
SHOWN = 5  # 메시지에 적는 후보 수(편수 순)

# 저자를 묻는 말입니다. 영어는 번역된 질문, 한국어는 원문에서 봅니다.
_CUE_EN = re.compile(r"\b(?:wrote|writes|written|authored|authors?|co-?authors?|"
                     r"co-?authored|by)\b", re.IGNORECASE)
_CUE_KO = re.compile(r"쓴|썼|저자|공저|지은")

# 대문자로 시작하는 낱말 - "Bengio", "LeCun", "O'Neil". 사람 이름일 수 있는 구간입니다.
_WORD = r"[A-Z][a-z]+(?:[A-Z][a-z]+)?(?:[-'][A-Z]?[a-z]+)?"
# 앞뒤는 라틴 글자가 아니면 됩니다 - "Bengio가"처럼 한국어 조사가 바로 붙습니다.
_NAME = re.compile(rf"(?<![A-Za-z]){_WORD}(?:\s+{_WORD}){{0,2}}(?![A-Za-z])")
# 문장 첫머리·질문 낱말처럼 대문자로 쓰였을 뿐 이름이 아닌 것들입니다.
_NOT_NAMES = frozenset("""
are is was were do does did can could would should will has have had there here what which
who whom whose when where why how find show list give tell recommend search please any some
the a an papers paper find yes no in on of for with and or by about from to this that these
those authors author wrote written graph neural networks network knowledge language large
models model learning deep reasoning
""".split())


def asks_author(question: str, english: str) -> bool:
    return bool(_CUE_KO.search(question or "") or _CUE_EN.search(english or ""))


def names(question: str, english: str) -> list[str]:
    """질문에서 저자 이름일 수 있는 구간. 이름이 아닌 낱말로 시작·끝나는 부분은 덜어 냅니다."""
    found: list[str] = []
    for text in (question or "", english or ""):
        for match in _NAME.finditer(text):
            words = match.group(0).split()
            while words and words[0].lower() in _NOT_NAMES:
                words.pop(0)
            while words and words[-1].lower() in _NOT_NAMES:
                words.pop()
            if words:
                found.append(" ".join(words))
    return list(dict.fromkeys(found))


def lucene_phrase(name: str) -> str:
    """전문 색인에 낱말 순서 그대로 묻습니다. 따옴표와 역슬래시만 막습니다."""
    return '"' + name.replace("\\", " ").replace('"', " ") + '"'


def unresolved(name: str, matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """딱 맞는 저자 노드가 없을 때의 후보(편수 순). 딱 맞는 이름이 있거나 후보가 없으면 []."""
    if any(str(m.get("name", "")).casefold() == name.casefold() for m in matches):
        return []
    return sorted(matches, key=lambda m: (-int(m.get("papers") or 0), str(m.get("name"))))


# 후보를 이름순으로 한 번에 조회하면 편수가 적은 사람이 25행을 채웁니다("Bengio": Emmanuel 4편 +
# Samy 21편). 질문이 고르지 않았으면 편수가 가장 많은 사람을 a.name = 으로 찾게 합니다.
HINT = (
    "No author in the graph is named exactly \"{name}\". These author names contain it "
    "(papers in the graph): {listed}. Do not use CONTAINS for this name. Unless the question "
    "says which of them it means, query {top} with a.name = \"{top}\", and mention the others "
    "with their paper counts above. If the question is not about the author \"{name}\", "
    "ignore this message."
)


def hint(name: str, candidates: list[dict[str, Any]]) -> str:
    shown = candidates[:SHOWN]
    listed = "; ".join(f"{str(c['name']).replace(chr(34), chr(39))} ({c['papers']})"
                       for c in shown)
    if len(candidates) > SHOWN:
        listed += f"; and {len(candidates) - SHOWN} more"
    top = str(candidates[0]["name"]).replace('"', "'")
    return HINT.format(name=name.replace('"', "'"), listed=listed, top=top)


def call_record(name: str, candidates: list[dict[str, Any]], seconds: float) -> dict[str, Any]:
    """앱이 찾은 저자 후보를 도구 호출 기록 모양으로. 논문 근거가 아니므로 `papers`는 없습니다."""
    return {
        "id": f"app:{TOOL}", "name": TOOL, "by": "app",
        "args": {"name": name},
        "status": "success",
        "output": {"name": name, "candidates": candidates},
        "seconds": round(seconds, 3),
    }
