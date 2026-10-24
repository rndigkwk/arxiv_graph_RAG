"""증분 수집 파이프라인입니다. 워터마크 → 수집 → 보강 → 용량·임베딩 검사 → 멱등 적재.

    uv run python -m pipeline.ingest
    uv run python -m pipeline.ingest --days 7
    uv run python -m pipeline.ingest --days 7 --load

기본값이 `--dry-run`입니다. `--load`를 명시하지 않으면 Aura에 아무것도 쓰지 않습니다. 이 그래프는
관계 한도의 84.6%를 이미 쓰고 있어서, 무심코 적재하면 되돌리기 어렵습니다.

## 다섯 가지 운영 장치

**워터마크.** 지난 실행에서 본 가장 늦은 OAI 날짜도장을 저장하고, 다음 실행은 그 다음날부터
받습니다. 전체 재수집을 하지 않으므로 실행 시간이 데이터 총량이 아니라 증가분에 비례합니다.

**부분 실패 격리.** 인용 보강은 논문마다 따로 실패합니다. 한 편이 실패해도 배치를 버리지 않고
사유와 함께 별도 목록(dead letter)에 남기고 계속 갑니다. 실패율이 임계값을 넘을 때만 실행을
실패로 처리합니다.

**용량 검사.** 적재 전에 늘어날 노드·관계 수를 세어 Aura 무료 한도와 비교합니다. 한도를 넘으면
적재하지 않고 멈춥니다. 이 검사는 게이트보다 먼저 돕니다. 데이터가 깨끗해도 들어갈 자리가
없으면 적재해서는 안 됩니다.

**임베딩 검사.** OAI 수집은 임베딩을 만들지 않습니다. 임베딩 없는 논문을 적재하면 그래프
질의에는 나오지만 **벡터 검색에 영원히 안 나옵니다** — 인덱스가 `embedding` 속성에 걸려
있기 때문입니다. 앱은 돌고 질문 일부만 그 논문을 못 찾으므로 **조용한 손상**입니다. 스냅샷
게이트가 커버리지 100%를 요구하지만 그것은 **이미 적재된 뒤에** 걸리므로, 같은 검사를
적재 전에 한 번 더 둡니다. 알고 넘기려면 `--allow-missing-embeddings`가 필요합니다.

**멱등 적재.** 모두 `MERGE`입니다. 같은 배치를 두 번 적재해도 노드·관계 수가 변하지 않아야
합니다. `--verify-idempotent`가 그것을 실제로 확인합니다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pipeline import embed, oai
from pipeline.console import use_utf8_stdout

# 스냅샷 게이트와 **같은 상수**를 씁니다. 여기서 다시 적으면 두 검사가 조용히 갈라지고,
# 그러면 상류에서 통과한 배치가 하류에서 막힙니다(ADR-045에서 한도를 중복으로 적었던 것과
# 같은 함정).
from pipeline.quality import EMBEDDING_DIMENSIONS, MIN_EMBEDDING_COVERAGE

STATE_PATH = Path("pipeline/state/ingest.json")
DEAD_LETTER_PATH = Path("pipeline/state/ingest_dead_letter.jsonl")
REPORT_PATH = Path("reports/ingest.json")

# 2026-10-14 실측: 노드 149,025 / 한도 200,000, 관계 338,366 / 한도 400,000.
# 한도에 딱 붙여 적재하면 다음 실행이 반드시 실패하므로 95%에서 멈춥니다.
NODE_LIMIT = 200_000
RELATIONSHIP_LIMIT = 400_000
CAPACITY_SAFETY = 0.95
MAX_ENRICHMENT_FAILURE_RATE = 0.1

MERGE_PAPER = """
UNWIND $rows AS row
MERGE (p:ArxivPaper {arxiv_id: row.arxiv_id})
SET p.title = row.title,
    p.abstract = row.abstract,
    p.primary_category = row.primary_category,
    p.categories = row.categories,
    p.pdf_url = row.pdf_url,
    p.source = row.source,
    p.oai_datestamp = row.oai_datestamp,
    // **`coalesce`입니다.** 레코드가 임베딩을 가져왔을 때만 씁니다 - 없으면 노드가 갖고
    // 있던 것을 그대로 남깁니다. 2026-10-24에 이 줄이 없어서 `--embed`가 만든 벡터 934개가
    // 조용히 버려졌고, 811편이 벡터 검색에 안 보이는 상태로 적재됐습니다.
    p.embedding            = coalesce(row.embedding, p.embedding),
    p.embedding_model      = coalesce(row.embedding_model, p.embedding_model),
    p.embedding_dimensions = coalesce(row.embedding_dimensions, p.embedding_dimensions),
    p.embedding_input      = coalesce(row.embedding_input, p.embedding_input)
