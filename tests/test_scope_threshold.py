"""범위 확인 기준을 재는 질문 셋과 요약(ADR-095). 접속 없이 봅니다."""

import json
from pathlib import Path

from evals import scope_threshold
from src import pre_scope


def test_the_set_has_fifty_on_each_side_without_duplicates():
    cases = json.loads(Path("evals/cases_scope.json").read_text(encoding="utf-8"))
    for kind in ("out", "in"):
        assert len(cases[kind]) == 50 == len(set(cases[kind])), kind
    assert not set(cases["out"]) & set(cases["in"])


def test_the_summary_counts_misses_and_refusals_per_threshold():
    rows = [
        {"kind": "out", "path": "score", "score": 0.65, "declined": True},
        {"kind": "out", "path": "score", "score": 0.71, "declined": False},
        {"kind": "out", "path": "cue", "score": None},
        {"kind": "in", "path": "score", "score": 0.69, "declined": True},
        {"kind": "in", "path": "score", "score": 0.80, "declined": False},
    ]
    summary = scope_threshold.summarize(rows)
    assert summary["decline_below"] == pre_scope.DECLINE_BELOW
    # 0.70: 0.65만 거절 → 범위 밖 3개 중 2개 놓침(단서로 빠진 것 포함), 범위 안 0.69 하나 오거절.
    assert summary["by_threshold"]["0.70"] == {"out_missed": 2, "in_refused": 1}
    assert summary["by_threshold"]["0.66"] == {"out_missed": 2, "in_refused": 0}
    assert summary["out"]["by_path"] == {"cue": 1, "author": 0, "score": 2}
