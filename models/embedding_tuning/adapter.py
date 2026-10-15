"""인용 관계로 학습하는 선형 임베딩 어댑터입니다. numpy만 씁니다.

Day 74 교안은 `sentence-transformers`에 MNR loss로 모델 자체를 파인튜닝합니다. 핵심 설계는
거기가 아니라 **hard negative를 그래프에서 뽑는다**는 데 있습니다 — 같은 대표 분류인데 인용
관계가 없는 논문은 주제가 가깝고 실제로는 무관하므로 정확히 hard negative의 정의입니다.

그 설계를 유지하면서 방법만 바꿉니다. 모델을 다시 학습하는 대신 **이미 있는 768차원 공간 위에
선형 변환 W를 학습**합니다. 이유는 셋입니다.

- 로드맵이 스스로 적은 배포 블로커가 사라집니다. 질의 시 768×768 행렬곱 한 번이면 되므로
  `sentence-transformers`도 torch도 ONNX 변환도 필요 없습니다.
- 배포 중인 임베딩 공간에서 직접 재므로 측정이 그대로 전이됩니다. 다른 모델을 튜닝하면
  "그 모델에서는 올랐다"가 되고, 실제 앱과의 연결이 끊깁니다.
- GPU가 필요 없습니다. 닫힌 해가 있어 학습이 한 번의 선형대수로 끝납니다.

## 목적함수

양성은 인용 쌍, 음성은 같은 분류의 비인용 쌍입니다. W를 항등행렬 근처로 묶어 둡니다.

    min_W  ||W v_a - v_b||^2  -  mu * ||W v_a - v_n||^2  +  lambda * ||W - I||^2

미분해서 0으로 두면 닫힌 해가 나옵니다.

    W [ (1 - mu) Va^T Va + lambda I ] = Vb^T Va - mu Vn^T Va + lambda I

`mu = 0`이면 순수 능형회귀(hard negative 미사용), `mu > 0`이면 대조 항이 들어갑니다.
`mu < 1`이어야 좌변이 양정치로 유지됩니다. 학습 루프도 난수 시드도 없으므로 같은 입력에서
항상 같은 W가 나옵니다.

변환은 **비대칭**입니다. 질의 쪽에만 W를 적용해 "이 논문이 인용할 논문"의 방향으로 보냅니다.
그래서 이것은 범용 검색기를 대체하지 않고 의도가 다른 두 번째 경로입니다. 아래 측정이 그것을
확인합니다.
"""

from __future__ import annotations

import numpy as np

IDENTITY_WEIGHT = 1.0
NEGATIVE_WEIGHT = 0.0


def unit(matrix: np.ndarray) -> np.ndarray:
    """Row-normalize, leaving zero rows alone so they cannot produce NaNs."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return matrix / norms


def fit(
    anchors: np.ndarray,
    positives: np.ndarray,
    negatives: np.ndarray | None = None,
    *,
    ridge: float = IDENTITY_WEIGHT,
    negative_weight: float = NEGATIVE_WEIGHT,
) -> np.ndarray:
    """Solve for W in closed form; the result is deterministic.

    `ridge` pulls W toward the identity, so a small training set degrades to "leave the space
    alone" instead of to noise. `negative_weight` must stay below 1 to keep the left-hand side
    positive definite.
    """
    if anchors.shape != positives.shape:
        raise ValueError("anchor와 positive의 모양이 같아야 합니다")
    if not 0.0 <= negative_weight < 1.0:
        raise ValueError("negative_weight는 0 이상 1 미만이어야 합니다")
    if ridge <= 0.0:
        raise ValueError("ridge는 양수여야 합니다")
    dimensions = anchors.shape[1]
    anchors = np.asarray(anchors, dtype=np.float64)
    positives = np.asarray(positives, dtype=np.float64)
    identity = np.eye(dimensions)

    gram = anchors.T @ anchors
    target = positives.T @ anchors
    scale = 1.0
    if negatives is not None and negative_weight:
        if negatives.shape != anchors.shape:
            raise ValueError("negative의 모양이 anchor와 같아야 합니다")
        target = target - negative_weight * (np.asarray(negatives, dtype=np.float64).T @ anchors)
        scale = 1.0 - negative_weight

    left = scale * gram + ridge * identity
    right = target + ridge * identity
    return np.linalg.solve(left.T, right.T).T


def apply(weights: np.ndarray, vectors: np.ndarray) -> np.ndarray:
    """Project and re-normalize, so cosine stays the comparison afterwards."""
    return unit(np.asarray(vectors, dtype=np.float32) @ np.asarray(weights, dtype=np.float32).T)


def identity_like(weights: np.ndarray) -> np.ndarray:
    return np.eye(weights.shape[1], dtype=np.float32)


def drift_from_identity(weights: np.ndarray) -> float:
    """How far W moved from leaving the space untouched; reported next to every score."""
    identity = np.eye(weights.shape[0])
    return float(
        np.linalg.norm(weights - identity) / np.linalg.norm(identity)
    )
