"""공동인용 링크 예측 기준선 사다리를 학습하고 평가합니다.

    uv run --extra ml python -m models.link_prediction.train
    uv run --extra ml python -m models.link_prediction.train --max-positives 60000

고정한 스냅샷만 읽습니다. 살아 있는 DB를 쓰면 Semantic Scholar 인용 보강이 과거 논문 쌍에도
공동인용을 역채움해서, 학습할 때 네거티브였던 쌍이 평가할 때 포지티브로 바뀝니다. 같은 이유로
스냅샷 버전과 papers sha256을 실행 기록에 함께 남깁니다.

세 가지를 함께 보고합니다. 하드 네거티브 성능, 같은 모델의 무작위 네거티브 성능, 그리고 앵커별
순위 지표입니다. 무작위 네거티브 점수를 따로 적는 이유는, 그 숫자가 얼마나 쉽게 부풀려지는지를
같은 표에서 보여 주기 위해서입니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from models.link_prediction import dataset as dataset_module
from models.link_prediction import metrics as metrics_module
from models.link_prediction.baselines import ladder
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_index, month_label
from pipeline.tracking import track

OUTPUT = Path("reports/link_prediction.json")
DEFAULT_TRAIN_CUT = "2026-07"
DEFAULT_TEST_CUT = "2026-08"
MAX_POSITIVES = 30000


def parse_month(value: str) -> int:
    """Accept YYYY-MM and turn it into the same month number the snapshot layer uses."""
    year, _, month = value.partition("-")
    if not (year.isdigit() and month.isdigit()):
        raise argparse.ArgumentTypeError(f"YYYY-MM 형식이어야 합니다: {value}")
    index = month_index(f"{int(year) % 100:02d}{int(month):02d}.00000")
    if index is None:
        raise argparse.ArgumentTypeError(f"월로 해석할 수 없습니다: {value}")
    return index


def evaluate(built: dict[str, Any]) -> list[dict[str, Any]]:
    """Fit each rung on the training split and score it on both test splits and the ranking."""
    splits = built["splits"]
    train, test_hard, test_random = splits["train"], splits["test_hard"], splits["test_random"]
    ranking_features = built["ranking"]["features"]
    results = []
    for model in ladder():
        model.fit(train.features, train.labels)
        row: dict[str, Any] = {
            "model": model.name,
            "detail": model.describe(),
            "test_hard_negatives": metrics_module.classification_metrics(
                test_hard.labels, model.score(test_hard.features)
            ),
            "test_random_negatives": metrics_module.classification_metrics(
                test_random.labels, model.score(test_random.features)
            ),
            "train_fit": metrics_module.classification_metrics(
                train.labels, model.score(train.features)
            ),
        }
        if ranking_features:
            row["ranking_hard_negatives"] = metrics_module.ranking_metrics(
                [model.score(features) for features in ranking_features]
            )
        results.append(row)
    return results


def feature_importance(built: dict[str, Any]) -> dict[str, float]:
    """Rank features by how far their means separate the two classes on the hard split.

    A standardized mean difference, not a model coefficient, so the number says something about
    the data rather than about one model's fit.
    """
    split = built["splits"]["test_hard"]
    labels = split.labels.astype(bool)
    importance = {}
    for index, name in enumerate(dataset_module.FEATURE_NAMES):
        values = split.features[:, index].astype(np.float64)
        positive, negative = values[labels], values[~labels]
        spread = np.sqrt((positive.var() + negative.var()) / 2) or 1.0
        importance[name] = round(float((positive.mean() - negative.mean()) / spread), 4)
    return dict(sorted(importance.items(), key=lambda item: -abs(item[1])))


def main() -> int:
    parser = argparse.ArgumentParser(description="공동인용 링크 예측 기준선을 측정합니다.")
    parser.add_argument("--version")
    parser.add_argument("--train-cut", default=DEFAULT_TRAIN_CUT,
                        help="이 달의 공동인용이 학습 라벨이 됩니다 (YYYY-MM).")
    parser.add_argument("--test-cut", default=DEFAULT_TEST_CUT,
                        help="이 달부터의 공동인용이 평가 라벨이 됩니다 (YYYY-MM).")
    parser.add_argument("--max-positives", type=int, default=MAX_POSITIVES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    train_cut, test_cut = parse_month(args.train_cut), parse_month(args.test_cut)
    built = dataset_module.build(
        snap, train_cut=train_cut, test_cut=test_cut, max_positives=args.max_positives
    )
    windows = built["windows"]
    results = evaluate(built)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "snapshot_papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "task": "co-citation link prediction",
        "task_note": (
            "직접 인용 예측은 실제 간선의 95.7%가 공통 이웃 0이어서 기각했습니다. "
            "reports/link_prediction_structure.json 참고."
        ),
        "split": {
            "key": "arxiv_id_yymm",
            "train_label_month": month_label(train_cut),
            "test_label_from": month_label(test_cut),
            **windows.counts,
            **built["coverage"],
        },
        "splits_used": {
            name: {
                "pairs": int(len(split.labels)),
                "positives": split.positive_count,
                "negatives": int(len(split.labels)) - split.positive_count,
                "negative_kind": split.negative_kind,
            }
            for name, split in built["splits"].items()
        },
        "ranking_task": {
            "groups": len(built["ranking"]["groups"]),
            "candidates_per_group": built["ranking"]["candidates"] + 1,
        },
        "feature_separation": feature_importance(built),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with track("link-prediction") as run:
        run.log_params({
            "snapshot_version": snap.version,
            "train_label_month": month_label(train_cut),
            "test_label_from": month_label(test_cut),
            "max_positives": args.max_positives,
            "task": "co-citation-link-prediction",
        })
        run.log_tags({"snapshot_papers_sha256": report["snapshot_papers_sha256"][:16]})
        for result in results:
            name = result["model"]
            for metric, value in result["test_hard_negatives"].items():
                run.log_metrics({f"{name}.hard.{metric}": value})
            for metric, value in result["test_random_negatives"].items():
                run.log_metrics({f"{name}.random.{metric}": value})
            for metric, value in result.get("ranking_hard_negatives", {}).items():
                run.log_metrics({f"{name}.rank.{metric}": value})
        run.log_artifact(args.output)

    print(json.dumps({
        "split": report["split"],
        "splits_used": report["splits_used"],
        "feature_separation": dict(list(report["feature_separation"].items())[:6]),
        "results": [
            {
                "model": result["model"],
                "hard_auc": result["test_hard_negatives"]["roc_auc"],
                "hard_ap": result["test_hard_negatives"]["average_precision"],
                "random_auc": result["test_random_negatives"]["roc_auc"],
                "mrr": result.get("ranking_hard_negatives", {}).get("mrr"),
                "hits_at_1": result.get("ranking_hard_negatives", {}).get("hits_at_1"),
            }
            for result in results
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
