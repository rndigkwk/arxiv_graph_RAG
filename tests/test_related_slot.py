"""배포된 관련 논문 경로가 공동인용 정답에서 하한을 지키는지 검증합니다.

[ADR-029](../docs/adr/ADR-029-ppr-beats-the-adapter-in-the-related-slot.md)가 이 슬롯에서
PPR이 어댑터를 이긴다고 측정했고, ADR-030이 다시 **공동인용 수 우선**으로 바꿨습니다
([ADR-030](../docs/adr/ADR-030-related-papers-needs-a-query-not-a-model.md)).
이 테스트는 "배포된 것"을 보는데, 전 판은 이름을
`ppr`로 **박아 두어** 배포가 바뀐 뒤에도 옛 경로를 보고 있었습니다. 지금은 보고서가 적어 둔
`incumbent`를 읽습니다 - 배포가 또 바뀌면 보고서가 그것을 말하고, 테스트는 따라갑니다
([ADR-034](../docs/adr/ADR-034-measure-every-eligible-seed.md)).

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


def deployed(report) -> str:
    """Which variant the report says is deployed. Never hard-coded here."""
    incumbent = report.get("incumbent")
    assert incumbent in report["summary"], incumbent
    return incumbent


def test_the_deployed_path_clears_the_floor(report, baseline):
    """하한은 배포되지 않은 모든 변형 위에 있습니다. 깨지면 조용히 되돌아간 것입니다."""
    floor = baseline["related_slot"]["floor"]
    measured = report["summary"][deployed(report)]["recall_at_k"]

    assert measured >= floor, (measured, floor)
    # 하한이 측정값과 같으면 스냅샷이 조금 바뀌어도 터집니다.
    assert floor < measured


def test_the_floor_sits_above_every_variant_that_is_not_deployed(report, baseline):
    """하한을 고른 이유입니다 - 이 선 아래로 내려가면 **배포 아닌 무엇**이 된 것입니다.

    전 판은 기각된 셋(`vector`·어댑터 둘)만 봤습니다. 지금은 `ppr`도 봅니다 - PPR은 기각된
    것이 아니라 **보충 경로**인데, 공동인용 정렬이 깨져 PPR만 남으면 그것도 회귀입니다.
    """
    floor = baseline["related_slot"]["floor"]
    incumbent = deployed(report)

    others = [name for name in report["summary"] if name != incumbent]
    assert others, "비교할 변형이 없으면 하한이 무엇 위에 있는지 말할 수 없습니다"
    for variant in others:
        assert report["summary"][variant]["recall_at_k"] < floor, variant


def test_the_baseline_records_the_measured_value(report, baseline):
    slot = baseline["related_slot"]

    assert slot["measured"] == report["summary"][deployed(report)]["recall_at_k"]
    # 어느 변형을 적은 값인지도 남아야 합니다. 배포가 바뀌면 이 둘이 함께 바뀝니다.
    assert slot["variant"] == deployed(report)


def test_the_report_covers_every_eligible_seed(report):
    """표본이 아니라 **전수**입니다. 300 시드에서 "0건"이던 성질이 3,740에서 깨졌습니다.

    LLM을 부르지 않는 과제라 전수가 공짜이고(PPR 11분), 표본을 쓰면 구간에 표본추출 변동이
    섞입니다(ADR-034).
    """
    gold = json.loads(Path(report["gold"]).read_text(encoding="utf-8"))

    assert report["cases"] == len(gold["cases"]) == gold["eligible_seeds"]


def test_this_floor_stays_out_of_the_answer_path_gate():
    """**2026-10-19에 여기 넣어 봤다가 모든 게이트 실행이 깨졌습니다.**

    `absolute_floor`의 이름은 평가 하네스가 **매 실행 내놓는** 지표여야 합니다. 관련 논문
    점수는 다른 하네스(`evals.related_study`)가 따로 내므로 평가 보고서에 없고, 그러면
    게이트가 "이번 결과에 없습니다"로 실패합니다. CI는 게이트 테스트를 건너뛰어서 초록이었고,
    다음 주간 크론에서야 터졌을 것입니다.
    """
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert "related_recall_at_k" not in baseline["absolute_floor"]
    assert "related_recall_at_k" not in (baseline["allowed_drop"] or {})


def test_the_report_was_built_from_the_current_snapshot(report):
    """스냅샷이 바뀌면 정답과 그래프가 모두 바뀝니다. 보고서가 그 전 것이면 거짓입니다."""
    from pipeline import snapshot as snapshot_module

    assert report["snapshot_version"] == snapshot_module.latest().version


def test_rerunning_a_subset_reproduces_the_report(report):
    """**결정적이라는 주장과 보고서의 신선도를 동시에 봅니다.**

    코드가 바뀌었는데 보고서를 다시 만들지 않으면 여기서 어긋납니다. 어댑터는 학습에 몇 분이
    걸리므로 **그래프에서 나오는 둘**(`ppr`과 배포된 `cocited`)만 다시 잽니다 - `cocited`가
    동점 끊기와 보충에 PPR을 쓰므로 둘의 비용이 같습니다.
    """
    try:
        from evals.related_study import Graph, cocited_ranking, rank_by_score
        from pagerank import personalized
        from pipeline import snapshot as snapshot_module

        snap = snapshot_module.latest()
        graph = Graph(snap)
    except Exception as exc:  # noqa: BLE001 - 스냅샷 캐시가 없으면 CI입니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")

    from evals.metrics import score_retrieval

    gold_of = {
        case["seed_id"]: case["expected_related_ids"]
        for case in json.loads(Path(report["gold"]).read_text(encoding="utf-8"))["cases"]
    }
    for row in report["results"][:RECHECK]:
        seed = row["seed_id"]
        rank = personalized(graph.matrix, graph.degree, graph.size, [graph.position[seed]])
        gold = gold_of[seed]
        for variant, ranked in (
            ("ppr", rank_by_score(graph, rank, seed, report["top_k"])),
            ("cocited", cocited_ranking(graph, seed, rank, report["top_k"])),
        ):
            if variant not in row:
                continue
            repeated = score_retrieval(gold, ranked, k=report["top_k"])
            # **보고서가 적어 둔 지표만** 비교합니다. 지표가 늘어나는 것은 재현 실패가
            # 아닙니다 - 2026-10-19에 집합 지표 둘이 더해지면서(ADR-033) 이 비교가
            # 터졌습니다. 재현이 확인할 것은 "그때 적은 값이 지금 다시 나오는가"입니다.
            assert {key: repeated[key] for key in row[variant]} == row[variant], (
                row["id"], variant, repeated
            )
