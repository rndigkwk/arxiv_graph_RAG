"""`stable_case` 규칙이 변동으로 터지는 비율을 잽니다 — 규칙을 고치기 전에.

    uv run python -m scripts.measure_stable_case_variance

2026-10-21에 PR #16의 `evaluate`가 `[stable_case] coauthor-01이 기준선에서는 만점이었는데
answer_evidence_recall=0.0`으로 떨어졌고, **같은 커밋에서 재실행하니 통과했습니다.** 답변은
내용상 맞았는데(`"The collection lists four other papers sharing the author Han Lu with …"`)
논문 ID를 하나도 인용하지 않아 `evidence_ids`가 비었습니다. 예외는 없었습니다.

API를 쓰지 않습니다. [`evals/ledger.py`](../evals/ledger.py)가 쌓은 장부를 읽습니다.

## 묶음은 여기서 짓지 않습니다

처음에는 이 스크립트가 보고서를 긁어 직접 묶었습니다. 그러다 **장부와 두 벌이 됐고 바로
갈라졌습니다** — 이쪽은 날짜로 가렸는데 장부는 안 가렸고, 모드와 최소 실행 수도 달랐습니다.
이 저장소에서 손으로 유지하는 두 벌이 갈라진 자리를 여덟 번 봤으므로, 묶는 규칙은
[`evals.ledger.groups`](../evals/ledger.py) **한 곳**에 둡니다. 이 스크립트에 남은 것은
**통계**뿐입니다.

## 전제 두 개가 틀렸습니다 — 먼저 적습니다

**① "`stable_case`에는 변동 보호가 없다" — 기준선 쪽에서는 틀렸습니다.**
[`evals.gate.merge`](../evals/gate.py)가 사례를 합칠 때 지표는 `min(...)`, `failed`는
`any(...)`를 씁니다. 그래서 `stable_cases`는 **3회 전부 만점이고 한 번도 멈추지 않은**
사례입니다 — [ADR-043](../docs/adr/ADR-043-the-floor-is-checked-against-the-worst-run.md)의
"최악 실행" 원칙이 여기에도 이미 적용돼 있습니다.

**② 규칙은 변동 한 채널을 이미 건너뜁니다.** 에이전트가 예외로 멈춘 사례는 보지 않습니다.
게이트 주석이 그 이유를 계산까지 해 뒀습니다 — *"안정 사례 29건에 사례당 실패율 5.6%면
적어도 한 건이 실패할 확률이 81%"*, 그러면 *"회귀가 없는 날에도 게이트가 다섯 번 중 네 번
빨갛게 되고, 그런 게이트는 꺼집니다."*

남는 것은 **비대칭**(기준선 3회 대 이번 1회)과, 멈추지 않고 **형식만 바뀌어** 지표가
내려가는 채널입니다.

## 넘어야 하는 바 — 재기 전에 적었습니다

| 결과 | 결정 |
|---|---|
| `P(N=30) >= 0.20` | 규칙이 변동으로 **자주** 터집니다 → 사례별 측정된 변동과 비교하도록 고칩니다 |
| `P(N=30) < 0.20` | 규칙을 **그대로 둡니다** |
| **상한이 0.20을 넘음** | **측정되지 않았습니다** → 고치는 것도 그대로 두는 것도 근거가 없습니다 |

**0.20을 고른 근거는 게이트 자신의 말입니다** — 81%를 "그런 게이트는 꺼집니다"로 기각했으니
상한은 그보다 훨씬 아래여야 합니다. 다섯 번에 한 번이면 사람이 빨간불을 잡음으로 읽기
시작하는 지점입니다. **판단이고, 판단이라고 적습니다.**

**사건이 0이어도 비율은 0이 아닙니다.** 95% 상한이 약 `3/n`이고(rule of three), 그 상한으로
`P`를 내야 "바를 통과했다"를 말할 수 있습니다.
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import UTC, datetime
from pathlib import Path

from evals import ledger

OUTPUT = Path("reports/stable_case_variance.json")
BASELINE = Path("baselines/eval_baseline.json")
PRIMARY = "answer_evidence_recall"
# 바: 이 위면 규칙이 변동으로 자주 터진다고 봅니다. 근거는 모듈 docstring에 있습니다.
TOLERABLE_RED_RATE = 0.20
# smoke는 가족마다 한 건씩 5건, full은 30건입니다.
POPULATIONS = (30, 5)


def red_rate(q: float, population: int) -> float:
    """회귀가 없어도 게이트가 빨개질 확률. `1 - (1-q)^N`."""
    return round(1.0 - (1.0 - q) ** population, 4)


def upper95(pairs: int, drops: int) -> float | None:
    """사건 0에 대한 비율의 95% 상한(rule of three). 사건이 있으면 None입니다."""
    if not pairs or drops:
        return None
    return 3.0 / pairs


def pairs_needed(population: int = max(POPULATIONS)) -> int:
    """`P(N) < 바`를 **사건 0으로** 보이려면 몇 쌍이 필요한가."""
    q = 1.0 - (1.0 - TOLERABLE_RED_RATE) ** (1.0 / population)
    return int(3.0 / q) + 1


def verdict(pooled: float, upper: float | None) -> str:
    """세 갈래. **상한이 바를 넘으면 "측정되지 않은 것"** 입니다."""
    if red_rate(pooled, max(POPULATIONS)) >= TOLERABLE_RED_RATE:
        return "rule_fires_on_variance"
    if upper is not None and red_rate(upper, max(POPULATIONS)) >= TOLERABLE_RED_RATE:
        return "not_resolved"
    return "within_tolerance"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="stable_case 규칙이 변동으로 터지는 비율을 잽니다."
    )
    parser.add_argument("--ledger", type=Path, default=ledger.LEDGER)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--min-runs", type=int, default=2,
                        help="이보다 적은 실행의 묶음은 버립니다. 1이면 하락이 보일 수 없습니다")
    parser.add_argument("--for-mode", default="compare",
                        help="게이트가 돌리는 모드. 다른 모드는 합산하지 않습니다")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    primary = PRIMARY
    if BASELINE.exists():
        primary = json.loads(BASELINE.read_text(encoding="utf-8")).get("primary_metric", PRIMARY)

    entries = [e for e in ledger.read(args.ledger) if e.get("primary_metric") == primary]
    shaped = ledger.groups(entries, min_runs=args.min_runs)
    counted = {k: g for k, g in shaped.items() if g["mode"] == args.for_mode}
    total_pairs = sum(g["eligible_pairs"] for g in counted.values())
    total_drops = sum(g["drops"] for g in counted.values())
    pooled = total_drops / total_pairs if total_pairs else 0.0
    upper = upper95(total_pairs, total_drops)
    needed = pairs_needed()

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "primary_metric": primary,
        "source": str(args.ledger).replace("\\", "/"),
        "tolerable_red_rate": TOLERABLE_RED_RATE,
        "for_mode": args.for_mode,
        "min_runs": args.min_runs,
        "ledger_entries": len(entries),
        "groups_counted": len(counted),
        "groups_seen": len(shaped),
        "pooled_eligible_pairs": total_pairs,
        "pooled_drops": total_drops,
        "pooled_drop_rate": round(pooled, 6),
        "pooled_drop_rate_upper95": round(upper, 6) if upper is not None else None,
        "red_rate_by_population": {str(n): red_rate(pooled, n) for n in POPULATIONS},
        "red_rate_upper95_by_population": (
            {str(n): red_rate(upper, n) for n in POPULATIONS} if upper is not None else None
        ),
        "pairs_needed_for_tolerance": needed,
        "pairs_short": max(0, needed - total_pairs),
        "verdict": verdict(pooled, upper),
        "groups": shaped,
        "note": (
            "묶음은 evals/ledger.py 한 곳에서 짓습니다 - 지문과 날짜와 모드와 top_k와 모델이 "
            "같은 실행만 한 묶음입니다. 지문 없는 줄은 버립니다(같은 코드 상태인지 말할 수 "
            "없습니다). 한 번만 돌린 묶음도 버립니다 - 거기서는 하락이 구조적으로 보일 수 "
            "없고 분모만 늘립니다. 에이전트가 멈춘 쌍은 세지 않습니다(규칙이 이미 건너뜁니다)."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    print(f"지표 {primary} / 장부 {len(entries)}줄 / 묶음 {len(shaped)}개 "
          f"(합산 {len(counted)}개, 모드 {args.for_mode})")
    by_mode: dict[str, int] = collections.defaultdict(int)
    for key, group in shaped.items():
        mark = "*" if group["mode"] == args.for_mode else " "
        by_mode[group["mode"]] += group["eligible_pairs"]
        print(f" {mark} {key}")
        print(f"      실행 {group['runs']}개  대상 쌍 {group['eligible_pairs']}  "
              f"하락 {group['drops']}")
    print(f"\n모드별 대상 쌍: {dict(by_mode)}")
    print(f"합산(모드 {args.for_mode}) q = {pooled:.4f}  ({total_drops}/{total_pairs})")
    for n in POPULATIONS:
        print(f"  P(N={n:2d}) = {red_rate(pooled, n):.4f}")
    if upper is not None:
        print(f"\n사건 0건이므로 비율 0이 아닙니다 - 95% 상한 q <= 3/{total_pairs} = {upper:.4f}")
        for n in POPULATIONS:
            print(f"  그 상한에서 P(N={n:2d}) <= {red_rate(upper, n):.4f}")
    print(f"\n바 {TOLERABLE_RED_RATE} -> 판정: {report['verdict']}")
    if report["pairs_short"]:
        print(f"  {report['pairs_short']}쌍 부족 "
              f"(필요 {needed}) — full 실행 {-(-report['pairs_short'] // 30)}회면 닿습니다")
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
