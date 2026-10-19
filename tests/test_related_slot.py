"""배포된 관련 논문 경로(PPR)가 공동인용 정답에서 하한을 지키는지 검증합니다.

[ADR-029](../docs/adr/ADR-029-ppr-beats-the-adapter-in-the-related-slot.md)가 이 슬롯에서
PPR이 어댑터를 이긴다고 측정했고, 그 뒤 이 슬롯을 지키는 것이 이 테스트입니다.

**이 지표는 결정적입니다.** LLM도 표본추출도 없어서 두 번 돌리면 집계가 완전히 같습니다.
그래서 변동폭 추정이 필요 없고, 대신 **보고서가 늙었는지**를 봐야 합니다 — 코드가 바뀌었는데
보고서를 다시 만들지 않으면 커밋된 숫자가 거짓이 됩니다. 그래서 일부 시드를 **다시 재서**
보고서와 정확히 같은지 맞춰 봅니다.

**CI에서는 건너뜁니다.** 스냅샷 테이블이 저장소에 없고(manifest 4KB만 추적) `derived/`는
gitignore 대상이라 CI가 이 과제를 돌릴 수 없습니다. 그 한계를 ADR-029에 적어 뒀습니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPORT = Path("reports/related_study.json")
BASELINE = Path("baselines/eval_baseline.json")
# 다시 재는 시드 수. 전부 다시 재면 어댑터 학습 없이도 수십 초가 들고, 결정적이므로 일부만
# 맞춰 봐도 코드 변화는 드러납니다.
RECHECK = 30


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"비교 보고서가 없습니다: {REPORT}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def baseline():
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_deployed_path_clears_the_floor(report, baseline):
    """하한은 기각된 대안들 위에 있습니다. 깨지면 그래프를 안 쓰게 된 것입니다."""
    floor = baseline["absolute_floor"]["related_recall_at_k"]
    measured = report["summary"]["ppr"]["recall_at_k"]

    assert measured >= floor, (measured, floor)
    # 하한이 측정값과 같으면 스냅샷이 조금 바뀌어도 터집니다.
    assert floor < measured


def test_the_floor_sits_above_the_rejected_alternatives(report, baseline):
    """0.25를 고른 이유입니다 - 이 선 아래는 "임베딩 수준으로 떨어졌다"는 뜻입니다."""
    floor = baseline["absolute_floor"]["related_recall_at_k"]

    for variant in ("vector", "adapter_cite", "adapter_cocite"):
        if variant in report["summary"]:
            assert report["summary"][variant]["recall_at_k"] < floor, variant


def test_the_baseline_records_the_measured_value(report, baseline):
    assert baseline["metrics"]["related_recall_at_k"] == report["summary"]["ppr"]["recall_at_k"]


def test_the_report_was_built_from_the_current_snapshot(report):
    """스냅샷이 바뀌면 정답과 그래프가 모두 바뀝니다. 보고서가 그 전 것이면 거짓입니다."""
    from pipeline import snapshot as snapshot_module

    assert report["snapshot_version"] == snapshot_module.latest().version


def test_rerunning_a_subset_reproduces_the_report(report):
    """**결정적이라는 주장과 보고서의 신선도를 동시에 봅니다.**

    코드가 바뀌었는데 보고서를 다시 만들지 않으면 여기서 어긋납니다. 어댑터는 학습에 몇 분이
    걸리므로 배포된 경로(PPR)만 다시 잽니다.
    """
    try:
        from evals.related_study import Graph, rank_by_score
        from pagerank import personalized
        from pipeline import snapshot as snapshot_module

        snap = snapshot_module.latest()
        graph = Graph(snap)
    except Exception as exc:  # noqa: BLE001 - 스냅샷 캐시가 없으면 CI입니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")

    from evals.metrics import score_retrieval

    for row in report["results"][:RECHECK]:
        seed = row["seed_id"]
        rank = personalized(graph.matrix, graph.degree, graph.size, [graph.position[seed]])
        ranked = rank_by_score(graph, rank, seed, report["top_k"])
        gold = next(
            case["expected_related_ids"]
            for case in json.loads(Path(report["gold"]).read_text(encoding="utf-8"))["cases"]
            if case["seed_id"] == seed
        )
        again = score_retrieval(gold, ranked, k=report["top_k"])
        assert again == row["ppr"], (row["id"], again, row["ppr"])
