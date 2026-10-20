"""품질 게이트와 기각 기록을 **한 화면에** 모읍니다. Streamlit을 가져오지 않습니다.

이 저장소의 가장 큰 자산은 측정 규율인데, 그것이 보고서 90여 개와 결정 기록 49건에 흩어져
있습니다. 처음 보는 사람이 2분 안에 확인할 방법이 없었습니다.

**새로 재지 않습니다.** 기준선 파일과 이미 커밋된 보고서를 읽어 모양만 바꿉니다 — 여기서
숫자를 만들면 그 숫자의 출처가 이 파일이 되고, 그러면 되짚을 수 없습니다.

`app.py`가 이 모듈을 불러 그립니다. 화면 코드와 읽기 코드를 나눠 두면 **접속 없이 테스트**할
수 있습니다(`abstain.py`와 같은 선).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASELINE = Path("baselines/eval_baseline.json")
RELATED_STUDY = Path("reports/related_study.json")
FUTURE_STUDY = Path("reports/related_future_study.json")
LINK_PREDICTION = Path("reports/link_prediction.json")
FLOOR_BASIS = Path("reports/ragas_floor_basis.json")

# 게이트가 올리는 위반 규칙. `evals/gate.py`의 표와 같은 순서입니다.
RULES = (
    ("cases", "기준선과 같은 사례 집합을 돌렸는가"),
    ("scoring_error", "채점 함수가 죽은 건이 없는가 (0이어야 함)"),
    ("floor", "지표가 절대 하한 이상인가"),
    ("drop", "기준선 대비 하락이 허용폭 이하인가"),
    ("stable_case", "기준선에서 만점이던 사례가 이번에도 통과하는가"),
    ("judge_model", "RAGAS를 기준선과 같은 모델이 채점했는가"),
    ("prompt_version", "답을 만든 프롬프트 본문이 기준선과 같은가"),
)

# 재서 기각한 모델. 각 줄의 숫자는 보고서나 결정 기록에 있는 값입니다.
REJECTED = (
    ("선형 어댑터 (인용 쌍 학습)", "관련 논문", "recall@5 0.1120 대 공동인용 0.8892", "ADR-029"),
    ("선형 어댑터 (공동인용 재학습)", "관련 논문", "0.1064 — '목표가 달랐다'를 닫음", "ADR-030"),
    ("로컬 임베딩 (SPECTER)", "벡터 검색", "recall@10 0.1296 대 0.2019", "EXPERIMENTS 11-5"),
    ("GPU 전체 파인튜닝", "임베딩", "넘지 못함", "EXPERIMENTS 11-6"),
    ("선형 어댑터 (미래 라벨)", "관련 논문", "벡터 대비 다섯 중 넷이 0 포함", "ADR-036"),
    ("graph-text-gbm (재정렬기)", "관련 논문", "집계 1위인데 양쪽 집단에서 다 짐", "ADR-037"),
    ("BGE-M3 (568M 다국어)", "임베딩",
     "간선 0개 집단에서 −0.0334, bge-base보다도 −0.0232", "ADR-046"),
    ("Qwen3-Embedding-0.6B (595M)", "임베딩",
     "−0.0073 (0 제외), bge-base와는 구별 안 됨 — 크기가 사는 것이 없음", "ADR-049"),
)


def load(path: Path) -> dict[str, Any] | None:
    """없으면 None. 보고서가 빠진 체크아웃에서도 화면이 떠야 합니다."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def lower_is_better(name: str) -> bool:
    """`evals/gate.py`와 같은 규칙입니다. 실패율은 낮을수록 좋습니다."""
    return name.endswith(("_failure_rate", "_errors"))


def headroom(name: str, measured: float, floor: float) -> float:
    """하한까지 남은 거리. 낮을수록 좋은 지표는 방향을 뒤집습니다."""
    return (floor - measured) if lower_is_better(name) else (measured - floor)


