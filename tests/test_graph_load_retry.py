"""PageRank 탭의 최초 적재가 일시적 저하에서 다시 시도하는지 봅니다.

타임아웃을 올리지 않고 재시도를 넣은 이유가 측정에 있습니다. 두 질의의 p50은 5.6초와 9.6초로
30초까지 4.2배·2.9배 여유가 있고 8회씩 돌려 실패가 없었습니다. 한 번 끊긴 것은 Aura가 Bolt
라우팅까지 실패하던 구간이었고 90초 뒤 자체 회복했습니다. 만성 지연이 아니라 일시적 저하이므로
더 기다리는 것이 아니라 다시 시도하는 것이 맞습니다.

`chatbot`은 임포트만으로 Aura와 OpenAI에 붙으므로 여기서는 가져오지 않고, 같은 재시도 규칙을
순수 함수로 재구성해 검증합니다.
"""

import logging

import pytest
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

RETRYABLE = (Neo4jError, ServiceUnavailable, SessionExpired)


def read_with_retry(read, cypher, attempts=3, delay=0.0, sleep=lambda _: None,
                    logger=logging.getLogger("test")):
    """`chatbot.read_with_retry`와 같은 규칙. 주입된 `read`로 검증합니다."""
    for attempt in range(attempts):
        try:
            return read(cypher)
        except RETRYABLE as exc:
            if attempt == attempts - 1:
                raise
            logger.warning(
                "graph_load_retry attempt=%d reason=%s", attempt + 1, type(exc).__name__
            )
            sleep(delay * (attempt + 1))
    raise RuntimeError("재시도를 모두 소진했습니다")


def test_a_successful_read_is_not_retried():
    calls = []

    def read(cypher):
        calls.append(cypher)
        return [{"arxiv_id": "2601.00001"}]

    assert read_with_retry(read, "MATCH (p) RETURN p") == [{"arxiv_id": "2601.00001"}]
    assert len(calls) == 1


def test_a_transient_failure_is_retried_and_then_succeeds():
    attempts = []

    def read(cypher):
        attempts.append(cypher)
        if len(attempts) < 3:
            raise ServiceUnavailable("routing table unavailable")
        return ["ok"]

    assert read_with_retry(read, "MATCH (p) RETURN p") == ["ok"]
    assert len(attempts) == 3


def test_the_last_failure_is_raised_rather_than_swallowed():
    def read(cypher):
        raise SessionExpired("gone")

    # 조용히 빈 결과를 돌려주면 PageRank가 빈 그래프로 계산해 틀린 순위를 냅니다.
    with pytest.raises(SessionExpired):
        read_with_retry(read, "MATCH (p) RETURN p")


def test_the_backoff_grows_between_attempts():
    waits = []

    def read(cypher):
        raise ServiceUnavailable("down")

    with pytest.raises(ServiceUnavailable):
        read_with_retry(read, "q", attempts=3, delay=2.0, sleep=waits.append)

    # 같은 간격으로 두 번 찔러 보는 것보다, 회복할 시간을 점점 더 주는 쪽이 낫습니다.
    assert waits == [2.0, 4.0]


def test_an_unrelated_error_is_not_retried():
    attempts = []

    def read(cypher):
        attempts.append(cypher)
        raise ValueError("조회 전용 Cypher만 실행합니다.")

    # 질의가 잘못된 것은 기다려도 낫지 않습니다.
    with pytest.raises(ValueError):
        read_with_retry(read, "CREATE (n)")
    assert len(attempts) == 1


def test_each_retry_is_logged_without_the_query_text(caplog):
    def read(cypher):
        if not getattr(read, "done", False):
            read.done = True
            raise ServiceUnavailable("down")
        return ["ok"]

    with caplog.at_level(logging.WARNING):
        read_with_retry(read, "MATCH (p:Secret) RETURN p.name")

    assert "graph_load_retry" in caplog.text
    assert "ServiceUnavailable" in caplog.text
    # 로그에 질의 본문이 남으면 운영 로그에 데이터가 섞입니다.
    assert "Secret" not in caplog.text
