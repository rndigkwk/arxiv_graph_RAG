"""임베딩 행렬 둘을 같은 과제에서 **연결 정도 집단별로** 비교합니다. API를 쓰지 않습니다.

    uv run python -m evals.related_study --cases evals/cases_related_future.json \\
        --variants vector --output reports/embedding_openai.json
    uv run python -m evals.related_study --cases evals/cases_related_future.json \\
        --variants vector --embeddings derived/2026-10-14/local-baai-bge-m3.npy \\
        --output reports/embedding_bge_m3.json
    uv run python -m evals.embedding_slice_study \\
        --baseline reports/embedding_openai.json --candidate reports/embedding_bge_m3.json

## 왜 이 과제에서 재는가

[11-5절](../EXPERIMENTS.md)은 **인용 과제**(논문이 인용할 논문 찾기, 365질의)에서 로컬 모델을
쟀고 `bge-base-en-v1.5`가 `text-embedding-3-large`와 **동등**이었습니다(−0.0109, 구간
[−0.0250, +0.0032], 0 포함).

같은 행렬을 **관련 논문 미래 과제**의 간선 0개 집단(1,112 시드)에서 재면 결론이 갈립니다.
점 추정은 거의 같은데(−0.0102) 구간이 2.5배 좁아져 0을 제외합니다. **모델이 거기서 더
나빠진 것이 아니라, 같은 차이를 이제 분해할 수 있는 것**입니다
([ADR-039](../docs/adr/ADR-039-measure-embeddings-where-the-signal-is-only-the-embedding.md)).

이유가 둘입니다 — 시드가 365개에서 1,112개로 늘었고, 그 집단에서는 **그래프가 0점**이라
임베딩이 유일한 신호입니다(ADR-036). 섞인 신호가 없으면 차이가 묻히지 않습니다.

## 집단을 나눠 보는 이유

집계만 보면 두 체제가 섞입니다 - 간선이 있는 시드에서는 그래프 쪽이 답을 끌고 가므로
임베딩 차이가 희석됩니다. ADR-037이 같은 함정을 다룹니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.related_study import paired_bootstrap

METRICS = ("recall_at_k", "hit_at_k", "reciprocal_rank")
OUTPUT = Path("reports/embedding_slice_study.json")


def groups(report: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Split the per-seed rows the way `by_degree_slice` does."""
    out: dict[str, list[dict[str, Any]]] = {
        "no_citation_edges": [], "degree_1_to_4": [], "degree_5_or_more": [],
    }
    for row in report["results"]:
        degree = row.get("citation_degree", 0)
        key = (
            "no_citation_edges" if degree == 0
            else "degree_1_to_4" if degree < 5
            else "degree_5_or_more"
        )
        out[key].append(row)
    return out


def compare(
    baseline: dict[str, Any], candidate: dict[str, Any], *, variant: str = "vector"
) -> dict[str, Any]:
    """Paired intervals per degree group, over the seeds both reports scored."""
    if baseline["cases"] != candidate["cases"]:
        raise ValueError(
            f"사례 수가 다릅니다: {baseline['cases']} vs {candidate['cases']}. "
            "같은 정답으로 돌린 보고서여야 비교가 성립합니다."
        )
    left, right = groups(baseline), groups(candidate)
    result: dict[str, Any] = {}
    for name in left:
        by_seed_left = {row["seed_id"]: row for row in left[name]}
        by_seed_right = {row["seed_id"]: row for row in right[name]}
        shared = sorted(set(by_seed_left) & set(by_seed_right))
        if not shared:
            continue
        entry: dict[str, Any] = {"seeds": len(shared)}
        for metric in METRICS:
            base = [by_seed_left[seed][variant][metric] for seed in shared]
            cand = [by_seed_right[seed][variant][metric] for seed in shared]
            entry[metric] = {
                "baseline": round(statistics.fmean(base), 4),
                "candidate": round(statistics.fmean(cand), 4),
                **paired_bootstrap(base, cand),
            }
        result[name] = entry
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="임베딩 행렬 둘을 집단별로 비교합니다.")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--variant", default="vector")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    by_group = compare(baseline, candidate, variant=args.variant)

    study = {
        "generated_at": datetime.now(UTC).isoformat(),
        "gold": baseline.get("gold"),
        "variant": args.variant,
        "baseline": {
            "report": str(args.baseline).replace("\\", "/"),
            "embeddings": baseline.get("embeddings") or "derived cache (text-embedding-3-large)",
            "dimensions": baseline.get("embedding_dimensions"),
        },
        "candidate": {
            "report": str(args.candidate).replace("\\", "/"),
            "embeddings": candidate.get("embeddings") or "derived cache",
            "dimensions": candidate.get("embedding_dimensions"),
        },
        "by_degree_slice": by_group,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(study, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"{'집단':<22}{'시드':>6}{'지표':>18}{'기준':>9}{'후보':>9}   구간")
    for name, entry in by_group.items():
        for metric in METRICS:
            row = entry[metric]
            mark = "0 제외" if row["excludes_zero"] else "0 포함"
            print(
                f"{name:<22}{entry['seeds']:>6}{metric:>18}"
                f"{row['baseline']:>9.4f}{row['candidate']:>9.4f}   "
                f"{row['mean']:+.4f} [{row['low']:+.4f}, {row['high']:+.4f}] {mark}"
            )
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
