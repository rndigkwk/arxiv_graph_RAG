"""추적을 켜면 Langfuse에 observation이 몇 개 쌓이는지 셉니다.

    uv run python scripts/measure_trace_volume.py

[ADR-023](../docs/adr/ADR-023-what-to-trace.md)의 "무엇을 남기고 무엇을 버릴지"는 발생량을
모르면 정할 수 없습니다. dograg는 그 수를 **세지 않고** 켜 둔 채 테스트를 돌려서 한 달치를
태웠습니다. 그래서 켜기 전에 셉니다.

세는 방법은 추정이 아니라 **그래프를 실제로 돌려** 노드 수를 보는 것입니다
([`agent_graph.py`](../agent_graph.py)). 모델도 Aura도 부르지 않습니다 — 의존성을 전부
주입하므로 경로만 재현됩니다. `send_trace()`가 노드 하나당 span 하나와 루트 하나를 만들므로
`observation = 노드 수 + 1`입니다.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `agent_graph`를 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUTPUT = Path("reports/trace_volume.json")
# 평가 한 번의 사례 수. CI의 full 단계가 쓰는 파일에서 읽습니다
# (`.github/workflows/regression-eval.yml`의 `--cases`). 손으로 적은 30이 아니라,
# 사례가 늘면 같이 늘어야 하는 값입니다.
CASES_FILE = Path("evals/cases_graph.json")
# 주간 크론(일요일 22:00 UTC)이 한 달에 도는 횟수.
RUNS_PER_MONTH = 4


def paths() -> dict[str, dict[str, int]]:
    """How many observations each answer path produces."""
    from tests.test_agent_graph import make_deps, run

    shapes = {
        "clean": {"verdict": None},
        "revise_once": {"format_failures": 1},
        "abstain": {"format_failures": 2},
    }
    counted: dict[str, dict[str, int]] = {}
    for label, kwargs in shapes.items():
        state = run(make_deps(**kwargs)[0])
        nodes = len(state["trace"])
        # 루트 observation 하나 + 노드마다 span 하나.
        counted[label] = {"nodes": nodes, "observations": nodes + 1}
    return counted


def main() -> int:
    from pipeline.console import use_utf8_stdout
    from pipeline.tracing import BUDGET_KEY, DEFAULT_BUDGET, monthly_traces

    use_utf8_stdout()
    per_path = paths()
    cases = len(json.loads(CASES_FILE.read_text(encoding="utf-8"))["cases"])
    best = per_path["clean"]["observations"]
    worst = per_path["abstain"]["observations"]

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "cases": cases,
        "cases_file": str(CASES_FILE).replace("\\", "/"),
        "per_path": per_path,
        # 평가 한 번. 전부 깨끗한 경우와 전부 보류로 끝나는 경우의 범위입니다.
        "per_eval_run": {"best": cases * best, "worst": cases * worst},
        "weekly_cron_per_month": {
            "best": cases * best * RUNS_PER_MONTH,
            "worst": cases * worst * RUNS_PER_MONTH,
        },
        # 예산은 trace 단위(답변 하나 = trace 하나)입니다. observation이 아닙니다.
        "budget_key": BUDGET_KEY,
        "budget_window": "하루(UTC)",
        "default_budget_traces": DEFAULT_BUDGET,
        "default_budget_observations_worst": DEFAULT_BUDGET * worst,
        # **손잡이는 하루 단위인데 한도는 월 단위입니다.** 31을 곱한 값을 여기 적어 두면
        # 문서가 그 수를 손으로 베끼지 않아도 됩니다 — 베낀 수는 노드가 늘면 갈라집니다.
        "default_budget_monthly_traces": monthly_traces(DEFAULT_BUDGET),
        "default_budget_monthly_observations_worst": monthly_traces(DEFAULT_BUDGET) * worst,
        "note": (
            "추정이 아니라 그래프를 실제로 돌려 센 값입니다. 보내는 내용은 노드 이름·시도 "
            "번호·지연뿐이고 질문도 답변도 포함하지 않습니다(ADR-023)."
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
