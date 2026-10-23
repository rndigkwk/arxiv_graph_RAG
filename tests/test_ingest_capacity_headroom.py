"""주 단위 창의 용량 여유를 지킵니다(EXPERIMENTS 11-42절).

11절이 *"관계 한도의 84.6%를 이미 쓰고 있어서 자동 적재를 켜면 몇 주 안에 부딪힙니다"* 로
닫았고, 그 "몇 주"가 숫자가 아니었습니다. 11-42절이 **4.2회**로 바꿨습니다.

이 파일이 지키는 것은 그 수가 아니라 **그 수가 무엇에 달려 있는지**입니다 — 배치 크기와
안전 천장. 둘 중 하나가 바뀌면 4.2도 바뀌고, 그때 문서를 함께 고쳐야 합니다.
"""

import json
from pathlib import Path

import pytest

from pipeline.ingest import CAPACITY_SAFETY, NODE_LIMIT, RELATIONSHIP_LIMIT

REPORT = Path("reports/ingest_weekly_window.json")
EXPERIMENTS = Path("EXPERIMENTS.md")


@pytest.fixture(scope="module")
def capacity():
    if not REPORT.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {REPORT}")
    return json.loads(REPORT.read_text(encoding="utf-8"))["capacity"]


# 보고서의 열 이름이 `nodes`/`relationships`인데 한도는 단수입니다. 문자열을 깎아 만들면
# 읽히지 않으므로 적어 둡니다.
LIMIT_OF = {"nodes": "node_limit", "relationships": "relationship_limit"}


def batches_to_ceiling(capacity, kind):
    """천장까지 이 배치 크기로 몇 번인가. 11-42절의 표가 이 식입니다."""
    added = capacity[f"projected_{kind}"] - capacity[f"current_{kind}"]
    ceiling = int(capacity[LIMIT_OF[kind]] * capacity["safety_ceiling"])
    return (ceiling - capacity[f"projected_{kind}"]) / added


def test_the_limits_come_from_the_pipeline_not_from_the_report(capacity):
    """보고서의 한도가 코드와 갈라지면 여유 계산이 다른 그래프를 재게 됩니다."""
    assert capacity["node_limit"] == NODE_LIMIT
    assert capacity["relationship_limit"] == RELATIONSHIP_LIMIT
    assert capacity["safety_ceiling"] == CAPACITY_SAFETY


def test_relationships_are_the_binding_constraint(capacity):
    """삭제를 고민할 대상이 노드가 아니라 관계라는 것이 11-42절의 결론입니다."""
    assert capacity["binding_constraint"] == "relationships"
    assert batches_to_ceiling(capacity, "relationships") < batches_to_ceiling(
        capacity, "nodes"
    )


def test_this_batch_still_fits(capacity):
    assert capacity["fits"] is True
    assert capacity["relationship_usage"] < CAPACITY_SAFETY


def test_the_headroom_the_document_states_matches_the_report(capacity):
    """**4.2회가 문서와 보고서 양쪽에 있어야 합니다.** 한쪽만 늙으면 거짓이 됩니다."""
    batches = batches_to_ceiling(capacity, "relationships")
    text = EXPERIMENTS.read_text(encoding="utf-8")

    assert f"약 {batches:.1f}회" in text, f"{batches:.1f}"
    # 노드 쪽도 같이 적혀 있어야 비교가 읽힙니다.
    assert f"약 {batches_to_ceiling(capacity, 'nodes'):.1f}회" in text


def test_the_document_says_the_number_depends_on_the_batch_size():
    """`--categories`를 넓히면 배치가 커지고 4.2회가 줄어듭니다. 그 사실이 빠지면 안 됩니다."""
    text = EXPERIMENTS.read_text(encoding="utf-8")
    assert "한 배치 크기에 달려 있다" in text


def test_loading_would_still_be_blocked_by_missing_embeddings():
    """ADR-047의 검사입니다. 이 창에서는 967편 전부에 임베딩이 없습니다."""
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    embeddings = report["embeddings"]

    assert embeddings["ready"] is False
    assert embeddings["missing"] == embeddings["new_papers"]
    assert embeddings["coverage"] == 0.0
    # 이 실행은 아무것도 쓰지 않았습니다.
    assert report["loaded"] is False
    assert report["stopped_at"] == "dry_run"


def test_the_window_is_a_week_because_the_watermark_is_not_carried(capacity):
    """워터마크가 Git에 없어 매 실행이 `--days`만큼 되돌아봅니다(11절의 한계 그대로)."""
    from datetime import date

    report = json.loads(REPORT.read_text(encoding="utf-8"))
    window = report["window"]
    span = date.fromisoformat(window["until"]) - date.fromisoformat(window["from"])
    assert span.days == 7, window
