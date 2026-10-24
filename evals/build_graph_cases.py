"""그래프가 없으면 답할 수 없는 평가 케이스를 만듭니다. 정답은 Cypher가 정합니다.

2026-10-13 측정에서 기존 평가 세트 세 개 모두 변별력이 없음이 확인됐습니다. 주제 질문의 정답
집합이 수백~수천 편이라 ID 기반 precision·recall이 품질을 재지 못하고, 정확한 제목 질문은
벡터 검색만으로 풀립니다(`advanced.md`의 "ID 기반 precision·recall은 품질을 재지 못합니다").

여기서 만드는 케이스는 둘을 동시에 만족합니다.

- **정답이 작고 정확합니다.** 저자·인용·분류 조건이 정답 집합을 2~5편으로 확정합니다.
- **벡터 검색으로는 풀리지 않습니다.** 임베딩 입력이 `title+abstract`라서 저자 이름과 인용
  관계는 임베딩에 아예 들어 있지 않습니다. 벡터 단독 기준선이 구조적으로 못 맞힙니다.

    uv run python -m evals.build_graph_cases
    uv run python -m evals.build_graph_cases --verify-against-db

정답은 스냅샷에서 계산하지만 케이스마다 같은 뜻의 Cypher를 함께 적습니다.
`--verify-against-db`가 그 Cypher를 살아 있는 DB에 돌려 저장된 정답과 맞는지 확인합니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
from pathlib import Path
from typing import Any

from pipeline import features
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import Snapshot

OUTPUT = Path("evals/cases_graph.json")
MIN_GOLD = 2
MAX_GOLD = 5
PER_FAMILY = 6
TOP_CITED_LIMIT = 3
SEED = 20261015


def sample(pool: list[Any], count: int, *, seed: int = SEED) -> list[Any]:
    """Pick cases deterministically without letting alphabetical order choose them.

    Sorting the eligible pool and slicing it gave six authors whose names all began with "A",
    because ties on gold-set size fell through to the name. Sorting first keeps the pool order
    reproducible; sampling from it then spreads the picks across the whole pool.
    """
    ordered = sorted(pool, key=repr)
    if len(ordered) <= count:
        return ordered
    return random.Random(seed).sample(ordered, count)

AUTHOR_CYPHER = (
    "MATCH (a:ArxivAuthorName {name: $name})-[:AUTHORED]->(p:ArxivPaper) "
    "RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id"
)
CITING_CYPHER = (
    "MATCH (p:ArxivPaper)-[:REFERENCES]->(q:ArxivPaper {arxiv_id: $arxiv_id}) "
    "RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id"
)
REFERENCE_CYPHER = (
    "MATCH (p:ArxivPaper {arxiv_id: $arxiv_id})-[:REFERENCES]->(q:ArxivPaper) "
    "WHERE q.primary_category = $category "
    "RETURN q.arxiv_id AS arxiv_id ORDER BY arxiv_id"
)
COAUTHOR_CYPHER = (
    "MATCH (p:ArxivPaper {arxiv_id: $arxiv_id})<-[:AUTHORED]-(:ArxivAuthorName)"
    "-[:AUTHORED]->(q:ArxivPaper) "
    "WHERE q.arxiv_id <> $arxiv_id "
    "RETURN DISTINCT q.arxiv_id AS arxiv_id ORDER BY arxiv_id"
)
TOP_CITED_CYPHER = (
    "MATCH (p:ArxivPaper)<-[:REFERENCES]-(:ArxivPaper) "
    "WHERE p.primary_category = $category "
    "WITH p.arxiv_id AS arxiv_id, count(*) AS citations "
    "RETURN arxiv_id ORDER BY citations DESC, arxiv_id ASC LIMIT $limit"
)


def load_tables(snap: Snapshot) -> dict[str, Any]:
    """Read the small tables plus the cached paper metadata."""
    cache = features.Cache(snap)
    meta = {row["arxiv_id"]: row for row in cache.meta()}
    authored: dict[str, list[str]] = collections.defaultdict(list)
    authors_of: dict[str, list[str]] = collections.defaultdict(list)
    for row in snap.rows("authored"):
        authored[row["author"]].append(row["paper"])
        authors_of[row["paper"]].append(row["author"])
    citing_of: dict[str, list[str]] = collections.defaultdict(list)
    cited_of: dict[str, list[str]] = collections.defaultdict(list)
    for row in snap.rows("references"):
        citing_of[row["cited"]].append(row["citing"])
        cited_of[row["citing"]].append(row["cited"])
    return {
        "meta": meta,
        "authored": authored,
        "authors_of": authors_of,
        "citing_of": citing_of,
        "cited_of": cited_of,
    }


def _usable_title(meta: dict[str, Any], arxiv_id: str) -> str:
    title = (meta.get(arxiv_id, {}).get("title") or "").strip()
    return " ".join(title.split())


def _case(
    case_id: str,
    question: str,
    search_query: str,
    gold: list[str],
    *,
    cypher: str,
    params: dict[str, Any],
    family: str,
    rationale: str,
) -> dict[str, Any]:
    return {
        "id": case_id,
        "category": family,
        "question": question,
        "search_query": search_query,
        "expected_ids": sorted(gold),
        "expected_answer_contains": [],
        "label_method": "snapshot_cypher",
        "requires_graph": True,
        "gold_size": len(gold),
        "cypher": cypher,
        "cypher_params": params,
        "label_rationale": rationale,
    }


def author_cases(tables: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask which papers list a given author name; the gold set is that name's papers."""
    meta = tables["meta"]
    picks = []
    for name, papers in sorted(tables["authored"].items()):
        unique = sorted(set(papers))
        if not MIN_GOLD <= len(unique) <= MAX_GOLD:
            continue
        # 이름이 ASCII 토큰 두 개 이상이어야 질문 문장이 자연스럽고 인코딩 문제도 없습니다.
        if len(name.split()) < 2 or not name.isascii():
            continue
        if any(not _usable_title(meta, paper) for paper in unique):
            continue
        picks.append((name, unique))
    cases = []
    for index, (name, papers) in enumerate(sample(picks, PER_FAMILY), start=1):
        cases.append(
            _case(
                f"author-{index:02d}",
                f"Which papers in this collection list an author named {name}?",
                name,
                papers,
                cypher=AUTHOR_CYPHER,
                params={"name": name},
                family="author",
                rationale=(
                    "저자 이름은 임베딩 입력(title+abstract)에 없으므로 AUTHORED 관계로만 "
                    "찾을 수 있습니다. 정답은 이 이름 노드가 가진 논문 전부입니다."
                ),
            )
        )
    return cases


