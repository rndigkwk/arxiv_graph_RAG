"""RAGAS 입력을 만드는 규칙과 요약을 검증합니다.

점수 자체는 판정 모델이 냅니다. 여기서 지키는 것은 **무엇을 입력으로 넘기는가**입니다 —
그래프 조건 질문에서 모델이 본 것은 초록이 아니라 Cypher 행이므로, ID로 초록을 되찾아 문맥이라고
넘기면 모델이 아니라 우리 입력 실수를 재게 됩니다.
"""

from evals.ragas_study import RETRIEVAL_METRICS, samples, summarize
from evals.run import generator_contexts


def compare_report(**overrides):
    row = {
        "id": "citing-01",
        "answer": "Three papers cite it.",
        "contexts": ["answer_value=A | evidence_ids=['2601.1']"],
        "failed": False,
        "recall_at_k": 1.0,
        "hit_at_k": 1.0,
        "answer_evidence_recall": 1.0,
    }
    row.update(overrides)
    return {
        "mode": "compare",
        "results": [
            {
                "id": "citing-01",
                "category": "citing",
                "question": "Which papers cite X?",
                "vector_only": {"id": "citing-01", "answer": "irrelevant"},
                "graph_rag": row,
            }
        ],
    }


def test_the_product_path_is_scored_not_the_contrast():
    rows, skipped = samples(compare_report())

    assert len(rows) == 1
    assert rows[0]["answer"] == "Three papers cite it."
    assert skipped == []


def test_a_failed_case_is_skipped_with_its_reason():
    """조용히 빠지면 평균이 무엇의 평균인지 알 수 없습니다."""
    rows, skipped = samples(compare_report(failed=True))

    assert rows == []
    assert skipped and "citing-01" in skipped[0]


def test_an_old_report_without_contexts_is_skipped_rather_than_guessed():
    """ID로 초록을 되찾아 넘기면 모델이 아니라 우리 입력 실수를 재게 됩니다."""
    rows, skipped = samples(compare_report(contexts=[]))

    assert rows == []
    assert "contexts" in skipped[0]


def test_an_empty_answer_is_skipped():
    rows, skipped = samples(compare_report(answer="   "))

    assert rows == []
    assert "답변" in skipped[0]


def test_the_question_can_come_from_the_case_file():
    report = compare_report()
    del report["results"][0]["question"]
    report["cases"] = [{"id": "citing-01", "question": "Which papers cite X?"}]

    rows, skipped = samples(report)

    assert rows[0]["question"] == "Which papers cite X?"
    assert skipped == []


def test_a_case_with_no_question_anywhere_is_skipped():
    report = compare_report()
    del report["results"][0]["question"]

    rows, skipped = samples(report)

    assert rows == []
    assert "질문" in skipped[0]


def test_graph_rows_are_the_context_for_a_graph_question():
    """초록이 아니라 Cypher 행이 문맥입니다. 저자 이름이나 피인용 수는 초록에 없습니다."""
    contexts = generator_contexts({
        "rows": [{"answer_value": "Limin Sun", "evidence_ids": ["2601.1"]}],
        "papers": [],
    })

    assert contexts == ["answer_value=Limin Sun | evidence_ids=['2601.1']"]


def test_vector_hits_contribute_title_and_abstract():
    contexts = generator_contexts({
        "rows": [],
        "papers": [
            {"arxiv_id": "2601.2", "title": "Graph Neural Networks", "abstract": "We study"}
        ],
    })

    assert contexts == ["Graph Neural Networks\nWe study"]


def test_duplicate_evidence_is_kept_once():
    # 같은 근거가 여러 호출에서 나오면 중복이 정밀도를 부풀립니다.
    row = {"answer_value": "A", "evidence_ids": ["2601.1"]}
    contexts = generator_contexts({"rows": [row, dict(row)], "papers": []})

    assert len(contexts) == 1


def test_the_summary_separates_the_retrieval_and_generation_sides():
    rows = [
        {
            "id": "a", "family": "citing",
            "faithfulness": 1.0, "answer_relevancy": 0.9,
            "context_precision": 0.5, "context_relevance": 0.4,
            "recall_at_k": 1.0, "hit_at_k": 1.0, "answer_evidence_recall": 1.0,
        }
    ]
    summary = summarize(rows, skipped=["b: 답변 없음"], model="gpt-test")

    diagnosis = summary["diagnosis"]
    assert set(diagnosis["retrieval_side"]) == set(RETRIEVAL_METRICS)
    assert diagnosis["generation_side"]["faithfulness"] == 1.0
    # 골드 ID로 정확히 센 값을 나란히 둬야 LLM 추정과 어긋나는 것을 볼 수 있습니다.
    assert diagnosis["exact_retrieval"]["recall_at_k"] == 1.0
    assert summary["cases_skipped"] == 1
    assert summary["judge_model"] == "gpt-test"


