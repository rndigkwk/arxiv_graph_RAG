"""질문마다 SLI 한 줄과 그 집계(ADR-126).

- 토큰은 공급자가 준 턴만 더하고, 하나도 없으면 0이 아니라 None입니다.
- 좋은 요청은 **답이** 목표 안에 끝난 것입니다. 기권·오류는 분모에만 들어갑니다.
- 로그의 다른 줄과 깨진 줄은 건너뜁니다.
"""

from __future__ import annotations

import io
import json

from scripts import summarize_sli
from src import sli

RESPONSE = {
    "generate_trace": {"model_turns": 2, "turns": [
        {"seconds": 1.0, "output_tokens": 40, "input_tokens": 3000, "cached_input_tokens": 2560},
        {"seconds": 2.0, "output_tokens": 60, "input_tokens": 3500, "cached_input_tokens": None},
    ]},
    "graph_trace": {"attempts": 1},
    "answer": "질문과 답은 남기지 않습니다",
}


def test_a_record_sums_what_the_provider_reported():
    row = sli.record("final", 4.56789, 1.2, RESPONSE, "abc1234")
    assert row == {
        "outcome": "final", "seconds": 4.568, "first_partial_seconds": 1.2,
        "model_turns": 2, "attempts": 1, "input_tokens": 6500, "cached_input_tokens": 2560,
        "output_tokens": 100, "commit": "abc1234",
    }
    empty = sli.record("error", 0.5, None, None, None, "RuntimeError")
    assert empty["input_tokens"] is None and empty["cached_input_tokens"] is None
    assert empty["error"] == "RuntimeError"


def test_the_log_line_is_one_parsable_line(capsys):
    stream = io.StringIO()
    handler = sli.logger.handlers[0]
    old = handler.setStream(stream)
    try:
        sli.log(sli.record("final", 1.0, 0.5, RESPONSE, "abc1234"))
    finally:
        handler.setStream(old)
    text = stream.getvalue()
    assert text.count("\n") == 1 and text.startswith(sli.PREFIX)
    assert "남기지" not in text
    assert sli.parse([text])[0]["commit"] == "abc1234"


def line(outcome, seconds, cached=None, total=None, commit="c1"):
    row = {"outcome": outcome, "seconds": seconds, "first_partial_seconds": 1.0,
           "input_tokens": total, "cached_input_tokens": cached, "commit": commit}
    return 'INFO: 1.2.3.4 "POST /api/ask"\n' + sli.PREFIX + json.dumps(row)


def test_good_ratio_counts_only_answers_within_the_target():
    text = "\n".join([
        line("final", 4.0, 900, 1000), line("final", 12.0, 100, 1000),
        line("abstain", 3.0), line("error", 0.2),
        sli.PREFIX + "{깨진 줄", "uvicorn started",
    ])
    summary = sli.summarize(sli.parse(text.splitlines()))
    assert summary["requests"] == 4
    assert summary["outcomes"] == {"final": 2, "abstain": 1, "error": 1}
    assert summary["good_ratio"] == 0.25
    assert summary["p50_seconds"] == 12.0  # 답 둘 - 위치로 읽고 보간하지 않습니다
    assert summary["cached_input_ratio"] == 0.5


def test_no_lines_gives_no_numbers():
    summary = sli.summarize([])
    assert summary["requests"] == 0 and summary["good_ratio"] is None
    assert summary["p50_seconds"] is None and summary["cached_input_ratio"] is None


def test_the_script_reads_a_log_and_filters_by_commit(tmp_path, capsys):
    log = tmp_path / "api.log"
    log.write_text("\n".join([line("final", 4.0, commit="old"), line("final", 6.0, commit="new")]),
                   encoding="utf-8")
    assert summarize_sli.main([str(log), "--commit", "new"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["requests"] == 1 and out["p50_seconds"] == 6.0
