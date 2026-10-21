"""인용 없는 답변을 **거절과 주장으로 가르는지** 봅니다.

[ADR-056](../docs/adr/ADR-056-no-mechanical-check-for-the-uncited-claim.md)의 요지가 여기
있습니다. `evidence_ids == []`를 결함으로 세면 **거짓 양성만** 나옵니다 — 저장소의 5건이
전부 정직한 거절이고, 실제 결함은 0건입니다.

가장 중요한 테스트는 마지막 묶음입니다: **거절을 결함으로 세지 않는가.** 그걸 틀리면
"인용 없으면 보류" 같은 검사를 붙이게 되고, 그 검사는 옳은 거절을 보류로 바꿉니다.
"""

import json
from pathlib import Path

import pytest

from scripts.measure_uncited_claims import REFUSAL_MARKERS, inspect, is_refusal, runs

REPORT = Path("reports/uncited_claims.json")


def row(case, answer, *, evidence=(), failed=False):
    return {
        "id": case, "answer": answer, "evidence_ids": list(evidence), "failed": failed,
    }


def run(rows, *, path="x.json", mode="compare"):
    return {"path": path, "generated_at": "2026-10-17T00:00:00", "mode": mode, "rows": rows}


# ── 거절을 가리는가 ────────────────────────────────────────────────────────

def test_the_archived_refusals_are_recognised():
    """**저장소에 실제로 있는 다섯 문장입니다.** 하나라도 놓치면 결함으로 세게 됩니다."""
    for answer in (
        "No papers could be confirmed to satisfy both the topic and citation conditions.",
        "I cannot confirm any paper that satisfies both conditions: being cited by PCBWorld",
        "I cannot confirm any other paper that satisfies both the author-sharing conditions.",
        "I could not confirm any other papers that satisfy both conditions: sharing an author",
        "I could not confirm any other paper that satisfies both conditions: being about",
    ):
        assert is_refusal(answer) is True, answer


def test_the_claim_that_started_this_is_not_a_refusal():
    """2026-10-21의 그 답변입니다. 네 편이 있다고 말하면서 한 편도 인용하지 않았습니다."""
    claim = (
        "The collection lists four other papers sharing the author Han Lu with "
        "“Fuzzy K-Means Clustering without Cluster Centroids.”"
    )

    assert is_refusal(claim) is False


def test_the_markers_are_case_insensitive():
    assert is_refusal("CANNOT CONFIRM anything") is True
    assert is_refusal("Cannot Confirm anything") is True


def test_a_korean_refusal_is_recognised():
    assert is_refusal("두 조건을 함께 만족하는 논문을 확인할 수 없습니다.") is True


# ── 셈 ──────────────────────────────────────────────────────────────────────

def test_a_cited_answer_is_not_counted_at_all():
    out = inspect(run([row("a-01", "Paper X shares an author.", evidence=["2404.04940"])]))

    assert out["answered"] == 1
    assert out["refusals"] == []
    assert out["uncited_claims"] == []


def test_a_refusal_is_counted_as_a_refusal_not_a_claim():
    """**이 테스트가 ADR-056을 지탱합니다.** 거절을 결함으로 세면 검사가 거짓 양성을 냅니다."""
    out = inspect(run([row("a-01", "I cannot confirm any paper.")]))

    assert len(out["refusals"]) == 1
    assert out["uncited_claims"] == []


def test_an_uncited_claim_is_counted_as_a_claim():
    out = inspect(run([row("a-01", "The collection lists four other papers.")]))

    assert out["uncited_claims"] == [
        {"case": "a-01", "answer": "The collection lists four other papers."}
    ]
    assert out["refusals"] == []


def test_a_stopped_case_is_not_counted():
    """에이전트가 멈춘 것은 답변이 아닙니다 - `answer_failure_rate`가 따로 셉니다."""
    out = inspect(run([row("a-01", "", failed=True), row("a-02", "x", failed=True)]))

    assert out["answered"] == 0
    assert out["refusals"] == out["uncited_claims"] == []


