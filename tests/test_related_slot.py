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
        from pipeline import snapshot as snapshot_module
        from src.pagerank import personalized

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


FUTURE_REPORT = Path("reports/related_future_study.json")


@pytest.fixture(scope="module")
def future_report():
    if not FUTURE_REPORT.exists():
        pytest.skip(f"미래 라벨 보고서가 없습니다: {FUTURE_REPORT}")
    return json.loads(FUTURE_REPORT.read_text(encoding="utf-8"))


def test_the_future_report_was_measured_on_a_cut_graph(future_report):
    """자르지 않은 그래프로 낸 숫자는 **거짓**입니다(ADR-035).

    보고서가 `feature_cut`과 제외한 간선 수를 적어야 하고, 둘 다 있어야 그 숫자를 믿을 수
    있습니다. 간선을 하나도 제외하지 않았다면 자르지 않은 것입니다.
    """
    assert future_report["feature_cut"]
    assert future_report["dropped_edges_outside_feature_window"] > 0


def test_the_aggregate_hides_two_opposite_regimes(future_report):
    """집계를 먼저 읽으면 틀립니다 - 두 체제의 가중합이라 어느 쪽도 설명하지 않습니다.

    **순수 그래프 변형(`ppr`)으로 고정합니다.** 전에는 `cocited`도 간선 0개 집단에서
    0.0000이었는데, 그 구멍을 임베딩 보충으로 메웠습니다(ADR-040). 집계가 두 체제를
    섞는다는 성질 자체는 그대로이고, 그것을 보이는 변형만 바뀝니다.
    """
    slices = future_report["by_degree_slice"]
    empty = slices["no_citation_edges"]
    thin = slices["degree_1_to_4"]

    assert empty["ppr"]["recall_at_k"] == 0.0
    assert empty["ppr"]["hit_at_k"] == 0.0
    assert thin["ppr"]["recall_at_k"] > 0.9

    aggregate = future_report["summary"]["ppr"]["recall_at_k"]
    assert empty["ppr"]["recall_at_k"] < aggregate < thin["ppr"]["recall_at_k"]


def test_only_the_embedding_scores_where_the_graph_has_no_edges(future_report):
    """이 프로젝트에서 **임베딩 경로가 그래프 경로가 못 하는 일을 하는 첫 사례**입니다.

    간선이 없는 시드는 순수 그래프 방법으로 점수를 낼 수 없습니다(ADR-035·ADR-036).
    그 자리에서 벡터 최근접은 미래 공동인용을 상위 5 안에 **절반 가까이** 맞히고,
    배포 경로는 이제 그것을 **보충으로 끌어다 씁니다**(ADR-040).
    """
    empty = future_report["by_degree_slice"]["no_citation_edges"]

    assert empty["share"] > 0.5  # 이 과제의 다수입니다
    assert empty["vector"]["hit_at_k"] > 0.4
    # 순수 그래프는 여전히 0입니다. 그것이 "임베딩만 할 수 있다"의 내용입니다.
    assert empty["ppr"]["recall_at_k"] == 0.0
    assert empty["vector"]["recall_at_k"] > empty["ppr"]["recall_at_k"]


def test_the_learned_adapter_still_does_not_beat_the_plain_embedding(future_report):
    """다섯 번째 측정된 기각입니다 - 이번에는 **라벨이 미래**이고 그래프가 0점인 자리입니다.

    어댑터에 모든 유리함을 준 측정입니다(동어반복 질의가 없고, 현직이 0점이라 여지가 최대).
    그래도 움직이지 않습니다 - 차이가 네 자리에서 보이지 않습니다. 구간은 ADR-036에
    적혀 있고, 여기서는 점 추정이 갈리지 않는다는 것만 고정합니다.
    """
    empty = future_report["by_degree_slice"]["no_citation_edges"]
    plain = empty["vector"]

    for variant in ("adapter_cite", "adapter_cocite"):
        for metric in ("recall_at_k", "hit_at_k", "reciprocal_rank"):
            gap = abs(empty[variant][metric] - plain[metric])
            assert gap < 0.01, (variant, metric, gap)


