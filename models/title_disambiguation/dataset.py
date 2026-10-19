"""제목 질의와 후보 논문의 쌍에 라벨을 붙여 데이터셋을 만듭니다. 라벨이 공짜입니다.

    uv run python -m models.title_disambiguation.dataset --papers 1200

**과제.** `resolve_paper`가 전문 검색으로 후보를 받으면 지금은 손으로 고른 임계값 두 개로
결정합니다 — 1·2위 점수 차 0.10, 적중률 0.8
([ADR-028](../../docs/adr/ADR-028-resolve-titles-with-a-fulltext-index.md)). 그 두 값은 양성
800건·음성 100건에서 **눈으로** 고른 것입니다. 같은 결정을 학습으로 하면 표본을 수천 건으로
늘릴 수 있고, 손으로 고른 규칙과 **같은 축에서** 비교할 수 있습니다.

**라벨이 공짜인 이유.** 질의를 논문 제목에서 만들므로 정답이 어느 논문인지 이미 압니다.
[`evals/title_resolution.py`](../../evals/title_resolution.py)가 쓰는 네 가지 질의 형태를
그대로 씁니다 — 제목 그대로, 앞 세 낱말, 낱말 하나 뺌, 순서 섞음. 거기에 **없는 논문
질의**(서로 다른 네 논문에서 낱말을 하나씩)를 섞어 "받아들일지 말지"도 배우게 합니다.

**피처는 Lucene이 버리는 것들입니다.** 전문 검색은 bag-of-words라 낱말 순서와 시작 위치를
보지 않습니다. 그런데 `prefix` 질의에서 틀리는 경우가 거기 걸려 있습니다 — `"when to plan"`의
정답은 제목이 그 구절로 **시작하는** 논문이었고 1위는 `"When to Re-Plan: ..."`이었습니다.

| 피처 | 왜 |
|---|---|
| `score`, `score_norm` | Lucene 점수와 1위 대비 비율 |
| `score_gap` | 1·2위 상대 차. 손으로 고른 규칙이 쓰는 값 |
| `query_coverage`, `title_coverage` | 두 방향 적중률. 손으로 고른 규칙이 쓰는 값 |
| `starts_with` | 제목이 질의로 시작하는가. **Lucene이 버리는 신호** |
| `order_kept` | 질의 낱말이 제목에서 같은 순서인가. 같음 |
| `title_words`, `query_words` | 길이 |
| `rank` | 전문 검색 순위 |
| `exact_unique` | 부분문자열이 정확히 한 편인가. 손으로 고른 규칙의 1단계 |

## 시간 분리

`month_index`(arxiv ID의 `YYMM`,
[ADR-002](../../docs/adr/ADR-002-arxiv-id-as-time-key.md))로 자릅니다. 논문 단위가 아니라
**시간 단위**로 나누는 이유는 11-3절과 같습니다 — 무작위 분할은 같은 시기의 제목 분포를 양쪽에
넣어 점수를 부풀립니다.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path
from typing import Any

from pipeline import features
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import Snapshot, month_index
from title_search import MIN_WORD, escape_lucene, query_coverage, title_coverage, words

OUTPUT = Path("derived/title_disambiguation.jsonl")
TITLE_INDEX = "arxiv_paper_title_fulltext"
CANDIDATES = 10
PREFIX_WORDS = 3
SEED = 20261019
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")
# 논문 몇 편마다 "없는 논문" 질의를 하나 섞는가.
FAKE_EVERY = 5

FULLTEXT = (
    "CALL db.index.fulltext.queryNodes($index, $needle, {limit: $limit}) YIELD node, score "
    "RETURN node.arxiv_id AS arxiv_id, node.title AS title, score ORDER BY score DESC"
)
EXACT = (
    "MATCH (p:ArxivPaper) WHERE toLower(p.title) CONTAINS $needle "
    "RETURN p.arxiv_id AS arxiv_id LIMIT 2"
)


def variants(title: str, rng: random.Random) -> dict[str, str]:
    """`evals/title_resolution.py`와 같은 네 형태입니다. 같은 분포를 배워야 합니다."""
    tokens = title.split()
    dropped = tokens[:]
    if len(dropped) > 4:
        del dropped[len(dropped) // 2]
    shuffled = tokens[:]
    rng.shuffle(shuffled)
    return {
        "full": title,
        "prefix": " ".join(tokens[:PREFIX_WORDS]),
        "dropped": " ".join(dropped),
        "shuffled": " ".join(shuffled),
    }


def fake_title(titles: list[str], rng: random.Random) -> str:
    """존재하지 않는 제목. 서로 다른 네 논문에서 낱말을 하나씩 뽑습니다."""
    picked = []
    for chosen in rng.sample(titles, min(4, len(titles))):
        tokens = chosen.split()
        if tokens:
            picked.append(rng.choice(tokens))
    return " ".join(picked)


def _alnum(word: str) -> str:
    return "".join(char for char in word if char.isalnum())


def order_kept(query: str, title: str) -> float:
    """질의 낱말이 제목에서 같은 순서로 나타나는 비율. Lucene은 순서를 보지 않습니다."""
    asked = [w for w in (query or "").lower().split() if len(w) >= MIN_WORD]
    found = [_alnum(w) for w in (title or "").lower().split()]
    positions = []
    for word in asked:
        cleaned = _alnum(word)
        for index, other in enumerate(found):
            if other == cleaned:
                positions.append(index)
                break
    if len(positions) < 2:
        return 1.0 if positions else 0.0
    rising = sum(1 for a, b in zip(positions, positions[1:], strict=False) if a < b)
    return round(rising / (len(positions) - 1), 4)


def row_features(
    query: str, candidate: dict[str, Any], rank: int, best: float, second: float
) -> dict[str, float]:
    title = candidate.get("title") or ""
    score = float(candidate.get("score") or 0.0)
    normalized = " ".join((query or "").lower().split())
    return {
        "score": round(score, 4),
        "score_norm": round(score / best, 4) if best > 0 else 0.0,
        "score_gap": round((best - second) / best, 4) if best > 0 else 0.0,
        "query_coverage": query_coverage(query, title),
        "title_coverage": title_coverage(query, title),
        "starts_with": float(title.lower().startswith(normalized)),
        "order_kept": order_kept(query, title),
        "title_words": float(len([w for w in words(title) if len(w) >= MIN_WORD])),
        "query_words": float(len([w for w in words(query) if len(w) >= MIN_WORD])),
        "rank": float(rank),
    }


def collect(snap: Snapshot, *, papers: int, output: Path) -> dict[str, Any]:
    import chatbot

    cache = features.Cache(snap)
    meta = {
        row["arxiv_id"]: " ".join((row.get("title") or "").split())
        for row in cache.meta()
    }
    usable = sorted(
        arxiv_id for arxiv_id, title in meta.items()
        if len(title.split()) >= 4 and ARXIV_ID.match(arxiv_id)
        and month_index(arxiv_id) is not None
    )
    rng = random.Random(SEED)
    chosen = rng.sample(usable, min(papers, len(usable)))
    titles = [meta[arxiv_id] for arxiv_id in chosen]

    written = queries = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for index, arxiv_id in enumerate(chosen):
            shapes = variants(meta[arxiv_id], rng)
            if index % FAKE_EVERY == 0:
                shapes["nonexistent"] = fake_title(titles, rng)
            for shape, needle in shapes.items():
                target = None if shape == "nonexistent" else arxiv_id
                found = chatbot.run_read(
                    FULLTEXT,
                    {"index": TITLE_INDEX, "needle": escape_lucene(needle),
                     "limit": CANDIDATES},
                )
                if not found:
                    continue
                exact = chatbot.run_read(EXACT, {"needle": needle.lower()})
                best = float(found[0]["score"] or 0.0)
                second = float(found[1]["score"] or 0.0) if len(found) > 1 else 0.0
                queries += 1
                for rank, candidate in enumerate(found, start=1):
                    handle.write(json.dumps({
                        "query_id": f"{arxiv_id}:{shape}",
                        "shape": shape,
                        "needle": needle,
                        "month_index": month_index(arxiv_id),
                        "arxiv_id": candidate["arxiv_id"],
                        # 정답이 없는 질의는 모든 후보가 0입니다.
                        "label": int(target is not None and candidate["arxiv_id"] == target),
                        "exact_unique": int(len(exact) == 1),
                        # 부분문자열이 한 편일 때 그 논문이 무엇인가. 손으로 고른 규칙의
                        # 1단계를 데이터에서 **그대로 재현**하려면 필요합니다 - 없으면
                        # 비교가 규칙에 불리해집니다.
                        "exact_id": exact[0]["arxiv_id"] if len(exact) == 1 else None,
                        **row_features(needle, candidate, rank, best, second),
                    }, ensure_ascii=False) + "\n")
                    written += 1
    return {
        "snapshot_version": snap.version,
        "papers": len(chosen),
        "queries": queries,
        "rows": written,
        "candidates_per_query": CANDIDATES,
        "fake_every": FAKE_EVERY,
        "output": str(output).replace("\\", "/"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="제목 해석 데이터셋을 만듭니다.")
    parser.add_argument("--version")
    parser.add_argument("--papers", type=int, default=1200)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    print(json.dumps(
        collect(snap, papers=args.papers, output=args.output), ensure_ascii=False, indent=2
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
