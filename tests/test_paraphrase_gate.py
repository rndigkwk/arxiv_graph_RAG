"""바꿔 말한 질문 60건을 게이트에 넣은 분할(`paraphrase`, ADR-093)."""

import json
from pathlib import Path

from evals import gate

BASELINE = json.loads(Path("baselines/eval_baseline.json").read_text(encoding="utf-8"))
WORKFLOW = Path(".github/workflows/regression-eval.yml").read_text(encoding="utf-8")


def _paraphrase_ids():
    data = json.loads(Path("evals/cases_graph_paraphrase.json").read_text(encoding="utf-8-sig"))
    return sorted(c["id"] for c in data["cases"])


def _report(values):
    primary = BASELINE["primary_metric"]
    return {
        "cases": {cid: {"failed": value is None, "metrics": {primary: value}}
                  for cid, value in values.items()},
        "scoring_errors": 0,
        "metrics": {},
        "prompt_versions": BASELINE["prompt_versions"],
    }


def test_the_split_covers_every_paraphrase_and_all_must_stay_perfect():
    ids = _paraphrase_ids()
    assert BASELINE["splits"]["paraphrase"] == ids
    assert BASELINE["split_stable_cases"]["paraphrase"] == ids


def test_a_perfect_run_passes_without_comparing_averages():
    report = _report({cid: 1.0 for cid in _paraphrase_ids()})
    assert gate.check(BASELINE, report, split="paraphrase") == []
    assert not gate.compares_aggregates(BASELINE, "paraphrase")


def test_one_broken_paraphrase_is_a_violation():
    values = {cid: 1.0 for cid in _paraphrase_ids()}
    broken = next(iter(values))
    values[broken] = 0.5
    violations = gate.check(BASELINE, _report(values), split="paraphrase")
    assert [v.rule for v in violations] == ["stable_case"]
    assert broken in str(violations[0])


def test_an_agent_failure_is_information_not_a_violation():
    values = {cid: 1.0 for cid in _paraphrase_ids()}
    failed = next(iter(values))
    values[failed] = None
    report = _report(values)
    assert gate.check(BASELINE, report, split="paraphrase") == []
    assert gate.transient_failures(BASELINE, report, "paraphrase") == [failed]


def test_the_full_split_does_not_look_at_paraphrases():
    assert set(gate.stable_cases(BASELINE, "full")) == set(BASELINE["stable_cases"])


def test_a_baseline_rewrite_keeps_the_paraphrase_split():
    carried = gate.carried_over(BASELINE)
    assert carried["split_stable_cases"] == BASELINE["split_stable_cases"]
    assert carried["splits"]["paraphrase"] == BASELINE["splits"]["paraphrase"]


def test_the_workflow_runs_it_weekly_on_its_own_file():
    assert 'cron: "0 23 * * 0"' in WORKFLOW
    assert '"${{ github.event.schedule }}" = "0 23 * * 0"' in WORKFLOW
    assert "cases=evals/cases_graph_paraphrase.json" in WORKFLOW
    assert "options: [smoke, full, paraphrase]" in WORKFLOW
    # 두 크론이 서로를 취소하지 않습니다.
    assert "github.event.schedule || inputs.split" in WORKFLOW
