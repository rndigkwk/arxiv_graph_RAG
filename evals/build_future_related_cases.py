"""공동인용이 **지금 얇은** 논문에 대해, 미래를 정답으로 삼는 관련 논문 과제를 만듭니다.

    uv run python -m evals.build_future_related_cases

## 왜 이것인가 — 요청받은 정답은 만들 수 없습니다

[ADR-034](../docs/adr/ADR-034-measure-every-eligible-seed.md)가 **배포 보충 구간**(공동인용
후보가 `top_k`보다 적어 PPR이 채우는 자리, 임베딩 말뭉치의 17.17% = 6,499편)이 평가 밖이라고
적었습니다. 그 구간의 정답을 만들려고 쟀더니 **공동인용으로는 만들 수 없습니다.**

| 사실 | 측정 |
|---|---|
| 그 구간에서 **피인용이 0편**인 논문 | 5,941 / 6,499 (91.4%) |
| 인용 간선이 **아예 없는** 논문 | 2,958 / 6,499 (45.5%) |
| W4(미래) 공동인용이 전체 스냅샷 공동인용의 부분집합인가 | 2,000/2,000 (구조적) |
| 그 구간에서 미래 정답 2편 이상을 얻는 논문 | **210 / 6,499** |

공동인용은 **공통 인용자**를 요구합니다. 인용하는 논문이 없으면 현재도 미래도 공동인용이
없습니다 — W4 인용자도 스냅샷 안에 있으므로, 전체에서 후보가 4편 이하면 W4에서도 4편
이하입니다. 다른 관계로 정답을 세우는 길도 둘 다 막혀 있습니다.

- **직접 인용**: Cypher 한 줄이 답하므로 모델이 겨룰 과제가 아닙니다
  ([ADR-027](../docs/adr/ADR-027-cocitation-gold-for-the-related-slot.md)). 게다가 이 구간의
  3,512편은 말뭉치 안에서 인용하는 논문도 0편입니다.
- **텍스트 유사도**: `vector`·어댑터가 **평가 대상**입니다. 그것을 정답으로 쓰면 순환입니다.

그리고 2,958편은 인용 간선이 없어 PPR 점수가 양수로 나오지 않습니다 — 그 자리는 "PPR이
채운다"가 아니라 **패널이 빈다**입니다. 어떤 방법도 바꿀 수 없습니다.

## 그래서 무엇을 만드는가

**희박함을 "예측 시점"에서 정의하고 미래를 정답으로 씁니다.** W3(= `test_cut` 이전 인용자가
만든 공동인용)에서 후보가 `top_k`보다 적은 시드를 고르고, 정답은 **W3에서 보이는 파트너 ∪ W4에서
새로 생긴 파트너**입니다.

[ADR-031](../docs/adr/ADR-031-the-model-that-wins-is-the-forecast.md)의 모델 과제 판별 기준
셋을 그대로 통과합니다 — 라벨이 **미래**라 어떤 질의도 계산하지 못하고, 시드가 1,377개이고,
sklearn 수준입니다.

**정답에 W3 파트너를 포함하는 이유**가 있습니다. 새로 생긴 쌍만 정답으로 두면 현재 공동인용으로
정렬하는 방법이 **정의상 0점**입니다(1,132 시드에서 겹침 0.0000). 그것은 예측 지표로는 맞지만
패널 지표로는 틀립니다 — 이미 함께 인용되는 논문을 보여 주는 것은 좋은 추천입니다. 둘을 합치면
질의가 **동어반복으로 이기지도, 정의상 지지도** 않습니다(정답의 17.9%만 예측 시점에 보입니다).

## 이 정답으로 평가할 때의 함정

**전체 스냅샷 그래프로 재면 미래가 새어 들어갑니다.** W4 인용자가 스냅샷 안에 있으므로,
`evals/related_study.py`의 `Graph`(전체 스냅샷)로 이 정답을 재면 `cocited` 변형이 **정답을
그대로 봅니다.** 반드시 `feature_cut` 이전 인용자만으로 만든 그래프를 써야 합니다. 그래서
데이터셋이 `feature_cut`을 적어 두고 테스트가 그것을 확인합니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics
from pathlib import Path
from typing import Any

from evals.build_related_cases import load_tables
from models.link_prediction.dataset import adjacency_of, build_windows
from models.link_prediction.train import DEFAULT_TEST_CUT, DEFAULT_TRAIN_CUT, parse_month
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import Snapshot, month_label

OUTPUT = Path("evals/cases_related_future.json")
# 배포가 보충을 시작하는 선과 같습니다(`chatbot.rank_related_papers`의 `top_k`). 그 선보다
# 후보가 적은 시드가 "증거가 얇은" 시드입니다.
TOP_K = 5
MIN_GOLD = 2
# 정답이 크면 recall@5가 움직이지 않습니다 - ADR-027이 중앙값 35편에서 겪은 함정입니다.
MAX_GOLD = 10


def build(snap: Snapshot, *, train_cut: int, test_cut: int) -> dict[str, Any]:
    windows = build_windows(snap, train_cut=train_cut, test_cut=test_cut)
    future = adjacency_of(windows.test_labels)
    visible = windows.test_feature
    tables = load_tables(snap)
    meta = tables["meta"]

    cases: list[dict[str, Any]] = []
    # 왜 떨어졌는지도 세어 둡니다. "1,377편을 골랐다"만 적으면 모수를 되짚을 수 없습니다.
    rejected: collections.Counter[str] = collections.Counter()
    for seed in sorted(meta):
        seen = sorted(paper for paper in visible.get(seed, ()) if paper in meta)
        if len(seen) >= TOP_K:
            rejected["not_sparse_at_prediction_time"] += 1
            continue
        new = sorted(paper for paper in future.get(seed, ()) if paper in meta)
        gold = sorted(set(seen) | set(new))
        if len(gold) < MIN_GOLD:
            rejected["gold_too_small"] += 1
            continue
        if len(gold) > MAX_GOLD:
            rejected["gold_too_large"] += 1
            continue
        cases.append({
            "id": f"future-{len(cases) + 1:04d}",
            "category": "related_future",
            "seed_id": seed,
            "seed_title": " ".join((meta[seed].get("title") or "").split()),
            "expected_related_ids": gold,
            # 예측 시점에 이미 보이던 정답. 이것으로 "공짜로 얻는 부분"을 분리해 볼 수 있습니다.
            "visible_at_prediction_time": seen,
            # 미래에 새로 생긴 정답. 둘의 합이 정답입니다.
            "new_in_label_window": new,
            "gold_size": len(gold),
        })

    sizes = [case["gold_size"] for case in cases]
    free = [
        len(case["visible_at_prediction_time"]) / case["gold_size"] for case in cases
    ]
    return {
        "dataset_version": 1,
        "snapshot_version": snap.version,
        "snapshot_cut_month": snap.manifest["cut"]["month"],
        "source": f"snapshots/{snap.version}/manifest.json",
        "label_method": "w3_sparse_future_cocitation",
        # **평가가 반드시 지켜야 하는 선입니다.** 이 달 이후의 인용자가 만든 공동인용이
        # 정답이므로, 그 인용자를 포함한 그래프로 재면 정답을 보고 답하는 것이 됩니다.
        "feature_cut": month_label(test_cut),
        "label_window": f"{month_label(test_cut)} 이후 인용자가 만든 새 공동인용",
        "top_k": TOP_K,
        "task": (
            "공동인용 증거가 예측 시점에 얇은 논문(후보 5편 미만)에 대해 관련 논문을 "
            "순위로 돌려주는 과제입니다. 정답은 예측 시점에 보이던 파트너와 그 뒤 새로 "
            "생긴 파트너의 합이고, 어떤 질의도 뒤쪽을 계산할 수 없습니다."
        ),
        "why_not_the_deployed_fill_slice": (
            "배포 보충 구간(전체 그래프에서 후보 5편 미만, 6,499편)은 공동인용으로 정답을 "
            "만들 수 없습니다 - 그중 5,941편(91.4%)이 피인용 0편이고, W4 공동인용은 전체 "
            "스냅샷 공동인용의 부분집합이라 미래에도 생기지 않습니다(정답 2편 이상을 얻는 "
            "시드가 210편뿐). 2,958편은 인용 간선이 아예 없어 PPR 점수도 양수가 아니고, "
            "그 자리는 패널이 빕니다. 이 데이터셋은 그 구간이 아니라 **예측 시점에 얇은** "
            "시드를 다룹니다."
        ),
        "leakage_warning": (
            "전체 스냅샷 그래프로 재면 안 됩니다. W4 인용자가 스냅샷 안에 있어서 "
            "공동인용 변형이 정답을 그대로 봅니다. feature_cut 이전 인용자만으로 만든 "
            "그래프를 쓰세요."
        ),
        "eligible_seeds": len(cases),
        "rejected": dict(rejected),
        "gold_size_median": statistics.median(sizes) if sizes else 0,
        "visible_share_mean": round(statistics.fmean(free), 4) if free else 0.0,
        "recall_at_k_ceiling": (
            round(statistics.fmean(min(TOP_K, size) / size for size in sizes), 4)
            if sizes else 0.0
        ),
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="미래를 정답으로 쓰는 관련 논문 과제를 만듭니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    parser.add_argument("--train-cut", default=DEFAULT_TRAIN_CUT, type=parse_month)
    parser.add_argument("--test-cut", default=DEFAULT_TEST_CUT, type=parse_month)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    dataset = build(snap, train_cut=args.train_cut, test_cut=args.test_cut)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sizes = [case["gold_size"] for case in dataset["cases"]]
    print(json.dumps({
        "output": str(args.output).replace("\\", "/"),
        "snapshot": dataset["snapshot_version"],
        "feature_cut": dataset["feature_cut"],
        "cases": len(dataset["cases"]),
        "rejected": dataset["rejected"],
        "gold_size": {
            "min": min(sizes), "median": statistics.median(sizes), "max": max(sizes),
            "mean": round(statistics.fmean(sizes), 2),
        },
        "visible_share_mean": dataset["visible_share_mean"],
        "recall_at_k_ceiling": dataset["recall_at_k_ceiling"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
