"""용량 계산이 **묶이는 축**을 보고, 지운 뒤 잃는 대답을 함께 세는지 검증합니다.

ADR-001이 한도를 적었고 ADR-038이 "다음 달 스냅샷이 필요하다"로 남겨 뒀습니다. 그 사이를
잇는 계산인데, 틀리기 쉬운 자리가 셋입니다.

1. **노드만 줄이는 방안은 자리를 만들지 못합니다.** 묶이는 쪽이 관계이기 때문입니다.
2. **수확만 세면 안 됩니다.** 지운 뒤 대답할 수 없게 되는 질문을 함께 세야 고를 수 있습니다.
3. **마지막 달은 부분값입니다.** 평균에 넣으면 월 생산량이 과소평가되고, 그러면 남은 달 수가
   과대평가됩니다.

Neo4j에 붙지 않습니다 - 스냅샷 파일만 읽습니다.
"""

from __future__ import annotations

import collections
import json
import re
from pathlib import Path

import pytest

from pipeline import capacity

REPORT = Path("reports/capacity.json")


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"보고서가 없습니다: {REPORT} (uv run python -m pipeline.capacity)")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_module_does_not_touch_the_database():
    source = Path("pipeline/capacity.py").read_text(encoding="utf-8")

    assert not re.search(r"^\s*(import neo4j|from neo4j)", source, re.MULTILINE)
    assert "run_read" not in source
    assert not re.search(r"^\s*(import openai|from openai)", source, re.MULTILINE)


def test_the_binding_axis_decides_how_many_papers_fit():
    """관계가 먼저 차면 노드 여유는 답을 바꾸지 않습니다."""
    use = {
        "nodes": {"free": 100_000, "parts": {"ArxivPaper": 1_000, "ArxivAuthorName": 2_000}},
        "relationships": {"free": 1_000},
    }
    rate = {"relationships_per_paper": 10.0, "papers_per_month": 50.0, "peak_month": ("x", 100)}

    room = capacity.headroom(use, rate)

    assert room["binding_axis"] == "relationships"
    assert room["papers_that_fit"] == 100
    # 노드 여유를 열 배로 늘려도 답이 같아야 합니다.
    use["nodes"]["free"] = 1_000_000
    assert capacity.headroom(use, rate)["papers_that_fit"] == 100


def test_the_other_axis_can_bind_too():
    use = {
        "nodes": {"free": 300, "parts": {"ArxivPaper": 1_000, "ArxivAuthorName": 2_000}},
        "relationships": {"free": 1_000_000},
    }
    rate = {"relationships_per_paper": 10.0, "papers_per_month": 50.0, "peak_month": ("x", 100)}

    room = capacity.headroom(use, rate)

    assert room["binding_axis"] == "nodes"
    # 논문 1편이 자기 노드 1개와 새 저자 이름 2개를 데려옵니다.
    assert room["papers_that_fit"] == 100


def test_a_cap_below_what_the_eval_needs_is_marked_as_breaking(report):
    """상한을 수확만 보고 고르면 정답을 지웁니다."""
    deepest = report["eval_dependency"]["deepest_author_position_the_eval_needs"]
    caps = {
        int(re.search(r"(\d+)명", row["candidate"]).group(1)): row
        for row in report["candidates"]
        if "상한" in row["candidate"]
    }
    assert caps, "상한 후보가 하나도 없습니다"

    for cap, row in caps.items():
        assert row["breaks_the_eval"] is (cap < deepest), (cap, deepest)


def test_the_eval_really_depends_on_a_deep_author_position(report):
    """이 저장소의 사실입니다 - 공유 저자가 860번째에 있는 사례가 있습니다."""
    deepest = report["eval_dependency"]["deepest_author_position_the_eval_needs"]

    assert deepest > 100, deepest
    # 그래서 쓸 만한 수확을 내는 상한은 전부 평가를 깨뜨립니다.
    useful = [
        row for row in report["candidates"]
        if "상한" in row["candidate"] and row["relationships_freed"] > 20_000
    ]
    assert useful and all(row["breaks_the_eval"] for row in useful)


