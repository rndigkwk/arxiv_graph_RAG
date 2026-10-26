"""새 논문의 참고문헌을 S2에서 받으면 그래프가 얼마나 늘어나는지 잽니다 - 쓰지 않습니다.

    uv run python scripts/measure_reference_growth.py

대상은 OAI로 들어온 논문(`source = 'oai'`)입니다. 2026-10-24에 적재한 934편이고, 주간 수집 한
번의 크기(cs.AI 1차 분류, 주 약 1,000편)와 같습니다. 그래프는 읽기만 하고, S2는 `/paper/batch`를
몇 번 부릅니다(0원).

나누는 것:

- **이미 그래프에 있는 인용** — 노드를 만들지 않고 `REFERENCES` 간선만 늘립니다.
- **그래프 밖의 인용** — 이전 계획 2단계에서 노드로 들일 논문입니다. 메타데이터는 arXiv API에서,
  임베딩은 OpenAI에서 와야 합니다(유료). 저자 간선은 아직 모르므로 그래프의 평균(편당 저자 수)으로
  추정합니다 - **잰 값이 아닙니다.**
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import s2  # noqa: E402
from pipeline.graph import connect  # noqa: E402

OUTPUT = Path("reports/reference_growth.json")


def summarize(targets: list[str], batch: s2.ReferenceBatch, in_graph: set[str],
              authors_per_paper: float) -> dict:
    cited = batch.cited
    counts = Counter(c for ids in batch.references.values() for c in ids)
    outside = sorted(cited - in_graph)
    edges_inside = sum(1 for ids in batch.references.values() for c in ids if c in in_graph)
    edges_all = sum(len(ids) for ids in batch.references.values())
    per_paper = [len(ids) for ids in batch.references.values()]
    return {
        "targets": len(targets),
        "requests": batch.requests,
        "found_with_references": len(batch.references),
        "found_empty": len(batch.empty),
        "not_found": len(batch.not_found),
        "failed": len(batch.failures),
        "arxiv_references_per_paper_p50": statistics.median(per_paper) if per_paper else 0,
        "non_arxiv_references_dropped": batch.non_arxiv,
        "edges": {"to_papers_in_graph": edges_inside,
                  "to_papers_outside": edges_all - edges_inside},
        "distinct_cited": len(cited),
        "distinct_cited_in_graph": len(cited & in_graph),
        "distinct_cited_outside": len(outside),
        # 그래프 밖 논문을 "새 논문 몇 편이 인용했는가"로 나눕니다. 들일 범위를 정하는 근거입니다.
        "outside_by_citing_count": {f">={k}": sum(1 for c in outside if counts[c] >= k)
                                    for k in (1, 2, 3, 5)},
        # 추정입니다: 그래프 밖 논문의 저자 간선을 그래프의 편당 평균으로 셉니다.
        "estimated_authored_for_outside": round(len(outside) * authors_per_paper),
        "authors_per_paper_in_graph": round(authors_per_paper, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="S2 참고문헌으로 늘어날 그래프 크기를 잽니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    with connect() as graph:
        targets = [row["id"] for row in graph.read(
            "MATCH (p:ArxivPaper {source: 'oai'}) RETURN p.arxiv_id AS id ORDER BY id")]
        batch = s2.fetch_references(targets)
        cited = sorted(batch.cited)
        in_graph = {row["id"] for row in graph.read(
            "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids RETURN p.arxiv_id AS id",
            {"ids": cited})}
        ratio = graph.read("RETURN toFloat(COUNT { ()-[:AUTHORED]->() }) / "
                           "COUNT { (:ArxivPaper) } AS r")[0]["r"]
    report = summarize(targets, batch, in_graph, ratio)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
