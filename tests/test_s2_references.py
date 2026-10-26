"""Semantic Scholar 참고문헌 조회(`pipeline/s2.py`). 네트워크를 타지 않습니다."""

from __future__ import annotations

import urllib.error

import pytest

from pipeline import s2


def record(*cited: str | None) -> dict:
    return {"references": [{"externalIds": {"ArXiv": c} if c else {"DOI": "10.1/x"}}
                           for c in cited]}


def test_keeps_only_arxiv_references_without_self_or_repeats():
    kept, dropped = s2.arxiv_references(record("1706.03762", None, "1706.03762", "2610.1"),
                                        "2610.1")
    assert kept == ["1706.03762"]
    assert dropped == 1


def test_sorts_found_empty_and_missing_papers_apart():
    responses = {"a": record("x", "y"), "b": record(), "c": None}

    def fetch(ids):
        return [responses[i] for i in ids]

    batch = s2.fetch_references(["a", "b", "c", "a"], fetch=fetch, sleep=lambda _: None)
    assert batch.references == {"a": ["x", "y"]}
    assert batch.empty == ["b"]  # 늦게 붙는 참고문헌 - 다음 주에 다시 묻습니다
    assert batch.not_found == ["c"]
    assert batch.cited == {"x", "y"}
    assert batch.requests == 1


def test_one_failed_chunk_does_not_lose_the_others():
    calls = []

    def fetch(ids):
        calls.append(list(ids))
        if len(calls) == 1:
            raise TimeoutError("read timed out")
        return [record("z") for _ in ids]

    pauses = []
    batch = s2.fetch_references(["a", "b", "c"], fetch=fetch, sleep=pauses.append,
                                batch_size=2)
    assert [f["arxiv_id"] for f in batch.failures] == ["a", "b"]
    assert batch.references == {"c": ["z"]}
    assert batch.requests == 2
    assert pauses == [s2.INTERVAL_WITHOUT_KEY]  # 묶음 사이에만 쉽니다


def test_a_short_answer_is_an_error_not_a_silent_shift():
    # 순서로 짝을 맞추므로, 개수가 다르면 참고문헌이 남의 논문에 붙습니다.
    with pytest.raises(RuntimeError, match="2편을 받고 1개"):
        s2.fetch_references(["a", "b"], fetch=lambda ids: [None], sleep=lambda _: None)


def test_network_errors_are_retried(monkeypatch):
    attempts = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"[null]"

    def urlopen(request, timeout):
        attempts.append(request)
        if len(attempts) < 3:
            raise urllib.error.URLError("timed out")
        return Response()

    monkeypatch.setattr(s2.urllib.request, "urlopen", urlopen)
    pauses = []
    assert s2.http_fetch(["a"], sleep=pauses.append) == [None]
    assert len(attempts) == 3
    assert pauses == [s2.BACKOFF_BASE_SECONDS, s2.BACKOFF_BASE_SECONDS * 2]


def test_key_shortens_the_interval_and_is_sent(monkeypatch):
    monkeypatch.setenv("SEMANTIC_SCHOLAR_API_KEY", "k")
    sent = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b"[null]"

    def urlopen(request, timeout):
        sent.update(request.headers)
        return Response()

    monkeypatch.setattr(s2.urllib.request, "urlopen", urlopen)
    pauses = []
    s2.fetch_references(["a", "b"], sleep=pauses.append, batch_size=1)
    assert sent.get("X-api-key") == "k"
    assert pauses == [s2.INTERVAL_WITH_KEY]


def test_growth_summary_splits_inside_and_outside():
    from scripts.measure_reference_growth import summarize

    batch = s2.ReferenceBatch(references={"n1": ["old", "x"], "n2": ["x", "y"]}, empty=["n3"])
    report = summarize(["n1", "n2", "n3"], batch, in_graph={"old"}, authors_per_paper=7.0)
    assert report["edges"] == {"to_papers_in_graph": 1, "to_papers_outside": 3}
    assert report["distinct_cited_outside"] == 2
    assert report["outside_by_citing_count"][">=2"] == 1  # x만 두 편이 인용
    assert report["estimated_authored_for_outside"] == 14
