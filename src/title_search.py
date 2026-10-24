"""제목으로 논문 하나를 고르는 순수 로직입니다. Neo4j를 가져오지 않습니다.

`chatbot.resolve_paper`가 두 조회를 보내고, 무엇을 돌려줄지는 여기서 정합니다. 질의를 보내는
부분과 고르는 부분을 나눠야 **접속 없이** 판정 규칙을 전수 검사할 수 있습니다.

## 왜 두 방법을 섞는가

제목을 arxiv_id로 바꾸는 방법을 200편 × 질의 4형태로 비교했습니다
([`evals/title_resolution.py`](evals/title_resolution.py) →
[`reports/title_resolution.json`](reports/title_resolution.json)).

| 질의 | `CONTAINS` | 전문 검색 | 혼합 |
|---|---:|---:|---:|
| 제목 그대로 | 0.990 | 1.000 | **1.000** |
| 앞 3낱말만 | **0.800** | 0.780 | **0.865** |
| 낱말 하나 뺌 | 0.045 | **1.000** | **1.000** |
| 낱말 순서 섞음 | 0.005 | **1.000** | **1.000** |
| 전체 | 0.4600 | 0.9450 | **0.9663** |

`CONTAINS`는 제목을 **그대로** 적을 때만 쓸 수 있습니다. 낱말 하나만 빠져도 94.5%가 0건이고
순서를 섞으면 99%가 0건입니다. 반대로 **짧은 구절에서는 `CONTAINS`가 더 낫습니다**(0.800 대
0.780) — 짧은 구절은 고유 부분문자열일 때가 많은데 전문 검색은 흔한 낱말로 많은 제목을
끌어옵니다.

그래서 **부분문자열이 정확히 한 편이면 그것을 믿고, 아니면 전문 검색 1위**를 씁니다. 이
혼합이 네 형태 모두에서 두 방법보다 같거나 낫습니다.

## 애매함을 어떻게 아는가

전문 검색은 늘 후보를 여러 개 돌려주므로 편수로는 알 수 없습니다. 1위와 2위의 **점수 차**가
가릅니다 — 맞을 때 중앙값 0.394~0.540, 틀릴 때 **0.038**입니다.

| 임계값 | 틀린 것 중 잡음 | 맞은 것 중 헛경보 |
|---:|---:|---:|
| 0.05 | 65.9% | 1.1% |
| **0.10** | **95.5%** | **2.9%** |
| 0.15 | 97.7% | 4.9% |
| 0.20 | 100.0% | 7.5% |

**0.10**을 씁니다. 마지막 4.5%를 더 잡으려고 0.20으로 올리면 헛경보가 2.6배가 됩니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 1위와 2위의 상대 점수 차가 이보다 작으면 애매하다고 말합니다. 위 표에서 고른 값입니다.
AMBIGUITY_GAP = 0.10
# 적중률이 이보다 낮으면 "수집 범위에 없는 논문일 것"으로 봅니다. 200편 양성 + 가짜 제목
# 100건으로 고른 값입니다 - 음성 94%를 막고 **맞은 양성은 0% 잃습니다**(아래 표).
MIN_COVERAGE = 0.8
# Lucene이 연산자로 읽는 문자들입니다. 논문 제목에 콜론·물음표·괄호가 흔합니다.
LUCENE_SPECIAL = '+-&|!(){}[]^"~*?:\\/'


@dataclass(frozen=True)
class Resolution:
    """무엇으로 정했고, 믿을 만한지."""

    arxiv_id: str | None
    method: str
    ambiguous: bool
    score_gap: float | None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    coverage: float | None = None
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "method": self.method,
            "ambiguous": self.ambiguous,
            "score_gap": self.score_gap,
            "candidates": self.candidates,
            "coverage": self.coverage,
            "note": self.note,
        }


def escape_lucene(text: str) -> str:
    """Neutralize Lucene operators so a title can be searched literally."""
    out = []
    for char in text or "":
        if char in LUCENE_SPECIAL:
            out.append("\\")
        out.append(char)
    return "".join(out)


# 적중률을 셀 때 보는 낱말의 최소 길이입니다. 한두 글자는 조사·관사라 신호가 없습니다.
MIN_WORD = 3


def words(text: str) -> set[str]:
    """Content words of a title or query, lowercased."""
    return {
        "".join(ch for ch in token if ch.isalnum())
        for token in (text or "").lower().split()
    } - {""} | set()


def title_coverage(query: str, title: str) -> float:
    """What fraction of the **title's** words the query mentioned.

    질의 쪽으로 정규화하면 낱말이 덧붙을 때 무너집니다 - "the paper titled Hyperbolic Neural
    Networks"가 0.500이 되어 정당한 질의가 거부됩니다. 제목 쪽으로 세면 덧붙은 낱말이 세지
    않으므로 그 경우가 1.000입니다.

    반대로 짧은 구절이 긴 제목을 가리킬 때는 낮게 나옵니다("when to plan" → 긴 제목). 그것은
    실제로 그 질의가 그 제목을 특정하지 못한다는 뜻이라 낮은 쪽이 맞습니다.
    """
    wanted = {word for word in words(title) if len(word) >= MIN_WORD}
    if not wanted:
        return 0.0
    asked = {word for word in words(query) if len(word) >= MIN_WORD}
    return round(len(wanted & asked) / len(wanted), 4)


def query_coverage(query: str, title: str) -> float:
    """What fraction of the query's words appear in the title.

    **점수 차만으로는 못 막는 실패가 있습니다.** 전문 검색은 흔한 낱말 하나만 맞아도 무언가를
    돌려줍니다. 2026-10-18 탐침에서 "a paper that certainly does not exist here xyzzy"가
    "Does Zero-Shot Reinforcement Learning Exist?"를 **점수 차 0.4315로 자신 있게** 돌려줬습니다.
    그 경우 1·2위 차이는 클 수 있으므로 애매함으로는 걸리지 않습니다.

    질의 낱말이 제목에 얼마나 들어 있는지는 질의마다 정규화되므로, Lucene 점수의 절대값과
    달리 임계값을 둘 수 있습니다.
    """
    asked = {word for word in words(query) if len(word) >= MIN_WORD}
    if not asked:
        return 0.0
    found = {word for word in words(title) if len(word) >= MIN_WORD}
    return round(len(asked & found) / len(asked), 4)


def relative_gap(candidates: list[dict[str, Any]]) -> float | None:
    """1위와 2위의 상대 점수 차. 후보가 하나뿐이면 None입니다."""
    if len(candidates) < 2:
        return None
    best = float(candidates[0].get("score") or 0.0)
    second = float(candidates[1].get("score") or 0.0)
    if best <= 0:
        return None
    return round((best - second) / best, 4)


def coverage(query: str, title: str) -> float:
    """How well the query and the title identify each other.

    두 정규화의 **최댓값**입니다. 하나만 쓰면 각각 다른 곳에서 깨집니다.

    | 질의 | 질의 쪽 | 제목 쪽 | max |
    |---|---:|---:|---:|
    | `the paper titled Hyperbolic Neural Networks` | 0.500 | 1.000 | **1.000** |
    | `when to plan` (긴 제목의 앞 세 낱말) | 1.000 | 0.375 | **1.000** |
    | `a paper that certainly does not exist here xyzzy` | 0.250 | 0.333 | **0.333** |

    질의 쪽은 덧붙은 낱말("the paper titled ...")에 무너지고, 제목 쪽은 정당한 짧은 구절을
    거부합니다(`prefix` 질의의 맞은 것들이 중앙값 0.375). 최댓값을 쓰면 네 질의 형태 모두
    맞은 경우의 p10이 1.000입니다.
    """
    return max(query_coverage(query, title), title_coverage(query, title))


def choose(
    exact: list[dict[str, Any]],
    ranked: list[dict[str, Any]],
    *,
    query: str = "",
    gap: float = AMBIGUITY_GAP,
    min_coverage: float = MIN_COVERAGE,
) -> Resolution:
    """Pick one paper, or say the question does not name one clearly.

    `exact`는 부분문자열로 걸린 논문들(점수 없음), `ranked`는 전문 검색 결과(점수 내림차순)
    입니다. 위 측정이 고른 순서로 봅니다.
    """
    if len(exact) == 1:
        # 부분문자열이 정확히 한 편이면 가장 확실합니다. 짧은 구절에서 전문 검색을 이깁니다.
        return Resolution(
            arxiv_id=exact[0]["arxiv_id"],
            method="exact_substring",
            ambiguous=False,
            score_gap=None,
            candidates=exact,
            note="제목 구절이 이 논문 하나에만 들어 있습니다.",
        )
    if not ranked:
        return Resolution(
            arxiv_id=None, method="none", ambiguous=False, score_gap=None,
            candidates=[],
            note=(
                "제목으로 찾은 논문이 없습니다. 수집 범위에 없는 논문일 수 있습니다. "
                "낱말을 더 주거나 다른 방법으로 찾으세요."
            ),
        )
    score_gap = relative_gap(ranked)
    ambiguous = score_gap is not None and score_gap < gap
    matched = coverage(query, ranked[0].get("title") or "") if query else 1.0
    weak = matched < min_coverage
    if weak:
        # **점수 차로는 못 막는 실패입니다.** 전문 검색은 흔한 낱말 하나만 맞아도 무언가를
        # 돌려줍니다. 가짜 제목 질의가 점수 차 0.4315로 "자신 있게" 무관한 논문을 받았습니다.
        return Resolution(
            arxiv_id=None,
            method="weak_match",
            ambiguous=False,
            score_gap=score_gap,
            candidates=ranked,
            coverage=matched,
            note=(
                "제목이 수집 범위의 어느 논문과도 충분히 맞지 않습니다. 가장 가까운 후보를 "
                "같이 돌려주지만 그 논문이라고 가정하지 마세요. 없는 논문일 수 있습니다."
            ),
        )
    return Resolution(
        arxiv_id=ranked[0]["arxiv_id"],
        method="fulltext",
        ambiguous=ambiguous,
        score_gap=score_gap,
        candidates=ranked,
        coverage=matched,
        note=(
            "1위와 2위의 점수가 비슷합니다. 질문이 어느 논문을 말하는지 후보에서 직접 "
            "확인하고, 확실하지 않으면 그렇다고 답하세요."
            if ambiguous
            else "제목과 가장 잘 맞는 논문입니다."
        ),
    )
