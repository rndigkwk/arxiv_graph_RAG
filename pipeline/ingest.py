"""증분 수집 파이프라인입니다. 워터마크 → 수집 → 보강 → 용량·임베딩 검사 → 멱등 적재.

    uv run python -m pipeline.ingest
    uv run python -m pipeline.ingest --days 7
    uv run python -m pipeline.ingest --days 7 --load
    uv run python -m pipeline.ingest --rewind-to 2026-10-20 --reason "보강이 빈 창을 다시 받습니다"

기본값이 `--dry-run`입니다. `--load`를 명시하지 않으면 Aura에 아무것도 쓰지 않습니다. 이 그래프는
관계 한도의 84.6%를 이미 쓰고 있어서, 무심코 적재하면 되돌리기 어렵습니다.

## 다섯 가지 운영 장치

**워터마크.** 지난 실행에서 본 가장 늦은 OAI 날짜도장을 저장하고, 다음 실행은 그 다음날부터
받습니다. 전체 재수집을 하지 않으므로 실행 시간이 데이터 총량이 아니라 증가분에 비례합니다.

**인용 보강.** 참고문헌은 Semantic Scholar에서 받고, 그래프 밖 논문은 새 논문이 누적 3편
인용하면 노드로 들입니다(ADR-099, ADR-104, `pipeline/references.py`).

**부분 실패 격리.** 인용 보강은 묶음마다 따로 실패합니다. 한 편이 실패해도 배치를 버리지 않고
사유와 함께 별도 목록(dead letter)에 남기고 계속 갑니다. 실패율이 임계값을 넘을 때만 실행을
실패로 처리합니다.

**용량 검사.** 적재 전에 늘어날 노드·관계 수를 세어 Aura 무료 한도와 비교합니다. 한도를 넘으면
적재하지 않고 멈춥니다. VM의 Neo4j에 쓸 때는 한도가 없으므로 세기만 합니다(ADR-104).
이 검사는 게이트보다 먼저 돕니다. 데이터가 깨끗해도 들어갈 자리가
없으면 적재해서는 안 됩니다.

**임베딩 검사.** OAI 수집은 임베딩을 만들지 않습니다. 임베딩 없는 논문을 적재하면 그래프
질의에는 나오지만 **벡터 검색에 영원히 안 나옵니다** — 인덱스가 `embedding` 속성에 걸려
있기 때문입니다. 앱은 돌고 질문 일부만 그 논문을 못 찾으므로 **조용한 손상**입니다. 스냅샷
게이트가 커버리지 100%를 요구하지만 그것은 **이미 적재된 뒤에** 걸리므로, 같은 검사를
적재 전에 한 번 더 둡니다. 알고 넘기려면 `--allow-missing-embeddings`가 필요합니다.

**멱등 적재.** 모두 `MERGE`입니다. 같은 배치를 두 번 적재해도 노드·관계 수가 변하지 않아야
합니다. `--verify-idempotent`가 그것을 실제로 확인합니다.

## 되감기 (ADR-074)

`--rewind-to`는 워터마크만 뒤로 옮기고 끝납니다. 다음 평상시 실행이 그 날짜부터 다시 받습니다.
**되돌리기가 아닙니다** — 적재가 모두 `MERGE`라 빠진 간선을 채우고 속성을 덮을 뿐, 잘못 들어간
논문·간선을 지우지 못합니다. 상태 파일을 손으로 고치지 않는 이유는 `runs`에 사유가 남지 않기
때문입니다.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pipeline import arxiv_api, embed, oai, s2
from pipeline import references as outside
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
# 한 실행에 들이는 밖 논문의 상한입니다. arXiv 조회 API로 100편씩 받으므로 요청 2번입니다.
# ADR-099의 측정은 주 8편이었습니다. 넘는 논문은 대기열에 남아 다음 주에 들어갑니다.
MAX_OUTSIDE_PER_RUN = 200
# 그래프에 있는지 한 번에 물어볼 ID 수입니다. 대기열은 주마다 3,000편 남짓씩 늘어납니다.
LOOKUP_CHUNK = 20_000


def targets_aura(uri: str | None) -> bool:
    """Aura Free에 쓰는지. 노드·관계 한도는 Aura Free의 것이라 VM의 Neo4j에는 없습니다(ADR-104)."""
    return "databases.neo4j.io" in (uri or "")


@dataclass
class ReferencePlan:
    """이번 실행이 쓸 참고문헌 간선과 밖 논문, 그리고 적재가 끝나면 저장할 대기열."""

    references: list[dict[str, str]]
    outside_records: list[dict[str, Any]]
    state: dict[str, Any]
    enrichment: Enrichment
    report: dict[str, Any]


def first_version_dates(
    records: list[dict[str, Any]], *, get_records: Callable[[list[str]], Any]
) -> dict[str, int]:
    """수집 레코드의 `created`를 arXiv 첫 버전 날짜로 바꿉니다(ADR-104, 11-06 추가).

    **OAI의 `created`는 첫 버전 날짜가 아닙니다.** 새 끝점(oaipmh.arxiv.org)은 마지막 버전의 날짜를
    줍니다 - 2602.12164는 v1이 02-12인데 OAI `created`는 v2 날짜인 10-22였습니다. 11-06 첫 적재의
    6,461편 중 992편이 이 때문에 ID의 달보다 늦은 발표일로 들어가, "최근" 질문에 몇 달 전 논문이
    최근 논문처럼 잡혔습니다. 조회 API의 `published`는 첫 버전 시각이므로 그것을 씁니다.

    못 받은 논문은 `created`를 비웁니다 - OAI 값으로 되돌아가지 않습니다(개정된 논문이면 틀립니다).
    발표일이 빈 채로 실리고, `scripts/backfill_published.py`가 다음에 채웁니다.
    """
    lookup = get_records([str(record["arxiv_id"]) for record in records])
    first = {str(row["arxiv_id"]): row.get("created") for row in lookup.records}
    changed = 0
    for record in records:
        value = first.get(str(record["arxiv_id"])) or None
        if value and record.get("created") and str(value)[:10] != str(record["created"])[:10]:
            changed += 1
        record["created"] = value
    return {
        "asked": len(records),
        "received": len(lookup.records),
        "differs_from_oai": changed,
        "missing": len(lookup.missing),
        "failed": len(lookup.failures),
    }


def plan_references(
    harvested_ids: list[str],
    state: dict[str, Any],
    *,
    fetch_references: Callable[[list[str]], s2.ReferenceBatch],
    in_graph: Callable[[set[str]], set[str]],
    get_records: Callable[[list[str]], arxiv_api.RecordLookup],
) -> ReferencePlan:
    """S2 참고문헌 → 그래프 안 간선, 밖 논문 대기열, 다시 물을 목록(ADR-099, ADR-104).

    **그래프에 쓰지 않습니다.** 받은 `state`도 바꾸지 않고 새 상태를 돌려줍니다 - 시험 실행이면
    버리고, 적재가 끝났을 때만 저장합니다. 적재하지 않은 논문의 인용을 대기열에 세면, 그 논문이
    그래프에 없는데도 밖 논문이 "3번 인용됐다"며 들어옵니다.
    """
    harvested = list(dict.fromkeys(harvested_ids))
    retry_ids = [arxiv_id for arxiv_id in state.get("retry", {}) if arxiv_id not in set(harvested)]
    asked = harvested + retry_ids
    batch = fetch_references(asked)
    enrichment = Enrichment(attempted=len(asked), failures=list(batch.failures))
    waiting_before = state.get("waiting", {})
    present = in_graph(outside.candidate_ids(batch.references, waiting_before)) | set(asked)
    edges, waiting = outside.count_citations(batch.references, waiting_before, in_graph=present)
    candidates = outside.ready(waiting)
    lookup = get_records(candidates[:MAX_OUTSIDE_PER_RUN])
    outside_records = [{**record, "source": "reference"} for record in lookup.records]
    outside_edges, waiting = outside.admit(
        waiting, [str(record["arxiv_id"]) for record in outside_records])
    unresolved = batch.empty + batch.not_found + [item["arxiv_id"] for item in batch.failures]
    retry, dropped = outside.next_retry(state.get("retry", {}), asked=unresolved,
                                        resolved=batch.references)
    enrichment.references = edges + outside_edges
    report = {
        "asked": len(asked),
        "asked_again": len(retry_ids),
        "with_references": len(batch.references),
        "empty": len(batch.empty),
        "not_found": len(batch.not_found),
        "non_arxiv_dropped": batch.non_arxiv,
        "s2_requests": batch.requests,
        "edges_in_graph": len(edges),
        "outside_ready": len(candidates),
        "outside_admitted": len(outside_records),
        "outside_edges": len(outside_edges),
        "outside_missing": len(lookup.missing),
        "outside_failed": len(lookup.failures),
        "waiting_before": len(waiting_before),
        "waiting_after": len(waiting),
        "retry_queue": len(retry),
        "retry_dropped": len(dropped),
        "min_citers": outside.OUTSIDE_MIN_CITERS,
    }
    return ReferencePlan(references=enrichment.references, outside_records=outside_records,
                         state={"waiting": waiting, "retry": retry}, enrichment=enrichment,
                         report=report)

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
    // arXiv 첫 버전 날짜입니다(ADR-104). 2026-10-24 적재는 이 줄이 없어 934편 중 811편이
    // 발표일 없이 들어갔고, "최근" 검색(ADR-090)과 앱의 날짜 확인(ADR-103)에서 빠졌습니다.
    // 이미 있는 발표일은 덮지 않습니다 - 씨앗 논문의 값은 그래프를 지을 때의 기준입니다.
    // `row.created`는 OAI 값이 아니라 조회 API의 첫 버전 시각입니다(`first_version_dates`).
    p.published = coalesce(p.published,
                           CASE WHEN row.created IS NULL THEN NULL ELSE datetime(row.created) END),
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


class RewindRefused(ValueError):
    """되감기를 거부한 이유입니다. 상태는 바뀌지 않습니다."""


REWIND_CANNOT_DELETE = (
    "되감기는 지우지 못합니다. 다음 실행이 같은 창을 다시 받아 빠진 간선을 채우고 속성을 "
    "덮을 뿐, 잘못 들어간 논문·간선은 그대로 남습니다."
)


def rewind(state: dict[str, Any], to: str, reason: str, *, now: datetime | None = None
           ) -> dict[str, Any]:
    """다음 실행이 `to`부터 받도록 워터마크를 옮기고, 그 기록을 돌려줍니다.

    워터마크는 "본 가장 늦은 날짜"이고 다음 창은 그 다음날부터이므로, 새 워터마크는 `to`의
    전날입니다. 앞으로 감기는 데이터를 조용히 건너뛰므로 거부합니다. **워터마크가 비어
    있어도 거부합니다** — 빈 워터마크에 과거 날짜를 넣으면 되감기가 아니라 시작점을 새로
    정하는 일이 되고, 그러면 `--days`가 정하던 창이 말없이 바뀝니다.
    """
    reason = reason.strip()
    if not reason:
        raise RewindRefused("사유가 필요합니다. 몇 달 뒤 runs를 볼 때 남는 것은 사유뿐입니다.")
    try:
        target = date.fromisoformat(to)
    except ValueError as exc:
        raise RewindRefused(f"날짜를 읽을 수 없습니다: {to!r} (YYYY-MM-DD)") from exc
    before = str(state.get("last_datestamp") or "")
    if not before:
        raise RewindRefused("되감을 것이 없습니다. 워터마크가 비어 있어 다음 실행은 --days로 "
                            "정한 창을 받습니다.")
    try:
        current = date.fromisoformat(before)
    except ValueError as exc:
        raise RewindRefused(f"지금 워터마크를 읽을 수 없습니다: {before!r}. 손으로 고친 값인지 "
                            "먼저 확인하세요.") from exc
    if target > current:
        raise RewindRefused(
            f"앞으로 감을 수 없습니다. 지금 워터마크는 {before}이고 다음 실행은 "
            f"{(current + timedelta(days=1)).isoformat()}부터 받습니다. {to}부터 받으면 그 사이를 "
            "건너뜁니다."
        )
    after = (target - timedelta(days=1)).isoformat()
    state["last_datestamp"] = after
    entry = {
        "at": (now or datetime.now(UTC)).isoformat(),
        "stopped_at": "rewound",
        "watermark_before": before,
        "watermark_after": after,
        "next_window_from": target.isoformat(),
        "reason": reason,
        "loaded": False,
        "watermark_advanced": False,
    }
    state.setdefault("runs", []).append(entry)
    state["runs"] = state["runs"][-50:]
    return entry


def mark_rewind_in_dead_letters(entry: dict[str, Any], path: Path = DEAD_LETTER_PATH) -> None:
    """dead letter는 덧붙이기만 합니다. 되감은 뒤의 같은 실패가 앞의 것과 섞이지 않도록 경계
    줄을 하나 넣습니다 - 중복 제거를 만드는 것보다 쌉니다."""
    if not path.exists():
        return
    with path.open("a", encoding="utf-8") as handle:
        marker = {"rewound": True, **{key: entry[key] for key in
                  ("watermark_before", "watermark_after", "reason")},
                  "recorded_at": entry["at"]}
        handle.write(json.dumps(marker, ensure_ascii=False) + "\n")


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
                        help="실제로 그래프에 적재합니다. 기본은 적재하지 않습니다.")
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
    parser.add_argument(
        "--rewind-to", metavar="YYYY-MM-DD",
        help=("다음 실행이 이 날짜부터 다시 받도록 워터마크만 옮기고 끝납니다. 수집·적재는 "
              "하지 않고, 지우지도 못합니다(ADR-074)."),
    )
    parser.add_argument("--reason", help="--rewind-to의 사유입니다. 필수입니다.")
    args = parser.parse_args()
    if args.rewind_to is not None or args.reason is not None:
        return _rewind_only(parser, args)
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
    # 하루씩 받습니다. OAI의 새 끝점은 긴 창에 응답하지 않습니다(ADR-104).
    harvested = oai.harvest_by_day(
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
            "days": harvested.days,
            "stopped_on": harvested.stopped_on,
            "truncated_on": harvested.truncated_on,
            "stop_error": harvested.stop_error,
        },
        "loaded": False,
    }

    if not harvested.records:
        report["stopped_at"] = "harvest_empty"
        _finish(report, args.output, state, harvested.last_datestamp, advance=True)
        return 0

    report["first_version"] = first_version_dates(harvested.records,
                                                  get_records=arxiv_api.get_records)

    from pipeline.graph import connect, env

    aura = targets_aura(env("NEO4J_URI"))
    reference_state = outside.read_state()
    with connect() as graph:
        def in_graph(ids: set[str]) -> set[str]:
            ordered = sorted(ids)
            found: set[str] = set()
            for start_at in range(0, len(ordered), LOOKUP_CHUNK):
                rows = graph.read(
                    "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids RETURN p.arxiv_id AS arxiv_id",
                    {"ids": ordered[start_at:start_at + LOOKUP_CHUNK]},
                )
                found.update(row["arxiv_id"] for row in rows)
            return found

        # 참고문헌은 S2에서 받습니다(ADR-104). 전에는 그래프 자신을 조회해서, 새 논문에는
        # 나가는 간선이 없으니 항상 비어 있었습니다(2026-10-24 적재 934편 중 888편이 0개).
        plan = plan_references(
            [str(paper["arxiv_id"]) for paper in harvested.records], reference_state,
            fetch_references=s2.fetch_references, in_graph=in_graph,
            get_records=arxiv_api.get_records,
        )
        enrichment = plan.enrichment
        # 들이는 밖 논문도 새 논문과 같은 길로 실립니다 - 저자·임베딩·되읽기 모두.
        papers = harvested.records + plan.outside_records
        authored = authored_rows(papers)
        counts = graph.read(COUNT_CYPHER)[0]
        harvested_ids = [str(paper["arxiv_id"]) for paper in papers]
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
            papers,
            enrichment.references,
            authored,
            counts={"nodes": counts["nodes"], "relationships": counts["relationships"]},
            known_papers={row["arxiv_id"] for row in known},
            known_authors={row["name"] for row in known_authors},
        )
        embeddings = plan_embeddings(
            papers, known_papers={row["arxiv_id"] for row in known}
        )
        report["enrichment"] = {
            "attempted": enrichment.attempted,
            "references": len(enrichment.references),
            "failures": len(enrichment.failures),
            "failure_rate": round(enrichment.failure_rate, 4),
            "dead_letters_written": write_dead_letters(enrichment.failures),
            **plan.report,
        }
        report["capacity"] = capacity.as_dict()
        report["embeddings"] = embeddings.as_dict()

        if enrichment.failure_rate > MAX_ENRICHMENT_FAILURE_RATE:
            report["stopped_at"] = "enrichment_failure_rate"
            _finish(report, args.output, state, harvested.last_datestamp, advance=False)
            return 1
        # 노드·관계 한도는 Aura Free의 것입니다. VM의 Neo4j에는 없으므로 수만 남깁니다(ADR-104).
        report["capacity"]["checked"] = aura
        if aura and not capacity.fits:
            report["stopped_at"] = "capacity"
            _finish(report, args.output, state, harvested.last_datestamp, advance=False)
            return 1
        # **용량 검사 뒤에 둡니다.** 들어갈 자리가 없는 배치에 임베딩 비용을 쓰지 않습니다.
        if args.embed:
            report["embedding_run"] = embed.attach_embeddings(
                papers,
                embed.client(),
                max_cost_usd=args.max_cost,
                already_embedded=already_embedded,
            )
            # 임베딩을 붙인 뒤 다시 셉니다. 이 수가 아래 가드가 보는 수입니다.
            embeddings = plan_embeddings(
                papers, known_papers={row["arxiv_id"] for row in known}
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
            graph.write, papers, authored, enrichment.references
        )
        after = graph.read(COUNT_CYPHER)[0]
        report["loaded"] = True
        # 대기열은 **적재한 뒤에만** 저장합니다. 인용한 새 논문이 그래프에 있어야 그 인용을 셉니다.
        outside.write_state(plan.state)
        report["counts"] = {"before": dict(before), "after": dict(after)}
        # **쓴 것을 되읽습니다.** 상류 가드는 수집 레코드를 보므로, 적재기가 그 값을
        # 버렸는지 알 수 없습니다 — 2026-10-24에 그렇게 버렸고 811편이 벡터 검색에
        # 안 보이는 상태로 들어갔습니다.
        report["loaded_embeddings"] = verify_loaded_embeddings(
            graph.read, papers
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
            load_batch(graph.write, papers, authored, enrichment.references)
            repeated = graph.read(COUNT_CYPHER)[0]
            report["idempotent"] = dict(repeated) == dict(after)
            report["counts"]["after_repeat"] = dict(repeated)
    _finish(report, args.output, state, harvested.last_datestamp, advance=True)
    return 0


def _rewind_only(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    """되감고 끝납니다. 되감기와 적재를 한 명령에 묶지 않아 사고 반경이 작습니다."""
    if args.rewind_to is None:
        parser.error("--reason은 --rewind-to와 함께만 씁니다.")
    if args.reason is None:
        parser.error("--rewind-to에는 --reason이 필요합니다.")
    if args.load or args.embed or args.verify_idempotent or args.allow_missing_embeddings:
        parser.error("--rewind-to는 되감고 끝납니다. 적재는 확인한 뒤 평상시 명령으로 하세요.")
    state = read_state()
    print(f"지금 워터마크: {state.get('last_datestamp') or '(비어 있음)'}")
    try:
        entry = rewind(state, args.rewind_to, args.reason)
    except RewindRefused as exc:
        print(f"되감지 않았습니다: {exc}")
        return 1
    write_state(state)
    mark_rewind_in_dead_letters(entry)
    print(f"되감았습니다: {entry['watermark_before']} → {entry['watermark_after']} "
          f"(다음 실행은 {entry['next_window_from']}부터 받습니다)")
    print(REWIND_CANNOT_DELETE)
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
