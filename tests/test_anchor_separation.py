"""질문이 지칭한 논문을 **검색 결과로 세지 않는지** 검증합니다.

[ADR-024](../docs/adr/ADR-024-route-by-question-type.md)에서 결함 셋의 뿌리가 하나였습니다 —
제목을 arxiv_id로 바꾸는 중간 조회의 행이 답변 근거와 섞여 세어졌습니다. 그래서 여기서
고정하는 것은 넷입니다.

1. 기준 논문을 **사례 정의에서** 가져오는가(추측하지 않는가).
2. 기준 논문이 정답에 **들어 있지 않은가** — 이 성질이 깨지면 제외가 정답을 지웁니다.
3. 해석 조회를 **조회문 문구가 아니라 구조로** 가려내는가.
4. 근거 지표는 **건드리지 않는가** — 질문이 지칭한 논문을 답변이 인용한 것은 측정 결함이
   아니라 답변 결함입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.rescore import context_is_lookup, rescore_row
from evals.run import ARXIV_ID, anchor_ids, evaluate_case, generator_contexts, is_lookup

CASES = Path("evals/cases_graph.json")
ANCHORED_FAMILIES = {"citing", "coauthor", "reference"}
PLAIN_FAMILIES = {"author", "topcited"}


@pytest.fixture(scope="module")
def cases():
    return json.loads(CASES.read_text(encoding="utf-8"))["cases"]


def test_the_anchor_comes_from_the_gold_cypher_not_a_guess(cases):
    """골드를 만든 Cypher가 기준 논문을 이미 적고 있습니다. 추측할 이유가 없습니다."""
    for case in cases:
        anchors = anchor_ids(case)
        if case["category"] in ANCHORED_FAMILIES:
            assert len(anchors) == 1, case["id"]
            assert ARXIV_ID.match(next(iter(anchors))), case["id"]
        else:
            # 저자 이름·분류 코드는 arxiv_id가 아니므로 기준 논문이 없습니다.
            assert anchors == set(), case["id"]


def test_no_anchor_is_also_a_gold_answer(cases):
    """제외가 정답을 지우지 않는다는 **전제**입니다. 깨지면 지표가 조용히 부풀려집니다."""
    for case in cases:
        assert not (anchor_ids(case) & set(case["expected_ids"] or [])), case["id"]


def test_families_without_an_anchor_are_untouched(cases):
    """`author`·`topcited`는 제목을 지칭하지 않으므로 이 변경이 닿지 않아야 합니다."""
    plain = [case for case in cases if case["category"] in PLAIN_FAMILIES]
    assert len(plain) == 12

    for case in plain:
        response = {"rows": [{"evidence_ids": ["2601.00001"]}], "evidence_ids": ["2601.00001"]}
        row = evaluate_case(case, response, mode="retrieval", top_k=5)
        assert row["retrieved_ids"] == ["2601.00001"], case["id"]
        assert "anchor_ids" not in row


def test_the_anchor_is_dropped_from_the_retrieved_list(cases):
    """시드가 1위를 차지해 정답을 2위로 밀던 것이 reciprocal_rank 상한의 원인이었습니다."""
    case = next(c for c in cases if c["id"] == "citing-03")
    anchor = next(iter(anchor_ids(case)))
    gold = case["expected_ids"]
    # 해석 조회가 먼저이므로 시드가 앞에 옵니다.
    response = {"rows": [{"evidence_ids": [anchor]}, {"evidence_ids": list(gold)}]}

    row = evaluate_case(case, response, mode="retrieval", top_k=5)

    assert anchor not in row["retrieved_ids"]
    assert row["retrieved_ids"][0] == gold[0]
    assert row["reciprocal_rank"] == 1.0
    # 무엇을 뺐는지 보고서가 스스로 말해야 합니다.
    assert row["anchor_ids"] == [anchor]


def test_a_lookup_is_recognised_by_structure_not_by_wording():
    """조회문 문구로 가려내면 모델이 문구를 바꿀 때마다 깨집니다."""
    anchors = {"2407.01437"}

    assert is_lookup([{"evidence_ids": ["2407.01437"]}], anchors) is True
    # 답변 조회입니다.
    assert is_lookup([{"evidence_ids": ["2603.04191"]}], anchors) is False
    # 기준 논문과 다른 것이 섞였으면 해석 조회로 보지 않습니다 - 그 행은 실제로 검색된
    # 결과이고, 정답 키를 보고 걸러내면 지표가 자기충족적이 됩니다.
    assert is_lookup([{"evidence_ids": ["2407.01437", "2603.04191"]}], anchors) is False
    assert is_lookup([], anchors) is False
    assert is_lookup([{"evidence_ids": ["2407.01437"]}], set()) is False


def test_the_lookup_context_is_withheld_from_the_generator_contexts():
    """RAGAS가 해석 조회를 쓸모없는 문맥으로 채점해 `citing`이 1.0000에서 0.5046이 됐습니다."""
    anchors = {"2407.01437"}
    response = {"tool_calls": [
        {"name": "search_graph", "status": "success", "output": {
            "cypher": 'MATCH (p) WHERE toLower(p.title) CONTAINS "needle" RETURN p.arxiv_id',
            "rows": [{"evidence_ids": ["2407.01437"]}]}},
        {"name": "search_graph", "status": "success", "output": {
            "cypher": "MATCH (p)-[:REFERENCES]->(q {arxiv_id: '2407.01437'}) RETURN p.arxiv_id",
            "rows": [{"evidence_ids": ["2603.04191"]}]}},
    ]}

    kept = generator_contexts(response, anchors)

    assert len(kept) == 1
    assert "REFERENCES" in kept[0]
    # 기준 논문을 모르면 아무것도 빼지 않습니다.
    assert len(generator_contexts(response)) == 2


def test_the_answer_metrics_are_not_rescored(cases):
    """질문이 지칭한 논문을 답변이 인용한 것은 **답변 결함**입니다. 지표가 잡는 것이 맞습니다."""
    case = next(c for c in cases if c["id"] == "citing-03")
    anchor = next(iter(anchor_ids(case)))
    row = {
        "retrieved_ids": [anchor, *case["expected_ids"]],
        "evidence_ids": [anchor, *case["expected_ids"]],
        "answer_evidence_precision": 0.6667,
        "answer_evidence_recall": 1.0,
    }

    rescored = rescore_row(row, case, 5)

    assert rescored["answer_evidence_precision"] == 0.6667
    assert rescored["answer_evidence_recall"] == 1.0
    # 검색 지표만 다시 셉니다.
    assert rescored["reciprocal_rank"] == 1.0


def test_a_stored_context_is_filtered_by_the_same_rule():
    """재채점은 원본 행이 아니라 굳어 버린 문자열을 봅니다. 판정 기준은 같아야 합니다."""
    anchors = {"2407.01437"}
    lookup = (
        'Cypher: MATCH (p) WHERE toLower(p.title) CONTAINS "needle"\n'
        "evidence_ids=['2407.01437']"
    )
    answer = "Cypher: MATCH (p)-[:REFERENCES]->(q)\nevidence_ids=['2603.04191'] | title=X"

    assert context_is_lookup(lookup, anchors) is True
    assert context_is_lookup(answer, anchors) is False
    # 초록 문맥(벡터 검색)은 Cypher로 시작하지 않습니다.
    assert context_is_lookup("Some title\nSome abstract", anchors) is False
    assert context_is_lookup(lookup, set()) is False