"""

MERGE_AUTHORED = """
UNWIND $rows AS row
MATCH (p:ArxivPaper {arxiv_id: row.paper})
MERGE (a:ArxivAuthorName {name: row.author})
MERGE (a)-[:AUTHORED]->(p)
"""

MERGE_REFERENCES = """
UNWIND $rows AS row
MATCH (citing:ArxivPaper {arxiv_id: row.citing})
MATCH (cited:ArxivPaper {arxiv_id: row.cited})
MERGE (citing)-[:REFERENCES]->(cited)
"""

COUNT_CYPHER = """
MATCH (n) WITH count(n) AS nodes
MATCH ()-[r]->() RETURN nodes, count(r) AS relationships
"""


def read_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"last_datestamp": "", "runs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def harvest_window(state: dict[str, Any], *, days: int, today: date | None = None
                   ) -> tuple[str, str]:
    """Pick the next incremental window: the day after the watermark through today.

    Without a watermark the window falls back to the last `days` days rather than the whole
    archive, so a first run cannot accidentally ask arXiv for twenty years of records.
    """
    end = today or datetime.now(UTC).date()
    watermark = str(state.get("last_datestamp") or "")
    if watermark:
        try:
            start = date.fromisoformat(watermark) + timedelta(days=1)
        except ValueError:
            start = end - timedelta(days=days)
    else:
        start = end - timedelta(days=days)
    if start > end:
        start = end
    return start.isoformat(), end.isoformat()


@dataclass
class Enrichment:
    """Citation enrichment results, with per-paper failures kept rather than raised."""

    references: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    attempted: int = 0

    @property
    def failure_rate(self) -> float:
        return len(self.failures) / self.attempted if self.attempted else 0.0


def enrich_references(
    papers: Iterable[dict[str, Any]],
    lookup: Callable[[str], list[str]],
) -> Enrichment:
    """Fetch each paper's references one at a time so one failure cannot lose the batch.

    `lookup` raising for a paper is normal operation, not an outage: a paper may be absent
    upstream, or one request may time out. The paper is recorded and retried on a later run.
    """
    enrichment = Enrichment()
    for paper in papers:
        arxiv_id = str(paper.get("arxiv_id") or "")
        if not arxiv_id:
            continue
        enrichment.attempted += 1
        try:
            cited_ids = lookup(arxiv_id)
        except Exception as exc:  # noqa: BLE001 - 개별 논문의 실패는 배치를 멈추지 않습니다
            enrichment.failures.append({
                "arxiv_id": arxiv_id,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}",
            })
            continue
        enrichment.references.extend(
            {"citing": arxiv_id, "cited": cited}
            for cited in dict.fromkeys(cited_ids)
            if cited and cited != arxiv_id
        )
    return enrichment


def authored_rows(papers: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"paper": str(paper["arxiv_id"]), "author": name}
        for paper in papers
        for name in dict.fromkeys(paper.get("authors") or ())
        if name
    ]


@dataclass
class CapacityCheck:
    """Whether this batch fits inside the free-tier limits before anything is written."""

    current_nodes: int
    current_relationships: int
    new_papers: int
    new_authors: int
    new_authored: int
    new_references: int

    @property
    def projected_nodes(self) -> int:
        return self.current_nodes + self.new_papers + self.new_authors

    @property
    def projected_relationships(self) -> int:
        return self.current_relationships + self.new_authored + self.new_references

    @property
    def node_usage(self) -> float:
        return self.projected_nodes / NODE_LIMIT

    @property
    def relationship_usage(self) -> float:
        return self.projected_relationships / RELATIONSHIP_LIMIT

    @property
    def fits(self) -> bool:
        return (
            self.node_usage <= CAPACITY_SAFETY
            and self.relationship_usage <= CAPACITY_SAFETY
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "current_nodes": self.current_nodes,
            "current_relationships": self.current_relationships,
            "projected_nodes": self.projected_nodes,
            "projected_relationships": self.projected_relationships,
            "node_limit": NODE_LIMIT,
            "relationship_limit": RELATIONSHIP_LIMIT,
            "node_usage": round(self.node_usage, 4),
            "relationship_usage": round(self.relationship_usage, 4),
            "safety_ceiling": CAPACITY_SAFETY,
            "fits": self.fits,
            "binding_constraint": (
                "relationships"
                if self.relationship_usage >= self.node_usage
                else "nodes"
            ),
        }


def plan_capacity(
    papers: list[dict[str, Any]],
    references: list[dict[str, str]],
    authored: list[dict[str, str]],
    *,
    counts: dict[str, int],
    known_papers: set[str],
    known_authors: set[str],
) -> CapacityCheck:
    """Count only what is genuinely new; MERGE on an existing node adds nothing."""
    incoming_papers = {str(paper["arxiv_id"]) for paper in papers}
    incoming_authors = {row["author"] for row in authored}
    return CapacityCheck(
        current_nodes=counts["nodes"],
        current_relationships=counts["relationships"],
        new_papers=len(incoming_papers - known_papers),
        new_authors=len(incoming_authors - known_authors),
        new_authored=len(authored),
        new_references=len(references),
    )


@dataclass
class EmbeddingCheck:
    """새 논문이 **벡터 인덱스에 보이게 되는지**를 적재 전에 셉니다.

    `MERGE_PAPER`는 `embedding`을 쓰지 않습니다. 그래서 임베딩 없는 논문을 적재하면
    그래프 질의(저자·인용·공동저자)에는 나오지만 **벡터 검색에는 영원히 안 나옵니다** —
    인덱스가 그 속성에 걸려 있기 때문입니다. 관련 논문 패널의 3단계 임베딩 보충
    (ADR-040)에서도 빠집니다.

    **조용한 종류의 손상입니다.** 앱은 돌고, 질문 중 일부만 그 논문을 못 찾습니다.
    스냅샷 게이트가 커버리지 100%를 요구하지만([`quality.py`](quality.py)) 그것은
    **이미 적재된 뒤에** 걸립니다. 그래서 같은 검사를 상류에 한 번 더 둡니다 —
    용량 검사가 "들어갈 자리가 없으면 적재하지 않는다"인 것과 같은 선입니다.
    """

    new_papers: int
    with_embedding: int
    wrong_dimensions: int

    @property
    def missing(self) -> int:
        return max(0, self.new_papers - self.with_embedding)

    @property
    def coverage(self) -> float:
        return self.with_embedding / self.new_papers if self.new_papers else 1.0

    @property
    def ready(self) -> bool:
        """임베딩 없는 **새** 논문이 하나도 없어야 합니다. 기존 논문은 이미 갖고 있습니다."""
        return self.missing == 0 and self.wrong_dimensions == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "new_papers": self.new_papers,
            "with_embedding": self.with_embedding,
            "missing": self.missing,
            "wrong_dimensions": self.wrong_dimensions,
            "coverage": round(self.coverage, 6),
            "required_coverage": MIN_EMBEDDING_COVERAGE,
            "dimensions": EMBEDDING_DIMENSIONS,
            "ready": self.ready,
            "note": (
                "OAI 수집은 임베딩을 만들지 않습니다. 임베딩 없이 적재하면 그 논문은 "
                "그래프 질의에는 나오지만 **벡터 검색에 영원히 안 나옵니다.**"
            ),
        }


def plan_embeddings(
    papers: list[dict[str, Any]], *, known_papers: set[str]
) -> EmbeddingCheck:
    """Count the new papers that carry a usable embedding.

    이미 그래프에 있는 논문은 세지 않습니다 — `MERGE_PAPER`의 `SET`이 `embedding`을
    건드리지 않으므로 다시 적재해도 갖고 있던 것이 남습니다.
    """
    new = [paper for paper in papers if str(paper["arxiv_id"]) not in known_papers]
    usable, wrong = 0, 0
    for paper in new:
        embedding = paper.get("embedding")
        if not isinstance(embedding, (list, tuple)) or not embedding:
            continue
        if len(embedding) == EMBEDDING_DIMENSIONS:
            usable += 1
        else:
            wrong += 1
    return EmbeddingCheck(
        new_papers=len(new), with_embedding=usable, wrong_dimensions=wrong
    )


def write_dead_letters(failures: list[dict[str, str]], path: Path = DEAD_LETTER_PATH) -> int:
    """Append failed papers so a later run can retry exactly those."""
    if not failures:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        for failure in failures:
            handle.write(json.dumps({**failure, "recorded_at": stamp}, ensure_ascii=False) + "\n")
    return len(failures)


# 적재한 논문이 **그래프에서** 임베딩을 갖고 있는지 되읽습니다. 레코드를 본 가드로는
# 적재기가 그 값을 버렸는지 알 수 없습니다(2026-10-24에 그렇게 버렸습니다).
LOADED_EMBEDDING_CYPHER = """
UNWIND $ids AS id
MATCH (p:ArxivPaper {arxiv_id: id})
RETURN count(p) AS papers,
       count(CASE WHEN p.embedding IS NOT NULL
                   AND size(p.embedding) = $dimensions THEN 1 END) AS with_embedding
