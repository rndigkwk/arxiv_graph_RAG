"""공동인용 링크 예측 데이터셋을 스냅샷에서 만듭니다.

과제는 "두 논문이 앞으로 같은 논문에 함께 인용될까"입니다. 원래 설계였던 "A가 B를 인용할까"를
버린 이유는 측정에 있습니다. 실제 인용 간선의 95.7%가 공통 이웃이 0이어서 그래프 피처에 신호가
없었습니다(`models.link_prediction.structure_report`). 수집이 "최근 논문 → 그 참고문헌" 방향
한 번뿐이라 과거 논문에는 나가는 간선이 없고, 그래서 삼각형이 생기지 않습니다.

공동인용 그래프는 같은 데이터에서 92.9만 쌍·평균 차수 59.4로 조밀합니다. 구조가 있는 쪽에서
링크 예측을 합니다.

## 시간 창을 세 개로 나눕니다

    W1  month <  train_cut                학습용 피처를 계산하는 그래프
    W2  train_cut <= month < test_cut     학습 라벨이 나오는 창
    W3  month <  test_cut                 평가용 피처를 계산하는 그래프
    W4  month >= test_cut                 평가 라벨이 나오는 창

피처는 라벨보다 과거의 그래프에서만 계산합니다. 학습 라벨 창(W2)의 공동인용이 학습 피처
그래프(W1)에 들어가지 않고, 평가 라벨 창(W4)은 어느 피처 그래프에도 들어가지 않습니다. 무작위
분할을 쓰면 미래의 공동인용이 피처로 새어 들어가 점수가 부풀려집니다.

## 네거티브는 같은 분야에서 뽑습니다

무작위 네거티브는 주제가 전혀 달라서 임베딩 유사도만으로 갈라집니다. 앵커 논문을 고정하고
상대를 포지티브 상대와 같은 대표 분류에서 뽑으면, 모델이 "분야가 같은데도 함께 인용될지"를
구분해야 합니다. 비교를 위해 무작위 네거티브 세트도 함께 만듭니다.
"""

from __future__ import annotations

import collections
import itertools
import random
from dataclasses import dataclass
from typing import Any

import numpy as np

from pipeline import features as features_module
from pipeline.snapshot import Snapshot, month_index

# 참고문헌이 150편을 넘는 논문 한 편이 공동인용 쌍 1만 개 이상을 혼자 만들어 그래프를
# 뒤덮습니다. 구조 측정과 같은 기준으로 잘라 냅니다.
MAX_REFERENCES_FOR_COCITATION = 150
SEED = 20261015
RANKING_CANDIDATES = 9
RANKING_GROUPS = 3000

FEATURE_NAMES = (
    "common_neighbors",
    "adamic_adar",
    "jaccard",
    "log_preferential_attachment",
    "log_degree_min",
    "log_degree_max",
    "log_citation_indegree_min",
    "log_citation_indegree_max",
    "embedding_cosine",
    "same_primary_category",
    "category_jaccard",
    "month_gap",
)
GRAPH_FEATURE_NAMES = FEATURE_NAMES[:8]


@dataclass
class Windows:
    """The three graphs and two label windows that the temporal split produces."""

    train_cut: int
    test_cut: int
    train_feature: dict[str, set[str]]
    train_labels: set[tuple[str, str]]
    test_feature: dict[str, set[str]]
    test_labels: set[tuple[str, str]]
    train_indegree: collections.Counter
    test_indegree: collections.Counter
    counts: dict[str, int]


def cocitation_pairs(references: dict[str, set[str]], citers: set[str]) -> set[tuple[str, str]]:
    """Every unordered pair of papers that one of these citers cites together."""
    pairs: set[tuple[str, str]] = set()
    for citer in citers:
        targets = references.get(citer)
        if not targets or len(targets) > MAX_REFERENCES_FOR_COCITATION:
            continue
        for left, right in itertools.combinations(sorted(targets), 2):
            pairs.add((left, right))
    return pairs


def adjacency_of(pairs: set[tuple[str, str]]) -> dict[str, set[str]]:
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    for left, right in pairs:
        adjacency[left].add(right)
        adjacency[right].add(left)
    return adjacency


def citation_indegree(references: dict[str, set[str]], citers: set[str]) -> collections.Counter:
    """How often each paper is cited inside one window; the popularity signal."""
    counter: collections.Counter = collections.Counter()
    for citer in citers:
        for target in references.get(citer, ()):
            counter[target] += 1
    return counter


