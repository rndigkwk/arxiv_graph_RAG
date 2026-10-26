"""메모리 측정기가 다른 실행의 보고서를 덮지 않습니다.

2026-10-25에 `--output` 기본값이 10-18 보고서를 조용히 덮었습니다(EXPERIMENTS 11-49절).
`--skip-app`으로 돌려 네트워크 없이 확인합니다.
"""

import json
import os
import subprocess
import sys

SCRIPT = "scripts/measure_app_memory.py"


def run(*args):
    return subprocess.run([sys.executable, SCRIPT, "--skip-app", *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})


def test_an_existing_report_is_not_overwritten(tmp_path):
    report = tmp_path / "app_memory.json"
    report.write_text('{"label": "2026-10-18"}', encoding="utf-8")

    result = run("--output", str(report))

    assert result.returncode == 2
    assert "--overwrite" in result.stderr
    assert json.loads(report.read_text(encoding="utf-8")) == {"label": "2026-10-18"}


def test_overwriting_has_to_be_asked_for(tmp_path):
    report = tmp_path / "app_memory.json"
    report.write_text('{"label": "2026-10-18"}', encoding="utf-8")

    assert run("--output", str(report), "--overwrite", "--label", "new").returncode == 0
    assert json.loads(report.read_text(encoding="utf-8"))["label"] == "new"


def test_a_new_name_is_written(tmp_path):
    report = tmp_path / "fresh.json"
    assert run("--output", str(report)).returncode == 0
    assert json.loads(report.read_text(encoding="utf-8"))["skipped_app"] is True
