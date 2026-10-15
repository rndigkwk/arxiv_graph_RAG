"""스냅샷 품질 게이트입니다.

파이프라인이 적재나 승격으로 넘어가기 전에 통과해야 하는 검사를 모아 둡니다. 각 검사는
이름·측정값·임계값·통과 여부를 돌려주므로, 실패했을 때 "무엇이 얼마나 어긋났는지"가 보고서에
그대로 남습니다.

임계값은 2026-10-14 스냅샷에서 실측한 값을 기준으로 정했습니다. 임베딩 커버리지처럼 지금
100%인 항목은 100%를 요구하고, 인용 시점 역전처럼 원본 데이터에 섞여 있는 항목은 실측값보다
약간 느슨하게 둡니다. 느슨한 쪽을 실측값에 딱 붙이면 정상적인 변동에도 게이트가 터집니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from pipeline.snapshot import month_index

EMBEDDING_DIMENSIONS = 768
MIN_EMBEDDING_COVERAGE = 1.0
MIN_MONTH_KEY_COVERAGE = 0.99
MAX_TIME_VIOLATION_RATE = 0.02
MAX_DANGLING_EDGE_RATE = 0.0


@dataclass
class Check:
    """One gate condition, carrying the measured value next to the threshold it was judged by."""

    name: str
    passed: bool
    value: float | int
    threshold: float | int
    comparison: str
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "value": self.value,
            "threshold": self.threshold,
            "comparison": self.comparison,
            "detail": self.detail,
        }


@dataclass
class PaperStats:
    """Counters collected in one pass over the papers table."""

    total: int = 0
    unique_ids: int = 0
    with_embedding: int = 0
    wrong_dimensions: int = 0
    with_month_key: int = 0
    blank_text: int = 0
    ids: set[str] = field(default_factory=set)


def scan_papers(rows: Iterable[dict[str, Any]]) -> PaperStats:
    """Collect paper-side counters; holds the ID set because edge checks need it."""
    stats = PaperStats()
    for row in rows:
        stats.total += 1
        arxiv_id = row.get("arxiv_id") or ""
        if arxiv_id:
            stats.ids.add(arxiv_id)
        embedding = row.get("embedding")
        if isinstance(embedding, (list, tuple)) and embedding:
            stats.with_embedding += 1
            if len(embedding) != EMBEDDING_DIMENSIONS:
                stats.wrong_dimensions += 1
        if month_index(arxiv_id) is not None:
            stats.with_month_key += 1
        if not (row.get("title") or "").strip() or not (row.get("abstract") or "").strip():
            stats.blank_text += 1
    stats.unique_ids = len(stats.ids)
    return stats


@dataclass
class EdgeStats:
    """Counters collected in one pass over the references table."""

    total: int = 0
    dangling: int = 0
    self_loops: int = 0
    time_violations: int = 0
    comparable: int = 0


def scan_references(rows: Iterable[dict[str, Any]], paper_ids: set[str]) -> EdgeStats:
    """Check referential integrity and that a citing paper is not older than what it cites."""
    stats = EdgeStats()
    for row in rows:
        stats.total += 1
        citing, cited = row.get("citing") or "", row.get("cited") or ""
        if citing not in paper_ids or cited not in paper_ids:
            stats.dangling += 1
        if citing == cited:
            stats.self_loops += 1
        citing_month, cited_month = month_index(citing), month_index(cited)
        if citing_month is None or cited_month is None:
            continue
        stats.comparable += 1
        if citing_month < cited_month:
            stats.time_violations += 1
    return stats


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def build_checks(papers: PaperStats, edges: EdgeStats, expected: dict[str, int]) -> list[Check]:
    """Turn the two scans into the gate's check list."""
    embedding_coverage = _rate(papers.with_embedding, papers.total)
    month_coverage = _rate(papers.with_month_key, papers.total)
    dangling_rate = _rate(edges.dangling, edges.total)
    violation_rate = _rate(edges.time_violations, edges.comparable)
    return [
        Check(
            "paper_count_matches_manifest",
            papers.total == expected.get("papers"),
            papers.total,
            expected.get("papers", 0),
            "==",
            "매니페스트의 행 수와 실제 행 수가 같아야 합니다",
        ),
        Check(
            "reference_count_matches_manifest",
            edges.total == expected.get("references"),
            edges.total,
            expected.get("references", 0),
            "==",
        ),
        Check(
            "arxiv_id_unique",
            papers.unique_ids == papers.total,
            papers.unique_ids,
            papers.total,
            "==",
            "arxiv_id 중복은 MERGE 적재에서 조용히 노드를 합쳐 버립니다",
        ),
        Check(
            "embedding_coverage",
            embedding_coverage >= MIN_EMBEDDING_COVERAGE,
            round(embedding_coverage, 6),
            MIN_EMBEDDING_COVERAGE,
            ">=",
            "임베딩 없는 논문이 섞이면 링크 예측의 텍스트 피처가 비어 버립니다",
        ),
        Check(
            "embedding_dimensions_uniform",
            papers.wrong_dimensions == 0,
            papers.wrong_dimensions,
            0,
            "==",
            f"모든 임베딩이 {EMBEDDING_DIMENSIONS}차원이어야 합니다",
        ),
        Check(
            "month_key_coverage",
            month_coverage >= MIN_MONTH_KEY_COVERAGE,
            round(month_coverage, 6),
            MIN_MONTH_KEY_COVERAGE,
            ">=",
            "신식 arXiv ID가 아니면 시간 분할의 기준 월을 정할 수 없습니다",
        ),
        Check(
            "text_present",
            papers.blank_text == 0,
            papers.blank_text,
            0,
            "==",
            "제목이나 초록이 빈 논문은 임베딩을 다시 만들어도 의미가 없습니다",
        ),
        Check(
            "no_dangling_references",
            dangling_rate <= MAX_DANGLING_EDGE_RATE,
            round(dangling_rate, 6),
            MAX_DANGLING_EDGE_RATE,
            "<=",
            "인용 간선의 양 끝 논문이 모두 스냅샷 안에 있어야 합니다",
        ),
        Check(
            "no_self_citation",
            edges.self_loops == 0,
            edges.self_loops,
            0,
            "==",
        ),
        Check(
            "citation_time_order",
            violation_rate <= MAX_TIME_VIOLATION_RATE,
            round(violation_rate, 6),
            MAX_TIME_VIOLATION_RATE,
            "<=",
            "인용하는 논문이 인용된 논문보다 과거면 둘 중 하나의 시점이 틀렸습니다",
        ),
    ]


def report(checks: Iterable[Check]) -> dict[str, Any]:
    """Collect the checks into a gate verdict; one failure blocks the whole snapshot."""
    collected = list(checks)
    failed = [check.name for check in collected if not check.passed]
    return {
        "passed": not failed,
        "failed": failed,
        "checks": [check.as_dict() for check in collected],
    }


def run(papers: Iterator[dict[str, Any]], references: Iterator[dict[str, Any]],
        expected: dict[str, int]) -> dict[str, Any]:
    """Scan both tables once and return the gate verdict."""
    paper_stats = scan_papers(papers)
    edge_stats = scan_references(references, paper_stats.ids)
    return report(build_checks(paper_stats, edge_stats, expected))