def citing_cases(tables: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask which papers cite a named paper; the gold set is its in-neighbours."""
    meta = tables["meta"]
    picks = []
    for arxiv_id, citers in sorted(tables["citing_of"].items()):
        unique = sorted(set(citers))
        title = _usable_title(meta, arxiv_id)
        if not MIN_GOLD <= len(unique) <= MAX_GOLD or not title:
            continue
        picks.append((arxiv_id, title, unique))
    cases = []
    for index, (arxiv_id, title, citers) in enumerate(sample(picks, PER_FAMILY), start=1):
        cases.append(
            _case(
                f"citing-{index:02d}",
                f"Which papers in this collection cite the paper titled {title}?",
                title,
                citers,
                cypher=CITING_CYPHER,
                params={"arxiv_id": arxiv_id},
                family="citing",
                rationale=(
                    "인용 방향이 핵심입니다. 벡터 검색은 제목이 비슷한 논문을 돌려주지만 "
                    "이 논문을 실제로 인용한 논문 집합은 REFERENCES 관계만 알고 있습니다."
                ),
            )
        )
    return cases


def reference_cases(tables: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask which of a paper's references sit in one primary category."""
    meta = tables["meta"]
    picks = []
    for arxiv_id, cited in sorted(tables["cited_of"].items()):
        title = _usable_title(meta, arxiv_id)
        if not title:
            continue
        grouped: dict[str, list[str]] = collections.defaultdict(list)
        for target in set(cited):
            category = meta.get(target, {}).get("primary_category")
            if category:
                grouped[category].append(target)
        for category, targets in grouped.items():
            if MIN_GOLD <= len(targets) <= MAX_GOLD:
                picks.append((arxiv_id, title, category, sorted(targets)))
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for arxiv_id, title, category, targets in sample(picks, PER_FAMILY * 4):
        if arxiv_id in seen:
            continue
        seen.add(arxiv_id)
        index = len(cases) + 1
        cases.append(
            _case(
                f"reference-{index:02d}",
                f"Among the papers cited by {title}, which ones have primary "
                f"category {category}?",
                title,
                targets,
                cypher=REFERENCE_CYPHER,
                params={"arxiv_id": arxiv_id, "category": category},
                family="reference",
                rationale=(
                    "인용 관계와 대표 분류 속성을 함께 걸어야 풀립니다. 두 조건 중 하나만 "
                    "쓰면 정답 집합이 달라집니다."
                ),
            )
        )
        if len(cases) >= PER_FAMILY:
            break
    return cases


def coauthor_cases(tables: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask which papers share an author with a named paper; a two-hop author join."""
    meta = tables["meta"]
    authors_of = tables["authors_of"]
    authored = tables["authored"]
    picks = []
    for arxiv_id in sorted(authors_of):
        title = _usable_title(meta, arxiv_id)
        if not title:
            continue
        neighbours = {
            other
            for author in authors_of[arxiv_id]
            for other in authored[author]
            if other != arxiv_id
        }
        if MIN_GOLD <= len(neighbours) <= MAX_GOLD:
            picks.append((arxiv_id, title, sorted(neighbours)))
    cases = []
    for index, (arxiv_id, title, neighbours) in enumerate(
        sample(picks, PER_FAMILY), start=1
    ):
        cases.append(
            _case(
                f"coauthor-{index:02d}",
                f"Which other papers in this collection share an author with {title}?",
                title,
                neighbours,
                cypher=COAUTHOR_CYPHER,
                params={"arxiv_id": arxiv_id},
                family="coauthor",
                rationale=(
                    "저자 이름 노드를 거치는 두 홉 질문입니다. 내용이 전혀 다른 논문이 정답에 "
                    "들어가므로 벡터 유사도와 정답이 어긋납니다."
                ),
            )
        )
    return cases


def top_cited_cases(tables: dict[str, Any]) -> list[dict[str, Any]]:
    """Ask for the most-cited papers inside one category, skipping boundary ties."""
    meta = tables["meta"]
    citing_of = tables["citing_of"]
    by_category: dict[str, list[tuple[int, str]]] = collections.defaultdict(list)
    for arxiv_id, row in meta.items():
        category = row.get("primary_category")
        citations = len(set(citing_of.get(arxiv_id, ())))
        if category and citations:
            by_category[category].append((citations, arxiv_id))
    cases: list[dict[str, Any]] = []
    for category, scored in sorted(by_category.items(), key=lambda item: -len(item[1])):
        ranked = sorted(scored, key=lambda item: (-item[0], item[1]))
        if len(ranked) <= TOP_CITED_LIMIT:
            continue
        # 경계에서 동점이면 정답 집합이 하나로 정해지지 않으므로 그 분류는 버립니다.
        if ranked[TOP_CITED_LIMIT - 1][0] == ranked[TOP_CITED_LIMIT][0]:
            continue
        gold = [arxiv_id for _, arxiv_id in ranked[:TOP_CITED_LIMIT]]
        if any(not _usable_title(meta, arxiv_id) for arxiv_id in gold):
            continue
        index = len(cases) + 1
        cases.append(
            _case(
                f"topcited-{index:02d}",
                f"Within primary category {category}, which {TOP_CITED_LIMIT} papers are "
                "cited most often by other papers in this collection?",
                f"{category} highly cited papers",
                gold,
                cypher=TOP_CITED_CYPHER,
                params={"category": category, "limit": TOP_CITED_LIMIT},
                family="topcited",
                rationale=(
                    "피인용 수 집계는 그래프에서만 나옵니다. 경계 동점이 있는 분류는 정답이 "
                    "하나로 정해지지 않으므로 제외했습니다."
                ),
            )
        )
        if len(cases) >= PER_FAMILY:
            break
    return cases


FAMILIES = (author_cases, citing_cases, reference_cases, coauthor_cases, top_cited_cases)


def build(snap: Snapshot) -> dict[str, Any]:
    """Generate every family's cases against one pinned snapshot."""
    tables = load_tables(snap)
    cases = [case for family in FAMILIES for case in family(tables)]
    return {
        "dataset_version": 1,
        "snapshot_version": snap.version,
        "snapshot_cut_month": snap.manifest["cut"]["month"],
        "source": f"snapshots/{snap.version}/manifest.json",
        "label_method": "snapshot_cypher",
        "note": (
            "정답은 스냅샷에서 계산하고, 같은 뜻의 Cypher를 케이스마다 적었습니다. "
            "evals.build_graph_cases --verify-against-db로 DB와 대조할 수 있습니다."
        ),
        "cases": cases,
    }


def verify_against_db(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """Run each recorded Cypher on the live DB and compare it to the stored gold set."""
    from src import chatbot

    findings = []
    for case in dataset["cases"]:
        rows = chatbot.run_read(case["cypher"], case["cypher_params"])
        found = sorted({row["arxiv_id"] for row in rows})
        expected = sorted(case["expected_ids"])
        findings.append(
            {
                "id": case["id"],
                "matches": found == expected,
                "missing_in_db": [value for value in expected if value not in found],
                "extra_in_db": [value for value in found if value not in expected],
            }
        )
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="그래프 조건 평가 케이스를 만듭니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷을 씁니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify-against-db", action="store_true")
    args = parser.parse_args()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    dataset = build(snap)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    summary = collections.Counter(case["category"] for case in dataset["cases"])
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cases": len(dataset["cases"]),
                "by_family": dict(sorted(summary.items())),
                "gold_sizes": sorted({case["gold_size"] for case in dataset["cases"]}),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if args.verify_against_db:
        findings = verify_against_db(dataset)
        mismatched = [row for row in findings if not row["matches"]]
        print(
            json.dumps(
                {
                    "verified": len(findings),
                    "mismatched": len(mismatched),
                    "details": mismatched[:10],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1 if mismatched else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
