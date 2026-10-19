"""링크 예측 지표입니다. 분류 지표와 순위 지표를 따로 둡니다.

AUC와 AP는 "두 부류를 가를 수 있는가"를 재고, Hits@K와 MRR은 "한 논문을 주고 후보를 보여줬을 때
진짜 상대가 위로 올라오는가"를 잽니다. 쓰임이 달라서 둘 다 보고합니다.

scipy와 numpy만 씁니다. 평가 지표를 외부 구현에 맡기지 않으면, 점수가 이상할 때 지표 쪽을
의심하고 직접 확인할 수 있습니다.
"""

from __future__ import annotations

import numpy as np


def roc_auc(labels: np.ndarray, scores: np.ndarray) -> float:
    """Rank-based AUC, equal to the probability a positive outranks a negative.

    Ties are credited a half, which matters here: the common-neighbour heuristic assigns the
    same score of zero to most pairs, and ignoring ties would flatter it.
    """
    labels = np.asarray(labels).astype(bool)
    scores = np.asarray(scores, dtype=np.float64)
    positives, negatives = int(labels.sum()), int((~labels).sum())
    if not positives or not negatives:
        raise ValueError("AUC는 양·음 표본이 모두 있어야 계산할 수 있습니다")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1, dtype=np.float64)
    sorted_scores = scores[order]
    start = 0
    for index in range(1, len(sorted_scores) + 1):
        if index == len(sorted_scores) or sorted_scores[index] != sorted_scores[start]:
            if index - start > 1:
                ranks[order[start:index]] = ranks[order[start:index]].mean()
            start = index
    return float((ranks[labels].sum() - positives * (positives + 1) / 2)
                 / (positives * negatives))


def average_precision(labels: np.ndarray, scores: np.ndarray) -> float:
    """Area under the precision-recall curve, summed over the recall steps."""
    labels = np.asarray(labels).astype(bool)
    scores = np.asarray(scores, dtype=np.float64)
    positives = int(labels.sum())
    if not positives:
        raise ValueError("AP는 양성 표본이 있어야 계산할 수 있습니다")
    order = np.argsort(-scores, kind="mergesort")
    hits = labels[order]
    cumulative = np.cumsum(hits)
    precision = cumulative / np.arange(1, len(hits) + 1)
    return float((precision * hits).sum() / positives)


def classification_metrics(labels: np.ndarray, scores: np.ndarray) -> dict[str, float]:
    labels = np.asarray(labels)
    return {
        "roc_auc": round(roc_auc(labels, scores), 4),
        "average_precision": round(average_precision(labels, scores), 4),
        "positive_rate": round(float(np.asarray(labels).astype(bool).mean()), 4),
        "pairs": int(len(labels)),
    }


def ranking_ranks(group_scores: list[np.ndarray]) -> np.ndarray:
    """The true partner's rank in each group, so two models can be compared pairwise.

    집계만 돌려주면 모델 둘의 차이에 구간을 낼 수 없습니다. 변동의 단위가 그룹이므로 그룹별
    순위를 그대로 꺼내 둡니다 - `train.py`가 그것으로 쌍체 부트스트랩을 합니다.

    A tie between the true partner and a decoy is counted at the worse rank, so a model that
    scores everything the same cannot collect credit.
    """
    if not group_scores:
        raise ValueError("순위 지표를 계산할 그룹이 없습니다")
    ranks = []
    for scores in group_scores:
        scores = np.asarray(scores, dtype=np.float64)
        truth = scores[0]
        ranks.append(1 + int((scores[1:] >= truth).sum()))
    return np.asarray(ranks, dtype=np.float64)


def ranking_metrics(group_scores: list[np.ndarray], *, ks: tuple[int, ...] = (1, 3, 5)
                    ) -> dict[str, float]:
    """Score per-anchor rankings where index 0 of each group is the true partner."""
    ranks_array = ranking_ranks(group_scores)
    ranks = ranks_array
    metrics = {
        "groups": len(ranks),
        "mrr": round(float((1.0 / ranks_array).mean()), 4),
        "mean_rank": round(float(ranks_array.mean()), 3),
    }
    for k in ks:
        metrics[f"hits_at_{k}"] = round(float((ranks_array <= k).mean()), 4)
    return metrics
