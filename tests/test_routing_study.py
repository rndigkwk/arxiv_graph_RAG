"""전후 비교가 **섞인 측정을 거부하는지** 검증합니다.

2026-10-17에 판사 모델이 다른 표본을 같은 분포로 읽어 잘못된 결론을 냈습니다. 그 실수는
숫자가 좋아 보일 때 하기 쉬우므로, 비교 자체가 거부해야 합니다. 그리고 보고서 두 모양
(`compare`/`agent`)에서 **같은 쪽**을 꺼내는지도 봅니다 - 벡터 기준선 행을 섞어 세면 라우팅
효과가 아니라 다른 시스템을 재는 것입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals.routing_study import (
    answers,
    called_search_papers,
    load,
    models,
    summarize,
)

STUDY = Path("reports/routing_study.json")


def test_the_agent_rows_are_taken_from_either_report_shape():
    compare_shaped = {
        "results": [
            {"id": "a", "graph_rag": {"id": "a", "hit_at_k": 1.0}, "vector_only": {"id": "a"}}
        ]
    }
    agent_shaped = {"results": [{"id": "a", "hit_at_k": 1.0}]}

    assert answers(compare_shaped) == [{"id": "a", "hit_at_k": 1.0}]
    assert answers(agent_shaped) == [{"id": "a", "hit_at_k": 1.0}]


def test_the_vector_call_is_counted_not_guessed_when_tool_names_exist():
    assert called_search_papers({"tool_names": ["search_graph", "search_papers"]}) is True
    assert called_search_papers({"tool_names": ["search_graph"]}) is False
    # 예전 보고서에는 도구 이름이 없습니다. 그때는 임베딩 토큰이 곧 `search_papers`였습니다.
    assert called_search_papers({"embedding_tokens_estimate": 12}) is True
    assert called_search_papers({"embedding_tokens_estimate": 0}) is False


def test_an_unrecorded_vector_call_is_unknown_not_zero():
    """0으로 적으면 "부르지 않았다"로 읽혀 결론이 뒤집힙니다."""
    assert called_search_papers({"id": "a"}) is None

    counted = summarize([{"id": "a"}, {"id": "b", "tool_names": ["search_papers"]}])

    # 모르는 한 건은 비율에서 빠지고, 몇 건을 셌는지가 남습니다.
    assert counted["search_papers_rate"] == 1.0
    assert counted["search_papers_measured"] == 1


def test_cost_carries_a_median_beside_the_mean():
    """12건에서 한 건이 평균을 끌고 갑니다 - 2차 측정의 36,735 토큰이 그랬습니다."""
    rows = [{"chat_input_tokens": 11000} for _ in range(5)]
    rows.append({"chat_input_tokens": 36735})

    counted = summarize(rows)

    assert counted["chat_input_tokens_p50"] == 11000
    assert counted["chat_input_tokens"] > counted["chat_input_tokens_p50"]


def test_different_models_are_detected_before_anything_is_compared():
    reports = [
        {"metadata": {"model": "gpt-6-luna"}},
        {"metadata": {"model": "gpt-4o-mini"}},
    ]

    assert len(models(reports)) == 2


def test_families_filter_keeps_only_the_asked_families(tmp_path):
    path = tmp_path / "r.json"
    path.write_text(
        json.dumps({"results": [
            {"id": "a", "category": "coauthor"},
            {"id": "b", "category": "author"},
        ]}),
        encoding="utf-8",
    )

    assert [row["id"] for row in load([path], ("coauthor",))] == ["a"]
    assert len(load([path], ())) == 2


def test_the_recorded_study_still_says_what_adr_024_claims():
    """ADR-024의 표가 이 파일을 가리킵니다. 숫자가 갈라지면 둘 중 하나가 거짓입니다."""
    study = json.loads(STUDY.read_text(encoding="utf-8"))

    assert study["model"] == "gpt-6-luna"
    # 지시를 지켰다는 주장의 근거입니다.
    assert study["after"]["search_papers_rate"] == 0
    assert study["after"]["search_papers_measured"] == 12
    # 정확도를 지켰다는 주장의 근거입니다.
    assert study["after"]["answer_evidence_precision"] == 1.0
    assert study["after"]["failed"] == 0
    # 토큰은 줄고 지연은 늘었습니다. 두 번째를 지우면 보고가 한쪽만 말합니다.
    assert study["deltas"]["chat_input_tokens_p50"] < 0
    assert study["deltas"]["elapsed_seconds_p50"] > 0