def test_a_metric_error_is_counted_not_hidden():
    rows = [{
        "id": "a", "family": "citing",
        "faithfulness": None, "faithfulness_error": "TimeoutError: ...",
        "answer_relevancy": 0.9, "context_precision": 0.5, "context_relevance": 0.4,
    }]
    summary = summarize(rows, skipped=[], model="gpt-test")

    assert summary["metrics"]["faithfulness"] is None
    assert summary["metric_errors"]["faithfulness"] == 1


def test_the_two_unused_metrics_say_why_they_are_unused():
    """쓰지 않은 지표를 적어 두지 않으면 "RAGAS 6종을 했다"로 읽힙니다."""
    summary = summarize([], skipped=[], model="gpt-test")

    assert "context_recall" in summary["not_measured"]
    assert "answer_correctness" in summary["not_measured"]


# --- 조회문이 문맥에 들어가야 하는 이유 -----------------------------------------------


def test_the_executed_cypher_rides_with_its_rows():
    r"""관계는 행이 아니라 조회문에 적혀 있습니다.

    조회문을 빼고 행만 넘겼을 때 `citing-01`의 faithfulness가 **0.0**으로 나왔습니다. 답변은
    정확했고(근거 recall 1.000) 제목·분류도 행과 일치했는데, 답변의 핵심 주장인 "이 논문들이
    X를 **인용한다**"가 행에는 없었습니다. 조회문을 같은 문맥에 담은 뒤 **1.0**이 됐습니다.
    """
    contexts = generator_contexts({
        "tool_calls": [{
            "name": "search_graph",
            "status": "success",
            "output": {
                "cypher": "MATCH (c)-[:REFERENCES]->(p)\n  WHERE p.title = 'X' RETURN 1",
                "rows": [{"answer_value": "A", "evidence_ids": ["2601.1"]}],
            },
        }],
    })

    assert len(contexts) == 1
    # 관계 이름이 문맥에 있어야 판정이 "인용한다"를 확인할 수 있습니다.
    assert "REFERENCES" in contexts[0]
    assert "answer_value=A" in contexts[0]
    # 줄바꿈이 섞인 Cypher는 한 줄로 눌러 담습니다.
    assert "Cypher: MATCH (c)-[:REFERENCES]->(p) WHERE p.title = 'X' RETURN 1" in contexts[0]


def test_one_retrieval_call_is_one_context():
    """Context Precision은 "쓸모 있는 근거가 위에 있는가"를 잽니다.

    조회 단위로 묶지 않으면 행 하나하나가 문맥이 되어 그 순서가 뜻을 잃습니다.
    """
    contexts = generator_contexts({
        "tool_calls": [
            {
                "name": "search_graph",
                "status": "success",
                "output": {"cypher": "MATCH (a) RETURN 1", "rows": [{"x": 1}, {"x": 2}]},
            },
            {
                "name": "search_graph",
                "status": "success",
                "output": {"cypher": "MATCH (b) RETURN 2", "rows": [{"y": 3}]},
            },
        ],
    })

    assert len(contexts) == 2


def test_a_failed_query_is_not_offered_as_evidence():
    # 오류가 돌아온 조회는 근거가 아닙니다.
    contexts = generator_contexts({
        "tool_calls": [{
            "name": "search_graph",
            "status": "success",
            "output": {"cypher": "MATCH bad", "rows": [], "error": "구문 오류"},
        }],
    })

    assert contexts == []


def test_a_huge_result_is_capped_and_says_so():
    """모델은 전부 봤지만 문맥을 무한히 넘길 수는 없습니다. 자른 사실을 적습니다."""
    from evals.run import MAX_CONTEXT_ROWS

    rows = [{"n": value} for value in range(MAX_CONTEXT_ROWS + 7)]
    contexts = generator_contexts({
        "tool_calls": [{
            "name": "search_graph",
            "status": "success",
            "output": {"cypher": "MATCH (p) RETURN p", "rows": rows},
        }],
    })

    assert "7 more rows" in contexts[0]


def test_tool_calls_are_preferred_over_the_flattened_rows():
    """두 경로가 다 있으면 조회 단위가 이깁니다. 평탄화된 행은 조회문을 잃습니다."""
    contexts = generator_contexts({
        "tool_calls": [{
            "name": "search_graph",
            "status": "success",
            "output": {"cypher": "MATCH (a) RETURN 1", "rows": [{"x": 1}]},
        }],
        "rows": [{"x": 1}],
    })

    assert len(contexts) == 1
    assert contexts[0].startswith("Cypher: ")
