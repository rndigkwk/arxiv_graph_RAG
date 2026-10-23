"""재정렬기를 **코사인이 약한 자리에서만** 쓸 수 있는지 잽니다(ADR-061).

    uv run python -m evals.rerank_selective --output reports/rerank_selective.json

[ADR-057](../docs/adr/ADR-057-fine-tune-the-reranker-on-cocitation.md)이 미세조정 재정렬기를
기각했는데, 사례별로 열어 보면 **균일하게 나쁜 것이 아닙니다** — 간선 0개 집단에서 139시드는
이기고(평균 +0.1724) 230시드는 집니다(−0.1862). 그래서 남은 질문이 하나입니다.

> **정답을 보지 않고 그 139를 골라낼 수 있는가.**

규칙의 모양을 미리 제한했습니다 — **특징 하나 + 문턱 하나**입니다. 학습된 선택기는 "코사인을
이겨야 하는 또 하나의 모델"이고 740시드로 학습하면 재는 것이 신호가 아니라 과적합입니다.

진단 2/3에서 특징과 문턱을 고르고, **최종 1/3은 고르는 동안 보지 않습니다.** 여섯 특징에
문턱을 훑으면 우연히 좋아 보이는 규칙이 반드시 하나 나오므로, 그것을 거르는 조각이 필요합니다
(ADR-059와 같은 규율).

LLM도 API도 쓰지 않습니다. 코사인은 스냅샷 임베딩에서, 재정렬 점수는 파일에서 옵니다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from evals.related_study import (
    GBM_POOL,
    Graph,
    paired_bootstrap,
    pool_candidates,
    rank_by_score,
)
from models.link_prediction.train import parse_month
from pipeline import snapshot as snapshot_module
from pipeline.console import use_utf8_stdout

CASES = Path("evals/cases_related_future.json")
SCORES = Path("derived/2026-10-14/rerank-baai-bge-reranker-v2-m3-cocite-ft.jsonl.gz")
OUTPUT = Path("reports/rerank_selective.json")
TOP_K = 5
SEED = 20261023
FINAL_SHARE = 1 / 3
# ADR-061이 미리 적은 여섯 개입니다. 고르고 나서 적으면 그건 고른 것이 아닙니다.
FEATURES = (
    "cos_top1", "cos_margin_5_6", "cos_spread_1_5", "rr_top1", "agree_at_5", "pool_size",
)
# 문턱은 진단 조각의 분위수에서 가져옵니다 - 값의 범위를 모르는 채 상수를 적을 수 없습니다.
QUANTILES = tuple(round(0.05 * step, 2) for step in range(1, 20))
METRICS = ("recall_at_k", "hit_at_k", "reciprocal_rank")


def read_scores(path: Path) -> dict[str, tuple[list[str], np.ndarray]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]
    return {
        row["seed"]: (list(row["candidates"]), np.asarray(row["scores"], dtype=np.float64))
        for row in rows
    }


def score_at_k(ranked: list[str], gold: set[str], k: int) -> dict[str, float]:
    """`related_study`가 쓰는 세 지표와 **같은 정의**여야 비교가 성립합니다."""
    top = ranked[:k]
    hits = [index for index, paper in enumerate(top, start=1) if paper in gold]
    return {
        "recall_at_k": len([p for p in top if p in gold]) / len(gold) if gold else 0.0,
        "hit_at_k": 1.0 if hits else 0.0,
        "reciprocal_rank": 1.0 / hits[0] if hits else 0.0,
    }


def features_of(
    graph: Graph, seed: str, scores: np.ndarray, candidates: list[str],
    rr_values: np.ndarray, cos_ranked: list[str], rr_ranked: list[str],
) -> dict[str, float]:
    """추론 시점에 계산되는 것만 씁니다. 정답은 들어오지 않습니다."""
    order = np.sort(scores)[::-1]
    # 시드 자신이 1등이므로 한 칸 밀어 읽습니다.
    top = order[1 : TOP_K + 2]
    return {
        "cos_top1": float(top[0]),
        "cos_margin_5_6": float(top[TOP_K - 1] - top[TOP_K]) if top.size > TOP_K else 0.0,
        "cos_spread_1_5": float(top[0] - top[TOP_K - 1]) if top.size >= TOP_K else 0.0,
        "rr_top1": float(rr_values.max()) if rr_values.size else 0.0,
        "agree_at_5": float(len(set(cos_ranked[:TOP_K]) & set(rr_ranked[:TOP_K]))),
        "pool_size": float(len(candidates)),
    }


def build(graph: Graph, cases: list[dict[str, Any]],
          scored: dict[str, tuple[list[str], np.ndarray]]) -> list[dict[str, Any]]:
    """시드별로 두 순위의 지표와 특징을 모읍니다."""
    rows = []
    for case in cases:
        seed, gold = case["seed_id"], set(case["expected_related_ids"])
        if seed not in graph.position:
            continue
        degree = len(graph.cites.get(seed, ())) + len(graph.cited_by.get(seed, ()))
        scores = graph.vectors @ graph.vectors[graph.position[seed]]
        entry = scored.get(seed)
        if entry is None:
            raise SystemExit(
                f"{seed}의 재정렬 점수가 없습니다({SCORES}). 같은 정답 파일로 내보낸 "
                "것인지 확인하세요."
            )
        candidates, values = entry
        expected = pool_candidates(graph, seed, scores, pool=GBM_POOL)
        if set(candidates) - set(expected):
            raise SystemExit(f"{seed}의 점수 파일이 다른 후보 집합입니다.")
        cos_ranked = rank_by_score(graph, scores, seed, TOP_K)
        rr_ranked = [candidates[i] for i in np.argsort(-values)[:TOP_K]]
        rows.append({
            "seed_id": seed,
            "citation_degree": degree,
            "vector": score_at_k(cos_ranked, gold, TOP_K),
            "rerank": score_at_k(rr_ranked, gold, TOP_K),
            "features": features_of(
                graph, seed, scores, candidates, values, cos_ranked, rr_ranked
            ),
        })
    return rows


def split(rows: list[dict[str, Any]]) -> None:
    """간선 0개 집단 안에서 진단/최종을 나눕니다. 바가 그 집단에 적혀 있습니다."""
    rng = random.Random(SEED)
    cohort = [row for row in rows if row["citation_degree"] == 0]
    order = list(cohort)
    rng.shuffle(order)
    final = round(len(order) * FINAL_SHARE)
    for index, row in enumerate(order):
        row["split"] = "final" if index < final else "diagnostic"
    for row in rows:
        row.setdefault("split", "outside_cohort")


def apply_rule(rows: list[dict[str, Any]], feature: str, threshold: float,
               above: bool) -> list[dict[str, float]]:
    """규칙이 고른 순위의 지표입니다. 고르지 않은 시드는 코사인 그대로입니다."""
    chosen = []
    for row in rows:
        use_rerank = (row["features"][feature] >= threshold) == above
        chosen.append(row["rerank"] if use_rerank else row["vector"])
    return chosen


def gain(rows: list[dict[str, Any]], feature: str, threshold: float,
         above: bool, metric: str) -> dict[str, Any]:
    picked = apply_rule(rows, feature, threshold, above)
    base = [row["vector"][metric] for row in rows]
    return paired_bootstrap(base, [entry[metric] for entry in picked])


def search(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """진단 조각에서 특징·문턱·방향을 훑습니다. **여기서 고른 것은 결론이 아닙니다.**"""
    found = []
    for feature in FEATURES:
        values = sorted(row["features"][feature] for row in rows)
        for quantile in QUANTILES:
            threshold = float(
                np.quantile(values, quantile)
            )
            for above in (True, False):
                used = sum(
                    1 for row in rows
                    if (row["features"][feature] >= threshold) == above
                )
                if used in (0, len(rows)):
                    continue  # 전부 또는 아무도 - 규칙이 아닙니다.
                interval = gain(rows, feature, threshold, above, "recall_at_k")
                found.append({
                    "feature": feature,
                    "threshold": round(threshold, 6),
                    "above": above,
                    "seeds_using_rerank": used,
                    "recall_at_k": interval,
                })
    found.sort(key=lambda entry: -entry["recall_at_k"]["mean"])
    return found


def confirm(rows: list[dict[str, Any]], rule: dict[str, Any]) -> dict[str, Any]:
    """최종 조각에서 **한 번만** 봅니다. 바의 네 조건을 그대로 적용합니다."""
    out = {
        metric: gain(rows, rule["feature"], rule["threshold"], rule["above"], metric)
        for metric in METRICS
    }
    out["seeds_using_rerank"] = sum(
        1 for row in rows
        if (row["features"][rule["feature"]] >= rule["threshold"]) == rule["above"]
    )
    out["seeds"] = len(rows)
    # 바 1: recall이 올라가고 구간이 0을 제외. 바 2: 나머지 둘이 내려가지 않음.
    beats = out["recall_at_k"]["excludes_zero"] and out["recall_at_k"]["mean"] > 0
    keeps = all(
        not (out[metric]["excludes_zero"] and out[metric]["mean"] < 0)
        for metric in ("hit_at_k", "reciprocal_rank")
    )
    out["passes_bar_1_recall_up"] = bool(beats)
    out["passes_bar_2_others_not_down"] = bool(keeps)
    return out


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="재정렬기를 코사인이 약한 자리에서만 쓸 수 있는지 잽니다(ADR-061)."
    )
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--scores", type=Path, default=SCORES)
    parser.add_argument("--version", default="")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = dataset["cases"] if isinstance(dataset, dict) else dataset
    cut = dataset.get("feature_cut") if isinstance(dataset, dict) else None
    if not cut:
        raise SystemExit(
            f"{args.cases}에 feature_cut이 없습니다. 미래 라벨 과제에서는 필수입니다"
            "(ADR-035)."
        )
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    # **`related_study`와 같은 함수로 자릅니다.** `int("202608")`을 넘기면 월 색인과
    # 단위가 달라 간선이 전부(또는 하나도) 잘립니다 - 2026-10-23에 그렇게 돌렸습니다.
    graph = Graph(snap, feature_cut=parse_month(cut))
    rows = build(graph, cases, read_scores(args.scores))
    split(rows)

    diagnostic = [row for row in rows if row["split"] == "diagnostic"]
    final = [row for row in rows if row["split"] == "final"]
    adjacent = [row for row in rows if 1 <= row["citation_degree"] <= 4]
    ranked = search(diagnostic)
    best = ranked[0] if ranked else None

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "feature_cut": cut,
        "scores": str(args.scores).replace("\\", "/"),
        "pool": GBM_POOL,
        "top_k": TOP_K,
        "seed": SEED,
        "features_considered": list(FEATURES),
        "cohort": {
            "no_citation_edges": sum(1 for row in rows if row["citation_degree"] == 0),
            "diagnostic": len(diagnostic),
            "final": len(final),
            "degree_1_to_4": len(adjacent),
        },
        # 재정렬기가 어디서 이기고 지는지. 이 분포가 이 측정의 출발점입니다.
        "per_seed_outcome": {
            name: {
                "rerank_wins": sum(
                    1 for row in group if row["rerank"]["recall_at_k"]
                    > row["vector"]["recall_at_k"]
                ),
                "ties": sum(
                    1 for row in group if row["rerank"]["recall_at_k"]
                    == row["vector"]["recall_at_k"]
                ),
                "rerank_loses": sum(
                    1 for row in group if row["rerank"]["recall_at_k"]
                    < row["vector"]["recall_at_k"]
                ),
            }
            for name, group in (("diagnostic", diagnostic), ("final", final))
        },
        # **몇 개를 훑었는지 적습니다.** "최선이 0이었다"는 그 수를 알아야 읽힙니다 -
        # 셋을 보고 못 찾은 것과 수백을 보고 못 찾은 것은 다른 결과입니다.
        "rules_searched": len(ranked),
        "diagnostic_top_rules": ranked[:10],
        "note": (
            "diagnostic_top_rules는 **결론이 아닙니다**. 여섯 특징에 문턱을 훑으면 우연히 "
            "좋아 보이는 규칙이 반드시 하나 나옵니다. 판정은 final 조각의 confirmed입니다."
        ),
    }
    if best is not None:
        report["chosen_rule"] = {
            key: best[key] for key in ("feature", "threshold", "above")
        }
        report["diagnostic_gain"] = best["recall_at_k"]
        report["confirmed"] = confirm(final, best)
        report["adjacent_cohort"] = (
            confirm(adjacent, best) if adjacent else "집단이 비어 있습니다"
        )
        passes = (
            report["confirmed"]["passes_bar_1_recall_up"]
            and report["confirmed"]["passes_bar_2_others_not_down"]
            and isinstance(report["adjacent_cohort"], dict)
            and report["adjacent_cohort"]["passes_bar_2_others_not_down"]
        )
        report["verdict"] = "채택 가능" if passes else "바를 넘지 못했습니다"
    else:
        report["verdict"] = "규칙 후보가 없습니다"

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(
        {key: report[key] for key in
         ("cohort", "per_seed_outcome", "chosen_rule", "diagnostic_gain",
          "confirmed", "verdict") if key in report},
        ensure_ascii=False, indent=2,
    ))
    median = statistics.median(
        row["features"]["cos_top1"] for row in rows
    ) if rows else 0.0
    print(f"cos_top1 중앙값 {median:.4f} | 판정: {report['verdict']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
