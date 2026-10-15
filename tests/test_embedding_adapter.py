"""인용 쌍으로 학습하는 선형 임베딩 어댑터의 닫힌 해를 검증합니다.

가장 중요한 성질은 **정규화가 충분히 세면 항등행렬로 수렴한다**는 것입니다. 그 성질이 없으면
학습 쌍이 적을 때 어댑터가 공간을 망가뜨리고, "아무것도 하지 않기"보다 나쁜 쪽으로 떨어집니다.
실제 측정에서 정규화가 약한 설정이 raw보다 나빴던 것이 이 성질의 반대 증거입니다.
"""

import numpy as np
import pytest

from models.embedding_tuning import adapter


def pairs(count=64, dimensions=16, seed=0):
    generator = np.random.default_rng(seed)
    anchors = adapter.unit(generator.normal(size=(count, dimensions)))
    positives = adapter.unit(anchors + 0.3 * generator.normal(size=(count, dimensions)))
    negatives = adapter.unit(generator.normal(size=(count, dimensions)))
    return anchors, positives, negatives


def test_unit_normalizes_rows_and_survives_a_zero_row():
    matrix = np.array([[3.0, 4.0], [0.0, 0.0]])

    result = adapter.unit(matrix)

    assert np.allclose(np.linalg.norm(result[0]), 1.0)
    # 0 벡터를 0으로 나누면 NaN이 되어 조용히 전체 점수를 망가뜨립니다.
    assert np.all(np.isfinite(result))
    assert np.allclose(result[1], 0.0)


def test_a_strong_ridge_drives_the_solution_to_the_identity():
    anchors, positives, negatives = pairs()

    weak = adapter.fit(anchors, positives, negatives, ridge=0.01, negative_weight=0.5)
    strong = adapter.fit(anchors, positives, negatives, ridge=1e7, negative_weight=0.5)

    assert adapter.drift_from_identity(strong) < 1e-4
    assert adapter.drift_from_identity(strong) < adapter.drift_from_identity(weak)


def test_the_solution_satisfies_its_own_normal_equation():
    anchors, positives, _ = pairs()
    ridge = 5.0

    weights = adapter.fit(anchors, positives, ridge=ridge, negative_weight=0.0)

    identity = np.eye(anchors.shape[1])
    left = anchors.T @ anchors + ridge * identity
    right = positives.T @ anchors + ridge * identity
    assert np.allclose(weights @ left, right, atol=1e-6)


def test_the_negative_term_changes_the_solution():
    anchors, positives, negatives = pairs()

    without = adapter.fit(anchors, positives, ridge=1.0, negative_weight=0.0)
    with_negatives = adapter.fit(
        anchors, positives, negatives, ridge=1.0, negative_weight=0.5
    )

    assert not np.allclose(without, with_negatives)


def test_fitting_is_deterministic():
    anchors, positives, negatives = pairs()

    first = adapter.fit(anchors, positives, negatives, ridge=2.0, negative_weight=0.5)
    second = adapter.fit(anchors, positives, negatives, ridge=2.0, negative_weight=0.5)

    # 닫힌 해이므로 학습 루프도 난수 시드도 없고, 같은 입력에서 같은 W가 나와야 합니다.
    assert np.array_equal(first, second)


def test_apply_returns_unit_rows():
    anchors, positives, _ = pairs()
    weights = adapter.fit(anchors, positives, ridge=1.0)

    projected = adapter.apply(weights, anchors)

    assert np.allclose(np.linalg.norm(projected, axis=1), 1.0, atol=1e-5)


def test_applying_the_identity_leaves_unit_vectors_alone():
    anchors, _, _ = pairs()

    projected = adapter.apply(np.eye(anchors.shape[1], dtype=np.float32), anchors)

    assert np.allclose(projected, anchors, atol=1e-5)


@pytest.mark.parametrize("negative_weight", [-0.1, 1.0, 1.5])
def test_the_negative_weight_must_stay_below_one(negative_weight):
    anchors, positives, negatives = pairs()

    # 1 이상이면 정규방정식의 좌변이 양정치를 잃고 해가 발산합니다.
    with pytest.raises(ValueError):
        adapter.fit(anchors, positives, negatives, negative_weight=negative_weight)


def test_a_nonpositive_ridge_is_rejected():
    anchors, positives, _ = pairs()

    with pytest.raises(ValueError):
        adapter.fit(anchors, positives, ridge=0.0)


def test_mismatched_shapes_are_rejected():
    anchors, positives, negatives = pairs()

    with pytest.raises(ValueError):
        adapter.fit(anchors, positives[:10])
    with pytest.raises(ValueError):
        adapter.fit(anchors, positives, negatives[:10], negative_weight=0.5)
