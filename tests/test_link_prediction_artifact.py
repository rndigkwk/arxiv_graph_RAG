"""학습 산출물과 그 우위의 구간이 **실제로 남는지** 검증합니다.

[ADR-031](../docs/adr/ADR-031-the-model-that-wins-is-the-forecast.md)에서 이 프로젝트의 학습
모델이 처음으로 휴리스틱을 **측정된 구간으로** 이겼습니다. 그 주장이 유지되려면 둘이 필요합니다.

1. **가중치가 저장돼 있어야** 합니다. 지표만 남기면 승격도 롤백도 말할 수 없습니다.
2. **우위에 구간이 있어야** 합니다. 점 추정만 적으면 "더 낫다"가 주장이 아닙니다.

`derived/`는 gitignore 대상이라 CI에는 없습니다. 보고서는 커밋되므로 **구간은 CI에서도**
검사하고, 가중치 파일은 있을 때만 봅니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPORT = Path("reports/link_prediction.json")
ARTIFACTS = Path("derived/link_prediction")
LEARNED = ("graph-logistic", "graph-text-gbm")


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"보고서가 없습니다: {REPORT}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_learned_rungs_beat_the_best_heuristic_with_an_interval(report):
    """구간이 0을 포함하면 "휴리스틱을 이긴다"를 적을 수 없습니다."""
    rows = {row["model"]: row for row in report["results"]}

    for name in LEARNED:
        assert name in rows, name
        interval = rows[name].get("mrr_vs_best_heuristic")
        assert interval is not None, f"{name}에 구간이 없습니다"
        assert interval["excludes_zero"], (name, interval)
        assert interval["mean"] > 0, (name, interval)
        assert interval["low"] > 0, (name, interval)


def test_the_baseline_of_the_interval_is_a_heuristic(report):
    """학습 모델끼리 비교하면 "휴리스틱이 필요 없다"는 주장이 되지 않습니다."""
    rows = {row["model"]: row for row in report["results"]}
    heuristics = {
        row["model"] for row in report["results"] if not row["detail"].get("fitted")
    }

    for name in LEARNED:
        assert rows[name]["mrr_vs_best_heuristic"]["baseline"] in heuristics


def test_the_report_records_where_the_weights_went(report):
    """가중치 경로가 보고서에 없으면 어느 파일이 그 지표를 냈는지 알 수 없습니다."""
    artifacts = report.get("artifacts")
    assert artifacts, "artifacts가 없습니다 - --no-save로 돌렸거나 저장이 빠졌습니다"

    for name in LEARNED:
        assert name in artifacts["models"], name
        assert artifacts["models"][name]["bytes"] > 0


def test_the_feature_window_is_recorded_so_leakage_stays_checkable(report):
    """피처 창과 라벨 창이 분리됐다는 사실이 보고서에 남아야 합니다."""
    split = report["split"]

    assert split["key"] == "arxiv_id_yymm"
    # 피처는 라벨보다 과거의 그래프에서만 계산합니다(ADR-011).
    assert split["citers_w1_train_feature"] > 0
    assert split["citers_w2_train_labels"] > 0
    assert split["train_labels_usable"] > 1000


def test_the_saved_models_load_and_score():
    """파일이 있는데 열리지 않으면 승격할 수 없습니다."""
    if not ARTIFACTS.exists():
        pytest.skip(f"산출물이 없습니다(gitignore 대상): {ARTIFACTS}")
    import numpy as np

    joblib = pytest.importorskip("joblib")
    from models.link_prediction.dataset import FEATURE_NAMES

    for name in LEARNED:
        path = ARTIFACTS / f"{name}.joblib"
        if not path.exists():
            pytest.skip(f"{path}가 없습니다")
        model = joblib.load(path)
        scores = model.score(np.zeros((3, len(FEATURE_NAMES)), dtype=np.float32))
        assert len(scores) == 3


def test_the_manifest_pins_the_snapshot_it_learned_from():
    """어떤 데이터로 학습했는지 묶이지 않으면 아티팩트가 있어도 되짚을 수 없습니다(ADR-003)."""
    manifest = ARTIFACTS / "manifest.json"
    if not manifest.exists():
        pytest.skip(f"manifest가 없습니다(gitignore 대상): {manifest}")
    from pipeline import snapshot as snapshot_module

    recorded = json.loads(manifest.read_text(encoding="utf-8"))

    assert recorded["snapshot_version"] == snapshot_module.latest().version
    assert recorded["split"]["citers_w1_train_feature"] > 0
    for name in LEARNED:
        assert recorded["metrics"][name]["ranking_mrr"] is not None
