"""제목을 arxiv_id로 바꾸는 두 방법을 같은 질의에서 비교합니다.

    uv run python -m evals.title_resolution --limit 200

**재는 것.** 모델은 질문에 적힌 제목으로 논문 하나를 특정해야 합니다. 지금 쓰는 방법은
`toLower(p.title) CONTAINS "구절"`이고, 대안은 전문 검색 인덱스
(`scripts/fulltext_index.py`)입니다.

**왜 `CONTAINS`가 깨지는가.** 순위가 없습니다 — 걸리거나 안 걸리거나입니다. 구절이 짧으면
여러 편이 걸리고, 모델이 그중 하나를 고를 근거가 없습니다. 2026-10-18에 `coauthor-04`가
`CONTAINS "when to plan"`으로 두 편을 잡고 두 시드의 공저자를 모두 펼쳐 25편을 인용했습니다
(ADR-024). 반대로 질문이 제목을 **바꿔 쓰면** 하나도 안 걸립니다.

**질의를 네 가지로 만듭니다.** 사용자가 제목을 그대로 적는다는 가정이 평가 세트의 가정일
뿐이므로, 거기서만 재면 실제 실패를 못 봅니다.

| 질의 | 무엇을 흉내내는가 |
|---|---|
| `full` | 제목을 그대로 붙여넣기 |
| `prefix` | 앞 세 낱말만 (`coauthor-04`를 깨뜨린 경우) |
| `dropped` | 가운데 낱말 하나를 뺌 (기억으로 적기) |
| `shuffled` | 낱말 순서를 섞음 (바꿔 적기) |

**지표는 top-1 정확도와 중의성입니다.** 평균 순위가 아니라 "1위가 그 논문인가"와 "몇 편이
걸렸는가"가 이 과제의 실패 모양입니다. `CONTAINS`는 점수가 없으므로 1위를 정할 수 없어
**걸린 편수가 1일 때만** 맞힌 것으로 셉니다 — 그것이 모델이 처한 상황입니다.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from title_search import query_coverage, title_coverage

OUTPUT = Path("reports/title_resolution.json")
CASES = Path("evals/cases_related.json")
TITLE_INDEX = "arxiv_paper_title_fulltext"
PREFIX_WORDS = 3
SEED = 20261018

CONTAINS_CYPHER = (
    "MATCH (p:ArxivPaper) WHERE toLower(p.title) CONTAINS $needle "
    "RETURN p.arxiv_id AS arxiv_id ORDER BY p.arxiv_id LIMIT 25"
)
FULLTEXT_CYPHER = (
    "CALL db.index.fulltext.queryNodes($index, $needle, {limit: 25}) "
    "YIELD node, score RETURN node.arxiv_id AS arxiv_id, node.title AS title, score "
    "ORDER BY score DESC"
)


def variants(title: str, rng: random.Random) -> dict[str, str]:
    """Four ways a question might name the same paper."""
    words = title.split()
    dropped = words[:]
    if len(dropped) > 4:
        # 가운데에서 하나 뺍니다. 양 끝은 사람이 잘 기억하는 자리입니다.
        del dropped[len(dropped) // 2]
    shuffled = words[:]
    rng.shuffle(shuffled)
    return {
        "full": title,
        "prefix": " ".join(words[:PREFIX_WORDS]),
        "dropped": " ".join(dropped),
        "shuffled": " ".join(shuffled),
    }


def escape_lucene(text: str) -> str:
    """Lucene 특수문자를 막습니다. 논문 제목에 콜론·물음표·괄호가 흔합니다."""
    out = []
    for char in text:
        if char in '+-&|!(){}[]^"~*?:\\/':
            out.append("\\")
        out.append(char)
    return "".join(out)


def resolve(chatbot, method: str, needle: str) -> list[tuple[str, float, str]]:
    """Candidates with scores. `contains`는 점수가 없으므로 전부 0.0입니다."""
    if method == "contains":
        rows = chatbot.run_read(CONTAINS_CYPHER, {"needle": needle.lower()})
        return [(row["arxiv_id"], 0.0, "") for row in rows]
    rows = chatbot.run_read(
        FULLTEXT_CYPHER, {"index": TITLE_INDEX, "needle": escape_lucene(needle)}
    )
    return [(row["arxiv_id"], float(row["score"]), row["title"] or "") for row in rows]


def score(
    found: list[tuple[str, float, str]], target: str, method: str, needle: str
) -> dict[str, Any]:
    """Did the method put the right paper where the model could use it?

    `contains`는 점수가 없어 1위를 정할 수 없습니다. 그래서 **걸린 편수가 1일 때만** 맞힌
    것으로 셉니다 - 여러 편이 걸리면 모델이 고를 근거가 없고, 그것이 25편 오답을 만든
    상황입니다. `fulltext`는 점수가 있으므로 1위로 봅니다.

    **중의성의 뜻이 방법마다 다릅니다.** `contains`에서는 "여러 편이 걸렸다"이고, `fulltext`는
    늘 여러 편을 돌려주므로 편수로는 알 수 없습니다. 1위와 2위의 **점수 차**가 작을 때가
    애매한 것입니다 - 도구가 "더 긴 제목을 달라"고 되물을 수 있어야 하므로 이 값이 필요합니다.
    """
    unique: list[tuple[str, float, str]] = []
    seen = set()
    for arxiv_id, value, title in found:
        if arxiv_id not in seen:
            seen.add(arxiv_id)
            unique.append((arxiv_id, value, title))
    ids = [arxiv_id for arxiv_id, _, _ in unique]
    if method == "contains":
        correct = len(ids) == 1 and ids[0] == target
        gap = None
    else:
        correct = bool(ids) and ids[0] == target
        gap = (
            None if len(unique) < 2 or unique[0][1] <= 0
            else round((unique[0][1] - unique[1][1]) / unique[0][1], 4)
        )
    return {
        "correct": float(correct),
        "matches": len(ids),
        "many_matches": float(len(ids) > 1),
        "missed": float(not ids),
        "target_in_results": float(target in ids),
        "score_gap": gap,
        # 1위 제목에 질의 낱말이 얼마나 들어 있는가. 점수 차로는 막지 못하는 "자신 있게
        # 틀리는" 경우를 가리는 값입니다.
        "coverage": (
            None if method == "contains" or not unique
            else query_coverage(needle, unique[0][2])
        ),
        # 제목 쪽 정규화. 질의에 덧붙은 낱말("the paper titled ...")을 세지 않습니다.
        "title_coverage": (
            None if method == "contains" or not unique
            else title_coverage(needle, unique[0][2])
        ),
    }


def fake_titles(cases: list[dict[str, Any]], count: int, rng: random.Random) -> list[str]:
    """Titles of papers that do not exist, built from words of four different ones.

    **200건 골드는 전부 존재하는 논문입니다.** 그래서 거기서는 "없는 논문을 물었을 때"를 못
    봅니다. 그런데 그것이 가장 나쁜 실패입니다 - 2026-10-18 탐침에서 전문 검색이 없는 논문
    질의에 **점수 차 0.4315로 자신 있게** 무관한 논문을 돌려줬습니다. 점수 차로는 못 막으므로
    적중률 임계값을 정해야 하고, 그러려면 음성 표본이 필요합니다.
    """
    pools = [case["seed_title"].split() for case in cases if case.get("seed_title")]
    made = []
    for _ in range(count):
        picked = []
        for chosen in rng.sample(pools, min(4, len(pools))):
            if chosen:
                picked.append(rng.choice(chosen))
        made.append(" ".join(picked))
    return made


def main() -> int:
    parser = argparse.ArgumentParser(description="제목 해석 두 방법을 비교합니다.")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--negatives", type=int, default=0,
                        help="없는 논문 질의를 이만큼 더 섞습니다(정답은 '없음').")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    import chatbot

    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"][: args.limit]
    rng = random.Random(SEED)
    rows: list[dict[str, Any]] = []
    for case in cases:
        title = case["seed_title"]
        if not title:
            continue
        for shape, needle in variants(title, rng).items():
            for method in ("contains", "fulltext"):
                found = resolve(chatbot, method, needle)
                rows.append({
                    "id": case["id"], "shape": shape, "method": method,
                    "needle": needle, **score(found, case["seed_id"], method, needle),
                })

    negatives: list[dict[str, Any]] = []
    for index, needle in enumerate(fake_titles(cases, args.negatives, rng), start=1):
        for method in ("contains", "fulltext"):
            found = resolve(chatbot, method, needle)
            # 정답이 "없음"이므로 맞힌 것은 **아무것도 돌려주지 않은 경우**입니다.
            unique = list(dict.fromkeys(a for a, _, _ in found))
            top_title = found[0][2] if found else ""
            negatives.append({
                "id": f"fake-{index:03d}", "shape": "nonexistent", "method": method,
                "needle": needle, "matches": len(unique),
                "returned_something": float(bool(unique)),
                "coverage": (
                    None if method == "contains" or not found
                    else query_coverage(needle, top_title)
                ),
                "title_coverage": (
                    None if method == "contains" or not found
                    else title_coverage(needle, top_title)
                ),
                "score_gap": (
                    None if method == "contains" or len(found) < 2 or found[0][1] <= 0
                    else round((found[0][1] - found[1][1]) / found[0][1], 4)
                ),
            })

    summary: dict[str, Any] = {}
    for method in ("contains", "fulltext"):
        summary[method] = {}
        for shape in ("full", "prefix", "dropped", "shuffled"):
            subset = [r for r in rows if r["method"] == method and r["shape"] == shape]
            if not subset:
                continue
            gaps = [r["score_gap"] for r in subset if r["score_gap"] is not None]
            wrong_gaps = [
                r["score_gap"] for r in subset
                if r["score_gap"] is not None and not r["correct"]
            ]
            right_gaps = [
                r["score_gap"] for r in subset
                if r["score_gap"] is not None and r["correct"]
            ]
            summary[method][shape] = {
                "cases": len(subset),
                "top1": round(statistics.fmean(r["correct"] for r in subset), 4),
                "missed": round(statistics.fmean(r["missed"] for r in subset), 4),
                "target_found": round(
                    statistics.fmean(r["target_in_results"] for r in subset), 4
                ),
                "matches_p50": statistics.median(r["matches"] for r in subset),
                # 1위와 2위의 점수 차. 틀린 경우에 이 값이 작으면 도구가 "애매하다"고
                # 되물을 수 있습니다. 틀렸는데 차이가 크면 자신 있게 틀린 것이라 막을 수 없습니다.
                "score_gap_p50": round(statistics.median(gaps), 4) if gaps else None,
                "score_gap_p50_when_right": (
                    round(statistics.median(right_gaps), 4) if right_gaps else None
                ),
                "score_gap_p50_when_wrong": (
                    round(statistics.median(wrong_gaps), 4) if wrong_gaps else None
                ),
            }

    if negatives:
        for method in ("contains", "fulltext"):
            subset = [r for r in negatives if r["method"] == method]
            covs = [r["coverage"] for r in subset if r["coverage"] is not None]
            summary[method]["nonexistent"] = {
                "cases": len(subset),
                "returned_something": round(
                    statistics.fmean(r["returned_something"] for r in subset), 4
                ),
                "coverage_p50": round(statistics.median(covs), 4) if covs else None,
                "coverage_max": round(max(covs), 4) if covs else None,
            }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": len(cases),
        "negatives": len(negatives) // 2,
        "index": TITLE_INDEX,
        "prefix_words": PREFIX_WORDS,
        "summary": summary,
        "note": (
            "contains는 점수가 없어 걸린 편수가 1일 때만 맞힌 것으로 셉니다. 여러 편이 "
            "걸리면 모델이 고를 근거가 없고, 그것이 2026-10-18 coauthor-04의 25편 오답을 "
            "만든 상황입니다(ADR-024)."
        ),
        "results": rows + negatives,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"cases": len(cases), "summary": summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
