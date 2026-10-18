"""PageRank 계산의 순수 부분입니다. Neo4j도 OpenAI도 가져오지 않습니다.

`chatbot.py`에 있던 세 조각을 그대로 옮겼습니다 — 전이 행렬, 멱승법, 그리고 무방향 간선
만들기. 숫자가 바뀌지 않도록 **식과 상수를 손대지 않고** 옮겼고, `chatbot`이 여기서
가져다 씁니다.

**옮긴 이유는 재려고입니다.** 앱의 개인화 PageRank가 관련 논문 슬롯을 맡고 있는데
([ADR-027](docs/adr/ADR-027-cocitation-gold-for-the-related-slot.md)), `chatbot`은 임포트만으로
Aura와 OpenAI에 붙기 때문에 그 알고리즘을 오프라인에서 재 볼 수가 없었습니다. 어댑터와
붙여 보려면 같은 함수를 **네트워크 없이** 돌릴 수 있어야 합니다.

`prompts.py`·`routing.py`·`abstain.py`와 같은 이동입니다 — 앱의 한 조각을 순수하게 떼어
테스트와 측정이 닿게 합니다.
"""

from __future__ import annotations

import numpy as np
from scipy.sparse import csr_array

DAMPING = 0.85          # 표준값. 15% 확률로 텔레포트 벡터로 점프합니다.
PAGERANK_ITERATIONS = 200
PAGERANK_TOLERANCE = 1e-11


def transition_matrix(rows, cols, size):
    """행 방향으로 정규화한 전이 행렬과 각 노드의 출력 차수를 만듭니다."""
    out_degree = np.bincount(rows, minlength=size).astype(np.float64)
    # 출력 차수가 0인 행은 어차피 비어 있으므로 0으로 나누지 않게만 막습니다.
    weights = 1.0 / np.where(out_degree[rows] == 0, 1.0, out_degree[rows])
    return csr_array((weights, (rows, cols)), shape=(size, size)), out_degree


def run_pagerank(matrix, out_degree, teleport):
    """멱승법으로 PageRank를 계산합니다. teleport의 합은 1이어야 합니다."""
    dangling = out_degree == 0
    rank = teleport.copy()
    for _ in range(PAGERANK_ITERATIONS):
        # 나가는 인용이 없는 논문에 고인 점수는 텔레포트 분포로 다시 뿌립니다.
        leaked = rank[dangling].sum()
        nxt = DAMPING * (matrix.T @ rank) + (DAMPING * leaked + 1.0 - DAMPING) * teleport
        moved = np.abs(nxt - rank).sum()
        rank = nxt
        if moved < PAGERANK_TOLERANCE:
            break
    return rank


def undirected_pairs(rows, cols):
    """양쪽 방향을 모두 넣고 중복된 쌍을 한 번만 남깁니다.

    인용 방향을 그대로 쓰면 시드가 인용한 논문 쪽으로만 흘러갑니다. 관련 논문을 찾는 것이
    목적이므로 인용한 쪽·인용된 쪽을 모두 이웃으로 봅니다.
    """
    return np.unique(
        np.stack([np.concatenate([rows, cols]), np.concatenate([cols, rows])]), axis=1
    )


def personalized(matrix, degree, size, seeds):
    """시드에서만 텔레포트하는 개인화 PageRank. 시드가 없으면 None입니다."""
    if not seeds:
        return None
    teleport = np.zeros(size)
    teleport[list(seeds)] = 1.0 / len(seeds)
    return run_pagerank(matrix, degree, teleport)
