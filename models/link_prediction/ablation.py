"""피처 절제와 cold-start를 한 번에 측정합니다. 스냅샷만 읽으므로 API 비용이 없습니다.

    uv run --extra ml python -m models.link_prediction.ablation
    uv run --extra ml python -m models.link_prediction.ablation --max-positives 30000

두 가지를 잽니다.

**피처 절제.** 기준선 사다리만으로는 "그래프 피처만으로 AUC 0.743, 전부 쓰면 0.796"까지만
말할 수 있습니다. 정작 궁금한 것은 **텍스트를 넣은 뒤에도 그래프 피처가 기여하는가**입니다.
그래프 구조를 쓰는 것이 이 프로젝트의 정체성이므로, 기여가 없다면 그렇게 말해야 합니다.
묶음을 하나씩 빼고(leave-one-group-out), 하나만 쓰고(group-only) 각각 재면 둘이 분리됩니다.

**cold-start.** 학습·평가는 양 끝이 모두 피처 그래프에 있는 쌍만 썼고, 그 조건이 평가 라벨의
74%를 걸러냅니다. 버린 쪽이 실제 운영에서는 더 흔한 경우(이력 없는 새 논문)이므로, "제외했다"로
끝내지 않고 그 구간의 성능을 따로 잽니다. 네거티브도 같이 차갑게 뽑아야 합니다 — 차가운
포지티브를 따뜻한 네거티브와 맞추면 모델이 차수 0만 보고 맞히고, 그것은 표본 설계를 재는
것입니다.
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
from models.link_prediction.baselines import Learned
from models.link_prediction.dataset import FEATURE_NAMES
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_label
from pipeline.tracking import track

OUTPUT = Path("reports/link_prediction_ablation.json")
DEFAULT_TRAIN_CUT = "2026-07"
DEFAULT_TEST_CUT = "2026-08"
MAX_POSITIVES = 30000

GROUPS: dict[str, tuple[str, ...]] = {
    "graph": (
        "common_neighbors",
        "adamic_adar",
        "jaccard",
        "log_preferential_attachment",
        "log_degree_min",
        "log_degree_max",
    ),
    "popularity": (
        "log_citation_indegree_min",
        "log_citation_indegree_max",
    ),
    "text": ("embedding_cosine",),
    "meta": ("same_primary_category", "category_jaccard", "month_gap"),
}


def parse_month(value: str) -> int:
    year, _, month = value.partition("-")
    if not (year.isdigit() and month.isdigit()):
        raise argparse.ArgumentTypeError(f"YYYY-MM 형식이어야 합니다: {value}")
    index = dataset_module.month_index(f"{int(year) % 100:02d}{int(month):02d}.00000")
    if index is None:
        raise argparse.ArgumentTypeError(f"월로 해석할 수 없습니다: {value}")
    return index


def fit_and_score(
    feature_names: tuple[str, ...],
    train: dataset_module.Split,
    tests: dict[str, dataset_module.Split],
) -> dict[str, Any]:
    """Fit one GBM on the chosen columns and score it on every test split."""
    model = Learned("gbm", "gbm", feature_names).fit(train.features, train.labels)
    return {
        "features": list(feature_names),
        "feature_count": len(feature_names),
        **{
            name: metrics_module.classification_metrics(split.labels, model.score(split.features))
            for name, split in tests.items()
        },
    }


def ablation(
    train: dataset_module.Split, tests: dict[str, dataset_module.Split]
) -> dict[str, Any]:
    """Score the full model, each group removed, and each group alone."""
    rows: dict[str, Any] = {"all": fit_and_score(FEATURE_NAMES, train, tests)}
    for name, columns in GROUPS.items():
        remaining = tuple(value for value in FEATURE_NAMES if value not in columns)
        rows[f"without_{name}"] = fit_and_score(remaining, train, tests)
        rows[f"only_{name}"] = fit_and_score(columns, train, tests)
    return rows


def contribution(rows: dict[str, Any], split: str) -> dict[str, Any]:
    """State each group's contribution as the AUC it costs to remove it."""
    full = rows["all"][split]["roc_auc"]
    return {
        name: {
            "auc_without": rows[f"without_{name}"][split]["roc_auc"],
            "auc_only": rows[f"only_{name}"][split]["roc_auc"],
            "drop_when_removed": round(full - rows[f"without_{name}"][split]["roc_auc"], 4),
        }
        for name in GROUPS
    }


