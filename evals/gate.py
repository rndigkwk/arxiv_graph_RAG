"""평가 결과를 기준선과 대조해 회귀를 판정합니다.

    # 평가 보고서를 게이트가 읽는 모양으로 정규화합니다
    uv run python -m evals.gate --report reports/rag_eval_graph_compare_final.json

    # 기준선을 새로 씁니다 (여러 번 돌린 보고서를 함께 주면 변동폭이 측정값이 됩니다)
    uv run python -m evals.gate --report run1.json run2.json run3.json --write-baseline

판정은 `tests/test_eval_gate.py`가 합니다. 이 모듈은 규칙을 함수로 들고 있고, 테스트는 그
함수가 돌려준 위반 목록을 규칙별로 봅니다. 규칙을 테스트 안에 적으면 **게이트 자체를 테스트할
수 없습니다** — 통과만 시키는 게이트는 게이트가 아니므로 `tests/fixtures/eval_gate/`의 가짜
기준선·결과 쌍으로 규칙마다 제대로 실패하는지 확인합니다.

위반으로 올라오는 규칙은 일곱 개입니다. 여기에 비교가 성립하는지 보는 `metrics_split`이
**전제로** 붙습니다 — 분할이 다르면 집계 규칙(`floor`·`drop`)을 아예 적용하지 않습니다.

| 규칙 | 내용 |
|---|---|
| `cases` | 기준선과 이번 평가의 케이스 집합이 같은가 |
| `scoring_error` | 채점 함수가 예외로 죽은 건이 없는가 (0이어야 함) |
| `floor` | 지표가 절대 하한 이상인가 |
| `drop` | 기준선 대비 하락이 허용폭 이하인가 |
| `stable_case` | 기준선에서 만점이던 사례가 이번에 깨졌는가 |
| `judge_model` | RAGAS를 기준선과 같은 모델이 채점했는가 |
| `prompt_version` | 답을 만든 프롬프트 본문이 기준선과 같은가 |

**마지막 규칙이 늦게 들어온 이유를 적어 둡니다.**
[ADR-018](../docs/adr/ADR-018-prompt-versioning.md)이 프롬프트 지문을 보고서에 적게
만들었는데(`metadata.prompt_versions`), **게이트가 그 칸을 읽지 않았습니다.** 점수를 움직이는
입력 중에 게이트가 모르는 것이 하나 남아 있었던 것입니다 — 프롬프트를 손으로 고치다 심판
정확도 21/21 → 11/21을 낸 사고(ROADMAP 11-2)가 그 칸입니다.
`judge_model`을 넣은 이유와 같은 모양인데 한쪽만 막혀 있었습니다.

`git_sha`로는 대신할 수 없습니다. 프롬프트를 고치지 않은 커밋에서도 점수는 흔들리고, 고친
커밋에서도 다른 파일만 바뀌었을 수 있습니다. **지문이 다르면 다른 프롬프트입니다.**

**에이전트 실패와 채점 오류를 구별합니다.** 교안은 "채점 오류 0"만 두지만, 이 프로젝트는
에이전트 실패율을 **5.6~8.3%로 측정했습니다**(`EXPERIMENTS.md` 10절: `coauthor-03` 12회 중 1회,
`topcited` 18회 중 1회). 실패를 0으로 두면 게이트가 변동으로 터지고, 터지는 게이트는 꺼집니다.
그래서 채점 오류만 0이고 에이전트 실패는 상한을 둔 지표로 다룹니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.console import use_utf8_stdout

BASELINE = Path("baselines/eval_baseline.json")
GATE_REPORT = Path("reports/eval_gate.json")
SUMMARY = Path("reports/summary.md")

# 비교 대상이 아닌 열입니다. 지연과 비용은 품질이 아니고 실행 환경에 따라 흔들립니다.
NOT_A_QUALITY_METRIC = frozenset({
    "elapsed_seconds",
    "latency_seconds",
    "chat_input_tokens",
    "chat_output_tokens",
    "chat_tokens_total",
    "embedding_tokens_estimate",
    "embedding_tokens_total_estimate",
    "estimated_api_cost_usd",
    "estimated_api_cost_usd_total",
})


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.detail}"


def _numeric(row: dict[str, Any]) -> dict[str, float]:
    """Keep the quality metrics, dropping None, booleans and cost/latency columns."""
    return {
        key: float(value)
        for key, value in row.items()
        if key not in NOT_A_QUALITY_METRIC
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
    }


# RAGAS 지표는 **집계만** 게이트합니다. 사례별 규칙을 붙이면 매번 빨갛게 됩니다 —
# `coauthor-02`의 faithfulness가 실행에 따라 0과 1을 왕복합니다(변동폭 1.0000).
# 그리고 `context_precision`은 **검색이 단단한 가족에서만** 봅니다. 전체 평균의 변동폭이
# 0.1342인데 그것을 `coauthor`·`reference` 두 가족이 전부 만들고, 품질과 무관하게 움직입니다.
# 근거는 EXPERIMENTS 11-12절, 결정은 ADR-021입니다.
RAGAS_FAITHFULNESS = "ragas_faithfulness"
RAGAS_STABLE_PRECISION = "ragas_context_precision_stable_families"


def ragas_judge(report: dict[str, Any]) -> str:
    """Which model scored this RAGAS report."""
    return report.get("summary", {}).get("judge_model", "")


def ragas_metrics(report: dict[str, Any], stable_families: Sequence[str]) -> dict[str, float]:
    """Reduce a RAGAS study report to the two numbers the gate watches.

    `stable_families`는 코드가 아니라 기준선이 정합니다. 어느 가족이 안정적인지는 측정으로
    정해진 값이고, 데이터가 바뀌면 다시 재야 합니다.
    """
    rows = report.get("results", [])
    metrics: dict[str, float] = {}
    scores = [
        row["faithfulness"] for row in rows
        if isinstance(row.get("faithfulness"), (int, float))
    ]
    if scores:
        metrics[RAGAS_FAITHFULNESS] = statistics.fmean(scores)
    stable = [
        row["context_precision"] for row in rows
        if row.get("family") in set(stable_families)
        and isinstance(row.get("context_precision"), (int, float))
    ]
    if stable:
        metrics[RAGAS_STABLE_PRECISION] = statistics.fmean(stable)
    return metrics


def normalize(report: dict[str, Any]) -> dict[str, Any]:
    """Reduce one harness report to the shape the gate compares.

    `--mode compare` nests each case under `vector_only` and `graph_rag`. The product path
    is `graph_rag`, so that is what the gate watches; `vector_only` stays in the report as
    the contrast but is not gated.
    """
    summary = report.get("summary", {})
    compare = "graph_rag" in summary
    rows = [row.get("graph_rag", {}) if compare else row for row in report.get("results", [])]

    cases: dict[str, Any] = {}
    answer_failures = 0
    for index, row in enumerate(rows):
        case_id = row.get("id") or report["results"][index].get("id") or f"case-{index}"
        failed = bool(row.get("failed"))
        answer_failures += int(failed)
        cases[case_id] = {"failed": failed, "metrics": _numeric(row)}

    # 품질 지표는 **답을 만든 사례만으로** 집계합니다. 하네스의 summary는 멈춘 사례를 0점으로
    # 섞어서, 근거 recall이 품질이 아니라 실패율을 재게 됩니다. 2026-10-16 세 번째 full 실행이
    # 그것을 보여 줬습니다 — 멈춘 사례를 섞으면 recall이 0.9667에서 0.9000으로 떨어지지만,
    # 답을 만든 사례만 보면 **세 실행 모두 1.0000**이고 변동폭이 0입니다. 실패는 아래의
    # `answer_failure_rate`가 자기 규칙으로 셉니다.
    completed = [case["metrics"] for case in cases.values() if not case["failed"]]
    keys = sorted(set().union(*(set(m) for m in completed)) if completed else ())
    metrics = {
        key: statistics.fmean(values)
        for key in keys
        if (values := [m[key] for m in completed if key in m])
    }
    metrics.pop("failed", None)  # 실패는 비율이 아니라 건수로 따로 셉니다.
    metrics["answer_failure_rate"] = answer_failures / len(cases) if cases else 0.0
    metrics["completed_cases"] = float(len(completed))
    return {
        "mode": report.get("mode"),
        "dataset": report.get("dataset"),
        "generated_at": report.get("generated_at"),
        "git_sha": report.get("metadata", {}).get("git_commit"),
        "metrics": metrics,
        "cases": cases,
        # 채점 함수가 죽은 건수입니다. 에이전트 실패와 다릅니다 — 모듈 설명 참고.
        "scoring_errors": int(report.get("scoring_errors", 0)),
        # 보고서가 적어 두었으면 그대로 들고 옵니다. `--ragas`로 주면 그쪽이 덮습니다.
        "ragas_judge_model": report.get("ragas_judge_model", ""),
        # 답을 만든 프롬프트 본문의 지문입니다(ADR-018, `evals.telemetry.prompt_manifest`).
        # 지문을 적기 전에 돌린 옛 보고서는 빈 dict가 되고, 게이트는 그것을 "기록 없음"으로
        # 올립니다 — 조용히 통과시키면 그 보고서가 어느 프롬프트였는지 영원히 알 수 없습니다.
        "prompt_versions": (report.get("metadata") or {}).get("prompt_versions") or {},
    }


def merge(reports: list[dict[str, Any]]) -> dict[str, Any]:
    """Average several normalized runs, and record the spread they actually showed."""
    if len(reports) == 1:
        single = dict(reports[0])
        single["runs"] = 1
        single["observed_spread"] = None
        single.setdefault("ragas_judge_model", reports[0].get("ragas_judge_model", ""))
        single.setdefault("prompt_versions", reports[0].get("prompt_versions") or {})
        return single
    keys = sorted(set().union(*(set(report["metrics"]) for report in reports)))
    metrics, spread = {}, {}
    for key in keys:
        values = [report["metrics"][key] for report in reports if key in report["metrics"]]
        metrics[key] = statistics.fmean(values)
        spread[key] = max(values) - min(values)
    merged = dict(reports[-1])
    merged["metrics"] = metrics
    merged["runs"] = len(reports)
    merged["observed_spread"] = spread
    # 사례는 모든 실행에서 같은 결과를 낸 것만 "그 결과였다"고 말할 수 있습니다. 마지막
    # 실행의 사례만 들고 오면, 한 번 멈춘 사례가 다음 기준선에서 안정으로 등록됩니다.
    judges = {run.get("ragas_judge_model") for run in reports if run.get("ragas_judge_model")}
    # 실행마다 판정 모델이 다르면 하나로 적을 수 없습니다. 비워 두면 게이트가 불일치로 막습니다.
    merged["ragas_judge_model"] = judges.pop() if len(judges) == 1 else ""
    # 프롬프트도 같습니다. 실행 도중에 본문을 고쳤으면 **세 실행의 평균이 무엇의 평균인지**
    # 말할 수 없습니다. 비워 두어 게이트가 "기록 없음"으로 막게 합니다.
    fingerprints = {
        json.dumps(report.get("prompt_versions") or {}, sort_keys=True) for report in reports
    }
    merged["prompt_versions"] = (
        reports[0].get("prompt_versions") or {} if len(fingerprints) == 1 else {}
    )
    merged["cases"] = {
        case_id: {
            "failed": any(report["cases"].get(case_id, {}).get("failed") for report in reports),
            "metrics": {
                key: min(
                    report["cases"][case_id]["metrics"][key]
                    for report in reports
                    if key in report["cases"].get(case_id, {}).get("metrics", {})
                )
                for key in set().union(
                    *(
                        set(report["cases"].get(case_id, {}).get("metrics", {}))
                        for report in reports
                    )
                )
            },
        }
        for case_id in set().union(*(set(report["cases"]) for report in reports))
    }
    return merged


# `make_baseline`이 **스스로 계산하는** 키입니다. 나머지는 전부 손으로 정한 값이므로
# 재작성에서 그대로 들고 옵니다.
#
# 전에는 반대로 "들고 올 키 목록"을 들고 있었습니다. 그 목록은 **빠뜨릴 수 있습니다** -
# `splits`가 한 번, `related_slot`이 한 번, `model_registry`(ADR-032)가 한 번 조용히
# 사라졌습니다. 세 번째를 겪고 방향을 뒤집었습니다. 목록을 더하는 것을 잊어도 키는
# 남습니다. 평가 보고서에 없는 값은 `merge`가 만들어 줄 수 없으므로, 사라지면 손으로
# 되살려야 합니다.
DERIVED_KEYS = frozenset({
    "generated_at",
    "note",
    "source",
    "primary_metric",
    "metrics",
    "absolute_floor",
    "allowed_drop",
    "case_ids",
    "stable_cases",
})


def carried_over(existing: dict[str, Any]) -> dict[str, Any]:
    """Everything in the old baseline that a rewrite must not drop."""
    return {key: value for key, value in existing.items() if key not in DERIVED_KEYS}


def make_baseline(
    merged: dict[str, Any],
    *,
    primary_metric: str,
    floors: dict[str, float],
    drops: dict[str, float] | None,
    note: str,
    carried: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the baseline file. Stable cases are the ones that scored full marks."""
    stable = sorted(
        case_id
        for case_id, case in merged["cases"].items()
        if not case["failed"] and case["metrics"].get(primary_metric) == 1.0
    )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "note": note,
        "source": {
            "mode": merged.get("mode"),
            "dataset": merged.get("dataset"),
            "runs": merged.get("runs"),
            "git_sha": merged.get("git_sha"),
            "observed_spread": merged.get("observed_spread"),
        },
        "primary_metric": primary_metric,
        # 이 지표를 어떤 사례 집합에서 쟀는지. 다른 분할에는 하한을 들이대지 않습니다.
        "metrics_split": "full",
        "metrics": merged["metrics"],
        "absolute_floor": floors,
        # 반복 측정이 없으면 비워 둡니다. 근거 없는 허용폭은 규칙이 아니라 숫자입니다.
        "allowed_drop": drops,
        "case_ids": sorted(merged["cases"]),
        "stable_cases": stable,
        **(carried or {}),
    }


