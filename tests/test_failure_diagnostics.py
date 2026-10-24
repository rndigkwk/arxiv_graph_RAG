"""실패한 사례가 **왜** 멈췄는지 보고서에 남는지 봅니다.

2026-10-16에 평가에서 멈춘 5건의 원인을 알려고 에이전트를 다시 돌려야 했습니다. 보고서에는
오류 문자열만 남고, 사유 코드는 CI 로그에만 있고, 둘을 이어 붙일 `request_id`가 보고서에
없었습니다. 되짚을 수 없는 실패 기록은 기록이 아닙니다.

`chatbot`은 임포트만으로 Aura와 OpenAI에 붙으므로 여기서는 가져오지 않고, 예외 타입과 하네스가
기록하는 규칙을 같은 모양으로 재구성해 검증합니다.
"""

import pytest

from src.answer_validation import reason_code


class AnswerValidationError(ValueError):
    """`chatbot.AnswerValidationError`와 같은 모양."""

    def __init__(self, message, *, request_id, stage, reason, attempts, cyphers):
        super().__init__(message)
        self.request_id = request_id
        self.stage = stage
        self.reason = reason
        self.attempts = attempts
        self.cyphers = list(cyphers)

    def diagnostics(self):
        return {
            "request_id": self.request_id,
            "stage": self.stage,
            "reason": self.reason,
            "attempts": self.attempts,
            "cyphers": self.cyphers,
        }


def record_failure(exc):
    """`evals/run.py`가 실패 행에 담는 규칙."""
    return {
        "failed": True,
        "error": f"{type(exc).__name__}: {exc}",
        **(exc.diagnostics() if hasattr(exc, "diagnostics") else {}),
    }


def make_error(**overrides):
    fields = {
        "request_id": "11322daf",
        "stage": "answer_format_citation",
        "reason": "invalid_graph_evidence_shape",
        "attempts": 2,
        "cyphers": ["MATCH (p:ArxivPaper) RETURN p.arxiv_id AS answer_value"],
    }
    fields.update(overrides)
    return AnswerValidationError("답변의 형식·인용을 확인하지 못했습니다.", **fields)


def test_the_error_is_still_a_value_error():
    """앱과 평가 하네스가 이미 ValueError를 잡습니다.

    그 동작이 바뀌면 실패가 밖으로 새어 나갑니다.
    """
    assert isinstance(make_error(), ValueError)
    with pytest.raises(ValueError):
        raise make_error()


def test_a_failed_row_records_the_reason_code_not_only_the_message():
    row = record_failure(make_error())

    # 사유 코드가 있어야 "다섯 건이 같은 결함인가"를 보고서만 보고 셀 수 있습니다.
    assert row["reason"] == "invalid_graph_evidence_shape"
    assert row["stage"] == "answer_format_citation"
    assert row["failed"] is True


def test_a_failed_row_carries_the_request_id_that_joins_it_to_the_logs():
    row = record_failure(make_error(request_id="a70a2d86"))

    # CI 로그의 `answer_validation_failed request_id=...` 줄과 이어 붙는 열입니다.
    assert row["request_id"] == "a70a2d86"


def test_a_failed_row_keeps_every_cypher_the_model_wrote():
    """조회 하나가 별칭을 빼먹으면 답변 전체가 거부됩니다(ADR-019).

    어느 조회였는지 알아야 고칠 수 있습니다.
    """
    queries = [
        'MATCH (a)-[:AUTHORED]->(p) RETURN p.arxiv_id AS answer_value, [p.arxiv_id] AS ev_ids',
        'MATCH (a)-[:AUTHORED]->(p) RETURN p.arxiv_id AS arxiv_id, a.name AS shared_author',
    ]
    row = record_failure(make_error(cyphers=queries))

    assert row["cyphers"] == queries
    assert row["attempts"] == 2


def test_an_ordinary_value_error_still_records_without_diagnostics():
    """검증 외의 실패(예: 생성 중단)도 한 건 때문에 전체 평가가 멈추면 안 됩니다."""
    row = record_failure(ValueError("답변 생성이 완료되지 않았습니다."))

    assert row["failed"] is True
    assert "reason" not in row
    assert "ValueError" in row["error"]


def test_the_reason_code_matches_what_the_logger_writes():
    """예외가 들고 나오는 코드와 로그에 찍히는 코드가 같아야 조인이 성립합니다."""
    observed = ValueError(
        "조회 결과의 evidence_ids가 arxiv_id 목록이 아닙니다. 다시 질문해 주세요."
    )
    assert reason_code(observed) == "invalid_graph_evidence_shape"


def test_an_unknown_message_is_labelled_rather_than_dropped():
    # 분류되지 않는 실패도 코드를 받아야 집계에서 사라지지 않습니다.
    assert reason_code(ValueError("처음 보는 실패")) == "unclassified_ValueError"
