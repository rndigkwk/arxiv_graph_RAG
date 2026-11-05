"""발표일이 빠진 수집 논문에 arXiv 첫 버전 날짜를 채웁니다 - 한 번만 쓰는 스크립트입니다(ADR-104).

2026-10-24 적재는 OAI 레코드의 `created`를 `published`로 옮기지 않아, 934편 중 811편이 발표일
없이 들어갔습니다. 그 논문들은 "최근" 검색(ADR-090)과 앱의 날짜 확인(ADR-103)에서 빠집니다.
지금의 `pipeline.ingest`는 적재할 때 채우지만, 이미 들어간 논문은 다시 수집되지 않으면 그대로입니다.

    uv run python scripts/backfill_published.py           # 몇 편인지, 무엇을 쓸지만 봅니다
    uv run python scripts/backfill_published.py --load    # 씁니다

**`published` 하나만 씁니다.** 이미 값이 있으면 덮지 않습니다(`coalesce`). 다른 속성·간선은
건드리지 않습니다. arXiv 조회 API로 100편씩 받으므로 811편이면 요청 9번입니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import arxiv_api  # noqa: E402
from pipeline.console import use_utf8_stdout  # noqa: E402

REPORT = Path("reports/backfill_published.json")

MISSING = """
MATCH (p:ArxivPaper)
WHERE p.source = 'oai' AND p.published IS NULL
RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id
"""

FILL = """
UNWIND $rows AS row
MATCH (p:ArxivPaper {arxiv_id: row.arxiv_id})
SET p.published = coalesce(p.published, datetime(row.created))
"""


def rows(records: list[dict]) -> list[dict[str, str]]:
    """쓸 줄입니다. 첫 버전 날짜가 없는 레코드는 뺍니다 - 추측해서 채우지 않습니다."""
    return [{"arxiv_id": str(record["arxiv_id"]), "created": str(record["created"])}
            for record in records if record.get("created")]


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--load", action="store_true",
                        help="그래프에 씁니다. 기본은 세기만 합니다.")
    parser.add_argument("--limit", type=int, default=None, help="앞에서 이만큼만 받습니다(시험용).")
    # VM에서는 git이 무시하는 `pipeline/state/` 아래로 줍니다. 추적되는 파일을 VM에서 고치면
    # 배포(deploy.sh)의 fast-forward가 막힙니다.
    parser.add_argument("--output", type=Path, default=REPORT)
    args = parser.parse_args()

    from pipeline.graph import connect

    with connect() as graph:
        ids = [row["arxiv_id"] for row in graph.read(MISSING)]
        todo = ids[:args.limit] if args.limit else ids
        print(f"발표일이 없는 수집 논문 {len(ids)}편, 이번에 받을 것 {len(todo)}편")
        lookup = arxiv_api.get_records(todo)
        fill = rows(lookup.records)
        report = {
            "generated_at": datetime.now(UTC).isoformat(),
            "missing_before": len(ids),
            "asked": len(todo),
            "received": len(lookup.records),
            "with_created": len(fill),
            "not_in_arxiv": len(lookup.missing),
            "failed": len(lookup.failures),
            "loaded": False,
        }
        if args.load and fill:
            counters = graph.write(FILL, {"rows": fill})
            report["loaded"] = True
            report["properties_set"] = counters.get("properties_set")
            report["missing_after"] = len(graph.read(MISSING))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.write_text(text, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
