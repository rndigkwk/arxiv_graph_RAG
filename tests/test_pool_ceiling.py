"""후보 풀 상한 측정이 **이미 발표한 수치를 재현하는지** 고정합니다.

새 스크립트의 첫 번째 위험은 "그럴듯한데 다른 것을 재는 것"입니다. 그래서 이 측정이
[ADR-051](../docs/adr/ADR-051-the-cross-encoder-loses-to-cosine.md)이 적은 두 점을 다시
내는지 봅니다 — 간선 0개 집단 **0.2690**, 간선 1~4개 집단 **0.9866**. 둘이 맞으면 상한의
정의와 집단 분류와 자르는 선이 `evals/related_study.py`와 같다는 뜻입니다.

보고서를 읽어서 확인합니다. 1,377 시드를 여섯 풀 크기로 다시 도는 것은 테스트에 둘 시간이
아니고, 재현 명령은 모듈 docstring에 적혀 있습니다.
"""

import json
from pathlib import Path

import pytest

from scripts.measure_pool_ceiling import CASES, DEFAULT_POOLS, recall, summarize

REPORT = Path("reports/pool_ceiling.json")


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"{REPORT}가 없습니다. uv run python -m scripts.measure_pool_ceiling")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_published_ceilings_are_reproduced(report):
    """**이 테스트가 측정을 ADR-051에 묶습니다.**

    같은 풀(50)에서 같은 두 수가 나오지 않으면, 이 스크립트는 다른 것을 재고 있습니다.
    """
    by_degree = report["by_degree"]

    assert by_degree["no_citation_edges"]["ceiling_at_pool"]["50"] == 0.2690
    assert by_degree["degree_1_to_4"]["ceiling_at_pool"]["50"] == 0.9866
    # 집단 크기까지 같아야 합니다. 상한이 맞고 모집단이 다르면 우연입니다.
    assert by_degree["no_citation_edges"]["seeds"] == 1112
    assert by_degree["degree_1_to_4"]["seeds"] == 251


def test_the_cut_is_the_one_the_gold_declares(report):
    """자르지 않으면 공동인용이 정답을 보고 상한이 1.0이 됩니다(ADR-035)."""
    declared = json.loads(CASES.read_text(encoding="utf-8"))["feature_cut"]

    assert report["feature_cut"] == declared
    # 간선이 없는 시드에 공동인용 후보가 있으면 자르는 선이 새고 있습니다.
    assert report["by_degree"]["no_citation_edges"]["cocited_only"] == 0.0


def test_the_ceiling_cannot_fall_as_the_pool_deepens():
    """풀은 커질 때 **포함 관계**입니다. 상한이 내려가면 계산이 틀렸습니다."""
    rows = [
        {"citation_degree": 0, "gold_size": 2, "cocited_only": 0.0,
         "pool_50": 0.25, "size_50": 49, "pool_500": 0.5, "size_500": 499},
    ]
    out = summarize(rows, (50, 500))["no_citation_edges"]

    assert out["ceiling_at_pool"] == {"50": 0.25, "500": 0.5}
    assert out["gain_from_deepening"] == 0.25


def test_an_empty_gold_does_not_divide_by_zero():
    assert recall({"a"}, []) == 0.0
    assert recall(set(), ["a"]) == 0.0
    assert recall({"a", "b"}, ["a", "c"]) == 0.5


def test_the_default_cases_file_is_the_future_labelled_one():
    """`cases_related.json`을 기본으로 두면 상한이 누출로 1.0이 됩니다."""
    assert CASES.name == "cases_related_future.json"
    assert json.loads(CASES.read_text(encoding="utf-8"))["feature_cut"]


def test_the_current_pool_is_among_the_measured_sizes():
    """지금 쓰는 값이 표에 없으면 "넓히면 얼마나 오르나"의 기준점이 없습니다."""
    from evals.related_study import GBM_POOL

    assert GBM_POOL in DEFAULT_POOLS
    assert DEFAULT_POOLS[0] == GBM_POOL
