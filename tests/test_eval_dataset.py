import json
from pathlib import Path

from evals.run import load_cases


def test_benchmark_cases_have_unique_ids_and_nonempty_gold_sets():
    cases = load_cases(Path("evals/cases.json"))

    assert len(cases) == 31
    assert len({case["id"] for case in cases}) == len(cases)
    assert all(case["expected_ids"] for case in cases)
    assert sum(case["category"] == "topic" for case in cases) == 10
    assert sum(case["category"] == "exact-title" for case in cases) == 21


def test_curated_topic_candidates_have_verifiable_source_evidence():
    dataset_path = Path("evals/cases_curated.json")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = dataset["cases"]
    topic_cases = [case for case in cases if case["category"] == "topic"]

    assert len(topic_cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    source_records = {}
    source_paths = [
        *Path("data/ai").glob("arxiv_cs_recent_3months_oai_part*.jsonl"),
        *Path("data/ai_references/arxiv_ai_references_api").glob(
            "arxiv_ai_references_api_part*.jsonl"
        ),
    ]
    for source_path in source_paths:
        for line in source_path.read_text(encoding="utf-8-sig").splitlines():
            record = json.loads(line)
            source_id = record.get("id", record.get("arxiv_id", ""))
            paper_id = source_id.rsplit("/", maxsplit=1)[-1].removesuffix("v1")
            if paper_id:
                source_records.setdefault(paper_id, (record, source_path.as_posix()))

    for case in topic_cases:
        assert 1 <= len(case["expected_ids"]) <= 5
        assert case["label_rationale"].strip()
        assert case["source_evidence"]

        # The status may only claim as much review as the file can show, so that nobody
        # promotes these to human_reviewed without recording who did it.
        assert case["review_status"] in {
            "pending_human_review",
            "assistant_reviewed",
            "human_reviewed",
        }
        if case["review_status"] != "pending_human_review":
            review = case["review"]
            assert review["reviewed_on"] and review["reviewed_by"] and review["method"]
            # Every gold ID carries a verdict, and a judgment call must say why.
            assert set(review["gold_id_checks"]) == set(case["expected_ids"])
            for check in review["gold_id_checks"].values():
                assert check["verdict"] in {"keep", "flag", "drop"}
                assert check["method"] in {"mechanical", "judgment"}
                assert check["evidence"].strip()
            assert not (case["expected_answer_contains"] and review.get("retired_answer_phrase"))
        if case["review_status"] == "assistant_reviewed":
            assert "사람 검토가 아니" in review["reviewed_by"]

        # A required phrase, where one remains, still has to be backed by an abstract excerpt.
        for fact in case["expected_answer_contains"]:
            assert any(
                evidence["field"] == "abstract"
                and fact.casefold() in evidence["excerpt"].casefold()
                for evidence in case["source_evidence"]
            )
        for evidence in case["source_evidence"]:
            source, source_file = source_records[evidence["arxiv_id"]]
            assert evidence["field"] in {"title", "abstract"}
            assert evidence["source_file"] == source_file
            assert evidence["excerpt"] in source[evidence["field"]]
        assert set(case["expected_ids"]).issubset(
            {evidence["arxiv_id"] for evidence in case["source_evidence"]}
        )
