"""배포된 공동인용 예측 모델의 월간 감시 결과를 `/api/health`에 보입니다(ADR-124).

감시는 VM의 타이머가 매달 돌립니다(`deploy/vm/forecast_monitor.sh`). 그 스크립트가 성공·실패와
상관없이 끝에 `pipeline/state/last-forecast-monitor.json`을 쓰고, 보고서는
`pipeline/state/reports/forecast-monitor-YYYY-MM.json`에 남습니다. 배포 점검
(`scripts/visit_deployed_app.py --forecast-monitor`)이 8시간마다 이것을 읽어 문제를 실패로 셉니다.

**파일만 읽습니다.** 그래프에 묻지 않고, 보고서의 숫자 몇 개만 내보냅니다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from src.ingest_status import _read, _report, _when

LAST_RUN_PATH = Path("pipeline/state/last-forecast-monitor.json")
REPORTS = Path("pipeline/state/reports")
# 타이머는 매달 3일입니다. 한 번 빠져도 다음 달 3일 전에는 알리도록 40일로 둡니다.
MAX_AGE_DAYS = 40


def summary(now: dt.datetime | None = None, last_run_path: Path = LAST_RUN_PATH,
            reports: Path = REPORTS) -> dict[str, Any]:
    """`status`는 ok·problem·unknown입니다.

    아직 한 번도 돌지 않았으면 unknown이고 문제로 세지 않습니다.
    """
    now = now or dt.datetime.now(dt.UTC)
    last = _read(last_run_path)
    if last is None:
        return {"status": "unknown", "problems": []}

    problems: list[str] = []
    finished = _when(last.get("finished_at"))
    if finished is None or now - finished > dt.timedelta(days=MAX_AGE_DAYS):
        problems.append("monitor_stale")
    result: dict[str, Any] = {"finished_at": last.get("finished_at"),
                              "exit_code": last.get("exit_code")}
    if last.get("exit_code") != 0:
        problems.append("monitor_failed")
    report = _report(last.get("report"), reports) if last.get("exit_code") == 0 else None
    if report is not None:
        gbm = next((row for row in report.get("results") or []
                    if row.get("model") == (report.get("model") or {}).get("rung")), {})
        interval = gbm.get("mrr_vs_best_heuristic") or {}
        result["report"] = {
            "month": report.get("month"),
            "verdict": report.get("status"),
            "groups": (report.get("counts") or {}).get("groups"),
            "mrr": (gbm.get("ranking_hard_negatives") or {}).get("mrr"),
            "vs_best_heuristic": interval.get("mean"),
            "interval": [interval.get("low"), interval.get("high")] if interval else None,
        }
        if report.get("status") == "below_bar":
            problems.append("below_bar")
    elif last.get("exit_code") == 0:
        problems.append("no_report")
    result["status"] = "problem" if problems else "ok"
    result["problems"] = problems
    return result
