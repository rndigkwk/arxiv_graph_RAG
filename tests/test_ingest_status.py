"""VM 주간 수집 상태의 시험입니다(ADR-106).

이 상태가 지키는 약속은 하나입니다 - **수집이 멈췄는데 ok라고 하지 않는다.** 타이머가 멈춘 경우,
수집이 예외로 죽은 경우, 하루가 페이지 한도를 넘어 워터마크가 멈춘 경우를 하나씩 봅니다.
"""

import datetime as dt
import json

from scripts.visit_deployed_app import ingest_problem
from src import ingest_status

NOW = dt.datetime(2026, 11, 9, 4, 0, tzinfo=dt.UTC)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def setup(tmp_path, *, load_at="2026-11-09T03:40:00+00:00", watermark="2026-11-06",
          last=None, report=None):
    state = tmp_path / "state" / "ingest.json"
    write(state, {"last_datestamp": watermark, "runs": [
        {"at": "2026-11-06T02:04:58+00:00", "loaded": True, "stopped_at": "completed"},
        {"at": load_at, "loaded": True, "stopped_at": "completed"},
        {"at": "2026-11-09T03:50:00+00:00", "loaded": False, "stopped_at": "dry_run"},
    ]})
    reports = tmp_path / "state" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    last_path = tmp_path / "state" / "last-ingest.json"
    if report is not None:
        write(reports / "r.json", report)
    if last is not None:
        last.setdefault("report", str(reports / "r.json"))
        write(last_path, last)
    return ingest_status.summary(now=NOW, state_path=state, last_run_path=last_path,
                                 reports=reports)


GOOD_REPORT = {"stopped_at": "completed", "loaded": True, "idempotent": True,
               "harvest": {"records": 1700, "stopped_on": "", "truncated_on": "",
                           "stop_error": "secret-looking text"}}


def test_a_fresh_load_is_ok_and_reads_the_report(tmp_path):
    result = setup(tmp_path, last={"exit_code": 0, "backfill_exit_code": 0}, report=GOOD_REPORT)
    assert result["status"] == "ok" and result["problems"] == []
    assert result["last_load_at"].startswith("2026-11-09")
    assert result["report"]["records"] == 1700
    # 오류 문구는 공개 로그로 나가지 않습니다.
    assert "stop_error" not in json.dumps(result)


def test_without_the_status_file_the_state_alone_decides(tmp_path):
    """11-06 첫 적재는 ingest.sh가 상태 파일을 쓰기 전이었습니다."""
    result = setup(tmp_path, load_at="2026-11-06T02:04:58+00:00")
    assert result["status"] == "ok" and "last_run" not in result


def test_a_stopped_timer_shows_as_a_stale_load(tmp_path):
    result = setup(tmp_path, load_at="2026-10-31T03:30:00+00:00")
    assert "load_stale" in result["problems"]


def test_a_crashed_ingest_is_a_problem_even_though_the_state_looks_fine(tmp_path):
    result = setup(tmp_path, last={"exit_code": 1, "backfill_exit_code": None}, report=GOOD_REPORT)
    assert result["status"] == "problem" and result["problems"] == ["ingest_failed"]
    assert "report" not in result  # 실패한 실행의 보고서는 옛것일 수 있어 읽지 않습니다


def test_backfill_failure_and_a_stop_reason_are_problems(tmp_path):
    report = dict(GOOD_REPORT, stopped_at="embedding_cost_ceiling", idempotent=False)
    result = setup(tmp_path, last={"exit_code": 0, "backfill_exit_code": 2}, report=report)
    assert result["problems"] == ["backfill_failed", "stopped_at_embedding_cost_ceiling",
                                  "not_idempotent"]


def test_a_stuck_watermark_is_a_problem(tmp_path):
    """하루가 페이지 한도를 넘으면 워터마크가 멈춥니다(ADR-104). 적재는 계속 돌아도 잡힙니다."""
    report = dict(GOOD_REPORT, harvest=dict(GOOD_REPORT["harvest"], truncated_on="2026-10-26"))
    result = setup(tmp_path, watermark="2026-10-25",
                   last={"exit_code": 0, "backfill_exit_code": 0}, report=report)
    assert result["problems"] == ["watermark_behind"]
    assert result["report"]["truncated_on"] == "2026-10-26"


def test_a_report_outside_the_reports_folder_is_not_read(tmp_path):
    outside = tmp_path / "elsewhere.json"
    write(outside, GOOD_REPORT)
    result = setup(tmp_path, last={"exit_code": 0, "backfill_exit_code": 0,
                                   "report": str(outside)})
    assert "report" not in result and result["status"] == "ok"


def test_missing_state_is_unknown(tmp_path):
    result = ingest_status.summary(now=NOW, state_path=tmp_path / "none.json",
                                   last_run_path=tmp_path / "none2.json", reports=tmp_path)
    assert result == {"status": "unknown", "problems": ["no_state"]}


def test_the_check_fails_on_anything_but_ok():
    assert ingest_problem({"ingest": {"status": "ok", "problems": []}}) is None
    assert "오래된 코드" in ingest_problem({"status": "ok"})
    reason = ingest_problem({"ingest": {"status": "problem",
                                        "problems": ["load_stale", "stopped_at_capacity"]}})
    assert "8일" in reason and "stopped_at_capacity" in reason
    assert "수집 상태 파일" in ingest_problem({"ingest": {"status": "unknown",
                                                        "problems": ["no_state"]}})
