"""모델 시간이 어디서 나오는지 저장된 실행에서 가릅니다 — 턴 수, 턴당 초, 줄일 수 있는 턴.

    uv run python scripts/measure_model_turns.py

`generate`의 79.6%가 모델입니다(EXPERIMENTS 11-52). 줄일 곳이 있다면 거기뿐인데, 모델 턴
하나의 안쪽은 공급자가 알려 주지 않습니다. 그래서 **턴의 수**를 봅니다 - 도구를 부를 때마다
한 턴이 늘고, 턴마다 그동안의 대화 전체를 다시 읽습니다.

**줄일 수 있는 턴**은 `resolve_paper`로 기준 논문을 먼저 확정한 뒤 `search_graph`와
`rank_related_papers`를 따로 부른 문항입니다. 그 뒤로는 둘 다 기준 논문 하나만 있으면 되고 서로의
결과를 쓰지 않습니다. `citing-02`가 실제로 둘을 한 턴에 불렀습니다(그래서 도구 시간이 겹쳤습니다 -
ADR-077). `resolve_paper`가 없는 문항(저자·최다 피인용)은 `search_graph`가 찾은 논문을
`rank_related_papers`에 넣으므로 **순서가 필요합니다** - 세지 않습니다. 지금 도구 설명은
"search_graph나 search_papers로 먼저 확인한 arxiv_id만 넣으라"고 적어 순서를 시킵니다.
**절약 추정은 잰 값이 아닙니다.** 턴당 초의 중앙값을 곱한 것이고, 한 턴이 줄면 다음 턴의
입력이 그만큼 커집니다. 바꾸려면 유료 평가로 품질과 시간을 같이 재야 합니다.

0원입니다. 저장된 보고서만 읽습니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

REPORT = Path("reports/inside_generate_2026-10-26.json")
OUTPUT = Path("reports/model_turns.json")
PAIR = ("search_graph", "rank_related_papers")


def separate_pair(row: dict[str, Any]) -> bool:
    """기준 논문을 먼저 확정했고, 두 도구를 각자 다른 턴에 불렀습니다."""
    trace = row["generate_trace"]
    names = row.get("tool_names") or []
    if "resolve_paper" not in names or not all(name in names for name in PAIR):
        return False
    resolved = names.index("resolve_paper")
    after = all(names.index(name) > resolved for name in PAIR)
    return after and trace["tool_calls"] <= max(trace["model_turns"] - 1, 0)


def correlation(xs: list[float], ys: list[float]) -> float:
    return round(statistics.correlation(xs, ys), 2)


def summarize(report: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in report["results"] if row.get("generate_trace")]
    turns = [row["generate_trace"]["model_turns"] for row in rows]
    model = [row["generate_trace"]["model_seconds"] for row in rows]
    per_turn = [s / t for s, t in zip(model, turns, strict=True) if t]
    tokens_in = [row["chat_input_tokens"] or 0 for row in rows]
    tokens_out = [row["chat_output_tokens"] or 0 for row in rows]
    cached = sum(row["chat_input_tokens_cached"] or 0 for row in rows)

    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        families[row["category"]].append(row)

    separate = [row["id"] for row in rows if separate_pair(row)]
    seconds_per_turn = statistics.median(per_turn)
    return {
        "source": REPORT.as_posix(),
        "cases": len(rows),
        "turns": {
            "distribution": {str(k): v for k, v in sorted(Counter(turns).items())},
            "mean": round(statistics.fmean(turns), 2),
            "seconds_per_turn_p50": round(seconds_per_turn, 2),
            "seconds_per_turn_max": round(max(per_turn), 2),
        },
        # 상관은 30건에서 잰 것입니다. 어느 쪽이 원인인지 말하지 않습니다.
        "model_seconds_correlation": {
            "turns": correlation(turns, model),
            "input_tokens": correlation(tokens_in, model),
            "output_tokens": correlation(tokens_out, model),
        },
        "input_tokens_cached_share": round(cached / sum(tokens_in), 3),
        "by_family": {
            name: {
                "cases": len(group),
                "turns_mean": round(statistics.fmean(
                    r["generate_trace"]["model_turns"] for r in group), 2),
                "model_seconds_mean": round(statistics.fmean(
                    r["generate_trace"]["model_seconds"] for r in group), 1),
            }
            for name, group in sorted(families.items())
        },
        "separate_pair": {
            "tools": list(PAIR),
            "cases": len(separate),
            "ids": separate,
            # 잰 값이 아닙니다. 턴당 초의 중앙값 x 문항 수입니다.
            "estimated_seconds_per_case": round(seconds_per_turn, 2),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="모델 턴 수와 턴당 초를 요약합니다.")
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    summary = summarize(json.loads(args.report.read_text(encoding="utf-8")))
    summary["source"] = args.report.as_posix()
    args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
