"""재정렬기 미세조정용 쌍을 내보냅니다 — **누출을 구조로 막고, 막혔는지 셉니다.**

    uv run python -m evals.export_rerank_training --max-seeds 2000

[ADR-057](../docs/adr/ADR-057-fine-tune-the-reranker-on-cocitation.md)이 바를 적었고 이
스크립트가 그 학습 데이터를 만듭니다. Aura도 OpenAI도 부르지 않습니다.

## 자르는 선이 둘입니다 — 이것이 전부입니다

| 창 | 인용자 | 쓰임 |
|---|---|---|
| w1 | < **2026-07** | **학습 피처 그래프** — 음성을 여기서 뽑습니다 |
| w2 | [2026-07, 2026-08) | **학습 라벨**(양성) |
| w3 | < 2026-08 | 평가 피처 그래프 |
| w4 | ≥ 2026-08 | 평가 정답 |

**그래프를 2026-07로 자릅니다.** 평가는 2026-08로 자르는데, 학습에 그 그래프를 쓰면 음성을
뽑는 풀이 **w2의 공동인용을 이미 보고 있습니다** — 양성이 음성 후보에서 미리 빠지므로 과제가
쉬워지고, 그 쉬움은 평가에 없습니다. 같은 함정을 다른 자리에서 적은 것이
[ADR-035](../docs/adr/ADR-035-the-fill-slice-cannot-be-labelled.md)입니다.

`test_labels = cocitation_pairs(w4) − cocitation_pairs(w3)`이고 w2 ⊂ w3이므로 **w2의 쌍은
평가 정답에서 빠집니다.** 그래서 겹침이 0인 것이 설계이고, 그래도 **세어서 확인합니다** —
구조를 믿고 세지 않으면 구조가 바뀔 때 조용히 샙니다.

## 음성은 배포가 보는 것과 같게 뽑습니다

음성은 **후보 풀에서 양성이 아닌 논문**입니다(`벡터 상위 50 ∪ 보이는 공동인용`).
무작위로 뽑으면 쉬운 문제를 푸는 모델이 되고, 그 쉬움은 배포에 없습니다. 풀은
[`pool_candidates`](related_study.py) **한 벌**을 부릅니다 — 두 벌이 되면 학습과
평가가 다른 후보를 보게 됩니다(ADR-029·050).

## 추론이 보는 것을 그대로 학습합니다 — 재 보고 고쳤습니다

처음에는 양성을 **전체 공동인용**에서 뽑고 음성을 1:1로 맞췄습니다. 재 보니 틀렸습니다.

| | 값 |
|---|---:|
| 풀 크기 | p50 **49** |
| 전체 양성 | p50 26, 평균 35.5 |
| **풀 안 양성** | p50 **2**, 평균 3.8 |
| 풀 안 양성이 0인 시드 | **19.0%** |

**추론에서 재정렬기가 보는 것은 49편 중 양성 2편(4%)** 입니다. 풀 밖 양성으로 학습하면
**추론에서 볼 일이 없는 쌍**을 가르치고, 1:1은 배포 비율(약 1:12)과 다른 사전 확률을
심습니다.

그래서 기본값은 **풀 전체**입니다 — 시드마다 `풀 ∩ 양성`을 1로, 나머지 풀을 0으로 냅니다.
표본 손잡이가 없는 것이 설계입니다. 풀 안 양성이 0인 시드는 **빼고 셉니다**(가르칠 것이
없습니다). 그 19%가 평가 상한 0.2690의 다른 얼굴입니다.

`--positives all`과 `--negatives-per-positive`로 예전 방식도 낼 수 있게 두었지만, 기본이
아닌 이유를 위 표가 말합니다.

[ADR-012](../docs/adr/ADR-012-graph-hard-negatives.md)가 같은 설계를 임베딩 어댑터에 썼다가
**기각됐습니다.** 다른 모델이므로 닫혀 있지는 않지만 선례로 적어 둡니다.

## 텍스트는 굳힌 말뭉치에서 가져옵니다

밖에서 다시 만들면 재는 것이 모델이 아니라 **전처리 차이**입니다(ADR-039). 텍스트가 없는
논문은 **빼고 셉니다** — 조용히 두면 빈 문자열로 학습합니다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.export_rerank_pairs import corpus_text
from evals.related_study import GBM_POOL, Graph, pool_candidates
from models.link_prediction.dataset import build_windows
from models.link_prediction.train import parse_month
from pipeline import snapshot as snapshot_module

OUTPUT = Path("exports/colab")
# 학습은 w1에서, 평가는 w3에서. 두 선이 다른 것이 이 스크립트의 요지입니다.
TRAIN_CUT = "2026-07"
TEST_CUT = "2026-08"
# 양성 하나에 음성 몇 개. **0이 기본이고 "풀 전체"를 뜻합니다** - 추론이 보는 비율
# (약 1:12)을 그대로 씁니다. 재 보고 1:1에서 바꿨습니다(모듈 docstring의 표).
NEGATIVES_PER_POSITIVE = 0
SEED = 20261021


def positives_by_seed(labels: set[tuple[str, str]]) -> dict[str, set[str]]:
    """무순서 쌍을 양방향 인접으로 펼칩니다. 재정렬은 시드가 있는 과제입니다."""
    shaped: dict[str, set[str]] = {}
    for left, right in labels:
        shaped.setdefault(left, set()).add(right)
        shaped.setdefault(right, set()).add(left)
    return shaped


def build(
    graph: Graph,
    positives: dict[str, set[str]],
    texts: dict[str, str],
    *,
    pool: int,
    negatives_per_positive: int,
    max_seeds: int,
    rng: random.Random,
    positives_from: str = "pool",
    max_positives_per_seed: int = 0,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """시드마다 양성과 **풀에서 뽑은** 음성을 짝지어 냅니다."""
    usable_seeds = sorted(
        seed for seed in positives
        if seed in graph.position and seed in texts
    )
    rng.shuffle(usable_seeds)
    if max_seeds:
        usable_seeds = usable_seeds[:max_seeds]

    rows: list[dict[str, Any]] = []
    per_seed_positives: list[int] = []
    per_seed_in_pool: list[int] = []
    counts = {
        "seeds": 0,
        "positives": 0,
        "negatives": 0,
        "seeds_without_positive_text": 0,
        "seeds_without_positive_in_pool": 0,
        "seeds_without_negative": 0,
        "positives_without_text": 0,
    }
    for seed in sorted(usable_seeds):
        gold = {paper for paper in positives[seed] if paper in texts}
        counts["positives_without_text"] += len(positives[seed]) - len(gold)
        if not gold:
            counts["seeds_without_positive_text"] += 1
            continue
        per_seed_positives.append(len(gold))
        scores = graph.vectors @ graph.vectors[graph.position[seed]]
        pooled = pool_candidates(graph, seed, scores, pool=pool)
        in_pool = {paper for paper in pooled if paper in gold}
        per_seed_in_pool.append(len(in_pool))
        if positives_from == "pool":
            # **추론이 보는 것만 가르칩니다.** 풀 밖 양성은 모델이 볼 일이 없는 쌍입니다.
            gold = in_pool
            if not gold:
                counts["seeds_without_positive_in_pool"] += 1
                continue
        if max_positives_per_seed and len(gold) > max_positives_per_seed:
            gold = set(rng.sample(sorted(gold), max_positives_per_seed))
        # **양성을 음성에서 뺍니다.** 같은 쌍이 두 라벨로 나가면 학습이 무너집니다.
        candidates = [
            paper for paper in pooled
            if paper not in gold and paper != seed and paper in texts
        ]
        if not candidates:
            counts["seeds_without_negative"] += 1
            continue
        if negatives_per_positive:
            wanted = min(len(candidates), len(gold) * negatives_per_positive)
            chosen = rng.sample(candidates, wanted)
        else:
            # 기본값: 풀 전체. 표본 손잡이가 없는 것이 설계입니다.
            chosen = candidates
        rows.append({
            "seed": seed,
            "positives": sorted(gold),
            "negatives": sorted(chosen),
        })
        counts["seeds"] += 1
        counts["positives"] += len(gold)
        counts["negatives"] += len(chosen)
    for label, values in (("positives_per_seed", per_seed_positives),
                          ("positives_in_pool_per_seed", per_seed_in_pool)):
        if not values:
            continue
        ordered = sorted(values)
        counts[f"{label}_p50"] = ordered[len(ordered) // 2]
        counts[f"{label}_p95"] = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
        counts[f"{label}_max"] = ordered[-1]
    return rows, counts


def leak_check(
    rows: list[dict[str, Any]], test_labels: set[tuple[str, str]]
) -> dict[str, Any]:
    """내보낸 쌍이 **평가 정답에 들어 있지 않은지** 셉니다.

    설계상 0이어야 합니다(w2 ⊂ w3). 그래도 세는 이유는, 구조를 믿고 세지 않으면 창을
    고칠 때 조용히 새기 때문입니다.
    """
    def pair(left: str, right: str) -> tuple[str, str]:
        return (left, right) if left <= right else (right, left)

    positives = {
        pair(row["seed"], other) for row in rows for other in row["positives"]
    }
    negatives = {
        pair(row["seed"], other) for row in rows for other in row["negatives"]
    }
    return {
        "exported_positive_pairs": len(positives),
        "exported_negative_pairs": len(negatives),
        "positives_in_test_labels": len(positives & test_labels),
        # 음성이 평가 정답에 있는 것은 **누출이 아니라 라벨 잡음**입니다 - 그 쌍은 미래에
        # 실제로 함께 인용되는데 학습에서 "아니다"로 가르칩니다. 0일 수 없고, 세어 둡니다.
        "negatives_in_test_labels": len(negatives & test_labels),
        "positive_negative_overlap": len(positives & negatives),
    }


def write(rows: list[dict[str, Any]], texts: dict[str, str], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    pairs_path = out / "rerank_training_pairs.jsonl.gz"
    texts_path = out / "rerank_training_texts.jsonl.gz"
    needed = {row["seed"] for row in rows}
    for row in rows:
        needed.update(row["positives"])
        needed.update(row["negatives"])
    with gzip.open(pairs_path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    with gzip.open(texts_path, "wt", encoding="utf-8", newline="\n") as handle:
        for arxiv_id in sorted(needed):
            handle.write(
                json.dumps({"arxiv_id": arxiv_id, "text": texts[arxiv_id]},
                           ensure_ascii=False) + "\n"
            )
    return {
        "pairs": str(pairs_path).replace("\\", "/"),
        "texts": str(texts_path).replace("\\", "/"),
        "papers": len(needed),
        "pairs_bytes": pairs_path.stat().st_size,
        "texts_bytes": texts_path.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="재정렬기 미세조정용 쌍을 내보냅니다(누출을 구조로 막습니다)."
    )
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    parser.add_argument("--train-cut", default=TRAIN_CUT)
    parser.add_argument("--test-cut", default=TEST_CUT)
    parser.add_argument("--pool", type=int, default=GBM_POOL)
    parser.add_argument("--positives", choices=("pool", "all"), default="pool",
                        help="pool이면 풀 안 양성만 - 추론이 보는 것과 같습니다")
    parser.add_argument("--negatives-per-positive", type=int,
                        default=NEGATIVES_PER_POSITIVE,
                        help="0이면 풀 전체를 음성으로 씁니다(기본)")
    parser.add_argument("--max-positives-per-seed", type=int, default=0,
                        help="0이면 제한 없음. 분포를 보고 정합니다")
    parser.add_argument("--max-seeds", type=int, default=2000,
                        help="0이면 전부. 작게 시작해 실측 시간을 보고 늘립니다(ADR-057)")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    train_cut, test_cut = parse_month(args.train_cut), parse_month(args.test_cut)
    if not train_cut < test_cut:
        parser.error("train-cut은 test-cut보다 앞서야 합니다")

    snap = (
        snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    )
    windows = build_windows(snap, train_cut=train_cut, test_cut=test_cut)
    # **그래프를 학습 선으로 자릅니다.** 평가 선으로 자르면 음성 풀이 w2를 미리 봅니다.
    graph = Graph(snap, feature_cut=train_cut)
    print(f"그래프를 {args.train_cut} 이전으로 잘랐습니다: 간선 {graph.dropped_edges}개 제외")

    texts = corpus_text()
    rows, counts = build(
        graph,
        positives_by_seed(windows.train_labels),
        texts,
        pool=args.pool,
        negatives_per_positive=args.negatives_per_positive,
        max_seeds=args.max_seeds,
        rng=random.Random(args.seed),
        positives_from=args.positives,
        max_positives_per_seed=args.max_positives_per_seed,
    )
    leaks = leak_check(rows, windows.test_labels)
    written = write(rows, texts, args.out)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot": snap.version,
        "train_cut": args.train_cut,
        "test_cut": args.test_cut,
        "pool": args.pool,
        "positives_from": args.positives,
        "negatives_per_positive": args.negatives_per_positive,
        "max_seeds": args.max_seeds,
        "max_positives_per_seed": args.max_positives_per_seed,
        "random_seed": args.seed,
        "dropped_edges_outside_train_window": graph.dropped_edges,
        "window_counts": windows.counts,
        "counts": counts,
        "leak_check": leaks,
        "files": written,
        "note": (
            "그래프를 train_cut으로 잘라 음성 풀이 학습 라벨 창을 미리 보지 않게 합니다. "
            "양성은 build_windows의 train_labels(피처 그래프에 없던 쌍)이고 평가 정답과 "
            "구조적으로 분리됩니다 - 그래도 세어서 확인합니다. 음성이 평가 정답에 있는 것은 "
            "누출이 아니라 라벨 잡음이므로 따로 셉니다. 텍스트는 굳힌 말뭉치 그대로입니다."
        ),
    }
    manifest_path = args.out / "rerank_training_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"\n시드 {counts['seeds']} / 양성 {counts['positives']} / 음성 {counts['negatives']}")
    print(f"논문 {written['papers']}편, 쌍 {written['pairs_bytes'] / 1e6:.1f}MB "
          f"+ 텍스트 {written['texts_bytes'] / 1e6:.1f}MB")
    print("\n누출 검사")
    for key, value in leaks.items():
        mark = "  " if key != "positives_in_test_labels" else "**"
        print(f" {mark} {key}: {value}")
    if leaks["positives_in_test_labels"]:
        raise SystemExit(
            "양성이 평가 정답에 들어 있습니다. 창 설정이 틀렸습니다 - 내보내지 않습니다."
        )
    print(f"\n{manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