def build_windows(snap: Snapshot, *, train_cut: int, test_cut: int) -> Windows:
    """Split the citation edges into the three feature graphs and two label windows."""
    if not train_cut < test_cut:
        raise ValueError("train_cut은 test_cut보다 앞서야 합니다")
    references: dict[str, set[str]] = collections.defaultdict(set)
    for row in snap.rows("references"):
        references[row["citing"]].add(row["cited"])
    dated = {
        citer: month for citer in references if (month := month_index(citer)) is not None
    }
    w1 = {citer for citer, month in dated.items() if month < train_cut}
    w2 = {citer for citer, month in dated.items() if train_cut <= month < test_cut}
    w3 = {citer for citer, month in dated.items() if month < test_cut}
    w4 = {citer for citer, month in dated.items() if month >= test_cut}

    train_feature_pairs = cocitation_pairs(references, w1)
    test_feature_pairs = cocitation_pairs(references, w3)
    # 라벨은 "피처 그래프에 아직 없던" 쌍만 씁니다. 이미 있는 쌍을 맞히는 것은 예측이 아닙니다.
    train_labels = cocitation_pairs(references, w2) - train_feature_pairs
    test_labels = cocitation_pairs(references, w4) - test_feature_pairs
    return Windows(
        train_cut=train_cut,
        test_cut=test_cut,
        train_feature=adjacency_of(train_feature_pairs),
        train_labels=train_labels,
        test_feature=adjacency_of(test_feature_pairs),
        test_labels=test_labels,
        train_indegree=citation_indegree(references, w1),
        test_indegree=citation_indegree(references, w3),
        counts={
            "citers_w1_train_feature": len(w1),
            "citers_w2_train_labels": len(w2),
            "citers_w3_test_feature": len(w3),
            "citers_w4_test_labels": len(w4),
            "pairs_train_feature": len(train_feature_pairs),
            "pairs_test_feature": len(test_feature_pairs),
            "pairs_train_labels_new": len(train_labels),
            "pairs_test_labels_new": len(test_labels),
        },
    )


