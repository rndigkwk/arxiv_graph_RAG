"""미세조정용 쌍 내보내기가 **누출을 막고 추론과 같은 것을 가르치는지** 봅니다.


[ADR-057](../docs/adr/ADR-057-fine-tune-the-reranker-on-cocitation.md)의 학습 데이터입니다.
여기서 틀리면 모델이 좋아 보이고 그 좋음이 가짜입니다 — 그게 이 파일이 있는 이유입니다.

고정하는 것이 셋입니다.

| | 왜 |
|---|---|
| **양성이 평가 정답에 없다** | 있으면 테스트 라벨로 학습합니다 |
| **그래프를 학습 선으로 자른다** | 평가 선으로 자르면 음성 풀이 라벨 창을 봅니다 |
| **추론이 보는 것만 가르친다** | 풀 밖 양성은 볼 일이 없는 쌍입니다(풀 안 양성 p50 **2**) |

마지막 것은 재 보고 고쳤습니다. 처음에 전체 공동인용에서 양성을 뽑고 음성을 1:1로 맞췄는데,
풀 49편에 양성이 평균 3.8개뿐이고 배포 비율이 약 1:12였습니다.
"""

import json

import numpy as np
import pytest

from evals import export_rerank_training as exporter


class FakeGraph:
    """`pool_candidates`가 쓰는 것만 흉내 냅니다 - 스냅샷도 임베딩도 읽지 않습니다.

    **코사인 순서를 통제합니다.** 2차원 단위 벡터를 써서 `ids[0]`과의 내적이
    `1.0, 0.9, 0.8 …`로 내려가게 둡니다. 처음에 `np.eye`를 썼다가 순서가 제 가정과 달라
    네 테스트가 깨졌습니다 - 풀에 무엇이 드는지가 이 파일의 주제이므로 그 순서가 정확해야
    합니다.
    """

    def __init__(self, ids, cocited=None):
        self.ids = list(ids)
        self.position = {paper: index for index, paper in enumerate(self.ids)}
        sims = [max(0.0, 1.0 - 0.1 * index) for index in range(len(self.ids))]
        self.vectors = np.array(
            [[sim, float(np.sqrt(max(0.0, 1.0 - sim * sim)))] for sim in sims],
            dtype=np.float32,
        )
        self._cocited = cocited or {}

    def cocited(self, seed):
        return self._cocited.get(seed, {})


def corpus(ids):
    return {paper: f"{paper} 제목\n{paper} 초록" for paper in ids}


# ── 누출 ────────────────────────────────────────────────────────────────────

def test_the_label_windows_are_disjoint_by_construction():
    """`test_labels`에서 `w3` 쌍을 빼므로 `w2`의 쌍은 평가 정답에 없습니다.

    이 성질이 깨지면 아래 모든 검사가 의미를 잃습니다 - 그래서 먼저 확인합니다.
    """
    from models.link_prediction.dataset import build_windows
    from models.link_prediction.train import parse_month
    from pipeline import snapshot as snapshot_module

    snap = snapshot_module.latest()
    windows = build_windows(
        snap, train_cut=parse_month("2026-07"), test_cut=parse_month("2026-08")
    )

    assert windows.train_labels & windows.test_labels == set()
    # 양성이 충분한가(ADR-031의 둘째 기준).
    assert len(windows.train_labels) > 100_000


def test_the_leak_check_counts_a_planted_positive():
    """**심은 누출을 잡는지** 봅니다. 잡지 못하는 검사는 검사가 아닙니다."""
    rows = [{"seed": "a", "positives": ["b"], "negatives": ["c"]}]

    clean = exporter.leak_check(rows, test_labels=set())
    dirty = exporter.leak_check(rows, test_labels={("a", "b")})

    assert clean["positives_in_test_labels"] == 0
    assert dirty["positives_in_test_labels"] == 1


