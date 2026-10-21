"""장부가 **같은 실행을 두 번 세지 않고, 묶을 수 없는 것을 묶지 않는지** 봅니다.

[ADR-055](../docs/adr/ADR-055-the-stable-case-rule-is-not-resolved.md)가 이 자리를
만들었습니다. 변동 질문에 답하려면 (사례, 실행) 쌍이 405개 필요한데, 모자란 이유가
**데이터를 버리고 있었기 때문**이었습니다.

장부를 쌓으면서 네 번 틀렸고 넷 다 여기서 고정합니다.

| 틀린 것 | 결과 |
|---|---|
| 지표를 안 담은 보고서를 받음 | `related_study`·`rerank_study`가 전부 `null`인 줄로 들어옴 |
| 파생물(`ragas_study_*`)을 받음 | **같은 실행을 두 번** 세어 표본이 가짜로 늘어남 |
| 묶음 열쇠에 `mode`가 없음 | `agent`와 `compare`가 한 묶음 — 모드 차이를 변동으로 셈 |
| 묶음 열쇠에 날짜가 없음 | 10-16·17·18이 한 묶음 — **코드 차이를 변동으로 셈** |

마지막 둘은 ADR-055가 이미 한 번 걸린 함정의 재발입니다.
"""

import json

import pytest

from evals import ledger


def report(cases, *, mode="compare", generated_at="2026-10-17T13:00:02",
           prompts=None, dataset="cases_graph.json", top_k=5, model="gpt-6-luna"):
    """`evals.run`이 쓰는 보고서의 모양만 흉내 냅니다."""
    return {
        "generated_at": generated_at,
        "mode": mode,
        "top_k": top_k,
        "dataset": dataset,
        "metadata": {
            "model": model,
            "mode": mode,
            "prompt_versions": {"answer": "abc"} if prompts is None else prompts,
        },
        "results": [
            {"id": case_id, "graph_rag": {"id": case_id, **metrics}}
            for case_id, metrics in cases.items()
        ],
        "summary": {"graph_rag": {}},
    }


# ── 무엇을 받고 무엇을 거부하는가 ──────────────────────────────────────────

def test_a_run_becomes_one_line():
    entry = ledger.entry_from_report(
        report({"a-01": {"answer_evidence_recall": 1.0}}), primary="answer_evidence_recall"
    )

    assert entry["cases"] == {"a-01": 1.0}
    assert entry["mode"] == "compare"
    assert entry["prompt_fingerprint"] != ledger.NO_FINGERPRINT


def test_a_report_without_the_metric_is_refused():
    """`related_study`·`rerank_study`도 `results`를 들고 있지만 이 지표가 없습니다."""
    entry = ledger.entry_from_report(
        report({"a-01": {"recall_at_k": 1.0}}), primary="answer_evidence_recall"
    )

    assert entry is None


def test_a_derived_report_is_refused():
    """**`mode`가 없으면 `evals.run`이 만든 실행이 아닙니다.**

    `ragas_study_*`는 기존 실행을 다시 채점한 파생물인데 지표를 그대로 들고 옵니다.
    받으면 같은 실행을 두 번 세고 표본이 가짜로 늘어납니다.
    """
    derived = report({"a-01": {"answer_evidence_recall": 1.0}})
    derived["mode"] = None
    derived["metadata"]["mode"] = None

    assert ledger.entry_from_report(derived, primary="answer_evidence_recall") is None


def test_a_stopped_case_is_null_not_zero():
    """0.0으로 적으면 실패가 품질 하락으로 섞입니다 - 규칙이 이미 건너뛰는 채널입니다."""
    broken = report({"a-01": {"answer_evidence_recall": 0.0}})
    broken["results"][0]["graph_rag"]["failed"] = True
    broken["results"][0]["graph_rag"]["answer_evidence_recall"] = 1.0

    entry = ledger.entry_from_report(broken, primary="answer_evidence_recall")

    assert entry is None  # 모든 사례가 null이면 그 지표의 관측이 아닙니다


