"""연결 획득 측정의 **계산부**를 접속 없이 검증합니다.

측정 자체는 Aura가 있어야 하므로 보고서를 읽어 확인하고, 두 식은 순수 함수라 그대로 잽니다.

**가장 중요한 것은 두 식이 섞이지 않는가입니다.** 건강한 경로의 시도당 비용은
`T + EXPLAIN + 문장`이고, 저하 구간에서는 연결을 못 얻어 문장이 돌지 않으므로 **`T` 하나**
입니다. 처음에 이 둘을 섞어서 반대 결론을 냈습니다 — "낮춰도 회복을 넘긴다"로 봤는데,
저하 식으로 다시 계산하니 30초 이하에서 전부 깨집니다.

ADR-015의 `3 × (60 + 30) + 6 = 276초`에는 `EXPLAIN`의 15초가 빠져 있었습니다. 그것도
아래에서 고정합니다 - 같은 자리를 또 틀리지 않게.
"""

import json
from pathlib import Path

import pytest

from scripts.measure_connection_acquisition import (
    CANDIDATES,
    EXPLAIN_TIMEOUT,
    RECOVERY_SECONDS,
    RETRIES,
    RETRY_WAITS,
    STATEMENT_TIMEOUT,
    degraded_last_attempt,
    driver_default_acquisition_timeout,
    healthy_worst,
    minimum_timeout_for_recovery,
    spread,
)

REPORT = Path("reports/connection_acquisition.json")


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(
            f"{REPORT}가 없습니다. uv run python -m scripts.measure_connection_acquisition"
        )
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ── 분포 ────────────────────────────────────────────────────────────────────

def test_the_spread_is_ordered_and_complete():
    out = spread([0.3, 0.1, 0.2])

    assert out["runs"] == 3
    assert out["p50"] == 0.2
    assert out["max"] == 0.3
    assert out["p50"] <= out["p95"] <= out["max"]


def test_a_single_sample_does_not_crash_the_percentiles():
    out = spread([0.5])

    assert out["p50"] == out["p95"] == out["max"] == 0.5


# ── 기본값을 글이 아니라 드라이버에서 ──────────────────────────────────────

def test_the_default_is_read_from_the_driver_not_from_the_adr():
    default = driver_default_acquisition_timeout()

    assert default is None or default > 0


def test_the_measured_default_matches_what_the_adr_assumed(report):
    """다르면 ADR-015·052의 계산이 틀린 값에서 나온 것입니다."""
    default = report["driver_default_acquisition_timeout"]

    assert default == 60.0, f"기본값이 {default}초입니다. ADR-015·052의 계산을 다시 하세요"


# ── 두 식이 섞이지 않는가 ──────────────────────────────────────────────────

def test_the_healthy_formula_counts_the_explain_round_trip():
    """**ADR-015가 빠뜨린 자리입니다.** `run_read`는 질의마다 두 문장을 돌립니다."""
    assert healthy_worst(60) == RETRIES * (60 + EXPLAIN_TIMEOUT + STATEMENT_TIMEOUT) + RETRY_WAITS
    assert healthy_worst(60) == 321
    # ADR-015가 적은 값. 이제 이것이 과소추정임을 고정합니다.
    assert RETRIES * (60 + STATEMENT_TIMEOUT) + RETRY_WAITS == 276
    assert healthy_worst(60) > 276


def test_the_degraded_formula_does_not_count_the_statements():
    """연결을 못 얻으면 EXPLAIN도 본 질의도 돌지 않습니다. 시도당 비용은 획득 하나입니다."""
    assert degraded_last_attempt(60) == 2 * 60 + RETRY_WAITS == 126
    # 건강한 식을 쓰면 126이 아니라 216이 나옵니다. 그 차이가 결론을 뒤집습니다.
    assert degraded_last_attempt(60) != 2 * (60 + EXPLAIN_TIMEOUT + STATEMENT_TIMEOUT) + RETRY_WAITS


