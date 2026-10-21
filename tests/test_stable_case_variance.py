"""`stable_case` 변동 측정의 **계산부와 묶음 규칙**을 검증합니다.

이 측정에서 가장 쉽게 틀리는 것이 둘인데 둘 다 실제로 틀렸습니다.

**① 묶음.** 처음에 지문 + 날짜로 묶었더니 2026-10-15의 네 실행이 한 묶음에 들어왔습니다.
그 보고서들은 ADR-018 이전이라 `prompt_versions`가 없고, 파일명이 `_after_fixes`·`_final`·
`_selfref`이며 패턴이 `[0.0, 1.0, 1.0, 1.0]`입니다 — **변동이 아니라 수정이 들어간 것**
입니다. 섞으면 11건이 하락으로 세여 `q`가 0.0313이 되고 판정이 뒤집힙니다.

**② 0건을 "비율 0"으로 읽는 것.** 240쌍에 사건 0이어도 비율이 0은 아닙니다. 95% 상한이
약 `3/n`이고, 그 상한으로 `P`를 내야 "바를 통과했다"를 말할 수 있습니다. 여기서는 상한이
바를 넘어 **측정되지 않았다**가 답입니다.
"""

import json
from pathlib import Path

import pytest

from scripts.measure_stable_case_variance import (
    POPULATIONS,
    TOLERABLE_RED_RATE,
    group_key,
    one_group,
    red_rate,
)

REPORT = Path("reports/stable_case_variance.json")


def run(cases):
    """`gate.normalize()`가 내놓는 모양만 흉내 냅니다."""
    return {"cases": cases, "path": "x.json", "generated_at": "2026-10-17T00:00:00"}


def case(value, *, failed=False):
    return {"failed": failed, "metrics": {} if value is None else {"m": value}}


def test_a_case_perfect_everywhere_contributes_pairs_but_no_drops():
    runs = [run({"a": case(1.0)}) for _ in range(3)]

    out = one_group(runs, "m")

    assert out["eligible_pairs"] == 3
    assert out["drops"] == []
    assert out["drop_rate"] == 0.0


def test_a_drop_is_counted_once_per_run():
    runs = [run({"a": case(1.0)}), run({"a": case(0.0)}), run({"a": case(0.5)})]

    out = one_group(runs, "m")

    assert out["eligible_pairs"] == 3
    assert out["drops"][0]["case"] == "a"
    assert out["drops"][0]["below"] == 2
    assert out["drop_rate"] == pytest.approx(2 / 3)


def test_an_agent_failure_is_not_counted_as_a_drop():
    """**규칙이 그 채널을 이미 건너뜁니다.** 여기서 세면 같은 변동에 두 번 벌점입니다.

    게이트 주석이 그 이유를 계산까지 해 뒀습니다 - 실패율 5.6%에 안정 사례 29건이면
    적어도 한 건이 실패할 확률이 81%이고, "그런 게이트는 꺼집니다".
    """
    runs = [run({"a": case(1.0)}), run({"a": case(0.0, failed=True)}), run({"a": case(1.0)})]

    out = one_group(runs, "m")

    assert out["agent_failures_skipped"] == 1
    assert out["eligible_pairs"] == 2
    assert out["drops"] == []


def test_a_case_never_perfect_is_not_eligible():
    """기준선이 안정으로 부를 수 없는 사례는 이 질문의 모집단이 아닙니다."""
    runs = [run({"a": case(0.5)}), run({"a": case(0.5)})]

    out = one_group(runs, "m")

    assert out["eligible_pairs"] == 0
    assert out["drops"] == []