def test_deleting_names_that_appear_once_cannot_break_a_coauthor_answer():
    """차수 1인 이름은 **두 논문을 이을 수 없습니다.** 구조라서 측정이 아니라 성질입니다."""
    order = {"p1": ["혼자", "둘"], "p2": ["둘", "혼자둘"]}
    degree = collections.Counter({"혼자": 1, "둘": 2, "혼자둘": 1})

    shared_before = set(order["p1"]) & set(order["p2"])
    once = {name for name, count in degree.items() if count == 1}
    shared_after = {name for name in shared_before if name not in once}

    assert shared_before == shared_after == {"둘"}


def test_the_once_only_candidate_states_the_loss_the_eval_does_not_measure(report):
    """평가를 깨뜨리지 않는 것과 손실이 없는 것은 다릅니다."""
    row = next(r for r in report["candidates"] if "한 편에만" in r["candidate"])

    assert row["breaks_the_eval"] is False
    assert row["relationships_freed"] > 0 and row["nodes_freed"] > 0
    # 저자를 전부 잃는 논문 수를 말해야 합니다 - 그것이 고르는 근거입니다.
    assert "저자를 전부 잃고" in row["why"]
    assert "평가가 묻지 않는 손실" in row["why"]


def test_deleting_isolated_papers_is_judged_against_the_gold_not_a_guess(report):
    """"검색이 그 논문을 돌려준다"는 추측입니다. 정답에 들어 있는지는 사실입니다."""
    row = next(r for r in report["candidates"] if "인용 간선이 없는" in r["candidate"])

    assert "gold_papers_deleted" in row
    assert row["breaks_the_eval"] is bool(row["gold_papers_deleted"])


def test_the_partial_last_month_is_kept_out_of_the_rate(report):
    """마지막 달을 평균에 넣으면 남은 달 수가 과대평가됩니다."""
    recent = report["rate"]["recent_months"]
    partial = report["rate"]["partial_last_month"]

    assert partial, "마지막 달을 따로 적어야 합니다"
    assert not set(partial) & set(recent)
    # 부분값이 평균보다 작으므로, 넣으면 평균이 내려가고 달 수가 늘어납니다.
    assert report["rate"]["papers_per_month"] > min(partial.values())


def test_one_more_month_fits_without_deleting_anything(report):
    """제가 "한 달치를 더하면 한도를 칩니다"라고 적었던 것이 틀렸습니다(ADR-045).

    깨지면 그 결론이 바뀐 것이므로 ADR-045와 ROADMAP을 함께 고쳐야 합니다.
    """
    room = report["headroom"]

    assert room["binding_axis"] == "relationships"
    assert room["papers_that_fit"] > report["rate"]["papers_per_month"]
    assert room["months_that_fit"] >= 1.0
    # 가장 생산량이 많았던 달의 속도로도 한 달은 들어갑니다.
    assert room["months_at_peak_rate"] >= 1.0


def test_the_biggest_candidate_is_not_judged_here(report):
    """DB에서만 잴 수 있는 것을 여기서 판정하면 근거 없는 결론이 됩니다."""
    row = next(r for r in report["candidates"] if "속성 배열" in r["candidate"])

    assert row["breaks_the_eval"] is None
    assert "DB에서만 잴 수 있습니다" in row["why"]
    # 수확은 측정값이어야 합니다.
    assert row["relationships_freed"] == report["usage"]["relationships"]["parts"]["AUTHORED"]
    assert row["nodes_freed"] == report["usage"]["nodes"]["parts"]["ArxivAuthorName"]


def test_the_usage_matches_the_snapshot_manifest(report):
    """화면·문서가 인용하는 수치입니다. 스냅샷과 갈라지면 둘 중 하나가 늙은 것입니다."""
    manifest = json.loads(
        Path(f"snapshots/{report['snapshot']}/manifest.json").read_text(encoding="utf-8")
    )
    counts = manifest["counts"]

    assert report["usage"]["nodes"]["used"] == counts["papers"] + counts["authors"]
    assert report["usage"]["relationships"]["used"] == counts["authored"] + counts["references"]
    assert report["usage"]["relationships"]["limit"] == capacity.RELATIONSHIP_LIMIT
