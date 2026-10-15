"""링크 예측의 시간 분할과 지표를 검증합니다.

가장 중요한 것은 누수 불변식입니다. 라벨로 쓰는 공동인용 쌍이 그 라벨을 맞히는 데 쓰는 피처
그래프에 들어 있으면, 모델은 예측이 아니라 암기를 하게 되고 점수가 통째로 거짓이 됩니다.
그래서 "라벨 쌍 ∩ 피처 그래프 = 공집합"을 테스트로 못박습니다.
"""

import numpy as np
import pytest

from models.link_prediction import dataset, metrics


class FakeSnapshot:
    """`build_windows`가 쓰는 유일한 접근자만 흉내 냅니다."""

    def __init__(self, edges):
        self._edges = edges

    def rows(self, table):
        assert table == "references"
        return iter({"citing": citing, "cited": cited} for citing, cited in self._edges)


def month(label):
    year, _, value = label.partition("-")
    return dataset.month_index(f"{int(year) % 100:02d}{int(value):02d}.00000")


def test_cocitation_pairs_are_unordered_and_deduplicated():
    references = {"A": {"x", "y", "z"}}

    pairs = dataset.cocitation_pairs(references, {"A"})

    assert pairs == {("x", "y"), ("x", "z"), ("y", "z")}
    assert all(left < right for left, right in pairs)


def test_a_paper_citing_too_many_references_is_left_out():
    wide = {f"r{index}" for index in range(dataset.MAX_REFERENCES_FOR_COCITATION + 1)}

    # 참고문헌 150편이 넘는 논문 하나가 공동인용 쌍 1만 개 이상을 혼자 만들어 그래프를 덮습니다.
    assert dataset.cocitation_pairs({"A": wide}, {"A"}) == set()
    narrow = {"r1", "r2"}
    assert dataset.cocitation_pairs({"A": narrow}, {"A"}) == {("r1", "r2")}


def test_label_pairs_never_appear_in_the_feature_graph_they_are_scored_against():
    edges = [
        # 2026-05 인용자: 학습 피처 그래프(W1)를 만듭니다.
        ("2605.00001", "1801.00001"),
        ("2605.00001", "1802.00002"),
        # 2026-07 인용자: 학습 라벨(W2)이 됩니다.
        ("2607.00002", "1802.00002"),
        ("2607.00002", "1803.00003"),
        # 2026-09 인용자: 평가 라벨(W4)이 됩니다.
        ("2609.00003", "1801.00001"),
        ("2609.00003", "1803.00003"),
    ]

    windows = dataset.build_windows(
        FakeSnapshot(edges), train_cut=month("2026-07"), test_cut=month("2026-08")
    )

    train_feature_pairs = {
        (left, right)
        for left, neighbours in windows.train_feature.items()
        for right in neighbours
        if left < right
    }
    test_feature_pairs = {
        (left, right)
        for left, neighbours in windows.test_feature.items()
        for right in neighbours
        if left < right
    }

    assert windows.train_labels & train_feature_pairs == set()
    assert windows.test_labels & test_feature_pairs == set()
    # 평가 라벨 창의 인용자는 어느 피처 그래프에도 기여하지 않아야 합니다.
    assert ("1801.00001", "1803.00003") in windows.test_labels
    assert ("1801.00001", "1803.00003") not in test_feature_pairs


def test_a_pair_already_known_in_the_feature_window_is_not_a_label():
    edges = [
        ("2605.00001", "1801.00001"),
        ("2605.00001", "1802.00002"),
        # 같은 쌍을 다시 함께 인용합니다. 이미 아는 쌍을 맞히는 것은 예측이 아닙니다.
        ("2609.00003", "1801.00001"),
        ("2609.00003", "1802.00002"),
    ]

    windows = dataset.build_windows(
        FakeSnapshot(edges), train_cut=month("2026-07"), test_cut=month("2026-08")
    )

    assert ("1801.00001", "1802.00002") not in windows.test_labels
    assert windows.test_labels == set()


def test_the_split_requires_the_training_cut_to_come_first():
    with pytest.raises(ValueError):
        dataset.build_windows(
            FakeSnapshot([]), train_cut=month("2026-08"), test_cut=month("2026-07")
        )


def test_citation_indegree_counts_only_the_given_window():
    references = {"2605.00001": {"x"}, "2609.00003": {"x", "y"}}

    early = dataset.citation_indegree(references, {"2605.00001"})
    both = dataset.citation_indegree(references, set(references))

    assert early["x"] == 1 and early["y"] == 0
    assert both["x"] == 2 and both["y"] == 1


def test_roc_auc_matches_hand_computed_values():
    # 완벽히 분리되면 1.0, 완전히 뒤집히면 0.0입니다.
    assert metrics.roc_auc(np.array([0, 0, 1, 1]), np.array([0.1, 0.2, 0.8, 0.9])) == 1.0
    assert metrics.roc_auc(np.array([1, 1, 0, 0]), np.array([0.1, 0.2, 0.8, 0.9])) == 0.0
    # 전부 동점이면 0.5여야 합니다. 동점을 무시하면 휴리스틱 기준선이 과대평가됩니다.
    assert metrics.roc_auc(np.array([0, 1, 0, 1]), np.array([1.0, 1.0, 1.0, 1.0])) == 0.5
    # 양성 하나가 음성 하나보다 아래인 경우: 3/4.
    assert metrics.roc_auc(np.array([0, 1, 0, 1]), np.array([0.1, 0.2, 0.3, 0.4])) == 0.75


def test_average_precision_matches_hand_computed_values():
    assert metrics.average_precision(np.array([1, 1, 0, 0]), np.array([4, 3, 2, 1])) == 1.0
    # 순위가 음성, 양성이면 AP = (1/2) / 1 = 0.5.
    assert metrics.average_precision(np.array([0, 1]), np.array([2, 1])) == 0.5


def test_metrics_refuse_a_single_class():
    with pytest.raises(ValueError):
        metrics.roc_auc(np.array([1, 1]), np.array([0.5, 0.6]))
    with pytest.raises(ValueError):
        metrics.average_precision(np.array([0, 0]), np.array([0.5, 0.6]))


def test_ranking_metrics_put_the_true_partner_at_index_zero():
    # 진짜 상대가 1위: rank 1.
    perfect = metrics.ranking_metrics([np.array([1.0, 0.5, 0.2])])
    assert perfect["hits_at_1"] == 1.0
    assert perfect["mrr"] == 1.0

    # 진짜 상대가 3위: MRR = 1/3.
    third = metrics.ranking_metrics([np.array([0.1, 0.9, 0.5])])
    assert third["mean_rank"] == 3.0
    assert third["mrr"] == round(1 / 3, 4)
    assert third["hits_at_1"] == 0.0

    # 동점은 나쁜 쪽 순위로 셉니다. 전부 같은 점수를 주고 1위를 얻을 수는 없습니다.
    tied = metrics.ranking_metrics([np.array([0.5, 0.5, 0.5])])
    assert tied["hits_at_1"] == 0.0
    assert tied["mean_rank"] == 3.0


def test_ranking_metrics_need_at_least_one_group():
    with pytest.raises(ValueError):
        metrics.ranking_metrics([])