def expected_cases(baseline: dict[str, Any], split: str) -> set[str]:
    """Which cases this split is supposed to cover.

    PR에서는 가족마다 한 건씩만 돌립니다(`smoke`). 분할을 모르면 "사례가 빠졌다"가 매 PR에서
    걸려서 분할 자체를 쓸 수 없게 됩니다.
    """
    splits = baseline.get("splits") or {}
    if split in splits:
        return set(splits[split])
    return set(baseline["case_ids"])


def compares_aggregates(baseline: dict[str, Any], split: str) -> bool:
    """Whether this split's averages can be compared with the baseline's.

    기준선의 지표는 **어떤 사례 집합에서** 쟀는지에 매여 있습니다. 30건 평균과 5건 평균은
    서로 다른 모수의 값이라 하한·허용폭을 그대로 들이댈 수 없습니다. 실제로 smoke가 고르는
    `-01` 사례들은 전체보다 쉬워서 평균이 1.0으로 나왔습니다 — 그 1.0은 "좋아졌다"가 아니라
    "다른 것을 쟀다"입니다.

    그래서 분할이 다르면 집계 규칙(`floor`·`drop`)을 적용하지 않습니다. 모수와 무관한
    규칙(`cases`·`scoring_error`·`stable_case`)만 봅니다. 5건으로 답할 수 있는 질문은
    "평균이 괜찮은가"가 아니라 "이 5건 중 깨진 것이 있는가"입니다.
    """
    return split == baseline.get("metrics_split", "full")


