"""질문이 어느 경로를 타야 하는지 **코드로** 정합니다.

[ADR-008](docs/adr/ADR-008-ppr-over-full-graphrag-index.md)에서 "어느 변형도 이기지 않고
**질문 유형이 승자를 정한다**"를 측정했는데([`EXPERIMENTS.md`](EXPERIMENTS.md) 11-1절),
경로 선택은 그 뒤로도 시스템 프롬프트의 문장으로만 남아 있었습니다. 모델이 그 문장을 지키는지
아무도 세지 않았고, 실제로 지키지 않았습니다.

**측정된 것**(full 3회, 가족별 18건):

| 가족 | hit@5 | `search_papers` 호출률 | 입력 토큰 평균 |
|---|---:|---:|---:|
| `author` | 0.944 | 0.00 | 9,874 |
| `citing` | 0.889 | 0.11 | 9,802 |
| `coauthor` | 0.167 | **0.83** | **18,946** |
| `reference` | 0.389 | **0.67** | 13,268 |
| `topcited` | 0.889 | 0.00 | 8,431 |

호출률이 높은 두 가족이 정확히 토큰이 두 배인 두 가족입니다. 그런데 그 벡터 결과는
**인용되지 않았습니다** — 같은 사례의 근거 precision·recall이 12회 전부 1.000입니다. 즉 모델은
질문에 적힌 **제목을 arxiv_id로 바꾸려고** 벡터 검색을 부르고, 돌아온 다섯 편 중 넷을 버립니다.

그 차이가 어디서 오는지도 보입니다. `citing`은 질문이 "the paper **titled** X"라고 적어 주고
호출률이 0.11인데, `coauthor`("share an author with X")와 `reference`("the papers cited by X")는
그 단서가 없어 0.83·0.67입니다. **제목이라고 말해 주면 Cypher로 찾고, 아니면 벡터로 갑니다.**

그래서 이 모듈은 질문을 보고 "저장된 관계만 묻는 질문"을 가려내, 제목은 Cypher로 풀고
`search_papers`는 부르지 말라는 지시를 그 턴에 붙입니다. **도구를 빼앗지는 않습니다** —
의미 검색이 정말 필요한 질문을 막는 쪽이 토큰 9천 개보다 비싸기 때문입니다. 지시를 지키는지는
`response["route"]`와 임베딩 토큰으로 세어서 판단합니다(ADR-024).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 저장된 관계·집계만 묻는 신호. 그래프에 **이미 들어 있는 것**을 묻는 표현들입니다.
RELATION_CUES: tuple[tuple[str, str], ...] = (
    ("share_author", r"share[sd]?\s+(?:an?\s+)?(?:author|co-?author)|co-?authored\s+with|공저"),
    ("author_named", r"author\s+named|written\s+by|authored\s+by|저자(?:가|는|를)?\s*\S*\s*논문"),
    ("citation", r"\bcite[sd]?\b|\bciting\b|\bcitation|\breference[sd]?\b|인용"),
    ("category", r"primary\s+category|\bcategor(?:y|ies)\b|분류"),
    ("counting", r"how\s+many|\bcount\b|most\s+often|most\s+cited|몇\s*(?:편|개)"),
    ("published", r"published\s+(?:in|on|before|after|between)|발행|게재"),
)

# 의미 검색이 필요하다는 신호. 하나라도 걸리면 경로를 좁히지 않습니다. 관계 신호와 **같이**
# 나오는 경우가 있습니다("확산 모델에 관한 논문 중 X와 공저자가 겹치는 것") — 그때는 벡터가
# 실제로 필요하므로 좁히는 쪽이 틀립니다.
TOPIC_CUES: tuple[tuple[str, str], ...] = (
    ("about", r"\babout\b|\bregarding\b|\bconcerning\b|\bdiscuss(?:es|ing)?\b"),
    ("similar", r"\bsimilar\b|\brelated\s+to\b|\blike\b.*\bpapers?\b|비슷|유사|관련된"),
    ("topic", r"\btopic\b|\bsubject\s+of\b|\bfield\s+of\b|\barea\s+of\b|주제|분야"),
    # 한국어의 "~에 관한/대한"은 거의 항상 주제 질문입니다. 관계 신호와 섞여 들어오는 경로가
    # 이것이어서(10-2절의 혼용 질문), 빼면 좁히면 안 되는 질문을 좁힙니다.
    ("ko_about", r"에\s*(?:관한|관하여|대한|대하여)"),
    ("explain", r"\bexplain\b|\bsummar(?:y|ise|ize)\b|\bwhat\s+is\b|설명|요약"),
)

# 질문이 논문을 **제목으로** 지칭하는 신호. 이것이 있으면 제목 해석 지시를 덧붙입니다.
TITLE_CUES = r"titled|\bthe\s+paper\b|\bpaper\s+called\b|제목"

GRAPH_ROUTE = "graph"
ANY_ROUTE = "any"

# 경로를 좁힐 때 그 턴에 붙이는 지시입니다. 짧게 둡니다 - 여기에 쓰는 토큰이 아끼려는 토큰에서
# 나갑니다.
# 제목 해석 문장이 조건문인 이유가 있습니다. 제목이 적혔는지를 **단서 낱말로는 가릴 수
# 없습니다** - 호출률이 가장 높은 `coauthor`가 바로 "titled"라고 말하지 않는 가족입니다
# ("share an author with Fuzzy K-Means Clustering..."). 대문자 구간을 세는 추측을 두는 대신,
# 판단을 모델에 맡기고 **방법**만 지정합니다. 틀려도 쓰이지 않는 한 문장이 남을 뿐입니다.
#
# 중의성 확인 문장은 **측정해서 붙였다가, 도구로 바꿨습니다.** 1차 측정에서 `coauthor-04`가
# `CONTAINS "when to plan"`으로 두 편을 잡고 25편을 인용했습니다(근거 precision 1.000 →
# 0.040). 지시로 "따로 조회해서 한 편인지 확인하라"를 붙여 고쳤지만, 그 지시가 조회를 둘로
# 쪼개서 `citing`의 RAGAS Context Precision을 1.0000 → 0.5046으로 깎았습니다.
# `resolve_paper`(전문 검색 인덱스)가 그 일을 도구 한 번으로 하고 혼합 정확도 0.9663입니다
# (`CONTAINS` 단독 0.4600). 그래서 지시는 "도구를 부르라"로 줄었습니다.
GRAPH_HINT = (
    "Routing hint: this question asks only about stored relationships (authors, citations, "
    "categories, counts, dates). Answer it with search_graph. If it names a paper, call "
    "resolve_paper with that name first and use the arxiv_id it returns - do not match the "
    "title with CONTAINS yourself. Do not call search_papers - its topic matches are not "
    "evidence here."
)


@dataclass(frozen=True)
class Route:
    """어느 경로이고, **왜** 그렇게 정했는지."""

    name: str
    cues: tuple[str, ...]
    vetoes: tuple[str, ...]

    @property
    def hint(self) -> str:
        return GRAPH_HINT if self.name == GRAPH_ROUTE else ""

    def as_dict(self) -> dict[str, object]:
        """보고서와 추적에 남길 형태. 판정 근거를 같이 남겨야 세어 볼 수 있습니다."""
        return {"name": self.name, "cues": list(self.cues), "vetoes": list(self.vetoes)}


def _matched(text: str, cues: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    return tuple(name for name, pattern in cues if re.search(pattern, text))


def classify(question: str) -> Route:
    """Decide the route from the question alone, with no model call.

    판정은 **보수적**입니다. 의미 신호가 하나라도 있으면 넓은 경로로 둡니다. 틀리는 방향을
    고르자면 "토큰을 더 쓴다"가 "답을 못 찾는다"보다 쌉니다.
    """
    text = (question or "").lower()
    cues = _matched(text, RELATION_CUES)
    vetoes = _matched(text, TOPIC_CUES)
    name = GRAPH_ROUTE if cues and not vetoes else ANY_ROUTE
    return Route(name=name, cues=cues, vetoes=vetoes)
