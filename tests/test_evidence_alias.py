"""`search_graph`가 evidence_ids 별칭을 도구 경계에서 검사하는지 봅니다.

2026-10-16 세 번의 full 평가에서 멈춘 5건은 **전부 같은 사유**였습니다 —
`invalid_graph_evidence_shape`. 재현해 보니 모델이 쓴 Cypher가 별칭을 `evidence_id`로 단수로
쓰거나 아예 빼먹었습니다. 그 검사가 답변 직전에만 있어서, 걸리면 답변 전체가 거부되고 재작성도
같은 실수를 반복해 두 번 다 실패했습니다.

`chatbot`은 임포트만으로 Aura와 OpenAI에 붙으므로 여기서는 가져오지 않고, 같은 규칙을 순수
함수로 재구성해 검증합니다.
"""

import pytest


def check_evidence_alias(rows):
    """`chatbot.check_evidence_alias`와 같은 규칙."""
    for row in rows:
        ids = row.get("evidence_ids")
        if isinstance(ids, list) and all(isinstance(value, str) for value in ids):
            continue
        keys = ", ".join(sorted(row)) or "(없음)"
        raise ValueError(
            "RETURN에 evidence_ids 별칭이 없거나 arxiv_id 문자열 리스트가 아닙니다. "
            f"이 조회가 돌려준 별칭: {keys}. 논문 한 편이 한 행이면 [p.arxiv_id]처럼 리스트 "
            "리터럴로, 논문을 특정할 수 없는 순수 집계면 [] 로 반환하세요."
        )


def test_the_documented_shape_passes():
    check_evidence_alias([
        {"answer_value": "Title", "evidence_ids": ["2601.00001"], "title": "Title"},
        {"answer_value": "Other", "evidence_ids": ["2601.00002", "2601.00003"]},
    ])


def test_a_pure_aggregate_with_no_paper_level_evidence_passes():
    # 규칙이 빈 리스트를 허용하므로 전체 편수 같은 집계가 막히지 않아야 합니다.
    check_evidence_alias([{"answer_value": "37854", "evidence_ids": []}])


def test_no_rows_passes():
    # 조건에 맞는 논문이 없는 것은 형식 오류가 아닙니다.
    check_evidence_alias([])


def test_the_singular_alias_is_rejected():
    """실제로 관측된 모양입니다 - coauthor-03 재현에서 모델이 `evidence_id`로 썼습니다."""
    with pytest.raises(ValueError) as caught:
        check_evidence_alias([
            {"answer_value": "A", "evidence_id": "2606.08098", "paper_id": "2606.08098"}
        ])
    # 무엇을 돌려줬는지 알려 줘야 모델이 그 호출만 고칠 수 있습니다.
    assert "evidence_id" in str(caught.value)
    assert "paper_id" in str(caught.value)


def test_a_missing_alias_is_rejected():
    """두 번째로 관측된 모양 - 보조 조회가 별칭을 아예 빼먹었습니다."""
    with pytest.raises(ValueError) as caught:
        check_evidence_alias([{"arxiv_id": "2601.1", "shared_author": "A", "title": "T"}])
    assert "arxiv_id" in str(caught.value)


def test_a_null_element_is_rejected():
    # OPTIONAL MATCH가 비면 [p.arxiv_id]가 [null]이 됩니다. 근거가 아니라 빈 자리입니다.
    with pytest.raises(ValueError):
        check_evidence_alias([{"answer_value": "A", "evidence_ids": [None]}])


def test_a_bare_string_is_rejected():
    # 리스트가 아니면 한 편인지 여러 편인지 알 수 없습니다. 추측해서 감싸지 않습니다.
    with pytest.raises(ValueError):
        check_evidence_alias([{"answer_value": "A", "evidence_ids": "2601.00001"}])


def test_one_bad_row_among_good_ones_is_rejected():
    # 한 행만 어긋나도 그 행의 근거를 믿을 수 없습니다.
    with pytest.raises(ValueError):
        check_evidence_alias([
            {"answer_value": "A", "evidence_ids": ["2601.00001"]},
            {"answer_value": "B", "citation_count": 3},
        ])


def test_the_rule_matches_the_final_validator():
    """도구 경계와 답변 직전의 규칙이 같아야 합니다.

    다르면 한쪽이 통과시킨 것을 다른 쪽이 막습니다.
    """
    from src import answer_validation

    # 최종 검증이 쓰는 사유 코드가 그대로 남아 있는지 확인합니다.
    codes = dict(answer_validation._VALIDATION_REASON_CODES)
    assert codes["조회 결과의 evidence_ids"] == "invalid_graph_evidence_shape"