def coldstart(
    built: dict[str, Any],
    snap: Any,
    *,
    train: dataset_module.Split,
    seed: int,
) -> dict[str, Any]:
    """Measure the 74% of labels the warm task drops, with equally cold negatives."""
    windows = built["windows"]
    table = dataset_module.PaperTable(snap)
    allowed = set(dataset_module.eligible_nodes(windows.test_feature, table))
    split_kinds = dataset_module.coldstart_pairs(windows.test_labels, allowed, table)

    forbidden = set(windows.test_labels)
    forbidden.update(
        (left, right)
        for left, neighbours in windows.test_feature.items()
        for right in neighbours
        if left < right
    )
    positives, negatives = dataset_module.sample_coldstart_negatives(
        split_kinds["one_sided"], allowed, forbidden, table, seed=seed
    )
    if not positives:
        return {"composition": {kind: len(v) for kind, v in split_kinds.items()},
                "note": "cold-start 쌍을 만들지 못했습니다."}
    cold = dataset_module.make_split(
        "test_cold",
        positives,
        negatives,
        windows.test_feature,
        windows.test_indegree,
        table,
        negative_kind="cold-same-category",
    )
    tests = {"cold": cold}
    graph_zero = float(
        np.mean(cold.features[:, [FEATURE_NAMES.index(n) for n in GROUPS["graph"]]] == 0.0)
    )
    return {
        "composition": {
            "one_sided": len(split_kinds["one_sided"]),
            "both_cold": len(split_kinds["both_cold"]),
            "pairs_scored": int(len(cold.labels)),
            "positives": cold.positive_count,
        },
        "graph_feature_zero_share": round(graph_zero, 4),
        "warm_trained_all_features": fit_and_score(FEATURE_NAMES, train, tests)["cold"],
        "warm_trained_text_only": fit_and_score(GROUPS["text"], train, tests)["cold"],
        "warm_trained_without_graph": fit_and_score(
            tuple(v for v in FEATURE_NAMES if v not in GROUPS["graph"]), train, tests
        )["cold"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="피처 절제와 cold-start를 측정합니다.")
    parser.add_argument("--version")
    parser.add_argument("--train-cut", default=DEFAULT_TRAIN_CUT)
    parser.add_argument("--test-cut", default=DEFAULT_TEST_CUT)
    parser.add_argument("--max-positives", type=int, default=MAX_POSITIVES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    train_cut, test_cut = parse_month(args.train_cut), parse_month(args.test_cut)
    built = dataset_module.build(
        snap, train_cut=train_cut, test_cut=test_cut, max_positives=args.max_positives
    )
    splits = built["splits"]
    train = splits["train"]
    tests = {"hard": splits["test_hard"], "random": splits["test_random"]}

    rows = ablation(train, tests)
    cold = coldstart(built, snap, train=train, seed=dataset_module.SEED + 23)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "snapshot_papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "split": {
            "train_label_month": month_label(train_cut),
            "test_label_from": month_label(test_cut),
            **built["coverage"],
        },
        "groups": {name: list(columns) for name, columns in GROUPS.items()},
        "ablation": rows,
        "contribution_on_hard_negatives": contribution(rows, "hard"),
        "coldstart": cold,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with track("link-prediction-ablation") as run:
        run.log_params({
            "snapshot_version": snap.version,
            "max_positives": args.max_positives,
            "train_label_month": month_label(train_cut),
            "test_label_from": month_label(test_cut),
        })
        run.log_tags({"snapshot_papers_sha256": report["snapshot_papers_sha256"][:16]})
        for name, row in rows.items():
            run.log_metrics({f"{name}.hard.roc_auc": row["hard"]["roc_auc"]})
            run.log_metrics({f"{name}.hard.ap": row["hard"]["average_precision"]})
        for name, value in report["contribution_on_hard_negatives"].items():
            run.log_metrics({f"drop_without_{name}": value["drop_when_removed"]})
        if "warm_trained_all_features" in cold:
            run.log_metrics({
                "cold.all.roc_auc": cold["warm_trained_all_features"]["roc_auc"],
                "cold.text_only.roc_auc": cold["warm_trained_text_only"]["roc_auc"],
                "cold.pairs": cold["composition"]["pairs_scored"],
            })
        run.log_artifact(args.output)

    print(json.dumps({
        "contribution_on_hard_negatives": report["contribution_on_hard_negatives"],
        "all_features_hard_auc": rows["all"]["hard"]["roc_auc"],
        "coldstart": {
            key: cold[key] for key in cold
            if key in ("composition", "graph_feature_zero_share")
        },
        "coldstart_auc": {
            name: cold[name]["roc_auc"]
            for name in ("warm_trained_all_features", "warm_trained_text_only",
                         "warm_trained_without_graph")
            if name in cold
        },
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
