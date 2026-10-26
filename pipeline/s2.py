"""Semantic Scholar에서 새 논문의 참고문헌을 받습니다 - 이전 계획 2단계.

주간 수집의 "인용 보강"은 지금 **그래프 자신을** 조회합니다(`pipeline/ingest.py`의 `lookup`).
새로 들어온 논문은 그래프에 나가는 간선이 없으므로 결과가 항상 비어 있고, 2026-10-24에 적재한
OAI 논문 934편 중 888편이 참고문헌 0개입니다. 그래프를 처음 지을 때는 이 API로 씨앗 6,467편의
참고문헌을 받아 38,665편까지 키웠습니다
(`notebooks/arxiv_citations_semantic_scholar_to_jsonl.ipynb`).
같은 일을 매주 합니다.

**키 없이 천천히 받습니다.** `/paper/batch`는 한 번에 500편을 받으므로 주 1,000편이면 요청 두
번입니다. 키가 없으면 모든 무키 사용자가 한 한도를 나눠 쓰므로 429가 잦습니다 - 요청 사이를 5초
두고, 429·5xx는 `Retry-After`를 따르거나 지수로 물러납니다. 키가 생기면 `SEMANTIC_SCHOLAR_API_KEY`에
넣으면 됩니다(간격 1초).

**arXiv 논문만 셉니다.** 참고문헌 중 `externalIds.ArXiv`가 없는 것(학회 논문·책)은 그래프에 노드로
들일 방법이 없어 버립니다. 버린 수는 `ReferenceBatch.non_arxiv`에 남습니다.

**참고문헌이 늦게 붙습니다.** S2는 새 논문의 참고문헌을 며칠~몇 주 뒤에 채우기도 합니다. 찾았지만
참고문헌이 0개인 논문은 `empty`로 따로 돌려줘, 수집이 다음 주에 다시 묻게 합니다.

HTTP는 `fetch` 인자로 주입합니다(`pipeline/oai.py`와 같은 이유 - 테스트가 네트워크를 타지 않게).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"
FIELDS = "externalIds,referenceCount,references.externalIds"
BATCH_SIZE = 500  # /paper/batch 한 번의 최대 개수
INTERVAL_WITHOUT_KEY = 5.0
INTERVAL_WITH_KEY = 1.0
MAX_RETRIES = 6
BACKOFF_BASE_SECONDS = 5.0
BACKOFF_MAX_SECONDS = 120.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
# 2026-10-27 측정에서 434편 묶음 하나가 통째로 실패했고, 같은 묶음을 바로 다시 보내니 16초에
# 끝났습니다. 상태 코드가 아닌 끊김(시간 초과 등)도 다시 시도합니다.
RETRYABLE_NETWORK = (urllib.error.URLError, TimeoutError, ConnectionError)

Fetch = Callable[[list[str]], list[dict[str, Any] | None]]


def api_key() -> str | None:
    return os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "").strip() or None


def backoff_delay(attempt: int, retry_after: str | None, interval: float) -> float:
    if retry_after:
        try:
            return max(float(retry_after), interval)
        except (TypeError, ValueError):
            pass
    return min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS)


def http_fetch(arxiv_ids: list[str], *, key: str | None = None,
               sleep: Callable[[float], None] = time.sleep) -> list[dict[str, Any] | None]:
    """한 묶음을 받습니다. 결과는 보낸 순서대로이고, S2에 없는 논문은 `None`입니다."""
    url = f"{BATCH_URL}?fields={urllib.parse.quote(FIELDS, safe=',.')}"
    body = json.dumps({"ids": [f"ARXIV:{arxiv_id}" for arxiv_id in arxiv_ids]}).encode()
    headers = {"Content-Type": "application/json", "User-Agent": "arxiv-graphrag-ingest/1.0"}
    if key:
        headers["x-api-key"] = key
    interval = INTERVAL_WITH_KEY if key else INTERVAL_WITHOUT_KEY
    for attempt in range(MAX_RETRIES + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt >= MAX_RETRIES:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            error.close()
            sleep(backoff_delay(attempt, retry_after, interval))
        except RETRYABLE_NETWORK:
            if attempt >= MAX_RETRIES:
                raise
            sleep(backoff_delay(attempt, None, interval))
    raise RuntimeError("재시도를 모두 소진했습니다")


@dataclass
class ReferenceBatch:
    """참고문헌 조회의 결과. 실패도 버리지 않고 사유와 함께 남깁니다(dead letter와 같은 선)."""

    references: dict[str, list[str]] = field(default_factory=dict)
    not_found: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)
    non_arxiv: int = 0
    requests: int = 0
    failures: list[dict[str, str]] = field(default_factory=list)

    @property
    def cited(self) -> set[str]:
        return {cited for ids in self.references.values() for cited in ids}


def arxiv_references(record: dict[str, Any], citing: str) -> tuple[list[str], int]:
    """S2 레코드에서 arXiv 참고문헌만 뽑습니다. 두 번째 값은 버린(arXiv가 아닌) 수입니다."""
    kept: list[str] = []
    dropped = 0
    for reference in record.get("references") or []:
        external = (reference or {}).get("externalIds") or {}
        cited = external.get("ArXiv")
        if not cited:
            dropped += 1
            continue
        if cited != citing and cited not in kept:
            kept.append(cited)
    return kept, dropped


def fetch_references(
    arxiv_ids: Iterable[str],
    *,
    fetch: Fetch | None = None,
    sleep: Callable[[float], None] = time.sleep,
    batch_size: int = BATCH_SIZE,
) -> ReferenceBatch:
    """논문마다 arXiv 참고문헌 목록을 받습니다. 한 묶음이 실패해도 나머지 묶음은 받습니다."""
    key = api_key()
    if fetch is None:
        def fetch(ids: list[str]) -> list[dict[str, Any] | None]:
            return http_fetch(ids, key=key, sleep=sleep)
    interval = INTERVAL_WITH_KEY if key else INTERVAL_WITHOUT_KEY
    ids = list(dict.fromkeys(arxiv_ids))
    result = ReferenceBatch()
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        if result.requests:
            sleep(interval)
        result.requests += 1
        try:
            records = fetch(chunk)
        except Exception as exc:  # noqa: BLE001 - 한 묶음의 실패는 다음 묶음을 막지 않습니다
            result.failures.extend({"arxiv_id": arxiv_id,
                                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                                   for arxiv_id in chunk)
            continue
        if len(records) != len(chunk):
            raise RuntimeError(f"S2가 {len(chunk)}편을 받고 {len(records)}개를 돌려줬습니다")
        for arxiv_id, record in zip(chunk, records, strict=True):
            if record is None:
                result.not_found.append(arxiv_id)
                continue
            kept, dropped = arxiv_references(record, arxiv_id)
            result.non_arxiv += dropped
            if kept:
                result.references[arxiv_id] = kept
            else:
                result.empty.append(arxiv_id)
    return result