def test_the_pair_key_is_orderless():
    """공동인용 쌍은 무순서입니다. 순서가 다르면 못 잡으면 누출이 샙니다."""
    rows = [{"seed": "z", "positives": ["a"], "negatives": []}]

    assert exporter.leak_check(rows, {("a", "z")})["positives_in_test_labels"] == 1


def test_a_negative_in_the_test_labels_is_counted_separately():
    """그것은 누출이 아니라 **라벨 잡음**입니다 - 0일 수 없으므로 따로 셉니다."""
    rows = [{"seed": "a", "positives": ["b"], "negatives": ["c"]}]

    out = exporter.leak_check(rows, {("a", "c")})

    assert out["positives_in_test_labels"] == 0
    assert out["negatives_in_test_labels"] == 1


def test_positives_and_negatives_never_overlap():
    ids = ["s", "p1", "p2", "n1", "n2", "n3"]
    rows, counts = exporter.build(
        FakeGraph(ids), {"s": {"p1", "p2"}}, corpus(ids),
        pool=4, negatives_per_positive=0, max_seeds=0, rng=_rng(),
    )

    assert rows
    for row in rows:
        assert set(row["positives"]).isdisjoint(row["negatives"])
    assert exporter.leak_check(rows, set())["positive_negative_overlap"] == 0


# ── 추론과 같은 것을 가르치는가 ───────────────────────────────────────────

def test_positives_outside_the_pool_are_dropped_by_default():
    """**측정이 고친 자리입니다.** 풀 밖 양성은 추론에서 볼 일이 없는 쌍입니다.

    풀 3이면 상위 3편이 `s, a, b`이고 시드를 빼면 풀은 `{a, b}`입니다. 양성 `d`는
    풀 밖이므로 빠지고, `a`만 남습니다.
    """
    ids = ["s", "a", "b", "c", "d"]
    rows, counts = exporter.build(
        FakeGraph(ids), {"s": {"a", "d"}}, corpus(ids),
        pool=3, negatives_per_positive=0, max_seeds=0, rng=_rng(),
    )

    assert rows[0]["positives"] == ["a"]
    assert rows[0]["negatives"] == ["b"]
    assert counts["positives_in_pool_per_seed_p50"] == 1


def test_positives_all_keeps_them_and_says_so():
    """예전 방식도 낼 수 있게 두었지만 기본이 아닙니다."""
    ids = ["s", "a", "b", "c", "d"]
    rows, _ = exporter.build(
        FakeGraph(ids), {"s": {"a", "d"}}, corpus(ids),
        pool=3, negatives_per_positive=0, max_seeds=0, rng=_rng(),
        positives_from="all",
    )

    assert set(rows[0]["positives"]) == {"a", "d"}


def test_a_seed_with_no_positive_in_the_pool_is_dropped_and_counted():
    """측정에서 **19%** 가 이 경우입니다. 가르칠 것이 없으므로 빼고, 세어 둡니다."""
    ids = ["s", "a", "b", "c", "d"]
    rows, counts = exporter.build(
        FakeGraph(ids), {"s": {"d"}}, corpus(ids),
        pool=3, negatives_per_positive=0, max_seeds=0, rng=_rng(),
    )

    assert rows == []
    assert counts["seeds_without_positive_in_pool"] == 1


def test_the_whole_pool_is_used_as_negatives_by_default():
    """표본 손잡이가 없는 것이 설계입니다 - 배포가 보는 비율을 그대로 씁니다."""
    ids = ["s", "p", "n1", "n2", "n3", "n4"]
    rows, _ = exporter.build(
        FakeGraph(ids), {"s": {"p"}}, corpus(ids),
        pool=5, negatives_per_positive=0, max_seeds=0, rng=_rng(),
    )

    # 상위 5편(s·p·n1·n2·n3)에서 시드와 양성을 뺀 전부.
    assert rows[0]["negatives"] == ["n1", "n2", "n3"]


def test_a_ratio_knob_still_works_when_asked():
    ids = ["s", "p", "n1", "n2", "n3", "n4"]
    rows, _ = exporter.build(
        FakeGraph(ids), {"s": {"p"}}, corpus(ids),
        pool=5, negatives_per_positive=2, max_seeds=0, rng=_rng(),
    )

    assert len(rows[0]["negatives"]) == 2


