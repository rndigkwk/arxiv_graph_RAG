"""파이프라인 전용 Neo4j 접속입니다. 앱과 분리합니다.

`chatbot.run_read`는 `EXPLAIN`으로 조회 전용임을 확인하고서야 실행합니다. 앱이 절대 쓰지
못한다는 보장이 보안 측면에서 의미가 있으므로, 적재를 위해 그 보장을 느슨하게 만들지 않고
파이프라인이 자기 드라이버를 따로 엽니다.

`chatbot`을 가져오지 않는 또 다른 이유는 비용입니다. 그 모듈은 임포트하는 것만으로 LLM과
임베딩 클라이언트를 만들고 벡터 인덱스 상태를 확인합니다. 수집 파이프라인에는 하나도
필요하지 않습니다.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase, Query
from neo4j.api import READ_ACCESS, WRITE_ACCESS

READ_TIMEOUT = 60
WRITE_TIMEOUT = 300


def env(*names: str, default: str | None = None) -> str | None:
    """Return the first environment variable that has a value, never printing it."""
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


def load_env(root: Path | None = None) -> None:
    """Load the project's .env when python-dotenv is available, without overriding the shell."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    base = root or Path(__file__).resolve().parents[1]
    load_dotenv(base / ".env", override=False)


class Graph:
    """A driver plus the two call shapes the pipeline needs."""

    def __init__(self) -> None:
        load_env()
        uri = env("NEO4J_URI")
        user = env("NEO4J_USER", "NEO4J_USERNAME")
        password = os.getenv("NEO4J_PASSWORD") or ""
        missing = [
            name for name, value in
            (("NEO4J_URI", uri), ("NEO4J_USER/NEO4J_USERNAME", user),
             ("NEO4J_PASSWORD", password))
            if not value
        ]
        if missing:
            raise ValueError(f".env에 다음 값이 없습니다: {missing}")
        self.database = env("NEO4J_DATABASE", default="neo4j")
        # **`connection_acquisition_timeout`을 주지 않습니다 — 기본 60초를 그대로 씁니다.**
        # 평상시에는 과하지만(최악 1.39초, 43배 여유) 그 값이 아니라 **저하 구간에서 하중을
        # 받습니다**(ADR-052). 라우팅이 죽어 패킷이 버려지면 획득이 타임아웃을 꽉 채우므로,
        # `read_with_retry`의 시도 간격을 사실상 이 값이 만듭니다 — 대기 2초+4초가 아닙니다.
        # 2026-10-15의 Aura 자체 회복이 90초였고, 마지막 시도가 그 뒤에 닿으려면
        # `2T + 6 >= 90`, 즉 **T >= 42초**가 필요합니다. 60초는 그 위에 18초 여유로 있습니다.
        # **"43배 여유"만 보고 내리면 세 시도가 전부 저하 구간 안으로 들어갑니다.**
        self.driver = GraphDatabase.driver(
            uri, auth=(user, password), notifications_min_severity="OFF"
        )

    def read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.driver.session(
            database=self.database, default_access_mode=READ_ACCESS
        ) as session:
            result = session.run(Query(cypher, timeout=READ_TIMEOUT), params or {})
            return [record.data() for record in result]

    def write(self, cypher: str, params: dict[str, Any] | None = None) -> dict[str, int]:
        """Run a write and return the counters, so a caller can assert what actually changed."""
        with self.driver.session(
            database=self.database, default_access_mode=WRITE_ACCESS
        ) as session:
            summary = session.run(
                Query(cypher, timeout=WRITE_TIMEOUT), params or {}
            ).consume()
            counters = summary.counters
            return {
                "nodes_created": counters.nodes_created,
                "relationships_created": counters.relationships_created,
                "properties_set": counters.properties_set,
            }

    def close(self) -> None:
        self.driver.close()


@contextmanager
def connect() -> Iterator[Graph]:
    graph = Graph()
    try:
        yield graph
    finally:
        graph.close()