"""


def verify_loaded_embeddings(
    read: Callable[[str, dict[str, Any]], list[dict[str, Any]]],
    papers: list[dict[str, Any]],
) -> dict[str, Any]:
    """**쓴 것을 되읽습니다.** 상류 가드는 레코드를 보므로 이것을 대신하지 못합니다."""
    ids = [str(paper["arxiv_id"]) for paper in papers]
    if not ids:
        return {"papers": 0, "with_embedding": 0, "coverage": 1.0, "complete": True}
    row = read(
        LOADED_EMBEDDING_CYPHER, {"ids": ids, "dimensions": EMBEDDING_DIMENSIONS}
    )[0]
    found, usable = int(row["papers"]), int(row["with_embedding"])
    coverage = usable / found if found else 0.0
    return {
        "papers": found,
        "with_embedding": usable,
        "coverage": round(coverage, 4),
        "complete": bool(found and usable == found),
    }


def load_batch(
    run_write: Callable[[str, dict[str, Any]], dict[str, int]],
    papers: list[dict[str, Any]],
    authored: list[dict[str, str]],
    references: list[dict[str, str]],
) -> dict[str, Any]:
    """Write the batch with MERGE only, papers first so the relationship MATCHes resolve.

    Reports the database's own counters rather than the row counts sent. A second run of the
    same batch sends the same rows and must create nothing, and only the counters show that.
    """
    stages = {
        "papers": run_write(MERGE_PAPER, {"rows": papers}),
        "authored": run_write(MERGE_AUTHORED, {"rows": authored}),
        "references": run_write(MERGE_REFERENCES, {"rows": references}),
    }
    return {
        "rows_sent": {
            "papers": len(papers),
            "authored": len(authored),
            "references": len(references),
        },
        "counters": stages,
        "nodes_created": sum(stage["nodes_created"] for stage in stages.values()),
        "relationships_created": sum(
            stage["relationships_created"] for stage in stages.values()
        ),
    }


def main() -> int:
    # **`parse_args` 전에 부릅니다.** `--help`의 출력도 이 모듈의 출력이고, cp949 콘솔에서는
    # 긴 줄표 하나에 UnicodeEncodeError로 죽습니다(2026-10-24에 그렇게 죽었습니다).
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="증분 수집 파이프라인을 실행합니다.")
    parser.add_argument("--days", type=int, default=7,
                        help="워터마크가 없을 때 되돌아볼 일수입니다.")
    parser.add_argument("--categories", default="cs.AI",
                        help="쉼표로 구분한 대표 분류. 비우면 cs 집합 전체입니다.")
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--load", action="store_true",
                        help="실제로 Aura에 적재합니다. 기본은 적재하지 않습니다.")
    parser.add_argument("--verify-idempotent", action="store_true",
                        help="같은 배치를 두 번 적재해 노드·관계 수가 그대로인지 확인합니다.")
    parser.add_argument(
        "--allow-missing-embeddings", action="store_true",
        help=("임베딩 없는 논문도 적재합니다. 그 논문은 벡터 검색에 나오지 않습니다 — "
              "적재 직후 임베딩을 채울 때만 쓰세요."),
    )
    parser.add_argument(
        "--embed", action="store_true",
        help=("임베딩이 없는 새 논문의 임베딩을 만듭니다(유료). --load와 함께 써야 "
              "합니다 - 임베딩은 메모리 레코드에 붙으므로 적재하지 않으면 버려집니다."),
    )
    parser.add_argument(
        "--max-cost", type=float, default=embed.MAX_COST_USD,
        help="임베딩 추정 비용의 천장(달러). 넘으면 호출하지 않습니다.",
    )
    parser.add_argument("--output", type=Path, default=REPORT_PATH)
    args = parser.parse_args()
    # **임베딩만 하고 적재하지 않으면 돈을 쓰고 버립니다.** 벡터는 메모리 레코드에만
    # 붙고 이 모듈은 그래프에 따로 쓰지 않습니다.
    if args.embed and not args.load:
        parser.error(
            "--embed는 --load와 함께 써야 합니다. 임베딩은 수집 레코드에 붙으므로 "
            "적재하지 않으면 그대로 버려집니다. 벡터 공간만 확인하려면 "
            "`python -m pipeline.embed --verify`를 쓰세요."
        )

    state = read_state()
    start, end = harvest_window(state, days=args.days)
    categories = (
        frozenset(code.strip() for code in args.categories.split(",") if code.strip())
        or None
    )
    harvested = oai.harvest(
        start, end, max_pages=args.max_pages, categories=categories
    )

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {"from": start, "until": end},
        "harvest": {
            "records": len(harvested.records),
            "pages": harvested.pages,
            "deleted": harvested.deleted,
            "skipped": harvested.skipped,
            "last_datestamp": harvested.last_datestamp,
            "truncated_by_page_limit": harvested.truncated_by_page_limit,
        },
        "loaded": False,
    }

    if not harvested.records:
        report["stopped_at"] = "harvest_empty"
        _finish(report, args.output, state, harvested.last_datestamp, advance=True)
        return 0

    from pipeline.graph import connect

    with connect() as graph:
        def lookup(arxiv_id: str) -> list[str]:
            rows = graph.read(
                "MATCH (p:ArxivPaper {arxiv_id: $id})-[:REFERENCES]->(q:ArxivPaper) "
                "RETURN q.arxiv_id AS arxiv_id",
                {"id": arxiv_id},
            )
            return [row["arxiv_id"] for row in rows]

        enrichment = enrich_references(harvested.records, lookup)
        authored = authored_rows(harvested.records)
        counts = graph.read(COUNT_CYPHER)[0]
        harvested_ids = [str(paper["arxiv_id"]) for paper in harvested.records]
        known = graph.read(
            "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids RETURN p.arxiv_id AS arxiv_id",
            {"ids": harvested_ids},
        )
        # **이미 벡터를 가진 논문에 다시 돈을 쓰지 않습니다.** 2026-10-24의 첫 실행은
        # 수집한 934편 중 이미 그래프에 벡터가 있던 123편(13%)까지 임베딩했습니다.
        already_embedded = {
            row["arxiv_id"]
            for row in graph.read(
                "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids "
                "AND p.embedding IS NOT NULL AND size(p.embedding) = $dimensions "
                "RETURN p.arxiv_id AS arxiv_id",
                {"ids": harvested_ids, "dimensions": EMBEDDING_DIMENSIONS},
            )
        }
        known_authors = graph.read(
            "MATCH (a:ArxivAuthorName) WHERE a.name IN $names RETURN a.name AS name",
            {"names": sorted({row["author"] for row in authored})},
        )
        capacity = plan_capacity(
            harvested.records,
            enrichment.references,
            authored,
            counts={"nodes": counts["nodes"], "relationships": counts["relationships"]},
            known_papers={row["arxiv_id"] for row in known},
            known_authors={row["name"] for row in known_authors},
        )
        embeddings = plan_embeddings(
            harvested.records, known_papers={row["arxiv_id"] for row in known}
        )
        report["enrichment"] = {
            "attempted": enrichment.attempted,
            "references": len(enrichment.references),
            "failures": len(enrichment.failures),
            "failure_rate": round(enrichment.failure_rate, 4),
            "dead_letters_written": write_dead_letters(enrichment.failures),
        }
        report["capacity"] = capacity.as_dict()
        report["embeddings"] = embeddings.as_dict()

        if enrichment.failure_rate > MAX_ENRICHMENT_FAILURE_RATE:
            report["stopped_at"] = "enrichment_failure_rate"
            _finish(report, args.output, state, harvested.last_datestamp, advance=False)
            return 1
        if not capacity.fits:
            report["stopped_at"] = "capacity"
            _finish(report, args.output, state, harvested.last_datestamp, advance=False)
            return 1
        # **용량 검사 뒤에 둡니다.** 들어갈 자리가 없는 배치에 임베딩 비용을 쓰지 않습니다.
        if args.embed:
            report["embedding_run"] = embed.attach_embeddings(
                harvested.records,
                embed.client(),
                max_cost_usd=args.max_cost,
                already_embedded=already_embedded,
            )
            # 임베딩을 붙인 뒤 다시 셉니다. 이 수가 아래 가드가 보는 수입니다.
            embeddings = plan_embeddings(
                harvested.records, known_papers={row["arxiv_id"] for row in known}
            )
            report["embeddings"] = embeddings.as_dict()
            if report["embedding_run"]["stopped_at"] == "cost_ceiling":
                report["stopped_at"] = "embedding_cost_ceiling"
                print(
                    f"추정 비용 ${report['embedding_run']['estimated_cost_usd']}가 천장 "
                    f"${args.max_cost}를 넘습니다. 호출하지 않았습니다 — "
                    "올리려면 --max-cost 로 주세요."
                )
                _finish(report, args.output, state, harvested.last_datestamp, advance=False)
                return 1
        # 임베딩 없는 논문을 적재하면 **벡터 검색에서 영원히 빠집니다.** 앱은 돌고 질문
        # 일부만 그 논문을 못 찾으므로 조용한 손상입니다. 알고 하는 경우만 통과시킵니다.
        if args.load and not embeddings.ready and not args.allow_missing_embeddings:
            report["stopped_at"] = "embeddings_missing"
            print(
                f"새 논문 {embeddings.new_papers}편 중 {embeddings.missing}편에 임베딩이 "
                f"없습니다(차원 불일치 {embeddings.wrong_dimensions}편). 적재하지 않았습니다 "
                "— 그 논문은 그래프 질의에는 나오지만 벡터 검색에 영원히 안 나옵니다.\n"
                "임베딩을 먼저 만드세요(--embed). 적재한 뒤 바로 "
                "임베딩을 채울 계획이라면 --allow-missing-embeddings 로 넘기세요."
            )
            _finish(report, args.output, state, harvested.last_datestamp, advance=False)
            return 1
        if not args.load:
            report["stopped_at"] = "dry_run"
            _finish(report, args.output, state, harvested.last_datestamp, advance=False)
            return 0

        before = graph.read(COUNT_CYPHER)[0]
        report["written"] = load_batch(
            graph.write, harvested.records, authored, enrichment.references
        )
        after = graph.read(COUNT_CYPHER)[0]
        report["loaded"] = True
        report["counts"] = {"before": dict(before), "after": dict(after)}
        # **쓴 것을 되읽습니다.** 상류 가드는 수집 레코드를 보므로, 적재기가 그 값을
        # 버렸는지 알 수 없습니다 — 2026-10-24에 그렇게 버렸고 811편이 벡터 검색에
        # 안 보이는 상태로 들어갔습니다.
        report["loaded_embeddings"] = verify_loaded_embeddings(
            graph.read, harvested.records
        )
        if not report["loaded_embeddings"]["complete"]:
            report["stopped_at"] = "loaded_without_embeddings"
            print(
                f"적재한 {report['loaded_embeddings']['papers']}편 중 "
                f"{report['loaded_embeddings']['with_embedding']}편만 그래프에서 임베딩을 "
                f"갖고 있습니다(커버리지 "
                f"{report['loaded_embeddings']['coverage']}). 나머지는 그래프 질의에는 "
                "나오지만 벡터 검색에 안 나옵니다."
            )
        if args.verify_idempotent:
            # 같은 배치를 다시 적재합니다. MERGE가 제대로라면 수가 그대로여야 합니다.
            load_batch(graph.write, harvested.records, authored, enrichment.references)
            repeated = graph.read(COUNT_CYPHER)[0]
            report["idempotent"] = dict(repeated) == dict(after)
            report["counts"]["after_repeat"] = dict(repeated)
    _finish(report, args.output, state, harvested.last_datestamp, advance=True)
    return 0


def _finish(
    report: dict[str, Any],
    output: Path,
    state: dict[str, Any],
    last_datestamp: str,
    *,
    advance: bool,
) -> None:
    """Write the report, and move the watermark only when the run really finished."""
    from pipeline.tracking import track

    if advance and last_datestamp:
        state["last_datestamp"] = last_datestamp
    state.setdefault("runs", []).append({
        "at": report["generated_at"],
        "window": report["window"],
        "records": report["harvest"]["records"],
        "stopped_at": report.get("stopped_at", "completed"),
        "loaded": report["loaded"],
        "watermark_advanced": bool(advance and last_datestamp),
    })
    state["runs"] = state["runs"][-50:]
    write_state(state)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with track("ingest") as run:
        run.log_params({
            "window_from": report["window"]["from"],
            "window_until": report["window"]["until"],
            "loaded": report["loaded"],
            "stopped_at": report.get("stopped_at", "completed"),
        })
        run.log_metrics({
            "harvest_records": report["harvest"]["records"],
            "harvest_pages": report["harvest"]["pages"],
            **{
                f"enrichment_{key}": value
                for key, value in report.get("enrichment", {}).items()
                if isinstance(value, (int, float))
            },
            **{
                f"capacity_{key}": value
                for key, value in report.get("capacity", {}).items()
                if isinstance(value, (int, float, bool))
            },
        })
        run.log_artifact(output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(main())