# ── 텍스트와 셈 ────────────────────────────────────────────────────────────

def test_a_paper_without_text_is_dropped_and_counted():
    """조용히 두면 빈 문자열로 학습합니다(ADR-039: 전처리 차이를 재게 됩니다)."""
    ids = ["s", "p1", "p2", "n1", "n2"]
    texts = corpus(ids)
    del texts["p2"]

    rows, counts = exporter.build(
        FakeGraph(ids), {"s": {"p1", "p2"}}, texts,
        pool=4, negatives_per_positive=0, max_seeds=0, rng=_rng(),
    )

    assert rows[0]["positives"] == ["p1"]
    assert counts["positives_without_text"] == 1


def test_a_seed_without_text_is_skipped():
    ids = ["s", "p1"]
    texts = corpus(ids)
    del texts["s"]

    rows, _ = exporter.build(
        FakeGraph(ids), {"s": {"p1"}}, texts,
        pool=1, negatives_per_positive=0, max_seeds=0, rng=_rng(),
    )

    assert rows == []


def test_the_positive_adjacency_is_both_ways():
    """공동인용은 무순서입니다. 한쪽만 펼치면 절반의 시드를 잃습니다."""
    shaped = exporter.positives_by_seed({("a", "b"), ("b", "c")})

    assert shaped["a"] == {"b"}
    assert shaped["b"] == {"a", "c"}
    assert shaped["c"] == {"b"}


def test_the_seed_selection_is_reproducible():
    """같은 씨앗이면 같은 시드를 고릅니다 - 아니면 두 내보내기가 비교되지 않습니다."""
    ids = [f"p{i}" for i in range(12)]
    positives = {f"p{i}": {f"p{(i + 1) % 12}"} for i in range(12)}
    graph = FakeGraph(ids)

    first, _ = exporter.build(graph, positives, corpus(ids), pool=4,
                              negatives_per_positive=0, max_seeds=3, rng=_rng())
    again, _ = exporter.build(graph, positives, corpus(ids), pool=4,
                              negatives_per_positive=0, max_seeds=3, rng=_rng())

    assert [row["seed"] for row in first] == [row["seed"] for row in again]


# ── 실제 내보낸 매니페스트 ────────────────────────────────────────────────

@pytest.fixture(scope="module")
def manifest():
    from pathlib import Path

    path = Path("exports/colab/rerank_training_manifest.json")
    if not path.exists():
        pytest.skip(f"{path}가 없습니다. uv run python -m evals.export_rerank_training")
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_export_leaked_nothing(manifest):
    """**이 단정이 이 파일의 이유입니다.**"""
    assert manifest["leak_check"]["positives_in_test_labels"] == 0
    assert manifest["leak_check"]["positive_negative_overlap"] == 0


def test_the_graph_was_cut_at_the_training_line(manifest):
    """평가 선으로 자르면 음성 풀이 라벨 창을 미리 봅니다.

    학습 선이 앞서므로 **더 많이** 잘려야 합니다 - 평가는 32,765간선을 뺐습니다.
    """
    assert manifest["train_cut"] == "2026-07"
    assert manifest["test_cut"] == "2026-08"
    assert manifest["dropped_edges_outside_train_window"] > 32_765


def test_the_default_mirrors_inference(manifest):
    assert manifest["positives_from"] == "pool"
    assert manifest["negatives_per_positive"] == 0
    counts = manifest["counts"]
    # 배포 비율(약 1:12)에 가깝습니다. 1:1이면 다른 사전 확률을 심습니다.
    assert counts["negatives"] > counts["positives"] * 5


def test_the_manifest_records_what_it_dropped(manifest):
    counts = manifest["counts"]

    for key in ("seeds_without_positive_in_pool", "positives_without_text",
                "positives_in_pool_per_seed_p50"):
        assert key in counts, key


def _rng():
    import random

    return random.Random(0)
