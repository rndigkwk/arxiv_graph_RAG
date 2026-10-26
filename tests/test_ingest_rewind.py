"""`--rewind-to`는 워터마크만 뒤로 옮깁니다(ADR-074).

위험한 방향은 둘입니다. 앞으로 감으면 데이터를 조용히 건너뛰고, 빈 워터마크에 날짜를 넣으면
되감기가 아니라 시작점을 새로 정하게 됩니다. 2026-10-24에 사용자가 워터마크를 손으로 비웠을 때
"원래 비어 있었나, 지워진 건가"를 상태 파일로 가를 수 없었습니다 - 그래서 사유를 필수로 남깁니다.
"""

import json
import sys
from datetime import UTC, datetime

import pytest

from pipeline import ingest

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def state(watermark="2026-10-23"):
    return {"last_datestamp": watermark, "runs": []}


def test_the_next_window_starts_on_the_rewound_date():
    s = state()
    entry = ingest.rewind(s, "2026-10-20", "보강이 빈 창", now=NOW)

    assert s["last_datestamp"] == "2026-10-19"
    start, _ = ingest.harvest_window(s, days=7, today=NOW.date())
    assert start == "2026-10-20" == entry["next_window_from"]


def test_the_run_log_keeps_before_after_and_the_reason():
    s = state()
    ingest.rewind(s, "2026-10-20", "보강이 빈 창", now=NOW)

    assert s["runs"][-1] == {
        "at": NOW.isoformat(), "stopped_at": "rewound",
        "watermark_before": "2026-10-23", "watermark_after": "2026-10-19",
        "next_window_from": "2026-10-20", "reason": "보강이 빈 창",
        "loaded": False, "watermark_advanced": False,
    }


def test_rewinding_to_the_watermark_itself_rereads_that_day():
    s = state()
    ingest.rewind(s, "2026-10-23", "마지막 날만", now=NOW)
    assert s["last_datestamp"] == "2026-10-22"


@pytest.mark.parametrize("to", ["2026-10-24", "2026-11-01"])
def test_winding_forward_is_refused_because_it_skips_days(to):
    """10-24는 다음 실행이 어차피 받을 날입니다. 되감기가 아니므로 기록하지 않습니다."""
    s = state()
    with pytest.raises(ingest.RewindRefused, match="앞으로"):
        ingest.rewind(s, to, "실수", now=NOW)
    assert s == state()


@pytest.mark.parametrize("watermark", ["", None])
def test_an_empty_watermark_has_nothing_to_rewind(watermark):
    s = {"last_datestamp": watermark, "runs": []}
    with pytest.raises(ingest.RewindRefused, match="되감을 것이 없습니다"):
        ingest.rewind(s, "2026-10-01", "처음부터", now=NOW)
    assert s["runs"] == []


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_rewind_without_a_reason_is_refused(reason):
    s = state()
    with pytest.raises(ingest.RewindRefused, match="사유"):
        ingest.rewind(s, "2026-10-20", reason, now=NOW)
    assert s == state()


def test_a_bad_date_and_a_hand_edited_watermark_are_refused():
    with pytest.raises(ingest.RewindRefused, match="날짜"):
        ingest.rewind(state(), "10/20", "x", now=NOW)
    with pytest.raises(ingest.RewindRefused, match="손으로"):
        ingest.rewind(state("2026-13-40"), "2026-10-01", "x", now=NOW)


def test_the_dead_letter_file_gets_a_boundary_line(tmp_path):
    path = tmp_path / "dead.jsonl"
    path.write_text('{"arxiv_id": "2610.00001", "reason": "timeout"}\n', encoding="utf-8")
    entry = ingest.rewind(state(), "2026-10-20", "보강이 빈 창", now=NOW)

    ingest.mark_rewind_in_dead_letters(entry, path)

    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert lines[-1] == {"rewound": True, "watermark_before": "2026-10-23",
                         "watermark_after": "2026-10-19", "reason": "보강이 빈 창",
                         "recorded_at": NOW.isoformat()}


def test_no_dead_letter_file_is_created_just_for_the_marker(tmp_path):
    path = tmp_path / "dead.jsonl"
    ingest.mark_rewind_in_dead_letters(ingest.rewind(state(), "2026-10-20", "x", now=NOW), path)
    assert not path.exists()


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """main()이 저장소의 진짜 상태 파일을 건드리지 않게 합니다."""
    path = tmp_path / "ingest.json"
    ingest.write_state(state(), path)
    monkeypatch.setattr(ingest, "read_state", lambda: ingest.json.loads(path.read_text("utf-8")))
    monkeypatch.setattr(ingest, "write_state", lambda s: path.write_text(json.dumps(s), "utf-8"))
    monkeypatch.setattr(ingest, "DEAD_LETTER_PATH", tmp_path / "dead.jsonl")
    monkeypatch.setattr(ingest.oai, "harvest", lambda *a, **k: pytest.fail("수집하면 안 됩니다"))
    return path


def run(monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["ingest", *argv])
    return ingest.main()


def test_main_rewinds_and_stops_without_harvesting(isolated, monkeypatch, capsys):
    assert run(monkeypatch, "--rewind-to", "2026-10-20", "--reason", "보강이 빈 창") == 0

    assert json.loads(isolated.read_text("utf-8"))["last_datestamp"] == "2026-10-19"
    out = capsys.readouterr().out
    assert "지금 워터마크: 2026-10-23" in out and "지우지 못합니다" in out


def test_main_leaves_the_state_alone_when_refused(isolated, monkeypatch, capsys):
    assert run(monkeypatch, "--rewind-to", "2026-11-01", "--reason", "x") == 1
    assert json.loads(isolated.read_text("utf-8")) == state()


@pytest.mark.parametrize("argv", [
    ["--rewind-to", "2026-10-20"],
    ["--reason", "x"],
    ["--rewind-to", "2026-10-20", "--reason", "x", "--load"],
])
def test_rewind_is_never_combined_with_loading_or_left_unexplained(isolated, monkeypatch, argv):
    with pytest.raises(SystemExit) as raised:
        run(monkeypatch, *argv)
    assert raised.value.code == 2
    assert json.loads(isolated.read_text("utf-8")) == state()