def test_runs_without_a_fingerprint_get_a_separate_key():
    """**이 구분이 판정을 뒤집습니다.** 지문 없는 묶음은 본문에서 버려집니다."""
    with_prints = {"prompt_versions": {"answer": "abc"}, "generated_at": "2026-10-17T01:00:00"}
    without = {"prompt_versions": {}, "generated_at": "2026-10-15T01:00:00"}

    assert group_key(with_prints) != group_key(without)
    assert group_key(without).startswith("none@")
    # 같은 지문이라도 날짜가 다르면 다른 묶음입니다 - 2026-10-18 라우팅 변경은 프롬프트를
    # 바꾸지 않았으므로 지문만으로는 코드 상태를 가릴 수 없습니다.
    later = {**with_prints, "generated_at": "2026-10-18T01:00:00"}
    assert group_key(with_prints) != group_key(later)


def test_the_key_is_reproducible_across_processes():
    """`hash()`는 프로세스마다 달라집니다(PYTHONHASHSEED). 보고서에 적히는 키이므로
    **다른 프로세스에서 같은 값이 나와야** 합니다.

    상수를 박지 않습니다 - 박으면 "그 값이 맞나"를 다시 확인할 수 없고, 재는 것이
    재현성이 아니라 제 기억이 됩니다. 씨앗을 바꿔 띄운 프로세스와 맞춰 봅니다.
    """
    import os
    import subprocess
    import sys

    run_one = {"prompt_versions": {"answer": "abc"}, "generated_at": "2026-10-17T01:00:00"}
    here = group_key(run_one)

    env = {**os.environ, "PYTHONHASHSEED": "12345", "PYTHONIOENCODING": "utf-8"}
    code = (
        "from scripts.measure_stable_case_variance import group_key;"
        "print(group_key({'prompt_versions': {'answer': 'abc'},"
        " 'generated_at': '2026-10-17T01:00:00'}))"
    )
    other = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=os.getcwd(),
    )

    assert other.returncode == 0, other.stderr[-400:]
    assert other.stdout.strip() == here
    assert here.endswith("@2026-10-17")


def test_the_red_rate_grows_with_the_population():
    """사례당 비율이 작아도 30건을 곱하면 커집니다 - 그게 이 질문의 요지입니다."""
    assert red_rate(0.0, 30) == 0.0
    assert red_rate(0.0125, 5) < red_rate(0.0125, 30)
    assert red_rate(0.0125, 30) == pytest.approx(0.3143, abs=1e-4)


# ── 실제 측정 결과 ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def measured():
    if not REPORT.exists():
        pytest.skip(f"{REPORT}가 없습니다. uv run python -m scripts.measure_stable_case_variance")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_old_reports_were_excluded(measured):
    """2026-10-15 묶음을 섞으면 코드 차이를 변동으로 세게 됩니다."""
    assert measured["groups_dropped_no_fingerprint"] == ["none@2026-10-15"]
    assert all(not key.startswith("none@") for key in measured["groups"])


def test_no_drop_was_seen_in_the_same_code_repeats(measured):
    assert measured["pooled_drops"] == 0
    assert measured["pooled_eligible_pairs"] == 240


def test_zero_events_is_not_a_rate_of_zero(measured):
    """**이 테스트가 결론을 고정합니다.** 상한이 바를 넘으므로 측정되지 않았습니다."""
    assert measured["pooled_drop_rate"] == 0.0
    assert measured["pooled_drop_rate_upper95"] == pytest.approx(3 / 240)
    upper = measured["red_rate_upper95_by_population"]

    assert upper[str(max(POPULATIONS))] > TOLERABLE_RED_RATE
    assert upper[str(min(POPULATIONS))] < TOLERABLE_RED_RATE
    assert measured["verdict"] == "not_resolved"


def test_the_report_says_how_many_pairs_would_settle_it(measured):
    """"더 재야 한다"로 끝내지 않습니다. 얼마나 더인지 적습니다."""
    needed = measured["pairs_needed_for_tolerance"]

    assert needed > measured["pooled_eligible_pairs"]
    assert red_rate(3.0 / needed, max(POPULATIONS)) < TOLERABLE_RED_RATE