def test_a_mixed_run_keeps_the_good_cases_and_nulls_the_stopped_one():
    mixed = report({
        "a-01": {"answer_evidence_recall": 1.0},
        "a-02": {"answer_evidence_recall": 1.0},
    })
    mixed["results"][1]["graph_rag"]["failed"] = True

    entry = ledger.entry_from_report(mixed, primary="answer_evidence_recall")

    assert entry["cases"] == {"a-01": 1.0, "a-02": None}


# ── 두 번 세지 않는가 ──────────────────────────────────────────────────────

def test_the_same_run_is_not_counted_twice(tmp_path):
    """다시 채점한 보고서는 생성 시각이 같습니다."""
    path = tmp_path / "ledger.jsonl"
    entry = ledger.entry_from_report(
        report({"a-01": {"answer_evidence_recall": 1.0}}), primary="answer_evidence_recall"
    )

    assert ledger.append(entry, path) is True
    assert ledger.append(entry, path) is False
    assert len(ledger.read(path)) == 1


def test_a_different_run_is_appended(tmp_path):
    path = tmp_path / "ledger.jsonl"
    for stamp in ("2026-10-17T13:00:02", "2026-10-17T13:34:25"):
        ledger.append(
            ledger.entry_from_report(
                report({"a-01": {"answer_evidence_recall": 1.0}}, generated_at=stamp),
                primary="answer_evidence_recall",
            ),
            path,
        )

    assert len(ledger.read(path)) == 2


def test_a_broken_line_does_not_sink_the_ledger(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"a": 1}\n이건 json이 아닙니다\n{"b": 2}\n', encoding="utf-8")

    assert ledger.read(path) == [{"a": 1}, {"b": 2}]


def test_recording_never_breaks_the_evaluation(tmp_path):
    """보고서는 이미 쓰였습니다. 장부를 못 쓰는 것이 평가의 실패는 아닙니다."""
    blocked = tmp_path / "taken"
    blocked.mkdir()

    assert ledger.record(
        report({"a-01": {"answer_evidence_recall": 1.0}}),
        primary="answer_evidence_recall",
        path=blocked,
    ) is False


# ── 묶음 ────────────────────────────────────────────────────────────────────

def entry(cases, **kwargs):
    return ledger.entry_from_report(report(cases, **kwargs), primary="answer_evidence_recall")


def test_two_modes_never_share_a_group():
    """`agent`와 `compare`는 같은 것을 재지 않습니다 - 섞으면 모드 차이를 변동으로 셉니다."""
    rows = [
        entry({"a-01": {"answer_evidence_recall": 1.0}}, mode="compare"),
        entry({"a-01": {"answer_evidence_recall": 1.0}}, mode="compare",
              generated_at="2026-10-17T14:00:00"),
        entry({"a-01": {"answer_evidence_recall": 0.0}}, mode="agent"),
        entry({"a-01": {"answer_evidence_recall": 1.0}}, mode="agent",
              generated_at="2026-10-17T15:00:00"),
    ]

    shaped = ledger.groups(rows)

    assert len(shaped) == 2
    assert {g["mode"] for g in shaped.values()} == {"compare", "agent"}


def test_two_dates_never_share_a_group():
    """**ADR-055가 걸린 함정입니다.** 지문이 같아도 코드가 다를 수 있습니다(ADR-024 라우팅)."""
    rows = [
        entry({"a-01": {"answer_evidence_recall": 1.0}}, generated_at="2026-10-17T01:00:00"),
        entry({"a-01": {"answer_evidence_recall": 1.0}}, generated_at="2026-10-17T02:00:00"),
        entry({"a-01": {"answer_evidence_recall": 0.0}}, generated_at="2026-10-18T01:00:00"),
        entry({"a-01": {"answer_evidence_recall": 1.0}}, generated_at="2026-10-18T02:00:00"),
    ]

    shaped = ledger.groups(rows)

    assert len(shaped) == 2
    assert sorted(g["dates"][0] for g in shaped.values()) == ["2026-10-17", "2026-10-18"]


