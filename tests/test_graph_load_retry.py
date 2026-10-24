"""PageRank 탭의 최초 적재가 일시적 저하에서 다시 시도하는지 봅니다.

타임아웃을 올리지 않고 재시도를 넣은 이유가 측정에 있습니다. 두 질의의 p50은 5.6초와 9.6초로
30초까지 4.2배·2.9배 여유가 있고 8회씩 돌려 실패가 없었습니다. 한 번 끊긴 것은 Aura가 Bolt
라우팅까지 실패하던 구간이었고 90초 뒤 자체 회복했습니다. 만성 지연이 아니라 일시적 저하이므로
더 기다리는 것이 아니라 다시 시도하는 것이 맞습니다.

`chatbot`은 임포트만으로 Aura와 OpenAI에 붙으므로 여기서는 가져오지 않고, 같은 재시도 규칙을
순수 함수로 재구성해 검증합니다.

**사본이라는 것이 이 파일의 약점입니다.** 2026-10-21에 실물에 `seconds=`와 마지막 시도
로그가 붙었는데(ADR-052·ADR-053) 이 사본은 그대로였습니다 — 손으로 유지하는 두 벌이
갈라진 자리입니다. 그래서 사본을 맞추는 것으로 끝내지 않고, **실물 소스를 읽어 같은 필드가
있는지 확인하는 테스트**를 아래에 함께 둡니다. 사본이 다시 갈라지면 그쪽이 막습니다.
"""

import ast
import logging
from pathlib import Path

import pytest
from neo4j.exceptions import Neo4jError, ServiceUnavailable, SessionExpired

RETRYABLE = (Neo4jError, ServiceUnavailable, SessionExpired)


def read_with_retry(read, cypher, attempts=3, delay=0.0, sleep=lambda _: None,
                    clock=lambda: 0.0, logger=logging.getLogger("test")):
    """`chatbot.read_with_retry`와 같은 규칙. 주입된 `read`로 검증합니다.

    `clock`은 경과 시간을 재는 자리입니다 - 실물은 `time.perf_counter`를 씁니다. 그 초가
    저하 모드를 구별하는 유일한 신호입니다(블랙홀과 거부가 둘 다 `ServiceUnavailable`).
    """
    for attempt in range(attempts):
        started = clock()
        try:
            return read(cypher)
        except RETRYABLE as exc:
            elapsed = clock() - started
            if attempt == attempts - 1:
                logger.warning(
                    "graph_load_failed attempt=%d reason=%s seconds=%.1f",
                    attempt + 1, type(exc).__name__, elapsed,
                )
                raise
            logger.warning(
                "graph_load_retry attempt=%d reason=%s seconds=%.1f",
                attempt + 1, type(exc).__name__, elapsed,
            )
            sleep(delay * (attempt + 1))
    raise RuntimeError("재시도를 모두 소진했습니다")


def real_source() -> str:
    """실물 `chatbot.read_with_retry`의 소스. **임포트하지 않고** 파싱만 합니다."""
    tree = ast.parse(Path("src/chatbot.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "read_with_retry":
            return ast.get_source_segment(Path("src/chatbot.py").read_text(encoding="utf-8"), node)
    raise AssertionError("chatbot.read_with_retry 를 찾지 못했습니다")


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

    messages = [record.getMessage() for record in caplog.records]

    assert any(m.startswith("graph_load_retry") for m in messages)
    assert any("ServiceUnavailable" in m for m in messages)
    # 로그에 질의 본문이 남으면 운영 로그에 데이터가 섞입니다.
    assert not any("Secret" in m for m in messages)


def test_the_elapsed_seconds_are_logged(caplog):
    """**초가 저하 모드를 구별하는 유일한 신호입니다**(ADR-052).

    블랙홀은 획득 타임아웃을 꽉 채우고(약 60초) 거부는 약 2초인데, 예외 유형은 둘 다
    `ServiceUnavailable`입니다. 초가 없으면 사고 기록에서 둘을 가를 수 없습니다.
    """
    ticks = iter([0.0, 60.1, 0.0, 0.0])

    def read(cypher):
        raise ServiceUnavailable("routing table unavailable")

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceUnavailable):
        read_with_retry(read, "q", attempts=2, clock=lambda: next(ticks))

    assert "seconds=60.1" in caplog.text


def test_the_final_failure_is_logged_too(caplog):
    """예전에는 마지막 시도를 그냥 올려보냈습니다 - 세 번 실패한 사고가 두 줄로만 남았습니다."""
    def read(cypher):
        raise ServiceUnavailable("down")

    with caplog.at_level(logging.WARNING), pytest.raises(ServiceUnavailable):
        read_with_retry(read, "q", attempts=3)

    # `caplog.text`가 아니라 메시지를 셉니다 - 그 문자열에는 **파일 이름**이 섞여 있고
    # 이 파일 이름이 "graph_load_retry"를 포함해서 3줄이 3번 더 세집니다.
    messages = [record.getMessage() for record in caplog.records]

    assert sum(m.startswith("graph_load_retry") for m in messages) == 2
    assert sum(m.startswith("graph_load_failed") for m in messages) == 1


def test_the_copy_has_not_drifted_from_the_real_function():
    """**이 파일이 사본이라는 약점을 막는 테스트입니다.**

    위 재구성이 실물과 갈라져도 다른 테스트는 전부 통과합니다 - 사본만 보기 때문입니다.
    그래서 실물 소스를 읽어 같은 필드가 있는지 확인합니다. 소스를 읽는 것이 거친 방법이지만,
    `chatbot`은 임포트만으로 Aura와 OpenAI에 붙으므로 부를 수가 없습니다.
    """
    source = real_source()

    for fragment in (
        "perf_counter",                 # 경과 시간을 실제로 잰다
        "graph_load_retry",             # 중간 시도
        "graph_load_failed",            # 마지막 시도 - 예전에는 없었습니다
        "seconds=%.1f",                 # 모드를 구별하는 필드
        "attempt=%d",
        "reason=%s",
    ):
        assert fragment in source, fragment

    # 그리고 질의 본문을 넘기지 않아야 합니다. `cypher`가 로그 인자에 들어가면 안 됩니다.
    for line in source.splitlines():
        if "graph_logger.warning" in line or "seconds=%.1f" in line:
            assert "cypher" not in line, line
