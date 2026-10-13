"""PageRank 재정렬이 이 검색에 쓸모가 있는지 재고, 재정렬의 여지 자체를 측정합니다.

Day 49 교안의 "PageRank 포화 점수로 검색 순위를 재정렬"과 Day 48 교안의 "후보 안에 정답이
있는가와 순서가 맞는가를 나눠 평가"를 한 번에 돌립니다.

    uv run --group dev python -m evals.rerank_study
    uv run --group dev python -m evals.rerank_study
        --cases evals/cases_curated.json --output reports/rerank_study_curated.json

모델 호출은 케이스마다 질의 임베딩 하나뿐입니다. 답변을 생성하지 않아 비용이 거의 들지 않습니다.
후보를 한 번만 가져와 모든 가중치에 같은 후보를 쓰므로, 검색 변동이 비교에 끼지 않습니다.

재정렬은 여기에만 있고 `chatbot`에는 넣지 않았습니다. 측정 결과 채택하지 않았기 때문입니다
(`advanced.md`의 "PageRank 재정렬" 참고). 재현하거나 다시 판단할 때 이 스크립트를 쓰세요.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import fmean
from typing import Any

import numpy as np

from evals.metrics import score_ceiling, score_retrieval

POOL = 20
K = 5
WEIGHTS = (0.0, 0.05, 0.1, 0.25, 0.5, 1.0)
CASES = Path("evals/cases.json")
OUTPUT = Path("reports/rerank_study.json")


def search_pool(chatbot: Any, query: str, pool: int = POOL) -> list[dict]:
    """재정렬 후보를 넉넉히 가져옵니다.

    에이전트 도구 `search_papers`의 top_k 상한(10)과 별개입니다. 교안대로 "가져올 검색 결과
    수"와 "답변에 쓸 논문 수"를 구분하기 위한 경로입니다.
    """
    found = chatbot.vector_retriever.search(query_text=query, top_k=max(1, int(pool)))
    return [{**item.metadata, "abstract": item.content} for item in found.items]


def pagerank_scale(chatbot: Any) -> float:
    """포화 함수의 기준값으로 전체 PageRank의 중앙값을 씁니다.

    이 그래프의 PageRank는 한쪽으로 크게 치우쳐 있습니다(중앙값 2.5e-05, 최대 6.2e-04).
    중앙값을 기준으로 두면 중앙 논문의 포화값이 0.5가 되고, 대부분의 논문이 그 근처에 몰려
    순위를 흔들지 않습니다. 가점을 실제로 받는 것은 상위 몇 %의 허브뿐입니다.
    """
    return float(np.median(chatbot.global_pagerank())) or 1.0


def rerank(chatbot: Any, papers: list[dict], weight: float, scale: float,
           top_k: int = K) -> list[dict]:
    """벡터 후보를 PageRank 포화 점수로 재정렬합니다.

    PageRank를 그대로 더하면 눈금이 1e-4 수준이라 영향이 없고, 정규화해서 더하면 유명한
    논문이 주제 적합도를 눌러 버립니다. `x / (x + 기준값)`으로 0~1에 눌러 담고 weight로
    벡터 점수 대비 비중을 제한합니다. weight=0은 벡터 순서 그대로이며 기준선입니다.
    """
    ranked = [dict(paper) for paper in papers]
    if not weight:
        return ranked[:top_k]
    graph = chatbot.citation_graph()
    scores = chatbot.global_pagerank()
    for paper in ranked:
        position = graph["index"].get(paper.get("arxiv_id"))
        raw = float(scores[position]) if position is not None else 0.0
        paper["pagerank"] = raw
        paper["pagerank_saturated"] = raw / (raw + scale)
        paper["combined_score"] = (
            float(paper.get("score") or 0.0) + weight * paper["pagerank_saturated"]
        )
    ranked.sort(key=lambda paper: paper["combined_score"], reverse=True)
    return ranked[:top_k]


def summarize(rows: list[dict]) -> dict:
    summary: dict[str, Any] = {"case_count": len(rows)}
    for metric in ("hit_in_pool", "ceiling_recall_in_pool", "ceiling_recall_by_k"):
        summary[metric] = fmean(row["ceiling"][metric] for row in rows)
    summary["by_weight"] = {
        str(weight): {
            metric: fmean(row["by_weight"][str(weight)][metric] for row in rows)
            for metric in ("hit_at_k", "recall_at_k", "reciprocal_rank")
        }
        for weight in WEIGHTS
    }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="재정렬 여지와 PageRank 가중치를 측정합니다.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    import chatbot

    raw = json.loads(args.cases.read_text(encoding="utf-8-sig"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    scale = pagerank_scale(chatbot)

    rows = []
    for case in cases:
        pool = search_pool(chatbot, case["search_query"])
        pool_ids = [paper["arxiv_id"] for paper in pool]
        rows.append({
            "id": case["id"],
            "category": case["category"],
            "gold_size": len(case["expected_ids"]),
            "ceiling": score_ceiling(case["expected_ids"], pool_ids, k=K),
            "by_weight": {
                str(weight): score_retrieval(
                    case["expected_ids"],
                    [p["arxiv_id"] for p in rerank(chatbot, pool, weight, scale)],
                    k=K,
                )
                for weight in WEIGHTS
            },
        })

    report = {
        "dataset": args.cases.name,
        "pool_size": POOL,
        "k": K,
        "weights": list(WEIGHTS),
        "pagerank_scale": scale,
        "note": (
            "같은 후보에 가중치만 바꿔 비교했습니다. weight=0이 벡터 단독 기준선입니다. "
            "ceiling_recall_in_pool은 후보 안에서 완벽히 재정렬했을 때의 상한이고, "
            "ceiling_recall_by_k는 k가 강제하는 상한입니다. 둘이 같으면 순서가 아니라 k가 "
            "상한을 정하고 있다는 뜻입니다."
        ),
        "overall": summarize(rows),
        "by_category": {
            category: summarize([r for r in rows if r["category"] == category])
            for category in sorted({r["category"] for r in rows})
        },
        "results": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"overall": report["overall"], "by_category": report["by_category"]},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
