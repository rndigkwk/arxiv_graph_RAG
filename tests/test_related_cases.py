"""관련 논문 정답 집합이 **겨룰 수 있는 과제**인지 검증합니다.

이 정답 집합의 목적은 앱에서 자체 학습 모델이 들어갈 수 있는 유일한 자리
(`rank_related_papers`)에 게이트를 걸 수 있게 하는 것입니다. 그러므로 정답 자체가 쓸 수
없으면 뒤의 모든 비교가 무의미해집니다. 고정하는 것은 넷입니다.

1. **정답이 작습니다.** `topic` 가족은 정답이 평균 1,162편이라 k=5 도달 상한이 0.012였습니다.
   같은 함정에 다시 빠지지 않는지 봅니다.
2. **시드가 자기 정답에 없습니다.** PPR은 시드를 결과에서 빼므로, 정답에 시드가 있으면
   구조적으로 못 맞히는 정답이 됩니다.
3. **1홉 조회로 풀리지 않습니다.** 정답이 시드의 직접 인용이면 Cypher 한 줄이 정확히 답하므로
   모델이 겨룰 과제가 아닙니다.
4. **기록한 Cypher가 저장한 정답과 같은 뜻입니다.** 임베딩 한정까지 적혀 있어야
   `--verify-against-db`가 뜻 있는 대조가 됩니다.

스냅샷 캐시(`derived/`)는 저장소에 올라가지 않으므로, 그것이 필요한 검사는 없으면
건너뜁니다. 파일 자체의 정합성은 캐시 없이 전부 봅니다.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path

import pytest

from evals.build_related_cases import MAX_GOLD, MIN_COCITERS, MIN_GOLD

DATASET = Path("evals/cases_related.json")


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

        snap = snapshot_module.latest()
        return load_tables(snap)
    except Exception as exc:  # noqa: BLE001 - 캐시가 없으면 이 검사만 건너뜁니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")


def test_the_gold_sets_stay_small_enough_for_recall_to_move(dataset):
    """`topic` 가족의 정답이 평균 1,162편이라 k=5 상한이 0.012였습니다. 그 함정입니다."""
    sizes = [len(case["expected_related_ids"]) for case in dataset["cases"]]

    assert sizes, "사례가 없습니다"
    assert min(sizes) >= MIN_GOLD
    assert max(sizes) <= MAX_GOLD
    # k=5에서 recall의 도달 상한이 절반은 넘어야 지표가 움직입니다.
    assert sum(min(size, 5) / size for size in sizes) / len(sizes) > 0.8


def test_no_seed_is_in_its_own_gold_set(dataset):
    """PPR은 시드를 결과에서 뺍니다. 정답에 시드가 있으면 못 맞히는 정답이 됩니다."""
    for case in dataset["cases"]:
        assert case["seed_id"] not in case["expected_related_ids"], case["id"]


def test_every_gold_paper_meets_the_co_citation_bar(dataset):
    """공동 인용자 한 명은 우연일 수 있습니다. 둘을 요구한 것이 정답의 정의입니다."""
    for case in dataset["cases"]:
        shared = case["shared_citers"]
        assert sorted(shared) == sorted(case["expected_related_ids"]), case["id"]
        assert all(count >= MIN_COCITERS for count in shared.values()), case["id"]


def test_the_recorded_cypher_means_the_same_as_the_stored_gold(dataset):
    """임베딩 한정이 빠지면 DB 대조가 저장된 정답보다 넓은 집합을 돌려줍니다.

    조회문은 **데이터셋 수준에** 한 번 있습니다(dataset_version 2). 사례마다 적으면
    3,740건에서 파일의 45.8%가 같은 문자열이 됩니다.
    """
    cypher = dataset["cypher"]

    assert "p.embedding IS NOT NULL" in cypher
    assert "count(DISTINCT c)" in cypher
    for case in dataset["cases"]:
        assert "cypher" not in case, case["id"]
        assert case["cypher_params"] == {
            "arxiv_id": case["seed_id"],
            "min_cociters": MIN_COCITERS,
        }, case["id"]


def test_the_ids_are_unique_and_ordered(dataset):
    """번호순과 사전순이 같아야 합니다 - 세 자리로는 3,740건에서 어긋났습니다.

    `related-1000`이 `related-999` **앞**에 옵니다. 300건에서는 드러나지 않던 결함입니다.
    """
    ids = [case["id"] for case in dataset["cases"]]

    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert all(len(case_id.split("-")[1]) == 4 for case_id in ids)


def test_the_dataset_pins_the_snapshot_it_was_built_from(dataset):
    """정답이 라이브 DB가 아니라 버전이 박힌 스냅샷에서 나와야 합니다(ADR-003)."""
    assert dataset["snapshot_version"]
    assert dataset["source"].startswith("snapshots/")
    assert dataset["label_method"] == "snapshot_cocitation"


# 정답이 시드의 직접 인용에 **전부** 들어 있는 사례의 허용 비율입니다. 300 시드에서는
# 0건이었는데 적격 시드 전부(3,740)로 늘리자 2건이 나왔습니다. "0건"은 과제의 성질이 아니라
# **표본의 성질**이었습니다 - 측정값을 요구사항으로 적어 둔 것입니다(ADR-034).
ONE_HOP_RATE = 0.01


def test_the_task_is_not_a_one_hop_lookup(dataset, snapshot_tables):
    """정답이 시드의 직접 인용이면 Cypher 한 줄이 답하므로 모델이 겨룰 과제가 아닙니다.

    **0건을 요구하지 않습니다.** 공동인용 정답이 우연히 시드의 참고문헌 안에 들어가는 일은
    일어날 수 있고, 3,740 시드에서 2건(0.05%)입니다. 과제가 한 홉 조회가 아니라는 성질은
    그 비율로 충분히 성립합니다 - 0건을 요구하면 다음에 시드를 늘릴 때 또 터집니다.
    """
    cited_of: dict[str, set[str]] = collections.defaultdict(set)
    for citing, cited in snapshot_tables["cited_of"].items():
        cited_of[citing] = set(cited)

    full_overlap = [
        case["id"] for case in dataset["cases"]
        if set(case["expected_related_ids"]) <= cited_of.get(case["seed_id"], set())
    ]
    rate = len(full_overlap) / len(dataset["cases"])

    assert rate < ONE_HOP_RATE, (len(full_overlap), rate, full_overlap[:5])


def test_seeds_and_gold_stay_inside_the_embedding_corpus(dataset, snapshot_tables):
    """어댑터는 임베딩 행렬만 봅니다. 밖의 정답을 섞으면 PPR과의 비교가 불공정합니다."""
    embedded = set(snapshot_tables["meta"])

    for case in dataset["cases"]:
        assert case["seed_id"] in embedded, case["id"]
        assert set(case["expected_related_ids"]) <= embedded, case["id"]