def test_a_run_without_a_fingerprint_is_dropped():
    """같은 코드 상태인지 말할 수 없으면 쓸 수 없습니다(ADR-018 이전 보고서)."""
    rows = [
        entry({"a-01": {"answer_evidence_recall": 1.0}}, prompts={}),
        entry({"a-01": {"answer_evidence_recall": 1.0}}, prompts={},
              generated_at="2026-10-17T14:00:00"),
    ]

    assert all(row["prompt_fingerprint"] == ledger.NO_FINGERPRINT for row in rows)
    assert ledger.groups(rows) == {}


def test_a_single_run_group_is_dropped():
    """**한 번만 돌린 묶음에서는 하락이 보일 수 없습니다.**

    모집단이 "적어도 한 번 만점"이므로 내려간 사례는 `max < 1.0`이 되어 애초에 세지
    않습니다. 그런 묶음은 분모만 늘려 `q`를 낮게 치우치게 합니다.
    """
    rows = [entry({"a-01": {"answer_evidence_recall": 1.0}})]

    assert ledger.groups(rows, min_runs=2) == {}
    assert ledger.groups(rows, min_runs=1) != {}


def test_a_drop_is_counted_and_a_never_perfect_case_is_not_eligible():
    rows = [
        entry({"a-01": {"answer_evidence_recall": 1.0},
               "a-02": {"answer_evidence_recall": 0.5}}),
        entry({"a-01": {"answer_evidence_recall": 0.0},
               "a-02": {"answer_evidence_recall": 0.5}},
              generated_at="2026-10-17T14:00:00"),
    ]

    group = next(iter(ledger.groups(rows).values()))

    # a-01만 모집단입니다(한 번 만점). a-02는 한 번도 만점이 아니라 빠집니다.
    assert group["eligible_pairs"] == 2
    assert group["drops"] == 1


def test_a_stopped_pair_is_not_counted():
    """규칙이 그 채널을 이미 건너뜁니다. 여기서 세면 같은 변동에 두 번 벌점입니다."""
    first = entry({"a-01": {"answer_evidence_recall": 1.0}})
    second = entry({"a-01": {"answer_evidence_recall": 1.0}},
                   generated_at="2026-10-17T14:00:00")
    second["cases"]["a-01"] = None

    group = next(iter(ledger.groups([first, second]).values()))

    assert group["eligible_pairs"] == 1
    assert group["drops"] == 0


# ── 담지 않는 것 ───────────────────────────────────────────────────────────

def test_the_ledger_holds_no_text():
    """질문도 답변도 Cypher도 넣지 않습니다(ADR-023 결정 3, ADR-053과 같은 선)."""
    rich = report({"a-01": {"answer_evidence_recall": 1.0}})
    rich["results"][0]["graph_rag"].update({
        "answer": "비밀 답변입니다",
        "question": "비밀 질문입니다",
        "cyphers": ["MATCH (p:Secret) RETURN p"],
        "contexts": ["비밀 문맥"],
    })

    entry_one = ledger.entry_from_report(rich, primary="answer_evidence_recall")
    text = json.dumps(entry_one, ensure_ascii=False)

    for secret in ("비밀", "Secret", "MATCH"):
        assert secret not in text, secret


def test_the_committed_ledger_is_readable_and_grouped():
    """저장소에 쌓인 장부가 실제로 묶입니다. 비어 있으면 건너뜁니다."""
    entries = ledger.read()
    if not entries:
        pytest.skip("장부가 비어 있습니다. uv run python -m evals.ledger --backfill")

    shaped = ledger.groups(entries)
    counted = [g for g in shaped.values() if g["mode"] == "compare"]

    assert counted, shaped
    assert all(g["runs"] >= 2 for g in shaped.values())
    assert sum(g["eligible_pairs"] for g in counted) > 0