def gate_rows(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """지표마다 측정값·하한·허용폭·여유·변동폭을 한 줄로.

    **여유와 변동폭을 나란히 둡니다.** 여유가 변동폭보다 좁으면 그 하한은 품질이 아니라
    운을 재게 됩니다([ADR-004](docs/adr/ADR-004-quality-gate-thresholds.md)).
    """
    metrics = baseline.get("metrics") or {}
    floors = baseline.get("absolute_floor") or {}
    drops = baseline.get("allowed_drop") or {}
    spread = (baseline.get("source") or {}).get("observed_spread") or {}
    rows = []
    for name in sorted(set(metrics) | set(floors)):
        measured = metrics.get(name)
        floor = floors.get(name)
        rows.append({
            "metric": name,
            "measured": measured,
            "floor": floor,
            "allowed_drop": drops.get(name),
            "headroom": (
                round(headroom(name, measured, floor), 4)
                if measured is not None and floor is not None else None
            ),
            "observed_spread": spread.get(name),
            "gated": floor is not None,
        })
    return rows


def tight_floors(rows: list[dict[str, Any]]) -> list[str]:
    """여유가 측정된 변동폭보다 좁은 지표. 있으면 게이트가 변동으로 터집니다."""
    return [
        row["metric"] for row in rows
        if row["headroom"] is not None
        and row["observed_spread"] is not None
        and row["headroom"] < row["observed_spread"]
    ]


def slot_rows(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """평가 하네스 밖에서 지키는 선들. 기준선이 들고 있지만 게이트가 보지 않습니다."""
    rows = []
    for key, label in (("related_slot", "관련 논문 슬롯"), ("model_registry", "모델 승격")):
        entry = baseline.get(key)
        if not entry:
            continue
        rows.append({
            "key": key,
            "label": label,
            "metric": entry.get("metric") or entry.get("rung"),
            "variant": entry.get("variant"),
            "floor": entry.get("floor") or entry.get("ranking_mrr_floor"),
            "measured": (
                entry.get("measured") if isinstance(entry.get("measured"), (int, float))
                else (entry.get("measured") or {}).get("ranking_mrr")
            ),
            "checked_by": entry.get("checked_by"),
            "rationale": entry.get("rationale", ""),
        })
    return rows


def basis_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """하한이 **파국을 잡는지** 한 줄로. `evals.ragas_spread --floors`가 센 값입니다.

    하한에 필요한 질문은 "평균이 하한보다 큰가"가 아니라 둘입니다(ADR-004·ADR-043) —
    정상이던 실행 중 최악이 하한을 넘지 않는가, 그리고 결함을 심으면 걸리는가. 둘째가
    아니면 그 하한은 상수에 걸려 있고 게이트가 아닙니다.
    """
    if not report or not report.get("verdict"):
        return []
    return [
        {
            "metric": name,
            "floor": entry.get("floor"),
            "runs_seen": entry.get("runs_seen"),
            "worst_normal_run": entry.get("worst_normal_run"),
            "margin": entry.get("margin_to_floor"),
            "survives": entry.get("survives_the_worst_normal_run"),
            "with_defect": entry.get("with_a_planted_defect") or [],
            "defect_trips_it": entry.get("a_planted_defect_trips_it"),
        }
        for name, entry in sorted(report["verdict"].items())
    ]


def prompt_rows(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    """기준선이 잰 프롬프트와 **지금 코드의 프롬프트**를 나란히 둡니다.

    게이트의 `prompt_version` 규칙이 보는 값입니다(ADR-041). 지금 코드를 읽을 수 없는
    체크아웃에서도 떠야 하므로 `prompts.py`를 못 읽으면 기준선 쪽만 적습니다.
    """
    recorded = baseline.get("prompt_versions") or {}
    live: dict[str, str] = {}
    try:
        from pipeline.prompt_registry import manifest

        live = {name: entry["sha256"] for name, entry in manifest().items()}
    except Exception:  # noqa: BLE001 - 화면이 프롬프트 모듈 때문에 죽으면 안 됩니다.
        live = {}
    return [
        {
            "prompt": name,
            "baseline": recorded.get(name),
            "code": live.get(name),
            "same": (live.get(name) == recorded.get(name)) if live and recorded else None,
        }
        for name in sorted(set(recorded) | set(live))
    ]


def variant_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """변형 비교 보고서를 표로. 현직을 표시해 '무엇과 겨뤘는지'를 남깁니다."""
    if not report:
        return []
    incumbent = report.get("incumbent")
    summary = report.get("summary") or {}
    against = report.get("vs_incumbent") or {}
    rows = []
    for name, metrics in summary.items():
        interval = (against.get(name) or {}).get("recall_at_k") or {}
        rows.append({
            "variant": name,
            "deployed": name == incumbent,
            "recall_at_k": metrics.get("recall_at_k"),
            "hit_at_k": metrics.get("hit_at_k"),
            "reciprocal_rank": metrics.get("reciprocal_rank"),
            "vs_incumbent": (
                f"{interval['mean']:+.4f} [{interval['low']:+.4f}, {interval['high']:+.4f}]"
                if interval else ""
            ),
            "excludes_zero": interval.get("excludes_zero"),
        })
    return sorted(rows, key=lambda row: -(row["recall_at_k"] or 0))


def degree_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    """연결 정도로 나눈 결과. **집계만 보면 두 체제가 섞입니다**(ADR-037)."""
    if not report or not report.get("by_degree_slice"):
        return []
    labels = {
        "no_citation_edges": "간선 0개",
        "degree_1_to_4": "간선 1~4개",
        "degree_5_or_more": "간선 5개 이상",
    }
    rows = []
    for key, group in report["by_degree_slice"].items():
        row = {"group": labels.get(key, key), "seeds": group.get("seeds"),
               "share": group.get("share")}
        for variant in report.get("variants", []):
            metrics = group.get(variant)
            if isinstance(metrics, dict):
                row[variant] = metrics.get("recall_at_k")
        rows.append(row)
    return rows


def summary(baseline: dict[str, Any]) -> dict[str, Any]:
    """화면 맨 위에 둘 숫자들."""
    source = baseline.get("source") or {}
    rows = gate_rows(baseline)
    return {
        "generated_at": baseline.get("generated_at"),
        "runs": source.get("runs"),
        "dataset": source.get("dataset"),
        "cases": len(baseline.get("case_ids") or ()),
        "stable_cases": len(baseline.get("stable_cases") or ()),
        "gated_metrics": sum(1 for row in rows if row["gated"]),
        "judge_model": baseline.get("ragas_judge_model"),
        "metrics_split": baseline.get("metrics_split"),
        "tight_floors": tight_floors(rows),
        # 지금 코드의 프롬프트가 기준선을 만든 실행과 다르면, 아래 숫자들은 **이 코드가 낸
        # 점수가 아닙니다.** 화면 맨 위에서 그 사실을 먼저 말해야 합니다.
        "prompts_drifted": sorted(
            row["prompt"] for row in prompt_rows(baseline) if row["same"] is False
        ),
    }
