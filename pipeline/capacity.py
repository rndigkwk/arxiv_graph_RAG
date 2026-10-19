"""Aura Free의 남은 자리를 세고, 무엇을 지워야 자리가 생기는지 **측정으로** 고릅니다.

    uv run python -m pipeline.capacity
    uv run python -m pipeline.capacity --output reports/capacity.json

[ADR-001](../docs/adr/ADR-001-aura-free-capacity.md)이 한도를 적고
[ADR-038](../docs/adr/ADR-038-the-forecast-panel.md)이 "정답은 아직 오지 않았습니다"로 남겨
둔 항목입니다. 예측 패널을 실제로 채점하려면 **다음 달 스냅샷**이 필요한데, 그러려면 자리가
있어야 합니다.

**Aura에 붙지 않습니다.** 스냅샷 파일만 읽습니다 — 세는 일에 DB가 필요하지 않고, 멈춘
인스턴스에서도 돌아야 합니다.

## 묶이는 쪽은 관계입니다

노드 여유와 관계 여유가 다릅니다. 둘 중 **먼저 차는 쪽**이 들어갈 논문 수를 정하므로, 노드만
줄이는 방안(예: 간선 없는 논문 삭제)은 **아무 자리도 만들지 못합니다.** 이 모듈이 두 축을
따로 세는 이유입니다.

## 후보를 지우기 전에 평가로 검사합니다

"지우면 자리가 얼마나 생기는가"만 세면 안 됩니다. 지운 뒤에 **대답할 수 없게 되는 질문**을
함께 세야 합니다. 그래서 각 후보를 `evals/cases_graph.json`의 정답에 대고 검사합니다 —
`author`·`coauthor` 가족 12건이 저자 그래프에 직접 의지합니다.
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import snapshot as snapshot_module

# ADR-001의 실측 한도입니다. 공식 문서 셋이 서로 다르게 적고 있어, 이 인스턴스가 실제로
# 보관하며 동작하는 값(149,025노드·338,366관계)이 들어가는 쪽을 씁니다.
NODE_LIMIT = 200_000
RELATIONSHIP_LIMIT = 400_000

CASES = Path("evals/cases_graph.json")
OUTPUT = Path("reports/capacity.json")

# 상한을 재 볼 값들입니다. 평가를 깨뜨리는 지점을 찾는 것이 목적이므로 넓게 훑습니다.
AUTHOR_CAPS = (5, 10, 15, 20, 30, 50, 100, 200, 500, 1000)


def author_index(
    snap: snapshot_module.Snapshot,
) -> tuple[dict[str, list[str]], collections.Counter]:
    """`paper -> [author, ...]`와 이름별 차수. 순서는 적재 순서 그대로입니다."""
    order: dict[str, list[str]] = collections.defaultdict(list)
    degree: collections.Counter = collections.Counter()
    for row in snap.rows("authored"):
        order[row["paper"]].append(row["author"])
        degree[row["author"]] += 1
    return dict(order), degree


def usage(snap: snapshot_module.Snapshot) -> dict[str, Any]:
    """두 축을 따로 셉니다. 먼저 차는 쪽이 들어갈 논문 수를 정합니다."""
    counts = snap.counts
    nodes = counts["papers"] + counts["authors"]
    relationships = counts["authored"] + counts["references"]
    return {
        "nodes": {
            "used": nodes,
            "limit": NODE_LIMIT,
            "share": round(nodes / NODE_LIMIT, 4),
            "free": NODE_LIMIT - nodes,
            "parts": {"ArxivPaper": counts["papers"], "ArxivAuthorName": counts["authors"]},
        },
        "relationships": {
            "used": relationships,
            "limit": RELATIONSHIP_LIMIT,
            "share": round(relationships / RELATIONSHIP_LIMIT, 4),
            "free": RELATIONSHIP_LIMIT - relationships,
            "parts": {"AUTHORED": counts["authored"], "REFERENCES": counts["references"]},
            # AUTHORED가 관계 예산의 몇 할인지. 지울 곳을 고르는 출발점입니다.
            "authored_share_of_relationships": round(counts["authored"] / relationships, 4),
        },
    }


def monthly(snap: snapshot_module.Snapshot, *, recent: int = 6) -> dict[str, Any]:
    """논문당·월당 비용. `arxiv_id`의 yymm으로 셉니다(`published`는 수집 시점이 섞입니다)."""
    counts = snap.counts
    per_month: collections.Counter = collections.Counter()
    for row in snap.rows("papers"):
        index = snapshot_module.month_index(row["arxiv_id"])
        if index is not None:
            per_month[index] += 1
    months = sorted(per_month)
    # 마지막 달은 수집 시점에 잘려 부분값입니다. 평균에서 뺍니다.
    usable = months[:-1] if len(months) > 1 else months
    tail = usable[-recent:]
    papers_per_month = sum(per_month[m] for m in tail) / len(tail) if tail else 0.0
    return {
        "authored_per_paper": round(counts["authored"] / counts["papers"], 4),
        "references_per_paper": round(counts["references"] / counts["papers"], 4),
        "relationships_per_paper": round(
            (counts["authored"] + counts["references"]) / counts["papers"], 4
        ),
        "recent_months": {snapshot_module.month_label(m): per_month[m] for m in usable[-recent:]},
        "partial_last_month": {
            snapshot_module.month_label(months[-1]): per_month[months[-1]]
        } if len(months) > 1 else {},
        "papers_per_month": round(papers_per_month, 1),
        "peak_month": max(
            ((snapshot_module.month_label(m), per_month[m]) for m in usable), key=lambda p: p[1]
        ) if usable else None,
    }


def headroom(use: dict[str, Any], rate: dict[str, Any]) -> dict[str, Any]:
    """How many papers and months fit, on whichever axis binds first."""
    per_paper_rel = rate["relationships_per_paper"]
    # 새 논문은 새 저자 이름도 데려옵니다. 지금 말뭉치에서 이름 하나가 평균 몇 편에 걸리는지로
    # 어림합니다 - 보수적으로 보려면 이 값이 작을수록(새 이름이 많을수록) 노드가 빨리 찹니다.
    names_per_paper = use["nodes"]["parts"]["ArxivAuthorName"] / use["nodes"]["parts"]["ArxivPaper"]
    per_paper_node = 1 + names_per_paper
    by_relationships = use["relationships"]["free"] / per_paper_rel if per_paper_rel else 0
    by_nodes = use["nodes"]["free"] / per_paper_node if per_paper_node else 0
    binding = "relationships" if by_relationships <= by_nodes else "nodes"
    papers = min(by_relationships, by_nodes)
    rate_per_month = rate["papers_per_month"] or 1
    return {
        "nodes_per_paper": round(per_paper_node, 4),
        "relationships_per_paper": per_paper_rel,
        "papers_by_relationships": int(by_relationships),
        "papers_by_nodes": int(by_nodes),
        "binding_axis": binding,
        "papers_that_fit": int(papers),
        "months_that_fit": round(papers / rate_per_month, 1),
        "months_at_peak_rate": (
            round(papers / rate["peak_month"][1], 1) if rate["peak_month"] else None
        ),
    }


def _case_dependencies(order: dict[str, list[str]], degree: collections.Counter) -> dict[str, Any]:
    """Where the eval's author questions sit in each paper's author list.

    상한을 걸면 **목록 뒤쪽 저자가 사라집니다.** 평가가 의지하는 가장 뒤 위치를 찾으면,
    그 아래의 상한은 전부 정답을 깨뜨립니다.
    """
    if not CASES.exists():
        return {"available": False}
    cases = json.loads(CASES.read_text(encoding="utf-8-sig"))
    cases = cases["cases"] if isinstance(cases, dict) else cases
    titles = {}
    author_rows: list[dict[str, Any]] = []

    for case in cases:
        if case["category"] == "author":
            name = case["question"].split("named ")[-1].rstrip("?").strip()
            positions = [
                order.get(paper, []).index(name) + 1
                for paper in case["expected_ids"]
                if name in order.get(paper, [])
            ]
            author_rows.append({
                "id": case["id"],
                "name_degree": degree.get(name, 0),
                "positions": positions,
                "deepest": max(positions, default=0),
            })
        elif case["category"] == "coauthor":
            asked = case["question"].split("share an author with ")[-1]
            titles[case["id"]] = asked.rstrip("?").strip()

    return {
        "available": True,
        "author_cases": author_rows,
        "coauthor_titles": titles,
        "author_min_degree": min((row["name_degree"] for row in author_rows), default=0),
        "author_deepest_position": max((row["deepest"] for row in author_rows), default=0),
    }


def coauthor_depth(
    order: dict[str, list[str]], titles: dict[str, str], snap: snapshot_module.Snapshot,
    cases: list[dict[str, Any]],
) -> dict[str, Any]:
    """How deep in an author list the shared author of each `coauthor` case sits."""
    by_title = {}
    for row in snap.rows("papers"):
        by_title[row["title"]] = row["arxiv_id"]
    rows = []
    for case in cases:
        if case["category"] != "coauthor":
            continue
        seed = by_title.get(titles.get(case["id"], ""))
        if seed is None:
            rows.append({"id": case["id"], "resolved": False})
            continue
        seed_names = order.get(seed, [])
        deepest = 0
        for other in case["expected_ids"]:
            other_names = order.get(other, [])
            for name in set(seed_names) & set(other_names):
                deepest = max(deepest, seed_names.index(name) + 1, other_names.index(name) + 1)
        rows.append({"id": case["id"], "resolved": True, "deepest": deepest})
    return {
        "cases": rows,
        "deepest_position": max((row.get("deepest", 0) for row in rows), default=0),
    }


def candidates(
    snap: snapshot_module.Snapshot,
    order: dict[str, list[str]],
    degree: collections.Counter,
    deepest_needed: int,
) -> list[dict[str, Any]]:
    """Each way of freeing space, with its measured yield **and what it breaks.**

    수확만 세면 안 됩니다. ADR-004가 "하한은 파국을 잡는 것"이라고 적은 것과 같은 선 —
    **지운 뒤에 대답할 수 없게 되는 질문을 함께 세야** 고를 수 있습니다.
    """
    counts = snap.counts
    rows: list[dict[str, Any]] = []

    # 1. 논문당 저자 상한
    sizes = [len(names) for names in order.values()]
    for cap in AUTHOR_CAPS:
        saved = sum(max(0, size - cap) for size in sizes)
        if not saved:
            continue
        kept = {name for names in order.values() for name in names[:cap]}
        rows.append({
            "candidate": f"논문당 저자 상한 {cap}명",
            "relationships_freed": saved,
            "nodes_freed": counts["authors"] - len(kept),
            "papers_touched": sum(1 for size in sizes if size > cap),
            "breaks_the_eval": cap < deepest_needed,
            "why": (
                f"평가가 {deepest_needed}번째 저자까지 의지합니다. 상한 {cap}은 그 저자를 "
                "지워 정답을 깨뜨립니다."
                if cap < deepest_needed else
                f"평가가 의지하는 가장 뒤 위치({deepest_needed})를 넘기므로 정답은 그대로입니다."
            ),
        })

    # 2. 한 편에만 등장하는 저자 이름
    once = {name for name, count in degree.items() if count == 1}
    emptied = sum(1 for names in order.values() if all(name in once for name in names))
    first_once = sum(1 for names in order.values() if names and names[0] in once)
    rows.append({
        "candidate": "한 편에만 등장하는 저자 이름 삭제",
        "relationships_freed": len(once),
        "nodes_freed": len(once),
        "papers_touched": sum(1 for names in order.values() if any(name in once for name in names)),
        # 차수 1인 이름은 **두 논문을 이을 수 없습니다.** 그래서 공동저자 답은 구조적으로
        # 그대로이고, 저자 질문의 정답도 gold가 2편 이상이라 차수 2 이상만 고릅니다.
        "breaks_the_eval": False,
        "why": (
            "차수 1인 이름은 두 논문을 이을 수 없어 공동저자 답이 구조적으로 그대로이고, "
            "저자 질문의 정답은 2편 이상을 요구하므로 차수 2 이상만 씁니다. 그러나 "
            f"**{emptied}편({emptied / len(order) * 100:.1f}%)이 저자를 전부 잃고** "
            f"{first_once}편은 1저자를 잃습니다 - 평가가 묻지 않는 손실입니다."
        ),
    })

    # 3. 인용 간선이 없는 논문 삭제
    cited, citing = set(), set()
    for row in snap.rows("references"):
        citing.add(row["citing"])
        cited.add(row["cited"])
    connected = citing | cited
    isolated = [paper for paper in order if paper not in connected]
    isolated_set = set(isolated)
    # 지운 논문에만 걸려 있던 이름은 고아가 되어 함께 사라집니다.
    elsewhere: collections.Counter = collections.Counter()
    for paper, names in order.items():
        if paper not in isolated_set:
            for name in set(names):
                elsewhere[name] += 1
    orphaned = {
        name for paper in isolated for name in order[paper] if not elsewhere[name]
    }
    # **정답에 들어 있는지를 셉니다.** "검색이 그 논문을 돌려준다"는 추측이고, 이것은 사실입니다.
    in_gold = {
        paper
        for case in (
            json.loads(CASES.read_text(encoding="utf-8-sig"))["cases"] if CASES.exists() else []
        )
        for paper in case["expected_ids"]
        if paper in isolated_set
    }
    rows.append({
        "candidate": "인용 간선이 없는 논문 삭제",
        "relationships_freed": sum(len(order[paper]) for paper in isolated),
        "nodes_freed": len(isolated) + len(orphaned),
        "papers_touched": len(isolated),
        "breaks_the_eval": bool(in_gold),
        "gold_papers_deleted": sorted(in_gold),
        "why": (
            f"정답에 들어 있는 논문 {len(in_gold)}편을 지웁니다 - 그만큼 재는 대상 자체가 "
            "줄어듭니다. 수확도 다른 방안보다 작습니다."
            if in_gold else
            "정답에 들어 있는 논문은 없습니다. 다만 수확이 작고, 벡터 검색이 돌려주던 "
            "논문이 사라져 재지 않는 자리에서 답이 얇아집니다."
        ),
    })

    # 4. AUTHORED를 논문 속성으로 접기
    rows.append({
        "candidate": "AUTHORED를 ArxivPaper 속성 배열로 접기",
        "relationships_freed": counts["authored"],
        "nodes_freed": counts["authors"],
        "papers_touched": len(order),
        "breaks_the_eval": None,
        "why": (
            "수확이 가장 큽니다 - 관계의 "
            f"{counts['authored'] / (counts['authored'] + counts['references']) * 100:.1f}%와 "
            f"노드의 {counts['authors'] / (counts['papers'] + counts['authors']) * 100:.1f}%. "
            "이름을 하나도 잃지 않습니다. 대신 공동저자 질의가 노드 경유 대신 전체 논문 "
            "스캔이 되고, **그 지연은 DB에서만 잴 수 있습니다** - 여기서 판정하지 않습니다."
        ),
    })
    return rows


def build(version: str | None = None) -> dict[str, Any]:
    snap = snapshot_module.load(version) if version else snapshot_module.latest()
    order, degree = author_index(snap)
    use = usage(snap)
    rate = monthly(snap)
    deps = _case_dependencies(order, degree)
    cases = []
    if CASES.exists():
        loaded = json.loads(CASES.read_text(encoding="utf-8-sig"))
        cases = loaded["cases"] if isinstance(loaded, dict) else loaded
    depth = (
        coauthor_depth(order, deps.get("coauthor_titles", {}), snap, cases)
        if deps.get("available") else {"deepest_position": 0}
    )
    deepest = max(deps.get("author_deepest_position", 0), depth.get("deepest_position", 0))
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot": snap.manifest["version"],
        "usage": use,
        "rate": rate,
        "headroom": headroom(use, rate),
        "eval_dependency": {
            **{key: value for key, value in deps.items() if key != "coauthor_titles"},
            "coauthor": depth,
            "deepest_author_position_the_eval_needs": deepest,
        },
        "candidates": candidates(snap, order, degree, deepest),
        "reading": (
            "묶이는 축을 먼저 봅니다 - 노드만 줄이는 방안은 자리를 만들지 못합니다. "
            "`breaks_the_eval`이 참인 방안은 자리를 만들지만 대답을 잃습니다. None은 "
            "여기서 판정할 수 없다는 뜻이며, 무엇을 더 재야 하는지가 `why`에 있습니다."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aura Free의 남은 자리를 셉니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    report = build(args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
