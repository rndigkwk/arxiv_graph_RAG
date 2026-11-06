"""VM의 주간 수집이 돌고 있는지 `/api/health`에 보입니다(ADR-106).

수집은 VM의 타이머가 돌립니다(ADR-104). GitHub의 실패 알림(`alert-on-failure.yml`)은 Actions
실행만 보므로, 키가 만료되거나 타이머가 멈춰도 그래프가 옛 논문에 조용히 머뭅니다. 그래서 VM에
남는 두 파일을 읽어 문제를 적고, 배포 점검(`scripts/visit_deployed_app.py --ingest`)이 8시간마다
이것을 읽어 실패로 셉니다.

- `pipeline/state/ingest.json` - 수집 모듈의 워터마크와 실행 기록입니다. 끝까지 간 실행만 남습니다.
- `pipeline/state/last-ingest.json` - `deploy/vm/ingest.sh load`가 성공·실패와 상관없이 끝에 씁니다.
  수집이 예외로 죽으면 앞 파일에는 아무것도 남지 않아서, 종료 코드는 여기서 봅니다.

**파일만 읽습니다.** 그래프에 묻지 않고 비밀값도 다루지 않습니다. 보고서의 오류 문구(`stop_error`)는
내보내지 않습니다 - 저장소가 공개라 CI 로그도 공개입니다.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

STATE_PATH = Path("pipeline/state/ingest.json")
LAST_RUN_PATH = Path("pipeline/state/last-ingest.json")
REPORTS = Path("pipeline/state/reports")
# 타이머는 월요일 03:19 UTC입니다. 다음 월요일 실행이 늦어도 하루는 기다립니다.
MAX_LOAD_AGE_DAYS = 8
# 워터마크는 받은 마지막 날입니다. 월요일 실행 직후에도 주말만큼 뒤라, 다음 실행 직전에는 열흘쯤
# 뒤입니다. 하루가 페이지 한도를 넘어 워터마크가 멈추면(`truncated_on`) 이것이 늘어납니다.
MAX_WATERMARK_DAYS = 12
FINISHED = {"completed", "harvest_empty"}


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _when(value: Any) -> dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.UTC)


def _report(path_text: Any, reports: Path) -> dict[str, Any] | None:
    """`last-ingest.json`이 가리키는 보고서입니다. 보고서 폴더 밖은 읽지 않습니다."""
    if not isinstance(path_text, str) or not path_text:
        return None
    path = Path(path_text)
    try:
        path.resolve().relative_to(reports.resolve())
    except ValueError:
        return None
    return _read(path)


def summary(now: dt.datetime | None = None, state_path: Path = STATE_PATH,
            last_run_path: Path = LAST_RUN_PATH, reports: Path = REPORTS) -> dict[str, Any]:
    """`status`는 ok·problem·unknown입니다. 문제는 `problems`에 짧은 이름으로 적습니다."""
    now = now or dt.datetime.now(dt.UTC)
    state = _read(state_path)
    if state is None:
        return {"status": "unknown", "problems": ["no_state"]}

    problems: list[str] = []
    runs = [run for run in state.get("runs") or [] if isinstance(run, dict)]
    loads = [run for run in runs if run.get("loaded")]
    last_load = _when(loads[-1].get("at")) if loads else None
    if last_load is None:
        problems.append("never_loaded")
    elif now - last_load > dt.timedelta(days=MAX_LOAD_AGE_DAYS):
        problems.append("load_stale")

    watermark = str(state.get("last_datestamp") or "")
    try:
        behind = (now.date() - dt.date.fromisoformat(watermark)).days
    except ValueError:
        behind = None
        problems.append("no_watermark")
    if behind is not None and behind > MAX_WATERMARK_DAYS:
        problems.append("watermark_behind")

    result: dict[str, Any] = {
        "last_load_at": last_load.isoformat() if last_load else None,
        "watermark": watermark or None,
        "watermark_days_behind": behind,
    }

    last = _read(last_run_path)
    if last is not None:
        exit_code = last.get("exit_code")
        backfill = last.get("backfill_exit_code")
        result["last_run"] = {"finished_at": last.get("finished_at"),
                              "exit_code": exit_code, "backfill_exit_code": backfill}
        if exit_code != 0:
            problems.append("ingest_failed")
        if backfill not in (0, None):
            problems.append("backfill_failed")
        report = _report(last.get("report"), reports) if exit_code == 0 else None
        if report is not None:
            harvest = report.get("harvest") or {}
            stopped = report.get("stopped_at", "completed")
            result["report"] = {
                "records": harvest.get("records"),
                "stopped_at": stopped,
                "loaded": report.get("loaded"),
                "idempotent": report.get("idempotent"),
                "stopped_on": harvest.get("stopped_on") or None,
                "truncated_on": harvest.get("truncated_on") or None,
            }
            if stopped not in FINISHED:
                problems.append(f"stopped_at_{stopped}")
            if report.get("idempotent") is False:
                problems.append("not_idempotent")

    result["status"] = "problem" if problems else "ok"
    result["problems"] = problems
    return result
