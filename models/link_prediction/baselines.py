"""기준선 사다리입니다. 휴리스틱 → 그래프 피처 학습 → 그래프+텍스트 학습 순으로 올립니다.

사다리를 쓰는 이유는 "모델이 좋다"가 아니라 "무엇이 점수를 올렸는지"를 말할 수 있어야 하기
때문입니다. 공통 이웃 하나로 얼마가 나오는지 먼저 보면, 학습 모델이 올린 몫과 피처가 올린 몫을
나눠 설명할 수 있습니다.

scikit-learn은 학습 모델에서만 필요하므로 그 안에서 늦게 가져옵니다. 휴리스틱 기준선은
의존성 없이 돌아가야 테스트에서도 쓸 수 있습니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from models.link_prediction.dataset import FEATURE_NAMES, GRAPH_FEATURE_NAMES


def column(name: str) -> int:
    return FEATURE_NAMES.index(name)


@dataclass
class Heuristic:
    """Rank by one feature as-is; nothing is fitted, so there is nothing to overfit."""

    name: str
    feature: str

    def fit(self, features: np.ndarray, labels: np.ndarray) -> Heuristic:
        return self

    def score(self, features: np.ndarray) -> np.ndarray:
        return np.asarray(features[:, column(self.feature)], dtype=np.float64)

    def describe(self) -> dict[str, Any]:
        return {"kind": "heuristic", "feature": self.feature, "fitted": False}


@dataclass
class Learned:
    """A fitted classifier over a chosen subset of the feature columns."""

    name: str
    kind: str
    feature_names: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)
    model: Any = None

    @property
    def columns(self) -> list[int]:
        return [column(name) for name in self.feature_names]

    def _build(self) -> Any:
        if self.kind == "logistic":
            from sklearn.linear_model import LogisticRegression
            from sklearn.pipeline import make_pipeline
            from sklearn.preprocessing import StandardScaler

            # 피처 눈금이 제각각입니다. 공통 이웃은 0~수백, 코사인은 -1~1이므로 표준화 없이는
            # 정규화 항이 한쪽 피처만 눌러 버립니다.
            return make_pipeline(
                StandardScaler(),
                LogisticRegression(max_iter=2000, **self.params),
            )
        if self.kind == "gbm":
            from sklearn.ensemble import HistGradientBoostingClassifier

            return HistGradientBoostingClassifier(random_state=0, **self.params)
        raise ValueError(f"알 수 없는 모델 종류: {self.kind}")

    def fit(self, features: np.ndarray, labels: np.ndarray) -> Learned:
        self.model = self._build()
        self.model.fit(features[:, self.columns], labels)
        return self

    def score(self, features: np.ndarray) -> np.ndarray:
        if self.model is None:
            raise ValueError("학습하지 않은 모델입니다")
        return self.model.predict_proba(features[:, self.columns])[:, 1]

    def describe(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "features": list(self.feature_names),
            "feature_count": len(self.feature_names),
            "params": self.params,
            "fitted": self.model is not None,
        }


def ladder() -> list[Heuristic | Learned]:
    """The four rungs, in the order their results should be read."""
    return [
        Heuristic("common-neighbors", "common_neighbors"),
        Heuristic("adamic-adar", "adamic_adar"),
        Learned("graph-logistic", "logistic", GRAPH_FEATURE_NAMES),
        Learned("graph-text-gbm", "gbm", FEATURE_NAMES),
    ]
