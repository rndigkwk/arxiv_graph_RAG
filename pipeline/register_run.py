"""다른 곳에서 돌린 실행의 보고서를 로컬 추적에 등록합니다.

    uv run python -m pipeline.register_run reports/embedding_finetune_colab.json
    uv run python -m pipeline.register_run reports/embedding_finetune_colab.json \
        --name colab-finetune

Colab 파인튜닝은 `report_to=[]`로 돌립니다. 그 VM 안에 MLflow를 켜면 `mlruns/`가 런타임과
함께 사라져서, 원격 추적 서버가 없는 동안은 기록이 아니라 착각만 남습니다. 대신 보고서
JSON을 받아 와 **여기서** 등록합니다. 실행 환경과 기록 위치를 분리하는 쪽이 사라지는 기계에서
안전합니다.

보고서 모양을 미리 정해 두지 않습니다. 중첩된 JSON을 훑어 숫자는 지표로, 짧은 문자열은
파라미터로, 긴 문자열은 버립니다. 이 저장소의 다른 보고서도 같은 명령으로 등록됩니다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pipeline.tracking import track

# MLflow의 파라미터 값 한도보다 넉넉히 아래에서 자릅니다. 긴 설명문은 지표도 파라미터도
# 아니므로 기록하지 않습니다.
MAX_PARAM_LENGTH = 250
SKIP_KEYS = frozenset({"note", "reading", "task_note", "limitation"})


def flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    """Walk the report and key every leaf by its path, so no shape has to be declared."""
    flat: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in SKIP_KEYS:
                continue
            flat.update(flatten(nested, f"{prefix}{key}."))
    elif isinstance(value, (list, tuple)):
        # 목록은 길이만 남깁니다. 원소까지 펼치면 지표 이름이 수십 개로 번집니다.
        flat[f"{prefix}count"] = len(value)
    else:
        flat[prefix.rstrip(".")] = value
    return flat


def split_leaves(flat: dict[str, Any]) -> tuple[dict[str, float], dict[str, str]]:
    """Numbers become metrics, short strings become params, long strings are dropped."""
    metrics: dict[str, float] = {}
    params: dict[str, str] = {}
    for key, value in flat.items():
        if isinstance(value, bool):
            metrics[key] = float(value)
        elif isinstance(value, (int, float)):
            metrics[key] = float(value)
        elif isinstance(value, str) and 0 < len(value) <= MAX_PARAM_LENGTH:
            params[key] = value
    return metrics, params


def main() -> int:
    parser = argparse.ArgumentParser(description="외부 실행 보고서를 추적에 등록합니다.")
    parser.add_argument("report", type=Path)
    parser.add_argument("--name", help="생략하면 파일 이름에서 정합니다.")
    parser.add_argument("--dry-run", action="store_true",
                        help="등록하지 않고 무엇이 기록될지만 보여 줍니다.")
    args = parser.parse_args()

    if not args.report.exists():
        parser.error(f"보고서가 없습니다: {args.report}")
    report = json.loads(args.report.read_text(encoding="utf-8"))
    name = args.name or args.report.stem.replace("_", "-")
    metrics, params = split_leaves(flatten(report))

    if args.dry_run:
        print(json.dumps({
            "name": name,
            "metrics": dict(sorted(metrics.items())),
            "params": dict(sorted(params.items())),
        }, ensure_ascii=False, indent=2))
        return 0

    with track(name) as run:
        run.log_params(params)
        run.log_metrics(metrics)
        # 보고서가 어디서 왔는지 남깁니다. 로컬에서 만든 것과 구별되어야 합니다.
        run.log_tags({"report_file": str(args.report), "registered_externally": "true"})
        run.log_artifact(args.report)

    print(json.dumps({
        "registered": name,
        "metrics": len(metrics),
        "params": len(params),
        "artifact": str(args.report),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