def prompt_drift(
    expected: dict[str, str] | None, actual: dict[str, str] | None
) -> list[Violation]:
    """Which prompts this run did not share with the baseline.

    **기준선이 지문을 들고 있지 않으면 아무것도 말하지 않습니다.** 규칙을 넣기 전에 쓴
    기준선에 이 규칙을 소급 적용하면 매 실행이 실패하고, 그러면 게이트가 꺼집니다
    (`stable_case`가 실패율을 두 번 세지 않는 것과 같은 선).

    반대로 기준선이 들고 있는데 이번 보고서가 비어 있으면 **올립니다.** 지문이 없는 실행은
    "같았다"가 아니라 "모른다"이고, 모르는 것을 통과시키면 규칙이 없는 것과 같습니다.
    """
    if not expected:
        return []
    if not actual:
        return [Violation(
            "prompt_version",
            "이번 보고서에 프롬프트 지문이 없습니다(metadata.prompt_versions). 어느 프롬프트로 "
            "낸 점수인지 알 수 없어 기준선과 비교할 수 없습니다.",
        )]
    changed = sorted(
        name for name in set(expected) | set(actual) if expected.get(name) != actual.get(name)
    )
    if not changed:
        return []
    detail = ", ".join(
        f"{name} {expected.get(name) or '(없음)'} -> {actual.get(name) or '(없음)'}"
        for name in changed
    )
    return [Violation("prompt_version", f"프롬프트 본문이 기준선과 다릅니다: {detail}")]


