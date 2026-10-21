"""`stable_case` 규칙이 변동으로 터지는 비율을 잽니다 — 규칙을 고치기 전에.

    uv run python -m scripts.measure_stable_case_variance

2026-10-21에 PR #16의 `evaluate`가 `[stable_case] coauthor-01이 기준선에서는 만점이었는데
answer_evidence_recall=0.0`으로 떨어졌고, **같은 커밋에서 재실행하니 통과했습니다.** 답변은
내용상 맞았는데(`"The collection lists four other papers sharing the author Han Lu with …"`)
논문 ID를 하나도 인용하지 않아 `evidence_ids`가 비었습니다. 예외는 없었습니다.

API를 쓰지 않습니다. 저장소가 이미 들고 있는 실행들을 읽습니다.

## 먼저 틀린 전제를 바로잡습니다

처음에 "`stable_case`에는 변동 보호가 없다"고 봤는데 **기준선 쪽에서는 틀렸습니다.**
[`evals.gate.merge`](../evals/gate.py)가 사례를 합칠 때 지표는 `min(...)`, `failed`는
`any(...)`를 씁니다. 그래서 `stable_cases`는 **3회 전부 만점이고 한 번도 멈추지 않은**
사례입니다 — [ADR-043](../docs/adr/ADR-043-the-floor-is-checked-against-the-worst-run.md)이
하한에 세운 "최악 실행" 원칙이 여기에도 이미 적용돼 있습니다.

그리고 규칙은 **에이전트가 멈춘 경우를 이미 건너뜁니다.** 게이트 주석이 그 이유를 계산까지
해서 적어 뒀습니다 — *"안정 사례 29건에 사례당 실패율 5.6%면 적어도 한 건이 실패할 확률이
81%"*, 그러면 *"회귀가 없는 날에도 게이트가 다섯 번 중 네 번 빨갛게 되고, 그런 게이트는
꺼집니다."*

## 그래서 남는 질문은 비대칭입니다

| | 표본 |
|---|---|
| 기준선의 "만점" | **3회 전부** |
| 이번 실행의 판정 | **한 번** |

멈추지 않고 **형식만 바뀌어** 지표가 내려가는 채널은 규칙이 건너뛰지 않습니다. 그 채널의
사례당 비율을 `q`라 하면, 회귀가 없어도 게이트는 실행마다 `1 - (1-q)^N` 확률로 빨개집니다.
3회가 전부 1.0이었다는 것은 `q`가 작다는 뜻이지만 **3표본으로 `q` ≈ 0.1을 배제할 수
없습니다.**

## 넘어야 하는 바 — 재기 전에 적습니다

같은 코드 상태의 반복 실행에서, 기준선이 안정으로 부를 사례가 **예외 없이** 1.0 아래로
내려간 (사례, 실행) 쌍의 비율을 `q`로 셉니다. 그리고 `P = 1 - (1-q)^N`을 full(N=30)과
smoke(N=5)로 냅니다.

| 결과 | 결정 |
|---|---|
| `P(N=30) >= 0.20` | 규칙이 변동으로 **자주** 터집니다. 고칠 방향은 임계값을 |
| | 낮추는 것이 아니라 **사례별 측정된 변동과 비교**하는 것입니다 |
| `P(N=30) < 0.20` | 규칙을 **그대로 둡니다.** 오늘 것은 예상 범위이고, |
| | 그 수를 적어 두어 다음에 다시 따지지 않습니다 |
| **상한이 0.20을 넘음** | **측정되지 않았습니다.** 사건이 0이어도 `q`의 95% 상한은 |
| | 약 `3/n`이고, 그것으로 `P`를 내야 "통과"를 말할 수 있습니다. |
| | 그때는 고치는 것도 그대로 두는 것도 근거가 없습니다 |

**0.20을 고른 근거**는 게이트 자신의 말입니다 — 81%를 "그런 게이트는 꺼집니다"로 기각했으니
상한은 그보다 훨씬 아래여야 합니다. 다섯 번에 한 번이면 사람이 빨간불을 잡음으로 읽기
시작하는 지점입니다. **판단이고, 판단이라고 적습니다.**

**함께 보는 것 하나.** 흔들리는 사례가 **한 가족에 몰려 있는가**. 몰려 있으면 고칠 곳은
게이트가 아니라 그 가족의 답변 형식입니다 — 결론이 전혀 달라집니다.

## 같은 코드 상태만 묶습니다

프롬프트 지문(`metadata.prompt_versions`)이 같고 **생성 시각이 같은 날 안**인 실행만 한 묶음
으로 봅니다. 지문이 같아도 코드가 다를 수 있어서(2026-10-18 라우팅 변경은 프롬프트를 바꾸지
않았습니다) 날짜로 한 겹 더 가립니다. 묶음을 잘못 지으면 재는 것이 변동이 아니라 **코드
차이**입니다.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.gate import normalize

OUTPUT = Path("reports/stable_case_variance.json")
BASELINE = Path("baselines/eval_baseline.json")
# 게이트가 보는 지표. 기준선이 들고 있는 값을 쓰되, 없으면 이 기본값입니다.
PRIMARY = "answer_evidence_recall"
# 바: 이 위면 규칙이 변동으로 자주 터진다고 봅니다. 근거는 모듈 docstring에 있습니다.
TOLERABLE_RED_RATE = 0.20
# smoke는 가족마다 한 건씩 5건, full은 30건입니다.
POPULATIONS = (30, 5)


def load_runs(paths: list[Path]) -> list[dict[str, Any]]:
    runs = []
    for path in paths:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        results = report.get("results")
        if not isinstance(results, list) or len(results) != 30:
            continue
        if not isinstance(results[0].get("graph_rag"), dict):
            continue
        shaped = normalize(report)
        shaped["path"] = path.name
        shaped["generated_at"] = (report.get("generated_at") or "")[:19]
        runs.append(shaped)
    return runs


def group_key(run: dict[str, Any]) -> str:
    """지문 + 날짜. 지문이 같아도 코드가 다를 수 있어 날짜로 한 겹 더 가립니다."""
    prints = json.dumps(run.get("prompt_versions") or {}, sort_keys=True)
    # `hash()`는 프로세스마다 달라집니다(PYTHONHASHSEED). 보고서에 적히는 키이므로
    # 재현되는 것이어야 합니다.
    digest = hashlib.sha1(prints.encode()).hexdigest()[:8] if prints != "{}" else "none"
    return f"{digest}@{run['generated_at'][:10]}"


def one_group(runs: list[dict[str, Any]], primary: str) -> dict[str, Any]:
    """한 묶음에서 (사례, 실행) 쌍의 하락 비율을 셉니다."""
    # 이 묶음에서 기준선이 안정으로 부를 사례 - 모든 실행에서 멈추지 않고 모두 1.0.
    case_ids = sorted(set().union(*(set(run["cases"]) for run in runs)))
    pairs, drops, failures = 0, [], 0
    per_case: dict[str, list[float | None]] = collections.defaultdict(list)
    for case_id in case_ids:
        values: list[float | None] = []
        for run in runs:
            case = run["cases"].get(case_id)
            if case is None:
                values.append(None)
                continue
            if case["failed"]:
                # 규칙이 건너뛰는 채널입니다. 여기서 세면 같은 변동에 두 번 벌점입니다.
                values.append(None)
                failures += 1
                continue
            values.append(case["metrics"].get(primary))
        per_case[case_id] = values
        seen = [v for v in values if v is not None]
        if not seen:
            continue
        # **기준선이 안정으로 부를 수 있는 사례만 봅니다** - 적어도 한 번 만점이어야 합니다.
        if max(seen) < 1.0:
            continue
        pairs += len(seen)
        below = [v for v in seen if v < 1.0]
        if below:
            drops.append({"case": case_id, "values": seen, "below": len(below)})
    q = sum(d["below"] for d in drops) / pairs if pairs else 0.0
    return {
        "runs": [run["path"] for run in runs],
        "generated_at": [run["generated_at"] for run in runs],
        "eligible_pairs": pairs,
        "agent_failures_skipped": failures,
        "drop_rate": round(q, 6),
        "drops": drops,
        "per_case": {k: v for k, v in per_case.items() if any(x is not None for x in v)},
    }


def red_rate(q: float, population: int) -> float:
    """회귀가 없어도 게이트가 빨개질 확률. `1 - (1-q)^N`."""
    return round(1.0 - (1.0 - q) ** population, 4)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="stable_case 규칙이 변동으로 터지는 비율을 잽니다."
    )
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--min-runs", type=int, default=3, help="이보다 작은 묶음은 버립니다")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    primary = PRIMARY
    if BASELINE.exists():
        primary = json.loads(BASELINE.read_text(encoding="utf-8")).get("primary_metric", PRIMARY)

    runs = load_runs(sorted(args.reports.glob("*.json")))
    # 같은 실행을 다시 채점한 보고서가 둘씩 있습니다(`_rescored`, `_setmetrics`). 생성 시각이
    # 같으면 같은 실행이므로 하나만 씁니다 - 세면 표본이 가짜로 두 배가 됩니다.
    unique: dict[str, dict[str, Any]] = {}
    for run in runs:
        unique.setdefault(run["generated_at"], run)
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for run in unique.values():
        grouped[group_key(run)].append(run)
    # **지문 없는 묶음은 버립니다.** ADR-018 이전 보고서에는 `prompt_versions`가 없어서
    # 같은 코드 상태인지 말할 수 없습니다. 2026-10-15의 네 실행이 그런 경우인데, 파일명이
    # `_after_fixes`·`_final`·`_selfref`이고 패턴이 "첫 실행만 0"이라 **변동이 아니라 수정이
    # 들어간 것**입니다. 섞으면 재는 것이 변동이 아니라 코드 차이입니다.
    usable = {
        k: v for k, v in grouped.items()
        if len(v) >= args.min_runs and not k.startswith("none@")
    }
    dropped_groups = sorted(
        k for k, v in grouped.items()
        if len(v) >= args.min_runs and k.startswith("none@")
    )

    groups = {key: one_group(runs, primary) for key, runs in sorted(usable.items())}
    total_pairs = sum(g["eligible_pairs"] for g in groups.values())
    total_below = sum(d["below"] for g in groups.values() for d in g["drops"])
    pooled = total_below / total_pairs if total_pairs else 0.0

    families = collections.Counter(
        d["case"].rsplit("-", 1)[0] for g in groups.values() for d in g["drops"]
    )
    # **0건을 "비율 0"으로 읽으면 안 됩니다.** 사건이 0이면 95% 상한이 약 3/n입니다
    # (rule of three). 그 상한으로 P를 내야 "바를 통과했다"를 말할 수 있습니다.
    upper_q = 3.0 / total_pairs if total_pairs and total_below == 0 else None
    needed = int(3.0 / (1.0 - (1.0 - TOLERABLE_RED_RATE) ** (1.0 / max(POPULATIONS)))) + 1
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "groups_dropped_no_fingerprint": dropped_groups,
        "pooled_drop_rate_upper95": round(upper_q, 6) if upper_q is not None else None,
        "red_rate_upper95_by_population": (
            {str(n): red_rate(upper_q, n) for n in POPULATIONS}
            if upper_q is not None else None
        ),
        "pairs_needed_for_tolerance": needed,
        "primary_metric": primary,
        "tolerable_red_rate": TOLERABLE_RED_RATE,
        "runs_considered": len(unique),
        "groups_used": len(groups),
        "pooled_eligible_pairs": total_pairs,
        "pooled_drops": total_below,
        "pooled_drop_rate": round(pooled, 6),
        "red_rate_by_population": {
            str(n): red_rate(pooled, n) for n in POPULATIONS
        },
        # 세 결과가 가능합니다. **상한이 바를 넘으면 "측정되지 않은 것"** 이고, 그때는
        # 규칙을 고치는 것도 그대로 두는 것도 근거가 없습니다 - 이 프로젝트가 구간이 0을
        # 포함할 때 "측정되지 않았다"고 적는 것과 같은 선입니다.
        "verdict": (
            "rule_fires_on_variance"
            if red_rate(pooled, max(POPULATIONS)) >= TOLERABLE_RED_RATE
            else "not_resolved"
            if upper_q is not None
            and red_rate(upper_q, max(POPULATIONS)) >= TOLERABLE_RED_RATE
            else "within_tolerance"
        ),
        "families_of_drops": dict(families),
        "groups": groups,
        "note": (
            "같은 실행을 다시 채점한 보고서는 생성 시각으로 묶어 하나로 셉니다. 에이전트가 "
            "멈춘 (사례, 실행) 쌍은 세지 않습니다 - stable_case 규칙이 그 채널을 이미 "
            "건너뛰고 answer_failure_rate가 따로 셉니다. 적어도 한 번 만점이었던 사례만 "
            "봅니다(기준선이 안정으로 부를 수 있는 사례)."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"지표 {primary} / 실행 {len(unique)}개 중 묶음 {len(groups)}개\n")
    for key, group in groups.items():
        print(f"  {key}  실행 {len(group['runs'])}개  "
              f"대상 쌍 {group['eligible_pairs']}  하락 {sum(d['below'] for d in group['drops'])}  "
              f"(멈춘 쌍 {group['agent_failures_skipped']} 제외)")
        for drop in group["drops"]:
            print(f"      {drop['case']}: {drop['values']}")
    if dropped_groups:
        print(f"  버린 묶음(지문 없음, 같은 코드 상태인지 말할 수 없음): {dropped_groups}")
    print(f"\n합산 하락 비율 q = {pooled:.4f}  ({total_below}/{total_pairs})")
    for n in POPULATIONS:
        print(f"  회귀 없이 빨개질 확률 P(N={n:2d}) = {red_rate(pooled, n):.4f}")
    if upper_q is not None:
        print(f"  사건 0건이므로 비율 0이 아닙니다 - 95% 상한 q <= 3/{total_pairs} = {upper_q:.4f}")
        for n in POPULATIONS:
            print(f"    그 상한에서 P(N={n:2d}) <= {red_rate(upper_q, n):.4f}")
        print(f"    바 {TOLERABLE_RED_RATE}를 보이려면 쌍이 {needed}개 필요합니다")
    print(f"\n바 {TOLERABLE_RED_RATE} -> 판정: {report['verdict']}")
    if families:
        print(f"흔들린 가족: {dict(families)}")
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
