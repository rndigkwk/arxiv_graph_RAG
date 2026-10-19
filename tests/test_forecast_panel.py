"""공동인용 예측 패널이 **학습한 모델과 같은 것을 먹이는지** 검증합니다.

이 패널은 이 저장소에서 학습 모델이 쓰이는 **유일한 자리**입니다
([ADR-038](../docs/adr/ADR-038-the-forecast-panel.md)). 그래서 고정할 것이 셋입니다.

1. **피처 순서가 학습과 같습니다.** 어긋나면 모델이 조용히 다른 열을 읽고, 점수는 그럴듯하게
   나옵니다 — 가장 잡기 어려운 종류의 고장입니다.
2. **지문이 맞지 않으면 거부합니다.** 어느 모델을 쓰는지 말할 수 없는 상태로 숫자를 보여
   주는 것이 비어 있는 것보다 나쁩니다.
3. **공동인용 이웃의 정의가 평가와 같습니다.** 서빙에서 다르게 세면 측정한 성능이 아닙니다.

가중치가 없는 환경(CI)에서는 그것이 필요한 검사만 건너뜁니다.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

import forecast


def test_the_feature_order_matches_what_the_model_was_trained_on():
    """**이 테스트가 이 파일에서 가장 중요합니다.**

    서빙 쪽 열 순서가 학습 쪽과 어긋나면 예외가 나지 않습니다. 모델은 12개 숫자를 받아
    그럴듯한 점수를 돌려주고, 그 점수는 아무 뜻이 없습니다.
    """
    from models.link_prediction.dataset import FEATURE_NAMES as TRAINED

    assert forecast.FEATURE_NAMES == TRAINED


def test_cocitation_neighbours_are_papers_cited_alongside_this_one():
    """정의가 평가의 `Graph.cocited`와 같아야 합니다 - 인용자를 거쳐 두 걸음."""
    ids = ["a", "b", "c", "d"]
    graph = {
        "ids": ids,
        "index": {name: position for position, name in enumerate(ids)},
        # d가 a와 b를 함께 인용하고, c가 a를 인용합니다.
        "rows": np.array([3, 3, 2], dtype=np.int32),
        "cols": np.array([0, 1, 0], dtype=np.int32),
    }

    assert forecast.cocitation_neighbours(graph, "a") == {"b"}
    assert forecast.cocitation_neighbours(graph, "b") == {"a"}
    # 아무도 인용하지 않은 논문은 공동인용이 없습니다. 패널이 비는 자리입니다(ADR-035).
    assert forecast.cocitation_neighbours(graph, "d") == set()
    assert forecast.cocitation_neighbours(graph, "없는-id") == set()


def test_month_gap_reads_the_arxiv_id_and_tolerates_old_style():
    assert forecast.month_of("2501.00001") == 2025 * 12
    assert forecast.month_of("2512.00001") == 2025 * 12 + 11
    # 옛 형식과 잘못된 월은 None입니다. 0으로 접으면 1월과 구별되지 않습니다.
    assert forecast.month_of("math/0601001") is None
    assert forecast.month_of("2513.00001") is None


def test_weights_with_a_different_fingerprint_are_refused(tmp_path):
    """파일이 바뀌었는데 설명이 그대로면 **어느 모델인지 말할 수 없습니다.**"""
    (tmp_path / "manifest.json").write_text(
        json.dumps({"digest": "0" * 16}), encoding="utf-8"
    )
    (tmp_path / f"{forecast.RUNG}.joblib").write_bytes(b"not the exported file")

    with pytest.raises(forecast.WeightsUnavailable, match="지문이 다릅니다"):
        forecast.load_model(tmp_path)


def test_a_missing_export_is_a_clear_refusal_not_a_crash(tmp_path):
    with pytest.raises(forecast.WeightsUnavailable, match="설명이 없습니다"):
        forecast.load_card(tmp_path)

    (tmp_path / "manifest.json").write_text(json.dumps({"digest": None}), encoding="utf-8")
    with pytest.raises(forecast.WeightsUnavailable, match="가중치 파일이 없습니다"):
        forecast.load_model(tmp_path)


def test_the_exported_card_records_what_is_needed_to_trust_the_file():
    """지문·스냅샷·sklearn 버전이 없으면 되짚을 수 없습니다(ADR-032의 "잇는 것은 지문")."""
    try:
        card = forecast.load_card()
    except forecast.WeightsUnavailable:
        pytest.skip("내보낸 가중치가 없습니다(CI)")

    assert card["digest"]
    assert card["snapshot_version"]
    assert card["sklearn_version"]
    assert card["rung"] == forecast.RUNG
    # 어떤 측정으로 통과한 모델인지도 함께 있어야 합니다.
    assert (card.get("metrics") or {}).get("ranking_mrr")


def test_ranking_drops_the_seed_and_explains_each_score():
    """숫자만 돌려주면 되짚을 수 없습니다. 무엇이 점수를 밀었는지 함께 적습니다."""
    try:
        model, _card = forecast.load_model()
    except forecast.WeightsUnavailable:
        pytest.skip("내보낸 가중치가 없습니다(CI)")

    ids = ["2501.00001", "2502.00002", "2503.00003"]
    graph = {
        "ids": ids,
        "index": {name: position for position, name in enumerate(ids)},
        "rows": np.array([2, 2], dtype=np.int32),
        "cols": np.array([0, 1], dtype=np.int32),
        "primary": ["cs.AI", "cs.AI", "cs.LG"],
        "categories": [{"cs.AI"}, {"cs.AI"}, {"cs.LG"}],
    }
    vectors = {name: np.ones(4, dtype=np.float32) / 2 for name in ids}

    ranked = forecast.rank(
        "2501.00001",
        ["2501.00001", "2502.00002", "2503.00003"],  # 시드가 섞여 들어와도
        graph=graph, vectors=vectors, indegree={}, model=model, top_k=5,
    )

    # 순서는 점수가 정하므로 고정하지 않습니다. 고정할 것은 시드가 빠지는 것과,
    # 각 줄이 **왜 그 점수인지**를 함께 들고 오는 것입니다.
    assert ranked
    assert "2501.00001" not in [hit["arxiv_id"] for hit in ranked]
    for hit in ranked:
        assert set(hit) >= {
            "arxiv_id", "score", "shared_cocitations",
            "embedding_cosine", "same_primary_category",
        }


def test_the_panel_is_not_an_agent_tool():
    """답변 경로에 들어가면 게이트가 보는 지표가 움직입니다. 이 기능은 표시 패널입니다.

    도구로 올리려면 기준선을 다시 재야 하고, 그 판단을 조용히 내리지 않습니다(ADR-038).
    """
    import re
    from pathlib import Path

    source = Path("chatbot.py").read_text(encoding="utf-8")
    decorated = re.findall(r"@tool\s*\ndef (\w+)", source)

    assert "forecast_cocitation" not in decorated
    assert decorated, "도구를 하나도 찾지 못했다면 이 검사가 뜻이 없습니다"
