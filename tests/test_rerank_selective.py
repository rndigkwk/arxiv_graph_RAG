"""재정렬기의 선택적 사용 측정을 지킵니다(ADR-061).

이 파일이 막는 것은 하나입니다 — **진단 조각의 숫자를 결론으로 읽는 것.** 186개 규칙 중
최선이 진단에서 +0.0016이었고 최종에서 −0.0103이 됐습니다. 부호가 바뀌었다는 사실이 이
측정의 값이고, 그것을 기록에서 지우면 다음 사람이 "+0.0016 개선"으로 읽습니다.
"""

import json
from pathlib import Path

import pytest

from evals import rerank_selective as rs

ADR = Path(
    "docs/adr/ADR-061-whether-the-reranker-is-worth-keeping-where-cosine-is-weak.md"
)
REPORT = Path("reports/rerank_selective.json")


@pytest.fixture(scope="module")
def report():
    if not REPORT.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {REPORT}")
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ── 규칙의 모양을 제한한 것이 설계입니다 ────────────────────────────────

def test_the_features_were_named_before_the_search():
    """고르고 나서 적으면 그건 고른 것이 아닙니다. ADR과 코드가 같아야 합니다."""
    text = ADR.read_text(encoding="utf-8")
    assert len(rs.FEATURES) == 6, rs.FEATURES
    for feature in rs.FEATURES:
        assert f"`{feature}`" in text, feature


def test_a_trained_selector_is_out_of_scope_and_the_adr_says_why():
    """학습된 선택기는 "코사인을 이겨야 하는 또 하나의 모델"입니다."""
    text = ADR.read_text(encoding="utf-8")
    assert "학습된 선택기는" in text
    source = Path("evals/rerank_selective.py").read_text(encoding="utf-8")
    # 선택기를 학습하는 코드가 들어오면 이 측정은 다른 것을 재게 됩니다.
    assert "fit(" not in source
    assert "LogisticRegression" not in source


def test_the_rule_never_sees_the_gold():
    """바 4번입니다. 특징이 정답을 쓰면 규칙이 추론 시점에 계산되지 않습니다."""
    source = Path("evals/rerank_selective.py").read_text(encoding="utf-8")
    body = source[source.index("def features_of("): source.index("def build(")]
    for forbidden in ("gold", "expected_related_ids"):
        assert forbidden not in body, forbidden


# ── 두 조각으로 나눈 것이 결론을 거릅니다 ───────────────────────────────

def test_the_split_is_inside_the_cohort_the_bar_names(report):
    cohort = report["cohort"]
    assert cohort["diagnostic"] + cohort["final"] == cohort["no_citation_edges"]
    assert cohort["final"] == round(cohort["no_citation_edges"] * rs.FINAL_SHARE)


def test_the_split_is_seeded_so_a_rerun_cannot_change_the_answer():
    def run():
        rows = [{"citation_degree": 0, "seed_id": f"s{i}"} for i in range(30)]
        rs.split(rows)
        return [(row["seed_id"], row["split"]) for row in rows]

    assert run() == run()


def test_seeds_outside_the_cohort_are_not_in_either_piece():
    rows = [{"citation_degree": degree, "seed_id": f"s{degree}"} for degree in (0, 1, 7)]
    rs.split(rows)
    assert rows[0]["split"] in ("diagnostic", "final")
    assert rows[1]["split"] == "outside_cohort"
    assert rows[2]["split"] == "outside_cohort"


# ── 결과: 부호가 뒤집혔다는 사실을 지키는 것이 요지입니다 ───────────────

def test_the_search_was_wide_and_its_breadth_is_recorded(report):
    """셋을 보고 못 찾은 것과 수백을 보고 못 찾은 것은 다른 결과입니다."""
    assert report["rules_searched"] >= 100
    assert str(report["rules_searched"]) in ADR.read_text(encoding="utf-8")


def test_the_best_diagnostic_rule_already_failed_on_its_own_split(report):
    """고른 조각에서조차 0을 제외하지 못했습니다. 그것이 가장 강한 부정입니다."""
    gain = report["diagnostic_gain"]
    assert gain["excludes_zero"] is False
    assert gain["low"] < 0 < gain["high"]


def test_the_sign_flipped_on_the_final_split(report):
    """**이 테스트가 이 파일의 이유입니다.**

    진단에서 양수였던 것이 최종에서 음수가 됩니다. 최종 조각을 떼어 두지 않았다면
    "+0.0016 개선"으로 적었을 것입니다.
    """
    assert report["diagnostic_gain"]["mean"] > 0
    assert report["confirmed"]["recall_at_k"]["mean"] < 0
    assert report["confirmed"]["recall_at_k"]["excludes_zero"] is True


def test_the_verdict_is_rejection_and_the_adr_agrees(report):
    assert report["verdict"] == "바를 넘지 못했습니다"
    assert report["confirmed"]["passes_bar_1_recall_up"] is False
    assert "기각" in ADR.read_text(encoding="utf-8").splitlines()[2]


def test_the_adjacent_cohort_was_not_broken(report):
    """부가 조건입니다 - 간선 1~4개 집단에서 유의하게 내려가면 안 됩니다."""
    adjacent = report["adjacent_cohort"]
    assert isinstance(adjacent, dict)
    for metric in ("recall_at_k", "hit_at_k", "reciprocal_rank"):
        assert adjacent[metric]["excludes_zero"] is False, metric


def test_the_win_loss_shape_is_stable_across_the_pieces(report):
    """분포는 안정적이고 "어느 시드가 이기는지"만 불안정합니다 - 그것이 결론의 근거입니다."""
    outcome = report["per_seed_outcome"]
    for piece in ("diagnostic", "final"):
        counts = outcome[piece]
        total = sum(counts.values())
        assert 0.05 < counts["rerank_wins"] / total < 0.20, piece
        assert 0.15 < counts["rerank_loses"] / total < 0.30, piece


def test_the_measurement_costs_nothing_to_rerun():
    """닫기 위해 재는 일이 싸야, 다음 사람이 3시간 GPU를 다시 치르지 않습니다."""
    source = Path("evals/rerank_selective.py").read_text(encoding="utf-8")
    for forbidden in ("openai", "OpenAI", "CrossEncoder", "torch"):
        assert forbidden not in source, forbidden