def test_the_floor_for_outlasting_the_recovery_is_forty_two():
    """`2T + 6 >= 90`. 이 수가 60초를 지키는 근거이고, 코드 주석에도 적혀 있습니다."""
    minimum = minimum_timeout_for_recovery()

    assert minimum == (RECOVERY_SECONDS - RETRY_WAITS) / 2 == 42
    assert degraded_last_attempt(minimum) == RECOVERY_SECONDS
    assert degraded_last_attempt(minimum - 1) < RECOVERY_SECONDS


def test_sixty_clears_the_floor_and_thirty_does_not(report):
    """**이 측정의 결론입니다.** 43배 여유만 보고 내리면 저하 구간이 깨집니다."""
    by_candidate = report["by_candidate"]

    assert by_candidate["60"]["outlasts_recovery"] is True
    assert by_candidate["30"]["outlasts_recovery"] is False
    # 42가 경계입니다. 그 아래 후보는 전부 못 넘겨야 합니다.
    for candidate in CANDIDATES:
        expected = candidate >= minimum_timeout_for_recovery()
        assert by_candidate[str(candidate)]["outlasts_recovery"] is expected, candidate


# ── 측정값 ──────────────────────────────────────────────────────────────────

def test_the_steady_state_does_not_need_sixty_seconds(report):
    """60초가 평상시용이 아니라는 쪽. 이것만 보면 "내리자"가 되므로 위 테스트와 짝입니다."""
    assert report["worst_cold_seconds"] < 3.0
    assert report["by_candidate"]["60"]["headroom_ratio"] > 20


def test_a_warm_query_is_an_order_of_magnitude_cheaper_than_a_cold_connect(report):
    measured = report["measured"]

    assert measured["warm_series"]["max"] < measured["cold_connect"]["p50"]
    # 드라이버 생성은 게으릅니다. 여기에 비용이 생기면 측정 전제가 바뀝니다.
    assert measured["construct"]["max"] < 0.05


def test_a_blackhole_exhausts_the_timeout_and_a_refusal_does_not(report):
    """**이것이 "낮추면 깨진다"의 증거입니다.**

    패킷이 버려지면 획득이 타임아웃을 꽉 채우므로 T가 재시도 간격을 만듭니다. 거부는
    T와 무관하므로 어떤 T로도 90초를 넘기지 못합니다 - 재시도가 두 모드 중 하나만 견딥니다.
    """
    modes = report["failure_modes"]
    if not modes:
        pytest.skip("--skip-failure-modes 로 돌린 보고서입니다")
    timeouts = sorted(modes["blackhole"], key=float)
    low, high = modes["blackhole"][timeouts[0]], modes["blackhole"][timeouts[-1]]

    # 비례합니다. 가장 큰 타임아웃에서 거의 그 값만큼 걸립니다.
    assert high > 2 * low
    assert high >= float(timeouts[-1]) * 0.9

    refused = [modes["refused"][t] for t in timeouts]
    assert max(refused) - min(refused) < 1.0, refused
    # 그리고 그 상수가 작아서 세 시도가 10초 안에 끝납니다.
    assert 2 * max(refused) + RETRY_WAITS < RECOVERY_SECONDS


def test_the_report_says_what_it_did_not_measure(report):
    """건강한 인스턴스에서 잰 값, 90초가 한 번의 관측이라는 사실이 보고서에 남아야 합니다."""
    note = report["note"]

    assert "90초" in note
    assert "2T+6" in note


def test_the_code_comment_carries_the_floor():
    """42초가 ADR에만 있으면 코드를 고치는 사람이 못 봅니다(ADR-052 결정 2)."""
    for path in ("src/chatbot.py", "pipeline/graph.py"):
        source = Path(path).read_text(encoding="utf-8")
        assert "ADR-052" in source, path
        assert "42" in source, path
