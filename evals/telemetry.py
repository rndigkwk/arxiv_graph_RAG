"""Evaluation telemetry with explicit unknowns and opt-in pricing."""

from __future__ import annotations

import hashlib
import math
import platform
import subprocess
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from statistics import fmean
from typing import Mapping


def summarize_latency(samples: list[float]) -> dict[str, float | None]:
    if not samples:
        return {"mean_seconds": None, "p50_seconds": None, "p95_seconds": None}
    ordered = sorted(float(sample) for sample in samples)

    def nearest_rank(percentile: float) -> float:
        return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]

    return {
        "mean_seconds": fmean(ordered),
        "p50_seconds": nearest_rank(0.50),
        "p95_seconds": nearest_rank(0.95),
    }


def count_embedding_tokens(queries: list[str], model: str) -> int:
    if not queries:
        return 0
    import tiktoken

    encoding = tiktoken.encoding_for_model(model)
    return sum(len(encoding.encode(query)) for query in queries)


def pricing_from_env(environ: Mapping[str, str]) -> dict[str, float | str] | None:
    keys = {
        "chat_input_usd_per_1m": "EVAL_CHAT_INPUT_USD_PER_1M",
        "chat_output_usd_per_1m": "EVAL_CHAT_OUTPUT_USD_PER_1M",
        "embedding_usd_per_1m": "EVAL_EMBEDDING_USD_PER_1M",
    }
    source = environ.get("EVAL_PRICING_SOURCE", "").strip()
    try:
        rates = {name: float(environ[key]) for name, key in keys.items()}
    except (KeyError, TypeError, ValueError):
        return None
    if not source or any(not math.isfinite(value) or value < 0 for value in rates.values()):
        return None
    return {**rates, "source": source}


def estimate_api_cost(
    chat_input_tokens: int | None,
    chat_output_tokens: int | None,
    embedding_tokens: int | None,
    rates: Mapping[str, float | str] | None,
) -> float | None:
    required = (chat_input_tokens, chat_output_tokens, embedding_tokens)
    rate_keys = ("chat_input_usd_per_1m", "chat_output_usd_per_1m", "embedding_usd_per_1m")
    if rates is None or not rates.get("source") or any(value is None for value in required):
        return None
    try:
        values = [float(rates[key]) for key in rate_keys]
        tokens = [int(value) for value in required]
    except (KeyError, TypeError, ValueError):
        return None
    if any(value < 0 or not math.isfinite(value) for value in values) or any(
        value < 0 for value in tokens
    ):
        return None
    return sum(token * rate / 1_000_000 for token, rate in zip(tokens, values))


def build_run_metadata(
    *,
    cases_path: Path,
    case_ids: list[str],
    mode: str,
    top_k: int,
    model: str,
    embedding_model: str,
) -> dict:
    repo_root = Path(__file__).resolve().parents[1]
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root, capture_output=True, text=True, check=True
        ).stdout.strip()
        git_dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo_root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        git_sha, git_dirty = None, None
    packages = {}
    for package in ("openai", "langchain", "neo4j-graphrag", "tiktoken"):
        try:
            packages[package] = version(package)
        except PackageNotFoundError:
            packages[package] = None
    data = cases_path.read_bytes()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_file": cases_path.name,
        "dataset_sha256": hashlib.sha256(data).hexdigest(),
        "mode": mode,
        "top_k": top_k,
        "case_ids": list(case_ids),
        "model": model,
        "embedding_model": embedding_model,
        "python_version": platform.python_version(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "packages": packages,
        "git_sha": git_sha,
        "git_dirty": git_dirty,
    }