def test_the_gbm_is_best_at_neither_subpopulation(future_report):
    """ADR-037의 핵심은 **어느 집단에서도 최선이 아니다**였고 그것은 그대로입니다.

    당시에는 그 모델이 **집계에서 1위**였고, 배포 경로가 다수 집단에서 0.0000이었기
    때문입니다. 그 구멍을 메우자(ADR-040) 집계 우위도 사라졌습니다 - ADR-037이 "두
    집단의 가중합이 만든 승리"라고 읽은 것이 맞았다는 확인입니다.
    """
    if "gbm" not in future_report["summary"]:
        pytest.skip("gbm 변형이 없는 보고서입니다")
    slices = future_report["by_degree_slice"]
    empty, thin = slices["no_citation_edges"], slices["degree_1_to_4"]

    # 다수 집단에서는 자기가 품은 임베딩보다 못합니다.
    assert empty["share"] > 0.5
    assert empty["gbm"]["recall_at_k"] < empty["vector"]["recall_at_k"]
    assert empty["gbm"]["hit_at_k"] < empty["vector"]["hit_at_k"]
    # 두 번째 집단에서는 질의보다 못합니다.
    assert thin["gbm"]["recall_at_k"] < thin["cocited"]["recall_at_k"]


def test_the_gbm_failure_is_ranking_not_retrieval(future_report):
    """후보 상한을 적지 않으면 "못 찾았다"와 "찾아 놓고 못 세웠다"를 구별할 수 없습니다.

    간선 1~4개 집단의 후보에는 정답이 0.98 들어 있는데 GBM은 0.53을 돌려줍니다 - 검색이
    아니라 순위의 실패입니다(ADR-037).
    """
    if "gbm" not in future_report["summary"]:
        pytest.skip("gbm 변형이 없는 보고서입니다")
    thin = future_report["by_degree_slice"]["degree_1_to_4"]
    ceiling = thin.get("gbm_pool_recall_ceiling")

    assert ceiling is not None, "후보 상한이 없으면 실패의 종류를 말할 수 없습니다"
    assert ceiling > 0.9
    assert thin["gbm"]["recall_at_k"] < ceiling * 0.7


def test_a_swapped_embedding_matrix_must_match_the_corpus():
    """행 수가 어긋나면 **조용히 다른 논문의 벡터**로 점수가 나옵니다(ADR-039)."""
    import numpy as np

    from evals.related_study import Graph

    try:
        from pipeline import snapshot as snapshot_module

        snap = snapshot_module.latest()
        graph = Graph(snap)
    except Exception as exc:  # noqa: BLE001 - 스냅샷 캐시가 없으면 CI입니다.
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")

    import tempfile

    with tempfile.TemporaryDirectory() as directory:
        wrong = Path(directory) / "wrong.npy"
        np.save(wrong, np.zeros((graph.size - 1, 768), dtype=np.float32))
        with pytest.raises(ValueError, match="임베딩 행 수가 말뭉치와 다릅니다"):
            Graph(snap, embeddings=wrong)


def test_the_embedding_comparison_refuses_reports_from_different_golds():
    """사례 수가 다른 보고서를 비교하면 모델이 아니라 **다른 과제**를 비교하게 됩니다."""
    from evals.embedding_slice_study import compare

    with pytest.raises(ValueError, match="사례 수가 다릅니다"):
        compare({"cases": 10, "results": []}, {"cases": 11, "results": []})


