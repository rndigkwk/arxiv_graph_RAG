"""앱의 PageRank를 **네트워크 없이** 검증합니다.

`chatbot`은 임포트만으로 Aura와 OpenAI에 붙어서, 이 알고리즘은 지금까지 테스트가 닿지
않았습니다. 관련 논문 슬롯을 PPR이 맡고 있고 어댑터와 붙여 보려면
([ADR-027](../docs/adr/ADR-027-cocitation-gold-for-the-related-slot.md)) 같은 함수를 오프라인에서
돌릴 수 있어야 해서 순수 모듈로 뺐습니다.

**식을 바꾸지 않았다는 것**이 여기서 지켜야 할 것입니다. 그래서 손으로 풀 수 있는 작은
그래프에서 성질을 고정합니다 - 분포의 합, 무방향 대칭, 시드 쪽에 몰리는지, 매달린 노드의
점수가 새지 않는지.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.pagerank import (
    DAMPING,
    personalized,
    run_pagerank,
    transition_matrix,
    undirected_pairs,
)


def chain(size=4):
    """0 -> 1 -> 2 -> 3. 마지막 노드는 나가는 간선이 없습니다(매달린 노드)."""
    rows = np.array([0, 1, 2], dtype=np.int32)
    cols = np.array([1, 2, 3], dtype=np.int32)
    return rows, cols, size


def test_the_rank_is_a_distribution():
    rows, cols, size = chain()
    matrix, degree = transition_matrix(rows, cols, size)

    rank = run_pagerank(matrix, degree, np.full(size, 1.0 / size))

    assert rank.shape == (size,)
    assert rank.sum() == pytest.approx(1.0, abs=1e-9)
    assert (rank >= 0).all()


def test_a_dangling_node_does_not_leak_the_score():
    """나가는 인용이 없는 논문에 점수가 고이면 분포의 합이 1 아래로 내려갑니다."""
    rows, cols, size = chain()
    matrix, degree = transition_matrix(rows, cols, size)

    rank = run_pagerank(matrix, degree, np.full(size, 1.0 / size))

    assert (degree == 0)[3]
    assert rank.sum() == pytest.approx(1.0, abs=1e-9)


def test_personalized_rank_concentrates_on_the_seed_side():
    """개인화의 뜻입니다 - 시드 주변이 전역 분포보다 높아야 합니다."""
    rows, cols, size = chain()
    matrix, degree = transition_matrix(*undirected_pairs(rows, cols), size)

    seeded = personalized(matrix, degree, size, [0])
    uniform = run_pagerank(matrix, degree, np.full(size, 1.0 / size))

    assert seeded[0] > uniform[0]
    assert seeded[3] < uniform[3]
    # 시드가 **절대 1위는 아닙니다.** 사슬에서 노드 1은 이웃이 둘이라 텔레포트가 시드에 전부
    # 있어도 질량이 더 쌓입니다(0.3582 대 0.3022). 개인화의 뜻은 "시드가 최고점"이 아니라
    # **시드 쪽으로 분포가 기운다**는 것이므로, 전역 대비 증가량이 시드에서 가장 큽니다.
    assert int(np.argmax(seeded - uniform)) == 0


def test_no_seed_means_no_score():
    rows, cols, size = chain()
    matrix, degree = transition_matrix(rows, cols, size)

    assert personalized(matrix, degree, size, []) is None


def test_the_undirected_graph_is_symmetric_and_deduplicated():
    """인용 방향을 그대로 쓰면 시드가 인용한 쪽으로만 흐릅니다."""
    rows = np.array([0, 1, 1], dtype=np.int32)
    cols = np.array([1, 0, 2], dtype=np.int32)

    pairs = undirected_pairs(rows, cols)

    seen = set(zip(pairs[0].tolist(), pairs[1].tolist(), strict=True))
    # (0,1)과 (1,0)이 각각 한 번씩만 남습니다.
    assert seen == {(0, 1), (1, 0), (1, 2), (2, 1)}


def test_two_seeds_share_the_teleport_mass_equally():
    rows, cols, size = chain()
    matrix, degree = transition_matrix(*undirected_pairs(rows, cols), size)

    both = personalized(matrix, degree, size, [0, 3])

    # 사슬의 양 끝은 대칭이므로 같은 점수여야 합니다.
    assert both[0] == pytest.approx(both[3], abs=1e-9)


def test_the_damping_constant_is_the_standard_value():
    """숫자를 옮기면서 바꾸지 않았다는 것을 고정합니다."""
    assert DAMPING == 0.85
