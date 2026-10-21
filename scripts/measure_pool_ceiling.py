"""후보 풀을 넓히면 상한이 올라가는지 **고치기 전에** 잽니다.

[ADR-051](../docs/adr/ADR-051-the-cross-encoder-loses-to-cosine.md)이 점 하나를 남겼습니다 —
후보 49편(벡터 상위 50 ∪ 보이는 공동인용)에서 간선 0개 집단의 상한이 **0.2690**이고, 코사인이
0.1227로 그 절반도 못 씁니다. 그래서 "재정렬보다 후보 생성에 여지가 있을 수 있다"로 닫았고,
**그쪽은 재지 않았습니다.**

점 하나로는 두 가지를 구별할 수 없습니다.

| 만약 | 뜻 |
|---|---|
| 깊게 파면 상한이 크게 오른다 | 정답이 코사인 순위에 **있는데 50등 밖**입니다 → 손잡이는 **깊이** |
| 깊게 파도 거의 그대로다 | 정답이 코사인으로 **닿지 않습니다** → **다른 신호**가 필요합니다 |

두 경우에 할 일이 전혀 다릅니다. 앞쪽이면 풀 크기를 키우는 한 줄이고, 뒤쪽이면 후보 생성기를
새로 만드는 일입니다. **그 갈림길을 짓기 전에 재는 것**이 이 스크립트입니다.

상한의 정의는 `evals/related_study.py`와 **같습니다** — `|정답 ∩ 풀| / |정답|`의 평균입니다.
새로 정의하면 0.2690과 비교가 성립하지 않습니다. recall@5의 느슨한 상한이고, 느슨한 쪽이
안전합니다(풀에 있어도 상위 5등에 못 들 수 있으므로 실제 달성치는 이보다 낮습니다).

API를 쓰지 않습니다. 스냅샷과 파생 임베딩만 읽는 순수 로컬 계산입니다.

```bash
uv run python -m scripts.measure_pool_ceiling
uv run python -m scripts.measure_pool_ceiling --pools 50 200 1000 --limit 100
```
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.related_study import GBM_POOL, Graph, parse_month, pool_candidates
from pipeline import snapshot as snapshot_module

OUTPUT = Path("reports/pool_ceiling.json")
# **미래 라벨 셋이 기본입니다.** `cases_related.json`은 `feature_cut`이 없어서 공동인용이
# 정답을 그대로 보고, 그러면 상한이 1.0으로 나옵니다 - 측정이 아니라 누출입니다(ADR-035).
CASES = Path("evals/cases_related_future.json")
# 50은 지금 쓰는 값입니다(`GBM_POOL`). 24,360편 말뭉치에서 2,000은 8.2%이고, 그보다 깊이
# 파는 것은 "후보 생성"이 아니라 "전수 재정렬"이라 다른 결정입니다.
DEFAULT_POOLS = (50, 100, 200, 500, 1000, 2000)


def ceiling_at(graph: Graph, cases: list[dict[str, Any]], pools: tuple[int, ...]):
    """사례마다 간선 수와 풀 크기별 상한을 냅니다."""
    rows: list[dict[str, Any]] = []
    for case in cases:
        seed, gold = case["seed_id"], case["expected_related_ids"]
        scores = graph.vectors @ graph.vectors[graph.position[seed]]
        row: dict[str, Any] = {
            "seed_id": seed,
            "gold_size": len(gold),
            # `related_study.py`와 같은 정의입니다. 다르면 집단이 갈라집니다.
            "citation_degree": (
                len(graph.cites.get(seed, ())) + len(graph.cited_by.get(seed, ()))
            ),
            # 공동인용만으로 들어오는 몫. 벡터를 0편으로 깎았을 때의 상한이고, 깊이를
            # 늘려도 **움직이지 않는 바닥**입니다. 올라가는 폭이 전부 벡터 쪽 기여입니다.
            "cocited_only": recall(set(graph.cocited(seed)) - {seed}, gold),
        }
        for pool in pools:
            pooled = set(pool_candidates(graph, seed, scores, pool=pool))
            row[f"pool_{pool}"] = recall(pooled, gold)
            row[f"size_{pool}"] = len(pooled)
        rows.append(row)
    return rows


def recall(pooled: set[str], gold: list[str]) -> float:
    return round(len(pooled & set(gold)) / len(gold), 6) if gold else 0.0


def summarize(rows: list[dict[str, Any]], pools: tuple[int, ...]) -> dict[str, Any]:
    groups = {
        "all": rows,
        "no_citation_edges": [r for r in rows if r["citation_degree"] == 0],
        "degree_1_to_4": [r for r in rows if 1 <= r["citation_degree"] < 5],
        "degree_5_or_more": [r for r in rows if r["citation_degree"] >= 5],
    }
    out: dict[str, Any] = {}
    for name, group in groups.items():
        if not group:
            out[name] = {"seeds": 0}
            continue
        at = {
            str(pool): round(statistics.fmean(r[f"pool_{pool}"] for r in group), 4)
            for pool in pools
        }
        base = at[str(pools[0])]
        out[name] = {
            "seeds": len(group),
            "cocited_only": round(
                statistics.fmean(r["cocited_only"] for r in group), 4
            ),
            "ceiling_at_pool": at,
            "mean_candidates": {
                str(pool): round(statistics.fmean(r[f"size_{pool}"] for r in group), 1)
                for pool in pools
            },
            # **이 수가 결정을 가릅니다.** 가장 깊은 풀이 지금 풀보다 얼마나 더 담는가.
            "gain_from_deepening": round(at[str(pools[-1])] - base, 4),
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="후보 풀 크기별 상한을 잽니다(고치기 전의 측정)."
    )
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부입니다.")
    parser.add_argument("--pools", type=int, nargs="+", default=list(DEFAULT_POOLS))
    parser.add_argument("--embeddings", type=Path)
    parser.add_argument(
        "--allow-uncut", action="store_true",
        help="feature_cut이 없는 정답으로도 재기. 상한이 누출로 1.0이 됩니다.",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    pools = tuple(sorted(set(args.pools)))
    snap = (
        snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    )
    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = dataset["cases"][: args.limit] if args.limit else dataset["cases"]
    # 자르는 선은 **정답 파일이 정합니다.** 자르지 않은 그래프로 재면 공동인용이 정답을
    # 그대로 보므로 상한이 가짜로 올라갑니다(ADR-035).
    cut_label = dataset.get("feature_cut")
    if not cut_label and not args.allow_uncut:
        parser.error(
            f"{args.cases}에 feature_cut이 없습니다. 자르지 않은 그래프에서는 공동인용이 "
            "정답을 그대로 보므로 상한이 1.0으로 나옵니다 - 측정이 아니라 누출입니다"
            "(ADR-035). 그래도 재려면 --allow-uncut."
        )
    graph = Graph(
        snap,
        feature_cut=parse_month(cut_label) if cut_label else None,
        embeddings=args.embeddings,
    )
    if cut_label:
        print(f"그래프를 {cut_label} 이전으로 잘랐습니다: 간선 {graph.dropped_edges}개 제외")
    if max(pools) >= graph.size:
        parser.error(f"풀이 말뭉치보다 큽니다: {max(pools)} >= {graph.size}")

    rows = ceiling_at(graph, cases, pools)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot": snap.version,
        "cases_file": str(args.cases).replace("\\", "/"),
        "cases": len(rows),
        "corpus": graph.size,
        "feature_cut": cut_label,
        "current_pool": GBM_POOL,
        "pools": list(pools),
        "by_degree": summarize(rows, pools),
        "note": (
            "상한은 |정답 ∩ 풀| / |정답|의 평균이고 evals/related_study.py와 같은 "
            "정의입니다. recall@5의 느슨한 상한입니다 - 풀에 있어도 상위 5등에 못 들 수 "
            "있으므로 실제 달성치는 이보다 낮습니다. cocited_only는 벡터를 0편으로 깎은 "
            "바닥이고, 깊이를 늘려도 움직이지 않습니다."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\n말뭉치 {graph.size}편, 사례 {len(rows)}건, 현재 풀 {GBM_POOL}\n")
    header = "집단".ljust(20) + "시드".rjust(6) + "공동인용만".rjust(11)
    header += "".join(f"{pool:>9}" for pool in pools) + "깊이 이득".rjust(11)
    print(header)
    print("-" * len(header))
    for name, stats in report["by_degree"].items():
        if not stats.get("seeds"):
            continue
        line = name.ljust(20) + str(stats["seeds"]).rjust(6)
        line += f"{stats['cocited_only']:>11.4f}"
        line += "".join(
            f"{stats['ceiling_at_pool'][str(pool)]:>9.4f}" for pool in pools
        )
        line += f"{stats['gain_from_deepening']:>+11.4f}"
        print(line)
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