def check(
    baseline: dict[str, Any], report: dict[str, Any], *, split: str = "full"
) -> list[Violation]:
    """Apply the rules in the module table and return every violation, not just the first."""
    violations: list[Violation] = []
    primary = baseline["primary_metric"]
    aggregates = compares_aggregates(baseline, split)

    expected, actual = expected_cases(baseline, split), set(report["cases"])
    if missing := sorted(expected - actual):
        violations.append(Violation("cases", f"기준선에 있던 사례가 빠졌습니다: {missing}"))
    if added := sorted(actual - expected):
        violations.append(Violation("cases", f"기준선에 없는 사례가 들어왔습니다: {added}"))

    # 판정 모델이 바뀌면 RAGAS 점수를 같은 표에 올릴 수 없습니다. 2026-10-17에 CI가
    # `OPENAI_MODEL`을 넘기지 않아 기준선과 다른 모델로 채점한 값을 비교했습니다.
    # 모수가 다르면 비교하지 않는다는 `metrics_split`과 같은 규칙입니다.
    expected_judge = baseline.get("ragas_judge_model")
    if aggregates and expected_judge:
        actual_judge = report.get("ragas_judge_model") or ""
        if actual_judge != expected_judge:
            violations.append(Violation(
                "judge_model",
                f"RAGAS 판정 모델이 기준선과 다릅니다: 기준선 {expected_judge!r}, "
                f"이번 {actual_judge or '(기록 없음)'!r}",
            ))

    # 프롬프트 본문이 바뀌면 점수가 움직이고, 그 움직임은 회귀가 아니라 **다른 것을 쟀다**
    # 입니다. `metrics_split`과 달리 분할에 매이지 않습니다 — 집계를 비교하지 않는 smoke
    # 분할에서도 `stable_case` 규칙이 돌고, 그 규칙은 프롬프트가 바뀌면 뜻이 달라집니다.
    violations.extend(prompt_drift(baseline.get("prompt_versions"), report.get("prompt_versions")))

    if report["scoring_errors"]:
        violations.append(
            Violation("scoring_error", f"채점이 {report['scoring_errors']}건에서 죽었습니다")
        )

    for name, floor in (baseline["absolute_floor"].items() if aggregates else ()):
        value = report["metrics"].get(name)
        if value is None:
            violations.append(Violation("floor", f"{name}이 이번 결과에 없습니다"))
        elif _worse(name, value, floor):
            violations.append(
                Violation("floor", f"{name} {value:.4f}이 하한 {floor:.4f}을 넘었습니다")
            )

    for name, allowed in ((baseline["allowed_drop"] or {}).items() if aggregates else ()):
        value, before = report["metrics"].get(name), baseline["metrics"].get(name)
        if value is None or before is None:
            continue
        moved = (value - before) if _lower_is_better(name) else (before - value)
        if moved > allowed:
            violations.append(
                Violation(
                    "drop",
                    f"{name} {before:.4f} -> {value:.4f}, 허용폭 {allowed:.4f}을 넘었습니다",
                )
            )

    for case_id in baseline["stable_cases"]:
        case = report["cases"].get(case_id)
        if case is None:
            continue  # 이번에 평가하지 않은 사례는 보지 않습니다(smoke 분할).
        if case["failed"]:
            # 에이전트가 예외로 멈춘 것은 측정된 변동이고 `answer_failure_rate`의 상한이
            # 이미 셉니다. 여기서 한 번 더 세면 같은 변동에 두 번 벌점을 주게 됩니다.
            # 안정 사례 29건에 사례당 실패율 5.6%면 **적어도 한 건이 실패할 확률이 81%** 입니다.
            # 그러면 회귀가 없는 날에도 게이트가 다섯 번 중 네 번 빨갛게 되고, 그런 게이트는
            # 꺼집니다. 어느 사례였는지는 요약에 정보로 적습니다.
            continue
        value = case["metrics"].get(primary)
        if value is None or value < 1.0:
            violations.append(
                Violation("stable_case", f"{case_id}이 기준선에서는 만점이었는데 {primary}={value}")
            )
    return violations


