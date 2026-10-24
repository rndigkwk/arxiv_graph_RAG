"""GraphRAG 변형별 검색 성능을 질문 유형별로 비교합니다 (Day 57 비교 틀).

    uv run python -m evals.variant_study
    uv run python -m evals.variant_study --cases evals/cases.json --output reports/...

Day 57 부록의 **HippoRAG 2**는 LLM이 추출한 트리플로 구 노드를 만들고, 질문으로 시드를 정해
**개인화 PageRank(PPR)로 문서를 고르는** 방식입니다. 이 프로젝트가 이미 같은 구조를 갖고 있고,
차이는 그래프의 출처입니다 — 추출한 트리플이 아니라 실제 `REFERENCES` 인용 관계입니다. 색인
LLM 비용이 0이고 표기 통일 문제도 없습니다.

그래서 여기서 재는 것은 "PPR이 좋은가"가 아니라 **PPR이 어떤 질문에서 작동하고 어떤 질문에서
작동하지 않는가**입니다. 평균 하나로 뭉치면 그 구분이 사라집니다.

## 비교하는 변형

| 변형 | 방식 |
|---|---|
| `vector` | 질의 임베딩과 제목+초록 임베딩의 코사인 상위 k |
| `ppr` | 벡터 상위 몇 편을 시드로 PPR을 돌려 상위 k (HippoRAG식, 시드 포함) |
| `ppr_no_seeds` | 같은 PPR에서 시드를 결과에서 제외 |
| `hybrid` | 벡터 후보 안에서 벡터 점수와 PPR 점수를 합쳐 재정렬 |

답변을 생성하지 않습니다. 케이스마다 질의 임베딩 하나만 쓰므로 비용이 거의 들지 않고, 네 변형이
**같은 후보와 같은 임베딩**을 보므로 검색 변동이 비교에 끼지 않습니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean
from time import perf_counter
from typing import Any

import numpy as np

from evals.metrics import score_retrieval
from pipeline import snapshot as snapshot_module

CASES = Path("evals/cases_graph.json")
OUTPUT = Path("reports/variant_study.json")
POOL = 20
SEEDS = 3
TOP_K = 5
HYBRID_WEIGHT = 0.5
VARIANTS = ("vector", "ppr", "ppr_no_seeds", "hybrid")


def snapshot_citation_graph(chatbot: Any, snap: Any) -> dict[str, Any]:
    """Build the undirected citation graph from the pinned snapshot, not from Aura.

    `chatbot.citation_graph()` reads 69,438 edges over the network and hit the 30s statement
    timeout while this study was running. An offline comparison has no reason to pull them:
    the snapshot holds the same edges, verified row for row against the database.

    The matrices come from `chatbot.transition_matrix`, so the algorithm being measured is the
    one the app runs — only the edge source differs.
    """
    ids = [row["arxiv_id"] for row in snap.rows("papers")]
    index = {arxiv_id: position for position, arxiv_id in enumerate(ids)}
    pairs = [
        (index[row["citing"]], index[row["cited"]])
        for row in snap.rows("references")
        if row["citing"] in index and row["cited"] in index
    ]
    rows = np.array([left for left, _ in pairs], dtype=np.int32)
    cols = np.array([right for _, right in pairs], dtype=np.int32)
    both = np.unique(
        np.stack([np.concatenate([rows, cols]), np.concatenate([cols, rows])]), axis=1
    )
    undirected, degree = chatbot.transition_matrix(both[0], both[1], len(ids))
    return {
        "ids": ids,
        "index": index,
        "size": len(ids),
        "undirected": undirected,
        "undirected_degree": degree,
    }


def personalized_pagerank(chatbot: Any, graph: dict[str, Any], seed_ids: list[str]):
    """Teleport on the seeds only, exactly as `chatbot.personalized_pagerank` does."""
    seeds = [graph["index"][arxiv_id] for arxiv_id in seed_ids if arxiv_id in graph["index"]]
    if not seeds:
        return None, []
    teleport = np.zeros(graph["size"])
    teleport[seeds] = 1.0 / len(seeds)
    scores = chatbot.run_pagerank(
        graph["undirected"], graph["undirected_degree"], teleport
    )
    return scores, seeds


def load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value["cases"] if isinstance(value, dict) else value


def vector_pool(chatbot: Any, query: str, pool: int = POOL) -> list[dict[str, Any]]:
    """Fetch a candidate pool once; every variant reads this same list."""
    found = chatbot.vector_retriever.search(query_text=query, top_k=max(1, int(pool)))
    return [
        {"arxiv_id": item.metadata["arxiv_id"], "score": float(item.metadata.get("score") or 0.0)}
        for item in found.items
    ]


def ppr_ranking(
    chatbot: Any, graph: dict[str, Any], pool: list[dict[str, Any]], *,
    seeds: int, top_k: int, drop_seeds: bool
) -> tuple[list[str], list[str]]:
    """Rank the whole corpus by personalized PageRank seeded on the top vector hits."""
    seed_ids = [paper["arxiv_id"] for paper in pool[:seeds]]
    scores, positions = personalized_pagerank(chatbot, graph, seed_ids)
    if scores is None:
        return [], seed_ids
    ranked = np.argsort(-scores)
    excluded = set(positions) if drop_seeds else set()
    picked = [
        graph["ids"][position]
        for position in ranked
        if position not in excluded
    ]
    return picked[:top_k], seed_ids


def hybrid_ranking(
    chatbot: Any, graph: dict[str, Any], pool: list[dict[str, Any]], *,
    seeds: int, top_k: int, weight: float
) -> list[str]:
    """Reorder the vector pool with PPR, so the graph can only move what retrieval found.

    The earlier study rejected reranking by *global* PageRank: it is query-independent and only
    pushed famous papers up. PPR is query-dependent, so it is a different question and gets its
    own measurement rather than inheriting that verdict.
    """
    seed_ids = [paper["arxiv_id"] for paper in pool[:seeds]]
    scores, _ = personalized_pagerank(chatbot, graph, seed_ids)
    if scores is None:
        return [paper["arxiv_id"] for paper in pool[:top_k]]
    pool_scores = [
        scores[graph["index"][paper["arxiv_id"]]]
        if paper["arxiv_id"] in graph["index"] else 0.0
        for paper in pool
    ]
    scale = float(np.median([value for value in pool_scores if value > 0] or [1.0])) or 1.0
    combined = [
        (paper["score"] + weight * (value / (value + scale)), paper["arxiv_id"])
        for paper, value in zip(pool, pool_scores, strict=True)
    ]
    combined.sort(reverse=True)
    return [arxiv_id for _, arxiv_id in combined[:top_k]]


def run_case(chatbot: Any, graph: dict[str, Any], case: dict[str, Any], *,
             top_k: int) -> dict[str, Any]:
    """Score every variant for one case against the same gold set."""
    start = perf_counter()
    pool = vector_pool(chatbot, case["search_query"])
    pool_seconds = perf_counter() - start
    rankings: dict[str, list[str]] = {
        "vector": [paper["arxiv_id"] for paper in pool[:top_k]]
    }
    timings = {"vector": pool_seconds}

    start = perf_counter()
    rankings["ppr"], seed_ids = ppr_ranking(
        chatbot, graph, pool, seeds=SEEDS, top_k=top_k, drop_seeds=False
    )
    timings["ppr"] = pool_seconds + (perf_counter() - start)

    start = perf_counter()
    rankings["ppr_no_seeds"], _ = ppr_ranking(
        chatbot, graph, pool, seeds=SEEDS, top_k=top_k, drop_seeds=True
    )
    timings["ppr_no_seeds"] = pool_seconds + (perf_counter() - start)

    start = perf_counter()
    rankings["hybrid"] = hybrid_ranking(
        chatbot, graph, pool, seeds=SEEDS, top_k=top_k, weight=HYBRID_WEIGHT
    )
    timings["hybrid"] = pool_seconds + (perf_counter() - start)

    return {
        "id": case["id"],
        "category": case["category"],
        "gold_size": len(case["expected_ids"]),
        "seed_ids": seed_ids,
        "variants": {
            name: {
                **score_retrieval(case["expected_ids"], ranking, k=top_k),
                "elapsed_seconds": round(timings[name], 4),
                "retrieved": ranking,
            }
            for name, ranking in rankings.items()
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_count": len(rows),
        **{
            name: {
                metric: round(
                    fmean(row["variants"][name][metric] for row in rows), 4
                )
                for metric in ("hit_at_k", "recall_at_k", "reciprocal_rank",
                               "elapsed_seconds")
            }
            for name in VARIANTS
            if rows
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="GraphRAG 변형별 검색을 비교합니다.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부 돌립니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    cases = load_cases(args.cases)
    if args.limit:
        cases = cases[: args.limit]

    from src import chatbot

    snap = snapshot_module.latest()
    graph = snapshot_citation_graph(chatbot, snap)
    rows = [run_case(chatbot, graph, case, top_k=args.top_k) for case in cases]
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": args.cases.name,
        "snapshot_version": snap.version,
        "top_k": args.top_k,
        "pool": POOL,
        "seeds": SEEDS,
        "hybrid_weight": HYBRID_WEIGHT,
        "note": (
            "네 변형이 같은 후보·같은 질의 임베딩을 봅니다. 답변은 생성하지 않으므로 "
            "검색 성능만 비교합니다."
        ),
        "overall": summarize(rows),
        "by_category": {
            category: summarize([row for row in rows if row["category"] == category])
            for category in sorted({row["category"] for row in rows})
        },
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {"overall": report["overall"], "by_category": report["by_category"]},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
