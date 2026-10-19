"""미래를 정답으로 쓰는 관련 논문 과제가 **겨룰 수 있는 과제**인지 검증합니다.

[ADR-035](../docs/adr/ADR-035-the-fill-slice-cannot-be-labelled.md)가 배포 보충 구간의 정답을
만들 수 없다고 측정하고, 대신 **예측 시점에 얇은** 시드를 다루는 이 데이터셋을 세웠습니다.
고정하는 것은 다섯입니다.

1. **정답이 작습니다.** 2~10편이고 recall@5 도달 상한이 0.78입니다. 큰 정답은 지표를 묶습니다
   (ADR-027이 중앙값 35편에서 겪은 함정).
2. **시드가 예측 시점에 얇습니다.** `visible_at_prediction_time`이 `top_k`보다 적어야
   "증거가 얇은 자리"라는 전제가 성립합니다.
3. **정답이 둘의 합이고 겹치지 않습니다.** 보이던 것과 새로 생긴 것의 합이 정답입니다.
4. **질의가 동어반복으로 이기지 못합니다.** 정답의 대부분이 예측 시점에 보이지 않습니다.
5. **누수 경고가 데이터셋에 적혀 있고, 그 누수가 실재합니다.** 전체 스냅샷 그래프로 재면
   공동인용 변형이 정답을 그대로 봅니다 - 그것을 테스트가 **확인**합니다.

스냅샷 캐시(`derived/`)는 저장소에 올라가지 않으므로 그것이 필요한 검사는 건너뜁니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.build_future_related_cases import MAX_GOLD, MIN_GOLD, TOP_K

DATASET = Path("evals/cases_related_future.json")


@pytest.fixture(scope="module")
def dataset():
    if not DATASET.exists():
        pytest.skip(f"정답 집합이 없습니다: {DATASET}")
    return json.loads(DATASET.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def snapshot_tables():
    """스냅샷이 없으면(CI) 건너뜁니다. `derived/`는 gitignore 대상입니다."""
    try:
        from evals.build_related_cases import load_tables
        from pipeline import snapshot as snapshot_module

        return load_tables(snapshot_module.latest())
    except Exception as exc:  # noqa: BLE001 - 캐시가 없으면 CI입니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")


def test_the_gold_is_small_enough_for_recall_at_k_to_move(dataset):
    for case in dataset["cases"]:
        assert MIN_GOLD <= case["gold_size"] <= MAX_GOLD, case["id"]
        assert case["gold_size"] == len(case["expected_related_ids"]), case["id"]
    # 상한이 1.0이 아니라는 것을 데이터셋이 스스로 적어야 합니다. 적지 않으면 0.78을
    # "0.78밖에 안 된다"로 읽습니다.
    assert 0.5 < dataset["recall_at_k_ceiling"] < 1.0


def test_every_seed_is_thin_at_prediction_time(dataset):
    """`top_k`보다 후보가 적은 자리가 이 과제의 전제입니다."""
    for case in dataset["cases"]:
        assert len(case["visible_at_prediction_time"]) < TOP_K, case["id"]


def test_the_gold_is_the_union_of_what_was_seen_and_what_came_later(dataset):
    for case in dataset["cases"]:
        seen = set(case["visible_at_prediction_time"])
        new = set(case["new_in_label_window"])
        assert not (seen & new), case["id"]  # 새로 생긴 쌍이므로 겹칠 수 없습니다
        assert seen | new == set(case["expected_related_ids"]), case["id"]
        assert case["seed_id"] not in case["expected_related_ids"], case["id"]


def test_the_present_graph_cannot_answer_most_of_the_gold(dataset):
    """질의가 동어반복으로 이기지 못해야 모델을 재는 자리가 됩니다(ADR-031의 1번 기준).

    반대쪽 함정도 함께 막습니다 - 새로 생긴 쌍만 정답으로 두면 현재 공동인용으로 정렬하는
    방법이 **정의상 0점**입니다. 둘을 합쳤으므로 보이는 몫이 0도, 1도 아니어야 합니다.
    """
    assert 0.0 < dataset["visible_share_mean"] < 0.25

    predicted = [case for case in dataset["cases"] if case["new_in_label_window"]]
    assert len(predicted) / len(dataset["cases"]) > 0.5


def test_the_dataset_pins_the_window_an_evaluation_must_respect(dataset):
    """`feature_cut`이 없으면 어떤 그래프로 재야 하는지 알 수 없습니다."""
    assert dataset["feature_cut"]
    assert dataset["label_method"] == "w3_sparse_future_cocitation"
    assert dataset["snapshot_version"]
    assert dataset["source"].startswith("snapshots/")
    assert "누수" in dataset["leakage_warning"] or "안 됩니다" in dataset["leakage_warning"]


def test_the_ids_are_unique_and_ordered(dataset):
    ids = [case["id"] for case in dataset["cases"]]

    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    # 네 자리입니다. 세 자리면 1,000건을 넘길 때 사전순과 번호순이 어긋납니다(ADR-034).
    assert all(len(case_id.split("-")[1]) == 4 for case_id in ids)


def test_the_leak_this_dataset_warns_about_is_real(dataset, snapshot_tables):
    """경고를 적어 두는 것으로 끝내지 않고 **누수가 실재함을 확인**합니다.

    전체 스냅샷 공동인용은 W4 인용자를 포함하므로, 미래 정답을 그대로 담고 있습니다.
    `evals/related_study.py`의 `Graph`가 그 전체 그래프를 씁니다 - 그대로 이 정답을 재면
    공동인용 변형이 답을 보고 답합니다.
    """
    from evals.build_related_cases import cocited

    leaked = 0
    checked = 0
    for case in dataset["cases"][:200]:
        if not case["new_in_label_window"]:
            continue
        checked += 1
        present = set(cocited(case["seed_id"], snapshot_tables))
        if present & set(case["new_in_label_window"]):
            leaked += 1

    assert checked, "미래 정답이 있는 사례가 없으면 이 검사가 뜻이 없습니다"
    # 전부 새어야 맞습니다 - W4 쌍은 전체 스냅샷 쌍의 부분집합입니다.
    assert leaked == checked, (leaked, checked)


def test_seeds_and_gold_stay_inside_the_embedding_corpus(dataset, snapshot_tables):
    """어댑터는 임베딩 행렬만 봅니다. 밖의 정답을 섞으면 비교가 불공정합니다(ADR-027)."""
    embedded = set(snapshot_tables["meta"])

    for case in dataset["cases"]:
        assert case["seed_id"] in embedded, case["id"]
        assert set(case["expected_related_ids"]) <= embedded, case["id"]


def test_the_dataset_says_why_it_is_not_the_deployed_fill_slice(dataset):
    """**이 데이터셋을 보충 구간 정답으로 읽으면 안 됩니다.**

    요청은 배포 보충 구간(전체 그래프에서 후보 5편 미만)이었고 그것은 만들 수 없습니다.
    대신 만든 것이 무엇인지 데이터셋이 스스로 말해야 하고, 그러지 않으면 다음에 읽는 사람이
    17.2%가 측정됐다고 믿습니다.
    """
    reason = dataset["why_not_the_deployed_fill_slice"]

    assert "6,499" in reason
    assert "피인용 0편" in reason


def test_cutting_the_graph_closes_the_leak(dataset):
    """경고가 아니라 **고쳤다는 것**을 확인합니다.

    `Graph(snap, feature_cut=...)`로 자른 그래프에서는 공동인용이 미래 정답을 담고 있지
    않아야 합니다. 자르지 않은 그래프에서는 전부 담고 있습니다(위 테스트) - 두 검사가 쌍으로
    "누수가 실재했고 자르면 닫힌다"를 말합니다(ADR-035).
    """
    try:
        from evals.related_study import Graph
        from models.link_prediction.train import parse_month
        from pipeline import snapshot as snapshot_module

        cut = parse_month(dataset["feature_cut"])
        graph = Graph(snapshot_module.latest(), feature_cut=cut)
    except Exception as exc:  # noqa: BLE001 - 스냅샷 캐시가 없으면 CI입니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")

    assert graph.dropped_edges > 0, "자른 간선이 0이면 자르지 않은 것입니다"

    checked = 0
    for case in dataset["cases"][:200]:
        if not case["new_in_label_window"]:
            continue
        checked += 1
        partners = set(graph.cocited(case["seed_id"]))
        leaked = partners & set(case["new_in_label_window"])
        assert not leaked, (case["id"], sorted(leaked))
    assert checked


def test_the_cut_matches_the_window_builder_except_for_the_reference_cap(dataset):
    """두 곳의 기준이 다르면 같은 W3라고 부르면서 다른 그래프를 씁니다.

    **처음에는 "같다"고 적었고 100 시드만 봤습니다. 전수로 보니 1건이 다릅니다.**
    `build_windows`는 참고문헌이 `MAX_REFERENCES_FOR_COCITATION`(150)편을 넘는 인용자를
    버리고(한 편이 공동인용 쌍 1만 개를 혼자 만듭니다) `Graph`는 앱과 같게 버리지
    않습니다. 그래서 "같다"가 아니라 **"다른 곳이 있으면 그 원인이 상한뿐이다"**를
    고정합니다 - 그것이 실제로 참인 성질입니다.

    전수에서 다른 시드는 1,377건 중 1건(`2606.09500`, 참고문헌 170편짜리 인용자 한 명)
    입니다. 말뭉치 표본 3,000편에서는 41편(1.4%)입니다.
    """
    try:
        from evals.related_study import Graph
        from models.link_prediction.dataset import MAX_REFERENCES_FOR_COCITATION, build_windows
        from models.link_prediction.train import DEFAULT_TRAIN_CUT, parse_month
        from pipeline import snapshot as snapshot_module

        snap = snapshot_module.latest()
        cut = parse_month(dataset["feature_cut"])
        graph = Graph(snap, feature_cut=cut)
        windows = build_windows(snap, train_cut=parse_month(DEFAULT_TRAIN_CUT), test_cut=cut)
    except Exception as exc:  # noqa: BLE001 - 스냅샷 캐시가 없으면 CI입니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")

    embedded = set(graph.position)
    differing = 0
    for case in dataset["cases"]:
        seed = case["seed_id"]
        mine = set(graph.cocited(seed))
        theirs = {p for p in windows.test_feature.get(seed, ()) if p in embedded}
        if mine == theirs:
            continue
        differing += 1
        # 차이는 **상한을 넘은 인용자**가 가져온 것이어야 합니다. 그 설명이 안 되면
        # 두 정의가 다른 뜻이 된 것이고, 그때는 같은 W3라고 부를 수 없습니다.
        big = [
            citer for citer in graph.cited_by.get(seed, ())
            if len(graph.cites.get(citer, ())) > MAX_REFERENCES_FOR_COCITATION
        ]
        from_big = {
            paper for citer in big for paper in graph.cites.get(citer, ())
            if paper != seed and paper in embedded
        }
        assert mine - theirs <= from_big, (case["id"], len(mine), len(theirs))
    # 드물어야 합니다. 흔해지면 두 그래프를 같은 이름으로 부르는 것이 위험해집니다.
    assert differing / len(dataset["cases"]) < 0.01, differing