def transient_failures(baseline: dict[str, Any], report: dict[str, Any]) -> list[str]:
    """Stable cases whose agent raised this run. Information, not a violation."""
    return sorted(
        case_id
        for case_id in baseline["stable_cases"]
        if (case := report["cases"].get(case_id)) and case["failed"]
    )


def _lower_is_better(name: str) -> bool:
    return name.endswith(("_failure_rate", "_errors"))


def _worse(name: str, value: float, floor: float) -> bool:
    return value > floor if _lower_is_better(name) else value < floor


def write_summary(path: Path, baseline: dict[str, Any], report: dict[str, Any],
                  violations: list[Violation]) -> None:
    """A table for the CI step summary, so a failure is readable without the artifact."""
    # 프롬프트가 같았는지를 통과한 실행에서도 적어 둡니다. 그래야 나중에 "무엇으로 낸
    # 점수인가"를 되짚을 때 본문을 찾으러 가지 않습니다(ADR-041). 세 상태를 구별합니다 -
    # 같다 / 다르다 / **기준선이 지문을 안 들고 있어 말할 수 없다**.
    if not baseline.get("prompt_versions"):
        prompts_line = "기준선에 지문이 없어 대조하지 않았습니다"
    elif prompt_drift(baseline.get("prompt_versions"), report.get("prompt_versions")):
        prompts_line = "**기준선과 다릅니다**"
    else:
        prompts_line = "기준선과 같습니다"
    lines = [
        "## 회귀 평가",
        "",
        f"- 분할 `{report.get('split', 'full')}` · 사례 {len(report['cases'])}건 "
        f"· 기준선 {baseline['source']['runs']}회 실행분",
        f"- 판정: {'실패' if violations else '통과'} (위반 {len(violations)}건)",
        f"- 프롬프트: {prompts_line}",
        *(
            []
            if compares_aggregates(baseline, report.get("split", "full"))
            else [
                f"- 기준선 지표는 `{baseline.get('metrics_split', 'full')}` 집합에서 쟀으므로 "
                "이 실행에는 하한·허용폭을 적용하지 않았습니다(모수가 다릅니다). "
                "사례별 회귀만 판정했습니다."
            ]
        ),
        "",
        "| 지표 | 기준선 | 이번 | 하한 |",
        "|---|---:|---:|---:|",
    ]
    for name in sorted(set(baseline["metrics"]) | set(report["metrics"])):
        before = baseline["metrics"].get(name)
        now = report["metrics"].get(name)
        floor = baseline["absolute_floor"].get(name)
        fmt = lambda value: "—" if value is None else f"{value:.4f}"  # noqa: E731
        lines.append(f"| `{name}` | {fmt(before)} | {fmt(now)} | {fmt(floor)} |")
    if transient := transient_failures(baseline, report):
        lines += [
            "",
            "### 일시 실패 (위반 아님)",
            "",
            f"- 안정 사례 중 이번에 에이전트가 멈춘 것: {', '.join(transient)}",
            f"- 실패율 {report['metrics'].get('answer_failure_rate', 0):.4f}로 상한 "
            f"{baseline['absolute_floor'].get('answer_failure_rate', 0):.4f} 안입니다. "
            "측정된 변동 범위(5.6~8.3%)이므로 회귀로 세지 않습니다.",
        ]
    if violations:
        lines += ["", "### 위반", ""] + [f"- {violation}" for violation in violations]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="평가 결과를 기준선과 대조합니다.")
    parser.add_argument("--report", type=Path, nargs="+", required=True)
    parser.add_argument("--ragas", type=Path, nargs="*", default=[],
                        help="같은 실행의 RAGAS study 보고서. 집계 두 개를 지표에 더합니다.")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--output", type=Path, default=GATE_REPORT)
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--split", default="full",
                        help="기준선의 splits 중 어느 집합을 돌렸는지. 기본 full.")
    parser.add_argument("--primary-metric", default="answer_evidence_recall")
    parser.add_argument("--note", default="")
    args = parser.parse_args()
    use_utf8_stdout()

    normalized = [
        normalize(json.loads(path.read_text(encoding="utf-8"))) for path in args.report
    ]
    existing_baseline = (
        json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline.exists() else {}
    )
    stable = existing_baseline.get("ragas_stable_families", ())
    if args.ragas:
        if len(args.ragas) != len(normalized):
            parser.error("--ragas 개수가 --report 개수와 같아야 합니다(실행끼리 짝지어야 합니다).")
        for run, path in zip(normalized, args.ragas, strict=True):
            ragas_report = json.loads(path.read_text(encoding="utf-8"))
            run["metrics"].update(ragas_metrics(ragas_report, stable))
            run["ragas_judge_model"] = ragas_judge(ragas_report)
    merged = merge(normalized)

    if args.write_baseline:
        if not args.note:
            parser.error("--write-baseline에는 --note가 필요합니다. 하한의 근거를 적으세요.")
        existing = existing_baseline
        carried = carried_over(existing)
        # 판정 모델은 손으로 정한 값이라 들고 오는데, 이번 실행이 **다른 모델**로 채점했다면
        # 들고 오는 것이 거짓말입니다. 그대로 쓰면 다음 게이트가 영원히 불일치로 막힙니다.
        # 어느 쪽이 맞는지는 코드가 모르므로 추측하지 않고 멈춥니다.
        measured_judge = merged.get("ragas_judge_model") or ""
        carried_judge = carried.get("ragas_judge_model") or ""
        if measured_judge and carried_judge and measured_judge != carried_judge:
            parser.error(
                f"RAGAS 판정 모델이 기준선({carried_judge!r})과 이번 실행({measured_judge!r})에서 "
                "다릅니다. 기준선의 ragas_judge_model을 손으로 정한 뒤 다시 쓰세요."
            )
        if measured_judge:
            carried["ragas_judge_model"] = measured_judge
        # 프롬프트도 같은 이유로 멈춥니다. 다만 여기서는 **고의로 고친 경우가 정상**이므로
        # (프롬프트를 바꿨으니 기준선을 다시 쓰는 것) 멈추는 조건이 하나 더 붙습니다 —
        # 실행들끼리 지문이 갈린 경우입니다. 그때는 평균이 무엇의 평균인지 말할 수 없습니다.
        measured_prompts = merged.get("prompt_versions") or {}
        if len(args.report) > 1 and not measured_prompts:
            parser.error(
                "실행마다 프롬프트 지문이 다릅니다. 같은 본문으로 돌린 실행들만 하나의 "
                "기준선이 될 수 있습니다(metadata.prompt_versions 확인)."
            )
        if measured_prompts:
            carried["prompt_versions"] = measured_prompts
        else:
            # 빈 값으로 덮지 않습니다. 보고서가 지문을 안 적는다는 사실이 기준선의 지문을
            # 지울 이유가 될 수 없습니다 — `splits`·`related_slot`·`model_registry`가
            # 조용히 사라진 세 번(ADR-034·ADR-032)과 같은 모양입니다.
            print(
                "주의: 보고서에 프롬프트 지문이 없습니다. 기준선의 prompt_versions는 "
                f"{'그대로 둡니다' if carried.get('prompt_versions') else '비어 있습니다'}."
            )
        baseline = make_baseline(
            merged,
            primary_metric=args.primary_metric,
            floors=existing.get("absolute_floor", {}),
            drops=existing.get("allowed_drop"),
            note=args.note,
            carried=carried,
        )
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(
            json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"기준선을 썼습니다: {args.baseline} (하한은 손으로 정합니다)")
        return 0

    merged["split"] = args.split
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    if args.baseline.exists():
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        violations = check(baseline, merged, split=args.split)
        write_summary(args.summary, baseline, merged, violations)
        for violation in violations:
            print(violation)
        print(f"위반 {len(violations)}건, 판정은 pytest가 합니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
