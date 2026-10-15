"""그래프 조건 평가 세트가 "그래프 없이는 못 푸는 작은 정답"이라는 계약을 지키는지 봅니다.

기존 평가 세트가 변별력을 잃은 원인이 둘이었습니다. 정답 집합이 너무 커서 recall이 의미를
잃었고, 질문이 벡터 검색만으로 풀렸습니다. 여기서는 그 두 가지를 테스트로 못박습니다.
"""

import json
import re
from pathlib import Path

from evals.build_graph_cases import FAMILIES, MAX_GOLD, MIN_GOLD, TOP_CITED_LIMIT
from pipeline.snapshot import MANIFEST_NAME, SNAPSHOT_ROOT

DATASET = Path("evals/cases_graph.json")
ARXIV_ID = re.compile(r"^(\d{2}\d{2}\.\d{4,5}|[a-z-]+(\.[A-Z]{2})?/\d{7})$")


def load_dataset():
    return json.loads(DATASET.read_text(encoding="utf-8"))


def test_dataset_is_pinned_to_a_registered_snapshot():
    dataset = load_dataset()
    manifest_path = SNAPSHOT_ROOT / dataset["snapshot_version"] / MANIFEST_NAME

    # 정답이 어느 스냅샷에서 나왔는지 적혀 있지 않으면 재현도 재측정도 할 수 없습니다.
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["cut"]["month"] == dataset["snapshot_cut_month"]
    assert manifest["gate"]["passed"] is True


def test_every_family_is_represented_with_unique_ids():
    cases = load_dataset()["cases"]

    assert len({case["id"] for case in cases}) == len(cases)
    families = {case["category"] for case in cases}
    assert len(families) == len(FAMILIES)
    for family in families:
        assert sum(case["category"] == family for case in cases) >= 2


def test_gold_sets_stay_small_enough_for_recall_to_mean_something():
    for case in load_dataset()["cases"]:
        gold = case["expected_ids"]

        # recall@5는 정답이 5편을 넘어가면 k가 상한을 정해 버려 품질을 재지 못합니다.
        assert MIN_GOLD <= len(gold) <= MAX_GOLD, case["id"]
        assert len(set(gold)) == len(gold), case["id"]
        assert case["gold_size"] == len(gold), case["id"]
        assert all(ARXIV_ID.match(paper_id) for paper_id in gold), case["id"]
        if case["category"] == "topcited":
            assert len(gold) == TOP_CITED_LIMIT, case["id"]


def test_each_case_records_the_cypher_that_defines_its_gold_set():
    for case in load_dataset()["cases"]:
        cypher = case["cypher"]

        assert case["label_method"] == "snapshot_cypher", case["id"]
        assert case["requires_graph"] is True, case["id"]
        assert cypher.startswith("MATCH "), case["id"]
        assert "RETURN" in cypher, case["id"]
        assert case["label_rationale"].strip(), case["id"]
        # 기록된 파라미터가 Cypher에 실제로 쓰여야 --verify-against-db가 같은 질의를 돌립니다.
        assert case["cypher_params"], case["id"]
        for name in case["cypher_params"]:
            assert f"${name}" in cypher, (case["id"], name)


def test_questions_name_a_relationship_or_aggregate_rather_than_only_a_topic():
    # 벡터 임베딩 입력은 title+abstract뿐이므로, 저자·인용·분류·집계를 거는 질문만
    # 벡터 단독으로 풀 수 없습니다. 주제어만 있는 질문이 섞이면 변별력이 다시 사라집니다.
    graph_words = ("author", "cite", "cited", "share an author", "primary category")
    for case in load_dataset()["cases"]:
        question = case["question"].casefold()
        assert any(word in question for word in graph_words), case["id"]


def test_required_answer_phrases_stay_retired():
    # answer_correct가 같은 코드에서 두 번 돌릴 때 뒤집히는 것을 확인했으므로,
    # 필수 문구 채점은 이 세트에서 쓰지 않습니다.
    for case in load_dataset()["cases"]:
        assert case["expected_answer_contains"] == [], case["id"]
