"""arXiv 조회 API(`export.arxiv.org/api/query`)로 논문 ID의 메타데이터를 받습니다(ADR-104).

주간 수집이 그래프 밖 논문을 노드로 들일 때, 그리고 발표일이 빠진 논문을 채울 때 씁니다. 그 논문은
이번 주의 OAI `ListRecords` 창에 없으므로 ID로 받습니다.

**OAI `GetRecord`를 쓰지 않습니다.** 2026-11-05에 재 보니 OAI의 새 끝점(oaipmh.arxiv.org)은 옛 형식
ID(`cs/0501001`)와 없는 ID에 응답하지 않았습니다(20초 넘게 기다려도 빈 응답). 식별자의 `:`를 `%3A`로
보내도 같았습니다. 이 API는 같은 세 ID를 0.4초에 돌려줬고, 요청 하나에 100편을 받습니다.

돌려주는 행은 `oai.parse_record`와 같은 모양입니다. 적재기(`ingest.MERGE_PAPER`)가 그대로 씁니다.
`created`는 첫 버전의 시각(`published`)입니다.
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable
from dataclasses import dataclass, field

API_URL = "https://export.arxiv.org/api/query"
BATCH_SIZE = 100
# arXiv가 안내하는 간격입니다(요청 사이 3초).
REQUEST_INTERVAL_SECONDS = 3.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 10.0
BACKOFF_MAX_SECONDS = 120.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
RETRYABLE_NETWORK = (urllib.error.URLError, TimeoutError, ConnectionError)

NAMESPACES = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
VERSION = re.compile(r"v\d+$")

Fetch = Callable[[list[str]], str]


@dataclass
class RecordLookup:
    """ID로 받은 레코드들. 못 받은 논문은 사유와 함께 남깁니다(dead letter와 같은 선)."""

    records: list[dict[str, object]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)


def http_fetch(arxiv_ids: list[str], *, sleep: Callable[[float], None] = time.sleep) -> str:
    query = urllib.parse.urlencode(
        {"id_list": ",".join(arxiv_ids), "max_results": str(len(arxiv_ids))}, safe=",/")
    request = urllib.request.Request(f"{API_URL}?{query}",
                                     headers={"User-Agent": "arxiv-graphrag-ingest/1.0"})
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt >= MAX_RETRIES:
                raise
            error.close()
        except RETRYABLE_NETWORK:
            if attempt >= MAX_RETRIES:
                raise
        sleep(min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS))
    raise RuntimeError("재시도를 모두 소진했습니다")


def _text(node: ElementTree.Element, path: str) -> str:
    child = node.find(path, NAMESPACES)
    return " ".join((child.text or "").split()) if child is not None else ""


def parse_entry(entry: ElementTree.Element) -> dict[str, object] | None:
    """Atom 항목 하나를 적재 행으로. 오류 항목(없는 ID)이면 None입니다."""
    url = _text(entry, "atom:id")
    if "/abs/" not in url:
        return None
    arxiv_id = VERSION.sub("", url.split("/abs/", 1)[1])
    primary = entry.find("arxiv:primary_category", NAMESPACES)
    categories = [node.attrib["term"] for node in entry.findall("atom:category", NAMESPACES)
                  if node.attrib.get("term")]
    primary_category = primary.attrib.get("term") if primary is not None else None
    return {
        "arxiv_id": arxiv_id,
        "title": _text(entry, "atom:title"),
        "abstract": _text(entry, "atom:summary"),
        "authors": [name for name in (_text(author, "atom:name") for author in
                                      entry.findall("atom:author", NAMESPACES)) if name],
        "categories": categories,
        "primary_category": primary_category or (categories[0] if categories else None),
        "created": _text(entry, "atom:published") or None,
        "updated": _text(entry, "atom:updated") or None,
        "doi": _text(entry, "arxiv:doi") or None,
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "source": "arxiv-api",
    }


def get_records(arxiv_ids: list[str], *, fetch: Fetch | None = None,
                sleep: Callable[[float], None] = time.sleep,
                batch_size: int = BATCH_SIZE) -> RecordLookup:
    """ID마다 메타데이터를 받습니다. 한 묶음이 실패해도 나머지 묶음은 받습니다."""
    if fetch is None:
        def fetch(ids: list[str]) -> str:
            return http_fetch(ids, sleep=sleep)
    ids = list(dict.fromkeys(arxiv_ids))
    result = RecordLookup()
    for start in range(0, len(ids), batch_size):
        chunk = ids[start:start + batch_size]
        if start:
            sleep(REQUEST_INTERVAL_SECONDS)
        try:
            document = ElementTree.fromstring(fetch(chunk))
        except Exception as exc:  # noqa: BLE001 - 한 묶음의 실패는 다음 묶음을 막지 않습니다
            result.failures.extend({"arxiv_id": arxiv_id,
                                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
                                   for arxiv_id in chunk)
            continue
        found = {}
        for entry in document.findall("atom:entry", NAMESPACES):
            parsed = parse_entry(entry)
            if parsed is not None:
                found[str(parsed["arxiv_id"])] = parsed
        for arxiv_id in chunk:
            if arxiv_id in found:
                result.records.append(found[arxiv_id])
            else:
                result.missing.append(arxiv_id)
    return result
