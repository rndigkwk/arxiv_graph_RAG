"""미세조정 재정렬기의 판정이 바를 그대로 확인하게 지킵니다(ADR-057).

이 파일이 막는 것은 하나입니다 — **바를 적어 두고 그것을 확인할 수 없는 도구.** 2026-10-23에
그 상태였습니다: 바는 코사인을 기준으로 간선 0개 집단에서 이기라고 적혀 있었는데, 하네스의
쌍체 구간은 기준이 `cocited`로 하드코딩되고 전체 시드로만 계산됐습니다. 그래서 바의 판정을
손으로 구해야 했습니다.
"""

import json
from pathlib import Path

import pytest

from evals import related_study

ADR = Path("docs/adr/ADR-057-fine-tune-the-reranker-on-cocitation.md")
REPORT = Path("reports/rerank_cocite_ft.json")
# ADR-057이 재기 전에 적은 코사인 기준선입니다.
BAR = {"recall_at_k": 0.1227, "hit_at_k": 0.4739, "reciprocal_rank": 0.3457}
COHORT = "no_citation_edges"


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"판정 보고서를 읽을 수 없습니다: {REPORT}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ── 하네스가 바를 확인할 수 있어야 합니다 ───────────────────────────────

def test_the_incumbent_is_an_argument_not_a_constant():
    """바가 코사인을 기준으로 적혀 있으므로 기준을 고를 수 있어야 합니다."""
    source = Path("evals/related_study.py").read_text(encoding="utf-8")
    assert '"--incumbent"' in source
    assert "args.incumbent" in source
    # 하드코딩된 기준으로 구간을 계산하는 자리가 남아 있으면 안 됩니다.
    assert "paired_bootstrap(per_seed[INCUMBENT]" not in source


def test_the_interval_is_computed_per_cohort():
    """바는 **간선 0개 집단**에 적혀 있습니다. 전체 구간만으로는 확인할 수 없습니다."""
    source = Path("evals/related_study.py").read_text(encoding="utf-8")
    head = source[: source.index("by_slice = {")]
    assert head.count('"vs_incumbent"') >= 1, "집단별 구간이 by_degree_slice에 없습니다"


def test_the_report_records_which_variant_was_the_baseline():
    """어느 기준으로 잰 구간인지 적지 않으면 그 구간을 읽을 수 없습니다."""
    source = Path("evals/related_study.py").read_text(encoding="utf-8")
    assert '"incumbent": args.incumbent,' in source


# ── 커밋된 판정 ─────────────────────────────────────────────────────────

def test_the_judgment_compared_against_cosine(report):
    assert report["incumbent"] == "vector"
    assert set(report["variants"]) >= {"rerank", "vector"}


def test_the_instrument_reproduced_the_bar_baseline(report):
    """코사인이 ADR에 적어 둔 값과 다르면 이 비교는 성립하지 않습니다."""
    cohort = report["by_degree_slice"][COHORT]
    for metric, expected in BAR.items():
        assert cohort["vector"][metric] == pytest.approx(expected, abs=5e-5), metric


def test_the_finetune_lost_on_every_metric_with_the_interval_excluding_zero(report):
    """기각의 근거입니다. 하나라도 뒤집히면 ADR-057을 다시 읽어야 합니다."""
    cohort = report["by_degree_slice"][COHORT]
    for metric in BAR:
        interval = cohort["vs_incumbent"]["rerank"][metric]
        assert interval["mean"] < 0, metric
        assert interval["excludes_zero"] is True, metric
        assert interval["high"] < 0, metric


def test_the_adjacent_cohort_was_not_broken(report):
    """부가 조건입니다 - 간선 1~4개 집단에서 유의하게 내려가면 안 됩니다."""
    cohort = report["by_degree_slice"]["degree_1_to_4"]
    for metric in BAR:
        interval = cohort["vs_incumbent"]["rerank"][metric]
        assert interval["excludes_zero"] is False, metric


def test_the_gap_to_cosine_halved_and_the_adr_says_so(report):
    """**기각의 이유가 "맞지 않다"가 아니라 "거리가 모자랐다"입니다.**

    사전학습이 −0.0359였고 미세조정이 그 절반 아래입니다. 다음 사람이 이 차이를 알아야
    하므로 ADR이 그것을 적고 있는지도 봅니다.
    """
    cohort = report["by_degree_slice"][COHORT]
    gap = cohort["rerank"]["recall_at_k"] - cohort["vector"]["recall_at_k"]
    assert -0.0359 / 2 < gap < 0, gap
    text = ADR.read_text(encoding="utf-8")
    assert "기각" in text.splitlines()[2]
    assert "절반" in text


def test_the_cohort_the_bar_names_is_the_one_measured(report):
    """집계를 먼저 읽으면 두 체제가 섞입니다(ADR-036)."""
    cohort = report["by_degree_slice"][COHORT]
    assert cohort["seeds"] == 1112
    # **풀 50이어야 ADR-051과 비교가 성립합니다.** `gbm_pool`은 gbm을 돌릴 때만 채워지므로
    # 풀은 따로 적습니다.
    assert report["pool"] == related_study.GBM_POOL == 50
