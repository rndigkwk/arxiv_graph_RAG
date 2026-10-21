"""평가 실행마다 **사례별 지표 한 줄**을 남겨, 변동 질문이 답할 수 있게 쌓습니다.

    uv run python -m evals.ledger --backfill     # 저장소의 기존 보고서로 채웁니다
    uv run python -m evals.ledger --summary      # 묶음별로 몇 쌍 모였는지

[ADR-055](../docs/adr/ADR-055-the-stable-case-rule-is-not-resolved.md)가 이 자리를 만들었습니다.
`stable_case`가 변동으로 터지는지를 재려면 (사례, 실행) 쌍이 **405개** 필요한데 240개뿐이고,
**모자란 이유가 데이터를 버리고 있었기 때문**입니다 — `reports/regression_*.json`이
`.gitignore`에 있고 CI는 커밋을 되돌려 보내지 않습니다. 주간 크론이 full 실행마다 30쌍을
만들어 내는데 매번 사라집니다.

그래서 **"더 재자"가 아니라 "버리지 말자"** 입니다.

## 한 실행이 한 줄입니다

JSONL입니다. 실행마다 정확히 한 줄이 늘어나므로 **diff가 읽힙니다** — 보고서 전체를 커밋하면
수백 줄이 움직이고, 그러면 아무도 보지 않습니다.

담는 것은 **지표 값과 묶음을 가릴 열쇠**뿐입니다. 질문 문장도 답변 본문도 Cypher도 넣지
않습니다 — [ADR-023](../docs/adr/ADR-023-what-to-trace.md) 결정 3번과
[ADR-053](../docs/adr/ADR-053-the-log-needed-a-clock-and-a-field.md)이 그은 선과 같습니다.

| 칸 | 왜 |
|---|---|
| `generated_at` | 같은 실행을 두 번 세지 않는 열쇠 |
| `prompt_fingerprint` | **묶음을 가립니다.** 지문이 다르면 다른 프롬프트입니다(ADR-041) |
| `dataset` · `split` · `mode` · `top_k` · `model` · `primary_metric` | 묶음의 나머지 조건 |
| `cases` | 사례 → 지표 값. **멈춘 사례는 `null`** 입니다 |

**멈춘 사례를 `null`로 적는 이유**가 있습니다. 0.0으로 적으면 실패가 품질 하락으로 섞이고,
그건 `stable_case` 규칙이 이미 건너뛰는 채널입니다 — 섞으면 같은 변동에 두 번 벌점입니다.

`mode`가 없는 보고서는 **받지 않습니다.** `ragas_study_*`는 기존 실행을 다시 채점한
파생물인데 지표를 그대로 들고 오므로, 받으면 같은 실행을 두 번 세고 표본이 가짜로 늘어납니다.

## 지문 없는 줄은 묶을 수 없습니다

ADR-018 이전 보고서에는 `prompt_versions`가 없습니다. 그런 줄은 `prompt_fingerprint`가
`"none"`이고, 분석이 **버립니다**. ADR-055에서 그것을 섞어 판정이 한 번 뒤집혔습니다 —
2026-10-15의 네 실행이 사실 다른 코드 상태였습니다.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any

LEDGER = Path("reports/case_ledger.jsonl")
# ADR-055가 센 값입니다. 바 0.20을 사건 0으로 보이려면 이만큼 필요합니다.
PAIRS_NEEDED = 405
NO_FINGERPRINT = "none"


def fingerprint(prompt_versions: dict[str, Any] | None) -> str:
    """프롬프트 지문. 없으면 `"none"` — 묶을 수 없다는 뜻이고 분석이 버립니다."""
    if not prompt_versions:
        return NO_FINGERPRINT
    shaped = json.dumps(prompt_versions, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(shaped.encode("utf-8")).hexdigest()[:8]


def entry_from_report(report: dict[str, Any], *, primary: str) -> dict[str, Any] | None:
    """하네스 보고서 하나를 장부 한 줄로. 사례별 값을 못 뽑으면 None입니다."""
    from evals.gate import normalize

    if not isinstance(report.get("results"), list) or not report["results"]:
        return None
    shaped = normalize(report)
    cases = {
        case_id: (None if case["failed"] else case["metrics"].get(primary))
        for case_id, case in shaped["cases"].items()
    }
    # **그 지표를 담지 않은 보고서는 그 지표의 관측이 아닙니다.** `related_study`·
    # `rerank_study`·`title_resolution`도 `results`를 들고 있지만 사례별
    # `answer_evidence_recall`이 없습니다 - 받으면 장부가 전부 null인 줄로 찹니다.
    if not cases or all(value is None for value in cases.values()):
        return None
    metadata = report.get("metadata") or {}
    mode = report.get("mode") or metadata.get("mode") or ""
    # **`mode`가 없으면 `evals.run`이 만든 실행이 아닙니다.** `ragas_study_*`는 기존 실행을
    # 다시 채점한 파생물인데 `answer_evidence_recall`을 그대로 들고 옵니다 - 받으면 같은
    # 실행을 두 번 세고 표본이 가짜로 늘어납니다.
    if not mode:
        return None
    return {
        "generated_at": (report.get("generated_at") or metadata.get("timestamp_utc") or "")[:19],
        "prompt_fingerprint": fingerprint(shaped.get("prompt_versions")),
        "dataset": report.get("dataset") or metadata.get("cases_file") or "",
        # 분할은 사례 수로 가립니다 - 보고서가 따로 적어 두지 않습니다.
        "split": "full" if len(cases) >= 30 else "smoke",
        "mode": mode,
        "top_k": report.get("top_k") or metadata.get("top_k") or 0,
        "model": metadata.get("model") or "",
        "primary_metric": primary,
        "cases": cases,
    }


def key_of(entry: dict[str, Any]) -> tuple[str, str, str]:
    """같은 실행을 두 번 세지 않는 열쇠. 다시 채점한 보고서는 시각이 같습니다."""
    return (entry["generated_at"], entry["dataset"], entry["primary_metric"])


def read(path: Path = LEDGER) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            # 한 줄이 깨져도 나머지는 읽습니다. 장부가 통째로 못 쓰게 되면 안 됩니다.
            continue
    return entries


def append(entry: dict[str, Any] | None, path: Path = LEDGER) -> bool:
    """한 줄을 덧붙입니다. 이미 있는 실행이면 **아무것도 하지 않고** False입니다."""
    if entry is None:
        return False
    if any(key_of(existing) == key_of(entry) for existing in read(path)):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return True


def record(report: dict[str, Any], *, primary: str, path: Path = LEDGER) -> bool:
    """`evals.run`이 부르는 자리. 실패가 평가를 망치지 않게 **삼킵니다.**"""
    try:
        return append(entry_from_report(report, primary=primary), path)
    except (OSError, KeyError, TypeError, ValueError):
        # 장부를 못 쓰는 것이 평가의 실패는 아닙니다. 보고서는 이미 쓰였습니다.
        return False


def groups(
    entries: list[dict[str, Any]], *, min_runs: int = 2
) -> dict[str, dict[str, Any]]:
    """묶음별로 몇 쌍 모였는지. **지문 없는 줄은 버립니다**(ADR-055).

    `min_runs`가 2인 이유가 있습니다. **한 번만 돌린 묶음에서는 하락이 보일 수 없습니다** -
    모집단이 "적어도 한 번 만점"인 사례이므로, 한 실행에서 내려간 사례는 `max < 1.0`이 되어
    애초에 세지 않습니다. 그런 묶음은 **분모만 늘려** `q`를 낮게 치우치게 합니다.
    2는 하락이 관측 가능해지는 최소값입니다(ADR-055는 3을 썼습니다).
    """
    buckets: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for entry in entries:
        if entry.get("prompt_fingerprint") in (None, NO_FINGERPRINT):
            continue
        # **`mode`와 `top_k`가 열쇠에 들어갑니다.** `agent` 실행과 `compare` 실행은 같은
        # 것을 재지 않으므로 한 묶음에 들어가면 재는 것이 변동이 아니라 모드 차이입니다.
        #
        # **날짜도 들어갑니다.** 지문이 같아도 코드가 다를 수 있습니다 - 2026-10-18 라우팅
        # 변경은 프롬프트를 바꾸지 않았습니다(ADR-024). 보고서의 `git_sha`가 비어 있어
        # 코드 상태를 직접 가릴 수 없으므로 **날짜를 대리로** 씁니다. 거친 대리이고,
        # 거칠다고 적습니다 - 같은 날 두 상태가 있으면 못 가립니다.
        key = "|".join((
            entry["prompt_fingerprint"], entry["generated_at"][:10],
            entry.get("dataset", ""),
            entry.get("split", ""), entry.get("mode", ""), str(entry.get("top_k", "")),
            entry.get("model", ""), entry.get("primary_metric", ""),
        ))
        buckets[key].append(entry)
    shaped = {}
    for key, rows in sorted(buckets.items()):
        if len(rows) < min_runs:
            continue
        # 적어도 한 번 만점이었던 사례만 이 질문의 모집단입니다.
        values: dict[str, list[float]] = collections.defaultdict(list)
        for row in rows:
            for case_id, value in row["cases"].items():
                if value is not None:
                    values[case_id].append(value)
        eligible = {c: v for c, v in values.items() if v and max(v) >= 1.0}
        pairs = sum(len(v) for v in eligible.values())
        drops = sum(1 for v in eligible.values() for x in v if x < 1.0)
        shaped[key] = {
            "runs": len(rows),
            "eligible_pairs": pairs,
            "drops": drops,
            "mode": rows[0].get("mode", ""),
            "split": rows[0].get("split", ""),
            "dates": sorted({row["generated_at"][:10] for row in rows}),
        }
    return shaped


def main() -> int:
    parser = argparse.ArgumentParser(description="사례별 지표 장부를 채우고 봅니다.")
    parser.add_argument("--backfill", action="store_true",
                        help="reports/ 의 기존 보고서로 장부를 채웁니다")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--reports", type=Path, default=Path("reports"))
    parser.add_argument("--ledger", type=Path, default=LEDGER)
    parser.add_argument(
        "--min-runs", type=int, default=2,
        help="이보다 적은 실행의 묶음은 버립니다. 1이면 하락이 보일 수 없습니다",
    )
    parser.add_argument("--primary", default="answer_evidence_recall")
    parser.add_argument(
        "--for-mode", default="compare",
        help="합산할 모드. 게이트가 돌리는 것이 compare이므로 기본값입니다",
    )
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    if args.backfill:
        added = 0
        for path in sorted(args.reports.glob("*.json")):
            if path.name == args.ledger.name:
                continue
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if append(entry_from_report(report, primary=args.primary), args.ledger):
                added += 1
                print(f"  + {path.name}")
        print(f"장부에 {added}줄 추가 (총 {len(read(args.ledger))}줄)")

    if args.summary or not args.backfill:
        entries = read(args.ledger)
        shaped = groups(entries, min_runs=args.min_runs)
        # **모드를 섞어 더하지 않습니다.** 게이트의 `stable_case`는 `compare` 실행에서
        # 돌므로 그 모드만 합산합니다. 다른 모드는 표에 보이되 합에 들어가지 않습니다.
        counted = {k: g for k, g in shaped.items() if g["mode"] == args.for_mode}
        total = sum(g["eligible_pairs"] for g in counted.values())
        print(f"\n장부 {len(entries)}줄, 묶을 수 있는 묶음 {len(shaped)}개")
        for key, group in shaped.items():
            mark = "*" if group["mode"] == args.for_mode else " "
            print(f" {mark} {key}")
            print(f"      실행 {group['runs']}개  대상 쌍 {group['eligible_pairs']}  "
                  f"하락 {group['drops']}  {group['dates']}")
        print(f"* 표시가 합산 대상입니다(모드 {args.for_mode}).")
        print(f"합산 대상 쌍 {total} / 필요 {PAIRS_NEEDED}")
        if total < PAIRS_NEEDED:
            short = PAIRS_NEEDED - total
            print(f"  {short}쌍 부족 — full 실행 {-(-short // 30)}회면 닿습니다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
