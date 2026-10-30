"""공동인용 쌍을 학습 산출물로 내보냅니다 (ADR-098).

    uv run python -m models.embedding_tuning.export_cocitation

ADR-097의 학습 쌍은 **인용** 쌍(논문 → 그 논문이 인용한 논문)이었습니다. 인용 과제에서는
OpenAI를 이겼지만 판정 자리(미래 **공동인용** 과제)에서는 동점이었습니다. 학습 목표를 판정
자리와 맞춥니다 — 같은 논문에 함께 인용된 두 논문이 한 쌍입니다.

**규모는 ADR-097과 같게 둡니다(36,554쌍).** 바꾸는 것이 쌍의 정의 하나여야 차이를 그것에
돌릴 수 있습니다.

**인용자마다 고르게 뽑습니다.** 학습 구간 인용자 1,645편의 참고문헌은 중앙값 19편이지만
가장 큰 것은 280편이고, 그 한 편이 공동인용 쌍 39,060개를 만듭니다 - 예산 전체보다 많습니다.
쌍을 전체에서 고르게 뽑으면 서베이 몇 편이 학습을 차지합니다. 그래서 인용자마다 같은 수까지만
뽑고, 그 수는 36,554쌍에 닿는 **가장 작은 값**으로 정합니다(참고문헌이 적은 인용자는 그만큼
못 채우므로 평균 몫보다 커집니다).

**누출.** 인용자는 `split_citers`로 `< 2026-08`만 씁니다 - 미래 과제의 `feature_cut`과
같습니다. 판정 자리의 시드(그 그래프에서 인용 간선 0개)는 학습 구간 인용자에게 인용되지
않았으므로 쌍에 나올 수 없습니다. 그것을 가정하지 않고 **세어서 0인지 확인**하고, 아니면
멈춥니다.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from models.embedding_tuning.export_for_colab import DEFAULT_CUT, write_gzip_jsonl
from models.embedding_tuning.train import SEED, Corpus, parse_month, split_citers
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_label

OUTPUT = Path("exports/colab")
FUTURE_CASES = Path("evals/cases_related_future.json")
TRAIN_PAIRS = 36554  # ADR-097의 인용 쌍 수와 같게
PER_CITER_CAP = 200  # 이 위로는 찾지 않습니다


def cocitation_rows(corpus: Corpus, citers: list[str], *, limit: int, per_citer: int,
                    seed: int) -> list[dict[str, str]]:
    """인용자마다 참고문헌 두 편씩의 쌍을 `per_citer`개까지 뽑고, 전체를 `limit`개로 맞춥니다."""
    rng = random.Random(seed)
    chosen: dict[tuple[str, str], str] = {}
    for citing in citers:
        refs = sorted(corpus.references[citing])
        if len(refs) < 2:
            continue
        pool = [(refs[i], refs[j]) for i in range(len(refs)) for j in range(i + 1, len(refs))]
        for pair in rng.sample(pool, min(per_citer, len(pool))):
            chosen.setdefault(pair, citing)
    pairs = sorted(chosen)
    if len(pairs) > limit:
        pairs = sorted(rng.sample(pairs, limit))
    rows = []
    for left, right in pairs:
        # 순서가 쌍의 뜻을 바꾸지 않으므로 앵커 쪽을 무작위로 둡니다.
        anchor, positive = (left, right) if rng.random() < 0.5 else (right, left)
        rows.append({"anchor": anchor, "positive": positive, "citing": chosen[(left, right)]})
    return rows


def smallest_per_citer(corpus: Corpus, citers: list[str], limit: int) -> int:
    """쌍이 `limit`개 이상 나오는 가장 작은 인용자당 상한.

    중복 쌍은 한 번만 세므로 셈으로 구하지 않고 실제로 뽑아 봅니다.
    """
    for per_citer in range(1, PER_CITER_CAP + 1):
        rows = cocitation_rows(corpus, citers, limit=limit, per_citer=per_citer, seed=SEED)
        if len(rows) >= limit:
            return per_citer
    raise SystemExit(f"인용자당 {PER_CITER_CAP}쌍으로도 {limit}쌍에 닿지 않습니다")


def isolated_seeds(corpus: Corpus, citers: list[str], seeds: set[str]) -> set[str]:
    """학습 구간 그래프에서 인용 간선이 하나도 없는 시드 - 판정 자리의 집단입니다."""
    touched = set(citers)
    for citing in citers:
        touched.update(corpus.references[citing])
    return seeds - touched


def main() -> int:
    parser = argparse.ArgumentParser(description="공동인용 학습 쌍을 내보냅니다.")
    parser.add_argument("--version", default="2026-10-14")
    parser.add_argument("--cut", default=DEFAULT_CUT)
    parser.add_argument("--limit", type=int, default=TRAIN_PAIRS)
    parser.add_argument("--per-citer", type=int, help="비우면 --limit에 닿는 가장 작은 값")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(args.version)
    corpus = Corpus(snap)
    citers = split_citers(corpus, parse_month(args.cut))["train"]
    per_citer = args.per_citer or smallest_per_citer(corpus, citers, args.limit)
    rows = cocitation_rows(corpus, citers, limit=args.limit, per_citer=per_citer, seed=SEED)

    future = json.loads(FUTURE_CASES.read_text(encoding="utf-8-sig"))
    if future["feature_cut"] != month_label(parse_month(args.cut)):
        raise SystemExit(f"자르는 선이 미래 과제와 다릅니다: {future['feature_cut']} != {args.cut}")
    seeds = {case["seed_id"] for case in future["cases"]}
    isolated = isolated_seeds(corpus, citers, seeds)
    in_pairs = {paper for row in rows for paper in (row["anchor"], row["positive"])}
    leaked = sorted(isolated & in_pairs)
    if leaked:
        raise SystemExit(f"판정 자리 시드가 학습 쌍에 있습니다: {leaked[:5]} ({len(leaked)}편)")

    file = write_gzip_jsonl(args.output / "cocite_pairs.jsonl.gz", rows)
    # 압축을 푼 내용으로 셉니다. gzip 헤더에 내보낸 시각이 들어가 파일 해시는 매번 다릅니다.
    with gzip.open(args.output / "cocite_pairs.jsonl.gz", "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    per_citing: dict[str, int] = {}
    for row in rows:
        per_citing[row["citing"]] = per_citing.get(row["citing"], 0) + 1
    manifest: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "adr": "ADR-098",
        "snapshot_version": snap.version,
        "snapshot_papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "task": "two papers cited together by the same earlier paper",
        "split": {"train_citers_before": month_label(parse_month(args.cut)),
                  "train_citers": len(citers), "citers_used": len(per_citing)},
        "sampling": {"limit": args.limit, "per_citer": per_citer, "seed": SEED,
                     "max_pairs_from_one_citer": max(per_citing.values())},
        "leakage": {"future_seeds": len(seeds), "isolated_seeds": len(isolated),
                    "isolated_seeds_in_pairs": len(leaked),
                    "seeds_in_pairs": len(seeds & in_pairs)},
        "papers_in_pairs": len(in_pairs),
        "file": {**file, "content_sha256": digest},
    }
    (args.output / "cocite_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
