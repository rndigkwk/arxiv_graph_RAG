"""평가 보고서가 SDK 재시도 횟수와 도구별 시간을 남깁니다.

ADR-073은 재시도를 2번에서 1번으로 줄이며 "재시도로 살아난 호출이 몇 번인지 모른다"고
적었습니다. 재시도는 SDK 안에서 끝나 응답에 남지 않으므로 SDK의 로그를 셉니다. 로그 문구가
바뀌면 조용히 0이 되므로, **진짜 SDK에 진짜 5xx를 돌려** 세어지는지 봅니다.
"""

from pathlib import Path

import httpx2
import openai
import pytest
from openai import _base_client

from evals import run


def client_failing_first(times, monkeypatch):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] <= times:
            return httpx2.Response(503, json={"error": {"message": "busy"}})
        return httpx2.Response(200, json={
            "object": "list", "model": "m",
            "data": [{"object": "embedding", "index": 0, "embedding": [0.1]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        })

    monkeypatch.setattr(_base_client.time, "sleep", lambda seconds: None)
    return openai.OpenAI(
        api_key="test", base_url="http://openai.test/v1", max_retries=1,
        http_client=openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    ), calls


def test_a_retried_request_is_counted_by_endpoint(monkeypatch):
    client, calls = client_failing_first(1, monkeypatch)
    with run.sdk_retries() as retries:
        client.embeddings.create(model="m", input="x")
    assert calls["n"] == 2
    assert retries == {"embeddings": 1}


def test_a_request_that_went_through_counts_nothing(monkeypatch):
    client, _ = client_failing_first(0, monkeypatch)
    with run.sdk_retries() as retries:
        client.embeddings.create(model="m", input="x")
    assert retries == {}


def test_a_request_that_failed_after_the_retry_still_counts_the_retry(monkeypatch):
    client, _ = client_failing_first(5, monkeypatch)
    with run.sdk_retries() as retries, pytest.raises(openai.InternalServerError):
        client.embeddings.create(model="m", input="x")
    assert retries == {"embeddings": 1}


def test_the_logger_level_is_restored():
    logger = run.logging.getLogger(run.SDK_LOGGER)
    before = logger.level
    with run.sdk_retries():
        pass
    assert logger.level == before and not any(
        isinstance(h, run._SdkRetryCounter) for h in logger.handlers)


def test_tool_seconds_keep_names_and_seconds_only():
    response = {"tool_calls": [
        {"name": "translate_to_english", "args": {"text": "질문"}, "seconds": 1.2},
        {"name": "search_graph", "args": {"cypher": "MATCH"}},
    ]}
    assert run.tool_seconds(response) == [{"name": "translate_to_english", "seconds": 1.2}]


def test_both_agent_paths_record_the_new_fields():
    source = Path(run.__file__).read_text(encoding="utf-8")
    assert source.count('"sdk_retries": dict(retries)') == 2
    assert source.count("with sdk_retries() as retries:") == 2
