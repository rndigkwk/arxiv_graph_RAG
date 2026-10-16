"""실험·파이프라인 실행 기록을 MLflow로 남기고, 없으면 파일로 떨어뜨립니다.

지금까지 손으로 적어 온 스냅샷 sha256·Git 커밋·패키지 버전이 그대로 실행 메타데이터입니다.
MLflow가 설치돼 있지 않거나 추적 서버에 닿지 못해도 파이프라인이 멈추면 안 되므로,
그 경우에는 `reports/runs/`에 같은 내용을 JSON으로 남기고 계속 진행합니다. 기록이 사라지는
것보다 기록 위치가 바뀌는 쪽이 낫습니다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FALLBACK_ROOT = Path("reports/runs")


def load_env(root: Path | None = None) -> None:
    """Load the project's .env so MLflow sees the tracking URI and credentials.

    MLflow reads MLFLOW_TRACKING_URI and the username/password from the environment itself,
    and nothing here put .env there. Without this the URI in .env is ignored and runs land in
    a local mlruns/ while appearing to be configured for the remote server.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    base = root or Path(__file__).resolve().parents[1]
    load_dotenv(base / ".env", override=False)


load_env()
EXPERIMENT = os.environ.get("MLFLOW_EXPERIMENT_NAME", "arxiv-graphrag")


def git_commit() -> str:
    """Return the current commit, or an empty string outside a checkout."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def environment_tags() -> dict[str, str]:
    """Record what produced a run, so a number can be traced back to the code that made it."""
    return {
        "git_commit": git_commit(),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }


class Run:
    """Collects params, metrics and artifacts for one run regardless of the backend."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.params: dict[str, Any] = {}
        self.metrics: dict[str, float] = {}
        self.tags: dict[str, str] = dict(environment_tags())
        self.artifacts: list[Path] = []

    def log_params(self, values: dict[str, Any]) -> None:
        self.params.update({key: value for key, value in values.items() if value is not None})

    def log_metrics(self, values: dict[str, Any]) -> None:
        for key, value in values.items():
            if isinstance(value, bool):
                self.metrics[key] = float(value)
            elif isinstance(value, (int, float)):
                self.metrics[key] = float(value)

    def log_tags(self, values: dict[str, Any]) -> None:
        self.tags.update({key: str(value) for key, value in values.items() if value is not None})

    def log_artifact(self, path: Path | str) -> None:
        self.artifacts.append(Path(path))

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "logged_at": datetime.now(UTC).isoformat(),
            "params": self.params,
            "metrics": self.metrics,
            "tags": self.tags,
            "artifacts": [str(path) for path in self.artifacts],
        }


def _import_mlflow():
    """Import MLflow lazily; absence is a normal configuration, not an error."""
    if os.environ.get("ARXIV_TRACKING_BACKEND") == "file":
        return None
    try:
        import mlflow
    except ImportError:
        return None
    return mlflow


def _write_fallback(run: Run, root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = root / f"{stamp}-{run.name}.json"
    path.write_text(
        json.dumps(run.as_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


@contextmanager
def track(name: str, *, fallback_root: Path = FALLBACK_ROOT):
    """Open a run, hand it to the caller, then flush it to MLflow or to a JSON file.

    The run is flushed even when the body raises, and the failure is tagged, because a failed
    run is exactly the one worth keeping.
    """
    run = Run(name)
    failure: BaseException | None = None
    try:
        yield run
    except BaseException as exc:  # noqa: BLE001 - re-raised after the run is recorded
        failure = exc
        run.log_tags({"status": "failed", "error": type(exc).__name__})
        raise
    finally:
        if failure is None:
            run.log_tags({"status": "succeeded"})
        mlflow = _import_mlflow()
        if mlflow is None:
            path = _write_fallback(run, fallback_root)
            print(f"추적 기록(파일): {path}")
        else:
            uri = os.environ.get("MLFLOW_TRACKING_URI", "")
            mlflow.set_experiment(EXPERIMENT)
            with mlflow.start_run(run_name=run.name):
                mlflow.set_tags(run.tags)
                mlflow.log_params(run.params)
                mlflow.log_metrics(run.metrics)
                for artifact in run.artifacts:
                    if artifact.exists():
                        mlflow.log_artifact(str(artifact))
            where = uri or "로컬 mlruns/"
            print(f"추적 기록(MLflow): {where} experiment={EXPERIMENT} run={run.name}")