def test_an_empty_answer_is_not_counted():
    out = inspect(run([row("a-01", "   ")]))

    assert out["answered"] == 0


# ── 어느 보고서를 읽는가 ──────────────────────────────────────────────────

def test_only_runs_from_the_harness_are_read(tmp_path):
    """`mode`가 없으면 파생물입니다 - 같은 실행을 두 번 셉니다(장부와 같은 선)."""
    (tmp_path / "derived.json").write_text(json.dumps({
        "results": [{"graph_rag": {"id": "a-01", "evidence_ids": [], "answer": "x"}}],
    }), encoding="utf-8")
    (tmp_path / "real.json").write_text(json.dumps({
        "mode": "compare",
        "metadata": {"prompt_versions": {"answer": "abc"}},
        "results": [{"graph_rag": {"id": "a-01", "evidence_ids": [], "answer": "x"}}],
    }), encoding="utf-8")

    found = runs(tmp_path)

    assert [r["path"] for r in found] == ["real.json"]
    assert found[0]["fingerprinted"] is True


def test_a_report_without_evidence_ids_is_skipped(tmp_path):
    (tmp_path / "other.json").write_text(json.dumps({
        "mode": "compare", "results": [{"graph_rag": {"id": "a-01", "recall_at_k": 1.0}}],
    }), encoding="utf-8")

    assert runs(tmp_path) == []


# ── 실제 측정 결과 ─────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def measured():
    if not REPORT.exists():
        pytest.skip(f"{REPORT}가 없습니다. uv run python -m scripts.measure_uncited_claims")
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_the_archive_holds_no_uncited_claim(measured):
    """**ADR-056의 결론입니다.** 854건에 0건이므로 검사를 붙일 모드가 아닙니다."""
    assert measured["fingerprinted"]["uncited_claims"] == 0
    assert measured["dropped_no_fingerprint"]["uncited_claims"] == 0
    assert measured["claim_cases"] == []


def test_the_check_i_almost_built_would_have_been_all_false_positives(measured):
    """막을 답변 5건이 전부 거절입니다 - 참 양성 0, 거짓 양성 5."""
    would_block = (
        measured["fingerprinted"]["refusals"] + measured["fingerprinted"]["uncited_claims"]
        + measured["dropped_no_fingerprint"]["refusals"]
        + measured["dropped_no_fingerprint"]["uncited_claims"]
    )
    real_defects = (
        measured["fingerprinted"]["uncited_claims"]
        + measured["dropped_no_fingerprint"]["uncited_claims"]
    )

    assert would_block == 5
    assert real_defects == 0


def test_the_current_prompts_always_cite_something(measured):
    """지문 있는 실행에서는 `evidence_ids`가 빈 답변이 아예 없습니다(0/618).

    이것이 양날입니다 - 결함이 드물다는 뜻이면서, **단일 표본 규칙은 드문 사건에
    터진다**는 뜻이기도 합니다(ADR-055가 열어 둔 질문).
    """
    kept = measured["fingerprinted"]

    assert kept["answered"] >= 618
    assert kept["refusals"] == 0
    assert kept["uncited_claims"] == 0


def test_the_report_keeps_the_markers_so_the_heuristic_is_visible(measured):
    """어림으로 갈랐다는 사실이 보고서에 남아야 합니다 - 이 결정의 민감도입니다."""
    assert measured["refusal_markers"] == list(REFUSAL_MARKERS)
    assert "어림" in measured["note"]


def test_the_report_holds_case_ids_not_answer_text(measured):
    """원본 보고서에 이미 있는 본문을 두 벌로 두지 않습니다.

    마커 문자열로 검사하면 안 됩니다 - `refusal_markers`가 그것을 **일부러** 담고 있어서
    제 첫 검사가 거기에 걸렸습니다. 봐야 하는 것은 **답변의 내용어**입니다.
    """
    text = json.dumps(measured, ensure_ascii=False)

    for content in ("PCBWorld", "Han Lu", "hyperbolic neural", "When to Plan", "Fuzzy K-Means"):
        assert content not in text, content
    assert "refusal_cases" in measured
