import hashlib
import json

import pytest

from evals.run import _token_totals
from evals.telemetry import (
    build_run_metadata,
    count_embedding_tokens,
    estimate_api_cost,
    pricing_from_env,
    summarize_latency,
)


def test_latency_summary_uses_nearest_rank_percentiles_and_handles_empty_samples():
    assert summarize_latency([5, 1, 4, 2, 3]) == {
        "mean_seconds": 3.0,
        "p50_seconds": 3.0,
        "p95_seconds": 5.0,
    }
    assert summarize_latency([]) == {
        "mean_seconds": None,
        "p50_seconds": None,
        "p95_seconds": None,
    }
    assert summarize_latency([2.5]) == {
        "mean_seconds": 2.5,
        "p50_seconds": 2.5,
        "p95_seconds": 2.5,
    }


def test_cost_is_calculated_only_with_usage_rates_and_source():
    rates = {
        "chat_input_usd_per_1m": 2.0,
        "chat_output_usd_per_1m": 4.0,
        "embedding_usd_per_1m": 0.12,
        "source": "test pricing sheet",
    }

    assert estimate_api_cost(1000, 500, 250, rates) == pytest.approx(0.00403)
    assert estimate_api_cost(None, 500, 250, rates) is None
    assert estimate_api_cost(1000, 500, 250, {**rates, "source": ""}) is None
    assert estimate_api_cost(1000, 500, 250, {**rates, "embedding_usd_per_1m": None}) is None


def test_pricing_config_requires_all_nonnegative_rates_and_a_source():
    env = {
        "EVAL_CHAT_INPUT_USD_PER_1M": "2",
        "EVAL_CHAT_OUTPUT_USD_PER_1M": "4",
        "EVAL_EMBEDDING_USD_PER_1M": "0.12",
        "EVAL_PRICING_SOURCE": "test source",
    }

    assert pricing_from_env(env)["chat_input_usd_per_1m"] == 2.0
    assert pricing_from_env({**env, "EVAL_CHAT_INPUT_USD_PER_1M": "-1"}) is None
    missing_source = {
        key: value for key, value in env.items() if key != "EVAL_PRICING_SOURCE"
    }
    assert pricing_from_env(missing_source) is None


def test_embedding_tokens_use_the_configured_model_tokenizer(monkeypatch):
    import tiktoken

    seen_models = []

    class Encoding:
        @staticmethod
        def encode(value):
            return value.split()

    def encoding_for_model(model):
        seen_models.append(model)
        return Encoding()

    monkeypatch.setattr(tiktoken, "encoding_for_model", encoding_for_model)
    assert count_embedding_tokens(["graph neural networks", "RAG"], "text-embedding-3-large") > 0
    assert count_embedding_tokens([], "text-embedding-3-large") == 0
    assert seen_models == ["text-embedding-3-large"]


def test_run_metadata_records_reproducibility_fields_without_environment_secrets(
    tmp_path, monkeypatch
):
    cases_path = tmp_path / "cases.json"
    cases_path.write_text('{"cases": []}', encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "do-not-record-this")
    monkeypatch.setenv("NEO4J_PASSWORD", "also-private")

    metadata = build_run_metadata(
        cases_path=cases_path,
        case_ids=["case-1"],
        mode="compare",
        top_k=5,
        model="test-chat-model",
        embedding_model="text-embedding-3-large",
    )

    assert metadata["dataset_sha256"] == hashlib.sha256(cases_path.read_bytes()).hexdigest()
    assert metadata["model"] == "test-chat-model"
    assert metadata["case_ids"] == ["case-1"]
    assert metadata["git_dirty"] is not None
    assert "do-not-record-this" not in json.dumps(metadata)
    assert "also-private" not in json.dumps(metadata)


def test_missing_provider_usage_is_none_not_zero():
    class MessageWithoutUsage:
        usage_metadata = None
        response_metadata = {}

    class MessageWithUsage:
        usage_metadata = {"input_tokens": 12, "output_tokens": 5}

    assert _token_totals([MessageWithoutUsage()]) == (None, None)
    assert _token_totals([MessageWithUsage()]) == (12, 5)