class PaperTable:
    """Metadata and normalized embeddings, keyed by arXiv ID."""

    def __init__(self, snap: Snapshot) -> None:
        cache = features_module.Cache(snap)
        self.position = cache.position
        rows = cache.meta()
        self.primary = {row["arxiv_id"]: row.get("primary_category") or "" for row in rows}
        self.categories = {
            row["arxiv_id"]: frozenset(row.get("categories") or ()) for row in rows
        }
        self.month = {row["arxiv_id"]: row.get("month_index") for row in rows}
        matrix = np.asarray(cache.embeddings(mmap=False), dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        self.unit = matrix / norms

    def cosine(self, left: str, right: str) -> float:
        first, second = self.position.get(left), self.position.get(right)
        if first is None or second is None:
            return 0.0
        return float(np.dot(self.unit[first], self.unit[second]))


def eligible_nodes(adjacency: dict[str, set[str]], table: PaperTable) -> list[str]:
    """Nodes a pair may use: present in the feature graph and carrying an embedding."""
    return sorted(
        node for node, neighbours in adjacency.items()
        if neighbours and node in table.position
    )


def usable_pairs(
    labels: set[tuple[str, str]], allowed: set[str]
) -> list[tuple[str, str]]:
    """Keep label pairs whose both endpoints exist in the feature graph.

    A pair with a brand-new paper has every graph feature at zero, so a model would learn
    "degree is zero" instead of learning structure. Those pairs are a cold-start problem of
    their own and are counted out rather than silently scored.
    """
    return [pair for pair in sorted(labels) if pair[0] in allowed and pair[1] in allowed]


def sample_hard_negatives(
    positives: list[tuple[str, str]],
    adjacency: dict[str, set[str]],
    forbidden: set[tuple[str, str]],
    table: PaperTable,
    *,
    seed: int,
) -> list[tuple[str, str]]:
    """For each positive (u, v), draw (u, w) with w in v's primary category and never co-cited."""
    rng = random.Random(seed)
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for node in eligible_nodes(adjacency, table):
        buckets[table.primary.get(node, "")].append(node)
    negatives = []
    for anchor, partner in positives:
        pool = buckets.get(table.primary.get(partner, ""), ())
        if len(pool) < 4:
            continue
        for _ in range(12):
            candidate = pool[rng.randrange(len(pool))]
            if candidate in (anchor, partner):
                continue
            pair = (anchor, candidate) if anchor < candidate else (candidate, anchor)
            if pair in forbidden:
                continue
            negatives.append(pair)
            break
    return negatives


def sample_random_negatives(
    count: int,
    adjacency: dict[str, set[str]],
    forbidden: set[tuple[str, str]],
    table: PaperTable,
    *,
    seed: int,
) -> list[tuple[str, str]]:
    """Draw unrelated pairs, kept only to show how much easier they are than hard negatives."""
    rng = random.Random(seed + 1)
    nodes = eligible_nodes(adjacency, table)
    negatives = []
    attempts = 0
    while len(negatives) < count and attempts < count * 40:
        attempts += 1
        left, right = nodes[rng.randrange(len(nodes))], nodes[rng.randrange(len(nodes))]
        if left == right:
            continue
        pair = (left, right) if left < right else (right, left)
        if pair in forbidden:
            continue
        negatives.append(pair)
    return negatives


def pair_features(
    pairs: list[tuple[str, str]],
    adjacency: dict[str, set[str]],
    indegree: collections.Counter,
    table: PaperTable,
) -> np.ndarray:
    """Compute the feature matrix for pairs, reading only the given feature graph."""
    matrix = np.zeros((len(pairs), len(FEATURE_NAMES)), dtype=np.float32)
    for index, (left, right) in enumerate(pairs):
        left_neighbours = adjacency.get(left, frozenset())
        right_neighbours = adjacency.get(right, frozenset())
        shared = left_neighbours & right_neighbours
        union = len(left_neighbours | right_neighbours)
        adamic = sum(
            1.0 / np.log(len(adjacency.get(node, frozenset())) + 1e-9)
            for node in shared
            if len(adjacency.get(node, frozenset())) > 1
        )
        degrees = sorted((len(left_neighbours), len(right_neighbours)))
        citations = sorted((indegree.get(left, 0), indegree.get(right, 0)))
        left_month, right_month = table.month.get(left), table.month.get(right)
        left_categories = table.categories.get(left, frozenset())
        right_categories = table.categories.get(right, frozenset())
        category_union = len(left_categories | right_categories)
        matrix[index] = (
            len(shared),
            adamic,
            len(shared) / union if union else 0.0,
            np.log1p(degrees[0] * degrees[1]),
            np.log1p(degrees[0]),
            np.log1p(degrees[1]),
            np.log1p(citations[0]),
            np.log1p(citations[1]),
            table.cosine(left, right),
            float(
                bool(table.primary.get(left))
                and table.primary.get(left) == table.primary.get(right)
            ),
            (
                len(left_categories & right_categories) / category_union
                if category_union else 0.0
            ),
            (
                abs(left_month - right_month)
                if left_month is not None and right_month is not None else 0.0
            ),
        )
    return matrix


def ranking_groups(
    positives: list[tuple[str, str]],
    adjacency: dict[str, set[str]],
    forbidden: set[tuple[str, str]],
    table: PaperTable,
    *,
    candidates: int,
    seed: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Build a per-anchor ranking task: one true partner against several same-field decoys.

    AUC over a shuffled pool answers "can the model separate the two classes at all".
    A recommender is used differently: given one paper, the true partner has to come out on top
    of the candidates shown. Hits@K and MRR measure that, so both are reported.
    """
    rng = random.Random(seed)
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for node in eligible_nodes(adjacency, table):
        buckets[table.primary.get(node, "")].append(node)
    groups: list[dict[str, Any]] = []
    for anchor, partner in positives:
        if len(groups) >= limit:
            break
        pool = buckets.get(table.primary.get(partner, ""), ())
        if len(pool) < candidates * 3:
            continue
        decoys: list[str] = []
        for _ in range(candidates * 12):
            if len(decoys) >= candidates:
                break
            candidate = pool[rng.randrange(len(pool))]
            if candidate in (anchor, partner) or candidate in decoys:
                continue
            pair = (anchor, candidate) if anchor < candidate else (candidate, anchor)
            if pair in forbidden:
                continue
            decoys.append(candidate)
        if len(decoys) == candidates:
            groups.append({"anchor": anchor, "partner": partner, "decoys": decoys})
    return groups


def group_features(
    group: dict[str, Any],
    adjacency: dict[str, set[str]],
    indegree: collections.Counter,
    table: PaperTable,
) -> np.ndarray:
    """Featurize one ranking group with the true partner at row 0."""
    anchor = group["anchor"]
    pairs = [(anchor, group["partner"])] + [(anchor, decoy) for decoy in group["decoys"]]
    return pair_features(pairs, adjacency, indegree, table)


@dataclass
class Split:
    """One labelled split: features, labels and the pairs they came from."""

    name: str
    pairs: list[tuple[str, str]]
    features: np.ndarray
    labels: np.ndarray
    negative_kind: str

    @property
    def positive_count(self) -> int:
        return int(self.labels.sum())


def make_split(
    name: str,
    positives: list[tuple[str, str]],
    negatives: list[tuple[str, str]],
    adjacency: dict[str, set[str]],
    indegree: collections.Counter,
    table: PaperTable,
    *,
    negative_kind: str,
) -> Split:
    pairs = positives + negatives
    labels = np.concatenate([
        np.ones(len(positives), dtype=np.int8),
        np.zeros(len(negatives), dtype=np.int8),
    ])
    return Split(
        name=name,
        pairs=pairs,
        features=pair_features(pairs, adjacency, indegree, table),
        labels=labels,
        negative_kind=negative_kind,
    )


def build(
    snap: Snapshot,
    *,
    train_cut: int,
    test_cut: int,
    max_positives: int,
    seed: int = SEED,
) -> dict[str, Any]:
    """Produce the training split, the hard-negative test split and the random-negative one."""
    windows = build_windows(snap, train_cut=train_cut, test_cut=test_cut)
    table = PaperTable(snap)
    rng = random.Random(seed)

    train_allowed = set(eligible_nodes(windows.train_feature, table))
    test_allowed = set(eligible_nodes(windows.test_feature, table))
    train_positives = usable_pairs(windows.train_labels, train_allowed)
    test_positives = usable_pairs(windows.test_labels, test_allowed)
    coverage = {
        "train_labels_total": len(windows.train_labels),
        "train_labels_usable": len(train_positives),
        "test_labels_total": len(windows.test_labels),
        "test_labels_usable": len(test_positives),
    }
    if len(train_positives) > max_positives:
        train_positives = rng.sample(train_positives, max_positives)
    if len(test_positives) > max_positives:
        test_positives = rng.sample(test_positives, max_positives)

    train_forbidden = windows.train_labels | set(windows.test_labels)
    train_forbidden.update(
        (left, right)
        for left, neighbours in windows.train_feature.items()
        for right in neighbours
        if left < right
    )
    test_forbidden = set(windows.test_labels)
    test_forbidden.update(
        (left, right)
        for left, neighbours in windows.test_feature.items()
        for right in neighbours
        if left < right
    )

    train = make_split(
        "train",
        train_positives,
        sample_hard_negatives(
            train_positives, windows.train_feature, train_forbidden, table, seed=seed
        ),
        windows.train_feature,
        windows.train_indegree,
        table,
        negative_kind="hard",
    )
    test_hard = make_split(
        "test_hard",
        test_positives,
        sample_hard_negatives(
            test_positives, windows.test_feature, test_forbidden, table, seed=seed + 7
        ),
        windows.test_feature,
        windows.test_indegree,
        table,
        negative_kind="hard",
    )
    test_random = make_split(
        "test_random",
        test_positives,
        sample_random_negatives(
            len(test_positives), windows.test_feature, test_forbidden, table, seed=seed + 7
        ),
        windows.test_feature,
        windows.test_indegree,
        table,
        negative_kind="random",
    )
    ranking = ranking_groups(
        test_positives,
        windows.test_feature,
        test_forbidden,
        table,
        candidates=RANKING_CANDIDATES,
        seed=seed + 11,
        limit=RANKING_GROUPS,
    )
    return {
        "windows": windows,
        "coverage": coverage,
        "splits": {split.name: split for split in (train, test_hard, test_random)},
        "ranking": {
            "groups": ranking,
            "features": [
                group_features(group, windows.test_feature, windows.test_indegree, table)
                for group in ranking
            ],
            "candidates": RANKING_CANDIDATES,
        },
    }