def test_the_recorded_embedding_comparison_still_separates_the_models():
    """11-5의 "동등"이 분해능의 사실이었다는 측정을 고정합니다(ADR-039).

    같은 `bge-base` 행렬이 인용 과제에서는 0을 포함하고(−0.0109 [−0.0250, +0.0032])
    여기서는 0을 제외합니다. 점 추정이 거의 같으므로 바뀐 것은 **구간**입니다.
    """
    recorded = Path("reports/embedding_slice_study.json")
    if not recorded.exists():
        pytest.skip(f"비교 보고서가 없습니다: {recorded}")
    study = json.loads(recorded.read_text(encoding="utf-8"))
    empty = study["by_degree_slice"]["no_citation_edges"]

    assert empty["seeds"] > 1000
    for metric in ("recall_at_k", "hit_at_k", "reciprocal_rank"):
        assert empty[metric]["excludes_zero"], metric
        # 후보가 기준보다 나쁩니다. 방향이 뒤집히면 기록을 다시 읽어야 합니다.
        assert empty[metric]["candidate"] < empty[metric]["baseline"], metric
    # 인용 과제의 구간 폭(0.0282)보다 좁아야 "분해능이 올랐다"가 성립합니다.
    width = empty["recall_at_k"]["high"] - empty["recall_at_k"]["low"]
    assert width < 0.0282, width


def test_the_deployed_ranker_falls_back_to_embeddings_when_the_graph_is_empty():
    """간선이 없으면 공동인용도 PPR도 0점이라 패널이 통째로 빕니다(ADR-040).

    합성 그래프로 봅니다 - 시드가 인용망에 전혀 닿지 않는데도 상위 k가 채워져야 하고,
    채운 것이 **임베딩 최근접**이어야 합니다.
    """
    import numpy as np

    from evals.related_study import cocited_ranking

    class FakeGraph:
        ids = ["seed", "near", "far"]
        position = {"seed": 0, "near": 1, "far": 2}
        size = 3
        # 시드와 가까운 순서: near > far
        vectors = np.array([[1.0, 0.0], [0.9, 0.44], [0.0, 1.0]], dtype=np.float32)

        def cocited(self, _seed):
            import collections

            return collections.Counter()  # 공동인용 없음

    graph = FakeGraph()
    score = np.zeros(3, dtype=np.float32)  # PPR도 전부 0 - 간선이 없는 상태

    ranked = cocited_ranking(graph, "seed", score, 2)

    assert ranked, "그래프가 비어도 패널은 비면 안 됩니다"
    assert "seed" not in ranked
    assert ranked[0] == "near", ranked  # 코사인이 가까운 쪽이 먼저


def test_filling_the_empty_panel_does_not_move_the_present_gold(future_report):
    """현재 정답에서는 채움이 **발동하지 않아야** 합니다 - 후보가 최소 7편입니다.

    발동하면 `related_slot`의 측정값(0.8892)과 하한이 함께 흔들립니다. 그 집합의 적격
    조건이 공동인용이 넉넉한 시드만 고르므로 구조적으로 발동할 수 없고, 그것을 고정합니다
    (ADR-040).
    """
    present = Path("reports/related_study.json")
    if not present.exists():
        pytest.skip(f"현재 정답 보고서가 없습니다: {present}")
    report = json.loads(present.read_text(encoding="utf-8"))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    assert report["summary"]["cocited"]["recall_at_k"] == baseline["related_slot"]["measured"]
    # 그 집합의 모든 시드가 top_k 이상의 공동인용 후보를 가집니다.
    assert min(row["cocited_candidates"] for row in report["results"]) >= report["top_k"]


def test_the_fill_closes_the_hole_the_measurement_found(future_report):
    """간선 0개 집단에서 배포 경로가 0.0000이던 것이 채워져야 합니다(ADR-040)."""
    if "gbm" not in future_report["summary"]:
        pytest.skip("gbm 변형이 없는 보고서입니다")
    empty = future_report["by_degree_slice"]["no_citation_edges"]

    assert empty["cocited"]["recall_at_k"] > 0.0
    assert empty["cocited"]["hit_at_k"] > 0.4
    # 그 자리에서 채우는 것은 임베딩이므로 vector와 같아야 합니다.
    assert empty["cocited"]["recall_at_k"] == empty["vector"]["recall_at_k"]
    # 그러면 GBM의 집계 우위가 사라집니다 - ADR-037이 "가중합이 만든 승리"라고 한 그대로.
    assert future_report["summary"]["gbm"]["recall_at_k"] < (
        future_report["summary"]["cocited"]["recall_at_k"]
    )
