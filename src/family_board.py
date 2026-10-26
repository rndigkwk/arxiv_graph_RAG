"""품질 게이트 탭의 분야별 표. 기준선을 만든 세 실행을 분야로 나눠 읽기만 합니다.

새 모듈인 이유: Streamlit Cloud는 머지 뒤 `src/` 모듈을 다시 불러오지 않습니다(ADR-072).
`quality_board`에 함수를 더하고 `app.py`에서 부르면 재부팅 전까지 `AttributeError`입니다.

**표본이 작다는 것을 표 안에 적습니다.** 분야마다 6문항이고 3회 실행이라 한 칸이 18번의
관측입니다. 6문항짜리 평균은 한 문항이 바뀌면 0.17씩 움직이므로, 수만 띄우지 않고 `n`을
같은 줄에 둡니다.

**검색·근거 지표는 다섯 분야 모두 1.0입니다.** 이 30문항이 분야끼리 가를 수 있는 것은
RAGAS뿐이고, 그중 `context_precision`은 두 분야(`ragas_stable_families`)만 게이트가 봅니다.
천장에 붙은 지표를 감추지 않고 그대로 보여 줍니다 - "분야별로 다 좋다"가 아니라 "이 문항으로는
분야를 가르지 못한다"가 읽혀야 합니다.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import fmean
from typing import Any

RUNS = tuple(Path(f"reports/baseline_resolve_run{i}_setmetrics.json") for i in (1, 2, 3))
RAGAS = tuple(Path(f"reports/baseline_resolve_run{i}_ragas.json") for i in (1, 2, 3))
EXACT = ("hit_at_k", "answer_evidence_recall")
JUDGED = ("faithfulness", "answer_relevancy", "context_precision")


def _load(paths: tuple[Path, ...]) -> list[dict[str, Any]] | None:
    try:
        return [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    except (OSError, ValueError):
        return None


def family_rows(runs: list[dict[str, Any]], ragas: list[dict[str, Any]],
                stable: list[str]) -> list[dict[str, Any]]:
    """분야마다 한 줄. 판정 지표는 실행별 분야 평균의 평균과 그 폭(최소~최대)입니다."""
    exact: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    cases: dict[str, set[str]] = defaultdict(set)
    for report in runs:
        for row in report["results"]:
            graph = row["graph_rag"]
            family = row["category"]
            cases[family].add(row["id"])
            exact[family]["failed"].append(1.0 if graph.get("failed") else 0.0)
            for name in EXACT:
                if graph.get(name) is not None:
                    exact[family][name].append(float(graph[name]))

    judged: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for report in ragas:
        for family, values in report["summary"]["by_family"].items():
            for name in JUDGED:
                if values.get(name) is not None:
                    judged[family][name].append(float(values[name]))

    rows = []
    for family in sorted(cases):
        row: dict[str, Any] = {
            "family": family,
            "cases": len(cases[family]),
            "runs": len(runs),
            "failure_rate": round(fmean(exact[family]["failed"]), 4),
            "gated_context_precision": family in stable,
        }
        for name in EXACT:
            values = exact[family][name]
            row[name] = round(fmean(values), 4) if values else None
        for name in JUDGED:
            values = judged[family][name]
            row[name] = round(fmean(values), 4) if values else None
            row[f"{name}_range"] = (
                (round(min(values), 4), round(max(values), 4)) if values else None)
        rows.append(row)
    return rows


def load_rows(stable: list[str]) -> list[dict[str, Any]] | None:
    runs, ragas = _load(RUNS), _load(RAGAS)
    if runs is None or ragas is None:
        return None
    return family_rows(runs, ragas, stable)


def at_ceiling(rows: list[dict[str, Any]]) -> bool:
    """검색·근거 지표가 모든 분야에서 1.0이고 실패가 없으면 참입니다."""
    return all(row[name] == 1.0 for row in rows for name in EXACT) and all(
        row["failure_rate"] == 0.0 for row in rows)
