"""그래프 안 논문끼리 빠진 인용 간선을 S2에서 채우면 관련 논문 칸이 나아지는지 잽니다 (ADR-100).

    uv run python scripts/measure_reference_backfill.py            # 받고(이어 받기) 잽니다
    uv run python scripts/measure_reference_backfill.py --offline  # 받아 둔 것으로 재기만

10-14 스냅숏 논문 37,854편 중 34,602편이 나가는 인용 간선이 없습니다. 밖 논문 31,387편은 들일 때 그
논문의 참고문헌을 묻지 않았고, 수집 논문 3,215편은 그때 S2가 비어 있었습니다. 이 스크립트는

1. 나가는 간선이 없는 논문의 참고문헌을 S2에서 받아, **그래프 안 논문을 가리키는 것만** 남깁니다.
   노드는 하나도 늘지 않습니다.
2. 미래 공동인용 과제(`evals/cases_related_future.json`, 판정 자리)에서 그 간선을 얹은 피처
   그래프와 얹지 않은 그래프를 시드 쌍으로 비교합니다. 정답은 그대로 둡니다.

**읽기만 합니다.** 그래프에도 VM에도 쓰지 않습니다. S2는 키 없이 부르므로 0원입니다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import related_study as study  # noqa: E402
from evals.metrics import score_retrieval  # noqa: E402
from pipeline import s2  # noqa: E402
from pipeline import snapshot as snapshot_module  # noqa: E402
from src import pagerank  # noqa: E402

SNAPSHOT = "2026-10-14"
CASES = Path("evals/cases_related_future.json")
OUTPUT = Path("reports/reference_backfill.json")
CACHE = Path("derived/reference_backfill/raw.json.gz")
SLICE = 2_500  # 이만큼 받을 때마다 저장합니다
SLOWER = 3  # 키 없는 공용 한도에 막히지 않게 S2 간격을 이만큼 늘립니다(11-57)
BYTES_PER_RELATIONSHIP = 599  # ADR-099와 같은 추정: VM 저장소에서 임베딩 몫을 뺀 나머지
METRICS = ("recall_at_k", "hit_at_k", "reciprocal_rank")


def targets(snap) -> list[str]:
    citing = {row["citing"] for row in snap.rows("references")}
    return sorted(row["arxiv_id"] for row in snap.rows("papers") if row["arxiv_id"] not in citing)


def load_cache() -> dict:
    if not CACHE.exists():
        return {"references": {}, "empty": [], "not_found": [], "requests": 0}
    with gzip.open(CACHE, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def save(raw: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE, "wt", encoding="utf-8") as handle:
        json.dump(raw, handle)


def fetch(raw: dict, ids: list[str]) -> bool:
    """받지 않은 것만 묶음으로 받습니다. 실패한 묶음이 있으면 저장하고 False입니다."""
    done = set(raw["references"]) | set(raw["empty"]) | set(raw["not_found"])
    todo = [i for i in ids if i not in done]
    for start in range(0, len(todo), SLICE):
        batch = s2.fetch_references(todo[start:start + SLICE],
                                    sleep=lambda seconds: time.sleep(seconds * SLOWER))
        raw["references"].update(batch.references)
        raw["empty"] += batch.empty
        raw["not_found"] += batch.not_found
        raw["requests"] += batch.requests
        save(raw)
        fetched = len(raw["references"]) + len(raw["empty"]) + len(raw["not_found"])
        print(f"  S2 {fetched:,}/{len(ids):,} (실패 {len(batch.failures)})", flush=True)
        if batch.failures:
            print("  S2가 막았습니다 - 받은 데까지 저장했습니다. 다시 돌리면 잇습니다.", flush=True)
            return False
    return True


def new_edges(raw: dict, papers: set[str], existing: set[tuple[str, str]]) -> list[dict]:
    return [{"citing": citing, "cited": cited}
            for citing, refs in sorted(raw["references"].items())
            for cited in refs
            if cited in papers and cited != citing and (citing, cited) not in existing]


def evaluate(snap, cases: dict, extra: list[dict]) -> dict:
    """얹기 전과 뒤의 피처 그래프에서 현직(`cocited`)과 `ppr`을 시드 쌍으로 비교합니다.

    집단은 **얹기 전** 그래프의 연결 정도로 나눕니다. 판정 자리(간선 0개 집단)는 예측 시점에 간선이
    없던 시드이고, 보강이 바로 그 시드에 간선을 붙이는지가 질문입니다.
    """
    cut = study.parse_month(cases["feature_cut"])
    top_k = cases["top_k"]
    graphs = {"before": study.Graph(snap, feature_cut=cut),
              "after": study.Graph(snap, feature_cut=cut, extra_references=extra)}
    rows = []
    for case in cases["cases"]:
        seed, gold = case["seed_id"], case["expected_related_ids"]
        before = graphs["before"]
        row = {"degree": len(before.cites.get(seed, ())) + len(before.cited_by.get(seed, ()))}
        for name, graph in graphs.items():
            rank = pagerank.personalized(graph.matrix, graph.degree, graph.size,
                                         [graph.position[seed]])
            row[f"{name}_degree"] = (len(graph.cites.get(seed, ()))
                                     + len(graph.cited_by.get(seed, ())))
            row[f"cocited_{name}"] = score_retrieval(
                gold, study.cocited_ranking(graph, seed, rank, top_k), k=top_k)
            row[f"ppr_{name}"] = score_retrieval(
                gold, study.rank_by_score(graph, rank, seed, top_k), k=top_k)
        rows.append(row)

    groups = {
        "all": rows,
        "no_citation_edges": [r for r in rows if r["degree"] == 0],
        "degree_1_to_4": [r for r in rows if 1 <= r["degree"] < 5],
        "degree_5_or_more": [r for r in rows if r["degree"] >= 5],
    }
    result = {"edges_kept_after_cut": {name: sum(len(v) for v in g.cites.values())
                                       for name, g in graphs.items()}}
    for name, group in groups.items():
        if not group:
            continue
        entry = {"seeds": len(group),
                 "gained_edges": sum(1 for r in group if r["after_degree"] > r["before_degree"]),
                 "still_no_edges": sum(1 for r in group if r["after_degree"] == 0)}
        for variant in ("cocited", "ppr"):
            entry[variant] = {
                metric: {
                    "before": round(statistics.fmean(r[f"{variant}_before"][metric]
                                                     for r in group), 4),
                    "after": round(statistics.fmean(r[f"{variant}_after"][metric]
                                                    for r in group), 4),
                    "diff": study.paired_bootstrap(
                        [r[f"{variant}_before"][metric] for r in group],
                        [r[f"{variant}_after"][metric] for r in group]),
                }
                for metric in METRICS
            }
        result[name] = entry
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="빠진 인용 간선을 채워 봅니다")
    parser.add_argument("--offline", action="store_true", help="받아 둔 것으로 재기만 합니다")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(SNAPSHOT)
    source = {row["arxiv_id"]: row.get("source") for row in snap.rows("papers")}
    existing = {(row["citing"], row["cited"]) for row in snap.rows("references")}
    ids = targets(snap)
    raw = load_cache()
    if not args.offline and not fetch(raw, ids):
        return 1

    extra = new_edges(raw, set(source), existing)
    by_source = Counter(source[edge["citing"]] for edge in extra)
    touched = {edge["citing"] for edge in extra} | {edge["cited"] for edge in extra}
    cited_before = {cited for _, cited in existing}
    citing_before = {citing for citing, _ in existing}
    isolated = {p for p in source if p not in cited_before and p not in citing_before}
    cases = json.loads(CASES.read_text(encoding="utf-8"))

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "snapshot": SNAPSHOT,
        "targets": {"papers_without_outgoing_edges": len(ids),
                    "by_source": dict(Counter(source[i] for i in ids))},
        "s2": {"requests": raw["requests"], "with_references": len(raw["references"]),
               "empty": len(raw["empty"]), "not_found": len(raw["not_found"]),
               "fetched": len(raw["references"]) + len(raw["empty"]) + len(raw["not_found"])},
        "edges": {
            "existing": len(existing),
            "new_inside_graph": len(extra),
            "growth": round(len(extra) / len(existing), 3),
            "by_citing_source": dict(by_source),
            "isolated_before": len(isolated),
            "isolated_connected": len(isolated & touched),
            "store_mb_estimated": round(len(extra) * BYTES_PER_RELATIONSHIP / 1024**2, 1),
        },
        # 인용자 출처별로도 얹어 봅니다. 밖 논문(옛 논문)의 참고문헌과 수집 논문(최근 논문)의
        # 참고문헌은 같은 간선이라도 미래 공동인용에 대해 말하는 바가 다를 수 있습니다.
        "future_related": {
            "cases": str(CASES).replace("\\", "/"),
            "feature_cut": cases["feature_cut"], "top_k": cases["top_k"],
            "by_edge_set": {
                name: {"edges": len(edges), **evaluate(snap, cases, edges)}
                for name, edges in (
                    ("all", extra),
                    ("ai_citers", [e for e in extra if source[e["citing"]] == "ai"]),
                    ("reference_citers",
                     [e for e in extra if source[e["citing"]] == "reference"]),
                )
            },
        },
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(json.dumps({k: report[k] for k in ("targets", "s2", "edges")}, ensure_ascii=False))
    for edge_set, result in report["future_related"]["by_edge_set"].items():
        print(f"== {edge_set} (간선 {result['edges']:,})")
        for name in ("all", "no_citation_edges", "degree_1_to_4", "degree_5_or_more"):
            entry = result.get(name)
            if entry:
                recall = entry["cocited"]["recall_at_k"]
                print(f"  {name:20s} 시드 {entry['seeds']:5d} 간선 얻음 {entry['gained_edges']:5d} "
                      f"cocited recall@5 {recall['before']:.4f} → {recall['after']:.4f} "
                      f"{recall['diff']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
