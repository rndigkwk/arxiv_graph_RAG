"""arXiv OAI-PMH 증분 수집 클라이언트입니다.

OAI-PMH는 `from`·`until` 날짜 범위로 조회하므로 증분 수집이 본질적으로 자연스럽습니다. 지난
실행의 워터마크 다음날부터 오늘까지만 받아 오면 됩니다.

HTTP 호출은 `fetch` 인자로 주입합니다. 테스트가 네트워크를 타지 않아야 하고, 재시도·백오프·
재개 토큰 논리는 네트워크 없이도 검증할 수 있어야 합니다.

세 가지를 지킵니다.

- **요청 간격.** arXiv가 안내하는 간격을 지키고, 503의 `Retry-After`를 우선합니다.
- **재개 토큰.** 같은 토큰이 다시 오면 무한 반복이므로 즉시 멈춥니다.
- **삭제 레코드.** `status="deleted"` 헤더는 건너뛰되 개수를 셉니다.
"""

from __future__ import annotations

import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

OAI_URL = "https://export.arxiv.org/oai2"
METADATA_PREFIX = "arXiv"
DEFAULT_SET = "cs"
REQUEST_INTERVAL_SECONDS = 3.0
MAX_RETRIES = 8
BACKOFF_BASE_SECONDS = 10.0
BACKOFF_MAX_SECONDS = 300.0
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

NAMESPACES = {
    "oai": "http://www.openarchives.org/OAI/2.0/",
    "arxiv": "http://arxiv.org/OAI/arXiv/",
}


def backoff_delay(attempt: int, retry_after: str | None) -> float:
    """Prefer the server's Retry-After, fall back to capped exponential backoff."""
    if retry_after:
        try:
            return max(float(retry_after), REQUEST_INTERVAL_SECONDS)
        except (TypeError, ValueError):
            pass
    return min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_MAX_SECONDS)


def http_fetch(params: dict[str, str], *, sleep: Callable[[float], None] = time.sleep) -> str:
    """Fetch one OAI-PMH page, retrying the statuses arXiv uses for rate limiting."""
    url = f"{OAI_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "arxiv-graphrag-ingest/1.0"}
    )
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            if error.code not in RETRYABLE_STATUS or attempt >= MAX_RETRIES:
                raise
            headers = getattr(error, "headers", None)
            retry_after = headers.get("Retry-After") if headers else None
            error.close()
            sleep(backoff_delay(attempt, retry_after))
    raise RuntimeError("재시도를 모두 소진했습니다")


def _text(node: ElementTree.Element, path: str) -> str | None:
    child = node.find(path, NAMESPACES)
    return child.text.strip() if child is not None and child.text else None


def parse_record(record: ElementTree.Element) -> dict[str, object] | None:
    """Turn one OAI record into the row shape the loader expects, or None to skip it.

    The OAI datestamp is kept under its own name rather than written into `published`. Writing
    it into `published` is what made 1,748 of the harvested papers report a 2026 publication
    date while their arXiv ID said 2025, which then made the field unusable as a time key.
    """
    header = record.find("oai:header", NAMESPACES)
    if header is None or header.attrib.get("status") == "deleted":
        return None
    metadata = record.find("oai:metadata/arxiv:arXiv", NAMESPACES)
    if metadata is None:
        return None
    arxiv_id = _text(metadata, "arxiv:id")
    if not arxiv_id:
        return None
    categories = (_text(metadata, "arxiv:categories") or "").split()
    authors = [
        " ".join(
            part
            for part in (
                _text(author, "arxiv:forenames"),
                _text(author, "arxiv:keyname"),
            )
            if part
        ).strip()
        for author in metadata.findall("arxiv:authors/arxiv:author", NAMESPACES)
    ]
    return {
        "arxiv_id": arxiv_id,
        "title": " ".join((_text(metadata, "arxiv:title") or "").split()),
        "abstract": " ".join((_text(metadata, "arxiv:abstract") or "").split()),
        "authors": [name for name in authors if name],
        "categories": categories,
        "primary_category": categories[0] if categories else None,
        "created": _text(metadata, "arxiv:created"),
        "updated": _text(metadata, "arxiv:updated"),
        "oai_datestamp": _text(header, "oai:datestamp"),
        "doi": _text(metadata, "arxiv:doi"),
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
        "source": "oai",
    }


@dataclass
class HarvestResult:
    """What one harvest produced, including what it refused and why."""

    records: list[dict[str, object]] = field(default_factory=list)
    pages: int = 0
    deleted: int = 0
    skipped: int = 0
    last_datestamp: str = ""
    truncated_by_page_limit: bool = False


def harvest(
    start: str,
    end: str,
    *,
    oai_set: str = DEFAULT_SET,
    fetch: Callable[[dict[str, str]], str] = http_fetch,
    sleep: Callable[[float], None] = time.sleep,
    max_pages: int = 200,
    categories: frozenset[str] | None = None,
) -> HarvestResult:
    """Walk the resumption tokens for one date range and return the parsed records."""
    params: dict[str, str] = {
        "verb": "ListRecords",
        "metadataPrefix": METADATA_PREFIX,
        "set": oai_set,
        "from": start,
        "until": end,
    }
    result = HarvestResult()
    seen_tokens: set[str] = set()
    for page in pages(params, fetch=fetch, sleep=sleep, max_pages=max_pages,
                      seen_tokens=seen_tokens):
        result.pages += 1
        for record in page.findall("oai:ListRecords/oai:record", NAMESPACES):
            header = record.find("oai:header", NAMESPACES)
            if header is not None and header.attrib.get("status") == "deleted":
                result.deleted += 1
                continue
            parsed = parse_record(record)
            if parsed is None:
                result.skipped += 1
                continue
            if categories and parsed["primary_category"] not in categories:
                result.skipped += 1
                continue
            result.records.append(parsed)
            stamp = str(parsed.get("oai_datestamp") or "")
            if stamp > result.last_datestamp:
                result.last_datestamp = stamp
        if result.pages >= max_pages:
            result.truncated_by_page_limit = True
            break
    return result


def pages(
    params: dict[str, str],
    *,
    fetch: Callable[[dict[str, str]], str],
    sleep: Callable[[float], None],
    max_pages: int,
    seen_tokens: set[str],
) -> Iterator[ElementTree.Element]:
    """Yield OAI pages, following resumptionToken and refusing to loop on a repeat."""
    request = dict(params)
    emitted = 0
    while True:
        document = ElementTree.fromstring(fetch(request))
        error = document.find("oai:error", NAMESPACES)
        if error is not None:
            code = error.attrib.get("code", "")
            if code == "noRecordsMatch":
                return
            raise RuntimeError(f"OAI 오류 {code}: {(error.text or '').strip()}")
        yield document
        emitted += 1
        token_node = document.find("oai:ListRecords/oai:resumptionToken", NAMESPACES)
        token = (token_node.text or "").strip() if token_node is not None else ""
        if not token or emitted >= max_pages:
            return
        if token in seen_tokens:
            raise RuntimeError("같은 resumptionToken이 반복되어 수집을 중단했습니다")
        seen_tokens.add(token)
        # 재개 요청에는 토큰만 보냅니다. 다른 인자를 함께 보내면 badArgument가 돌아옵니다.
        request = {"verb": "ListRecords", "resumptionToken": token}
        sleep(REQUEST_INTERVAL_SECONDS)
