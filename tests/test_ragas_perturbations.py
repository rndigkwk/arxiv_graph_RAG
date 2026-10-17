"""일부러 심은 결함이 **의도한 결함**인지 검증합니다.

천장에 붙은 표본으로는 지표가 나쁜 답을 걸러내는지 알 수 없으므로 결함을 심습니다. 그러면
변형 자체가 틀리면 안 됩니다 — 심은 것과 다른 결함이 섞이면 "어떤 결함이 어떤 지표를 떨어뜨리는지"를
말할 수 없습니다.
"""

import pytest

from evals.ragas_perturbations import (
    ARXIV_ID,
    CONTRADICTION_ANSWER,
    FABRICATED_ID,
    build,
    fabricated_id,
    truncated,
)

TABLE_ANSWER = (
    "The collection contains five papers:\n"
    "\n"
    "| arXiv ID | Title | Primary category |\n"
    "|---|---|---|\n"
    "| 2607.03478 | First paper | cs.AI |\n"
    "| 2608.10209 | Second paper | cs.AI |\n"
    "| 2507.08249 | Third paper | cs.AI |\n"
)


def report_with(answer=TABLE_ANSWER, **overrides):
    row = {
        "id": "citing-01",
        "answer": answer,
        "contexts": ["Cypher: MATCH (c)-[:REFERENCES]->(p) RETURN 1", "answer_value=First paper"],
        "failed": False,
    }
    row.update(overrides)
    return {
        "mode": "compare",
        "results": [
            {"id": "citing-01", "category": "citing", "question": "Which papers cite X?",
             "graph_rag": row}
        ],
    }


def test_the_fabricated_id_replaces_a_real_one():
    broken = fabricated_id(TABLE_ANSWER, contexts=["nothing relevant"])

    assert FABRICATED_ID in broken
    assert "2607.03478" not in broken
    # 나머지 ID는 그대로여야 합니다. 결함은 하나만 심습니다.
    assert "2608.10209" in broken


def test_planting_an_id_that_is_already_in_the_context_is_refused():
    """문맥에 있는 ID를 심으면 "문맥에 없는 주장"이 아니게 되어 측정이 무의미해집니다."""
    with pytest.raises(AssertionError):
        fabricated_id(TABLE_ANSWER, contexts=[f"answer_value={FABRICATED_ID}"])


def test_an_answer_without_an_id_gets_no_fabricated_variant():
    assert fabricated_id("No papers match.", contexts=[]) is None


def test_truncation_keeps_the_header_and_one_row():
    shortened = truncated(TABLE_ANSWER)
    ids = ARXIV_ID.findall(shortened)

    assert ids == ["2607.03478"]
    # 표 머리글이 남아야 답변이 깨진 글이 아니라 "짧은 답변"이 됩니다.
    assert "| arXiv ID | Title | Primary category |" in shortened
    assert "|---|---|---|" in shortened


def test_truncation_says_only_things_that_are_supported():
    """`truncated`는 말한 것은 다 맞고 빠뜨린 답변입니다.

    남은 ID가 원본에 있던 것이어야 faithfulness가 유지되고, 그래야 "네 지표로는 누락을 못
    잡는다"를 재는 변형이 됩니다.
    """
    shortened = truncated(TABLE_ANSWER)

    assert set(ARXIV_ID.findall(shortened)) <= set(ARXIV_ID.findall(TABLE_ANSWER))


def test_a_short_table_is_not_truncated_further():
    short = "| ID | Title |\n|---|---|\n| 2601.1 | Only |\n"

    assert truncated(short) is None


def test_every_defect_keeps_the_original_contexts():
    """바꾸는 것은 답변뿐이어야 원인이 답변에 있습니다."""
    built, _ = build(report_with())

    for result in built["results"]:
        assert result["graph_rag"]["contexts"] == report_with()["results"][0]["graph_rag"][
            "contexts"
        ]


def test_each_case_carries_its_defect_label_and_base_case():
    built, _ = build(report_with())
    defects = {result["defect"] for result in built["results"]}

    assert defects == {"contradiction", "off_topic", "fabricated_id", "truncated"}
    assert all(result["base_case"] == "citing-01" for result in built["results"])
    assert all(result["id"].startswith("citing-01::") for result in built["results"])


def test_the_question_is_written_into_every_perturbed_case():
    """변형 id는 케이스 파일과 맞지 않습니다. 질문을 적지 않으면 채점기가 전부 건너뜁니다."""
    built, notes = build(report_with())

    assert notes == []
    for result in built["results"]:
        assert result["question"] == "Which papers cite X?"
        assert result["graph_rag"]["question"] == "Which papers cite X?"


def test_the_question_can_come_from_the_case_file():
    report = report_with()
    del report["results"][0]["question"]

    built, notes = build(report, {"citing-01": "Which papers cite X?"})

    assert notes == []
    assert built["results"][0]["question"] == "Which papers cite X?"


def test_a_case_with_no_answer_is_not_perturbed():
    built, notes = build(report_with(answer="", failed=True))

    assert built["results"] == []
    assert notes and "변형하지 않았습니다" in notes[0]


def test_the_contradiction_variant_states_the_opposite():
    built, _ = build(report_with())
    contradiction = next(r for r in built["results"] if r["defect"] == "contradiction")

    assert contradiction["graph_rag"]["answer"] == CONTRADICTION_ANSWER
    # 행이 있는데 "없다"고 말하는 것이 결함입니다.
    assert ARXIV_ID.findall(contradiction["graph_rag"]["answer"]) == []
