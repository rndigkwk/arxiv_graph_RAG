"""학습된 모델에 버전을 매겨 MLflow 레지스트리에 올리고, **기준을 통과할 때만** 별칭을 옮깁니다.

    uv run --extra ml python -m pipeline.model_registry --status
    uv run --extra ml python -m pipeline.model_registry --register
    uv run --extra ml python -m pipeline.model_registry --promote production
    uv run --extra ml python -m pipeline.model_registry --rollback production

[`prompt_registry.py`](prompt_registry.py)가 프롬프트에 대해 하는 일을 모델에 합니다
([ADR-018](../docs/adr/ADR-018-prompt-versioning.md)). 다른 점이 하나 있습니다 — 프롬프트는
이력만 남기면 되지만 모델은 **올릴지 말지를 판정해야** 합니다.

## 승격은 판정입니다

지표를 올리는 것만으로는 "이 버전을 쓴다"가 되지 않습니다. 기준선의 `model_registry` 항목이
조건을 적어 두고, 이 모듈이 **보고서를 읽어 그 조건을 검사**합니다. 통과하지 못하면 별칭을
옮기지 않고 사유를 돌려줍니다.

조건은 둘입니다([ADR-031](../docs/adr/ADR-031-the-model-that-wins-is-the-forecast.md)).

1. **최고 휴리스틱 대비 쌍체 구간이 0을 제외해야** 합니다. 점 추정으로 올리면 변동을 승격하는
   것입니다.
2. **순위 MRR이 하한 위여야** 합니다. 구간만 보면 "조금 나은 나쁜 모델"도 올라갑니다.

## 앱은 레지스트리를 조회하지 않습니다

`prompt_registry.py`와 같은 이유입니다 — 배포 앱이 추적 서버에 매이면 그 서버가 죽을 때 앱이
죽습니다([ADR-016](../docs/adr/ADR-016-stay-on-streamlit.md)의 메모리 실측도 그 가정 위에
있습니다). 레지스트리가 맡는 것은 **이력과 판정**이고, 앱이 쓰는 가중치는
`derived/link_prediction/`의 파일입니다. 둘을 잇는 것은 **지문**입니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.console import use_utf8_stdout
from pipeline.tracking import load_env

MODEL_NAME = "arxiv-cocitation-link-prediction"
ARTIFACTS = Path("derived/link_prediction")
REPORT = Path("reports/link_prediction.json")
BASELINE = Path("baselines/eval_baseline.json")
# 어느 사다리 칸을 올리는가. 측정에서 이긴 칸입니다.
RUNG = "graph-text-gbm"
# MLflow가 저장을 막는 타입. 우리 학습이 만든 파일이라 신뢰하지만, 보고된 것만 적습니다.
TRUSTED_SKLEARN_TYPES = [
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
]


def digest(path: Path) -> str:
    """가중치 파일의 지문. 레지스트리의 버전과 디스크의 파일을 잇습니다."""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def criterion() -> dict[str, Any]:
    """승격 조건. 기준선에 적혀 있고 여기서 읽습니다."""
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    return baseline.get("model_registry") or {}


def manifest(artifacts: Path = ARTIFACTS) -> dict[str, Any]:
    """무엇을 올리려 하는지. 네트워크를 쓰지 않습니다."""
    recorded = json.loads((artifacts / "manifest.json").read_text(encoding="utf-8"))
    weights = artifacts / f"{RUNG}.joblib"
    return {
        "model_name": MODEL_NAME,
        "rung": RUNG,
        "weights": str(weights).replace("\\", "/"),
        "digest": digest(weights) if weights.exists() else None,
        "snapshot_version": recorded["snapshot_version"],
        "split": recorded["split"],
        "metrics": recorded["metrics"].get(RUNG, {}),
    }


def judge(
    report: dict[str, Any] | None = None, *, path: Path = REPORT
) -> dict[str, Any]:
    """Does this model clear the bar recorded in the baseline?

    **통과하지 못하면 별칭을 옮기지 않습니다.** 게이트를 통과한 것만 올라간다는 규칙이
    말로만 있으면 규칙이 아닙니다.
    """
    rules = criterion()
    if not rules:
        # **조건이 없으면 통과가 아니라 실패입니다.** 기준선 항목이 빠진 채로 구간만 보고
        # 올리면 "게이트를 통과한 것만 승격한다"가 빈 문장이 됩니다. `EVAL_GATE_REQUIRED`가
        # 평가 파일이 없을 때 실패로 두는 것과 같은 이유입니다(ADR-017).
        return {
            "passed": False,
            "reasons": ["기준선에 model_registry 조건이 없습니다"],
            "criterion": {},
        }
    data = report or json.loads(path.read_text(encoding="utf-8"))
    row = next((r for r in data["results"] if r["model"] == RUNG), None)
    if row is None:
        return {"passed": False, "reasons": [f"보고서에 {RUNG}이 없습니다"]}

    interval = row.get("mrr_vs_best_heuristic") or {}
    mrr = (row.get("ranking_hard_negatives") or {}).get("mrr")
    floor = rules.get("ranking_mrr_floor")
    reasons = []
    if not interval:
        reasons.append("최고 휴리스틱 대비 구간이 없습니다")
    elif not interval.get("excludes_zero"):
        reasons.append(
            f"구간이 0을 포함합니다: [{interval.get('low')}, {interval.get('high')}]"
        )
    elif interval.get("mean", 0) <= 0:
        reasons.append(f"휴리스틱보다 낮습니다: {interval.get('mean')}")
    if mrr is None:
        reasons.append("순위 MRR이 없습니다")
    elif floor is not None and mrr < floor:
        reasons.append(f"MRR {mrr} < 하한 {floor}")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "observed": {"ranking_mrr": mrr, "vs_best_heuristic": interval or None},
        "criterion": rules,
    }


def _client():
    load_env()
    from mlflow.tracking import MlflowClient

    return MlflowClient()


def status() -> dict[str, Any]:
    """레지스트리에 무엇이 있고 별칭이 어디를 가리키는지."""
    client = _client()
    versions = []
    for found in client.search_model_versions(f"name='{MODEL_NAME}'"):
        # `search_model_versions`는 DagsHub에서 별칭을 비워 돌려줍니다. 버전을 하나씩 다시
        # 읽어야 별칭이 보입니다 - 검색 결과만 믿으면 "승격했는데 별칭이 없다"로 읽힙니다.
        version = client.get_model_version(MODEL_NAME, found.version)
        versions.append({
            "version": version.version,
            "aliases": list(getattr(version, "aliases", []) or []),
            "digest": (version.tags or {}).get("digest"),
            "snapshot_version": (version.tags or {}).get("snapshot_version"),
            "ranking_mrr": (version.tags or {}).get("ranking_mrr"),
        })
    versions.sort(key=lambda row: int(row["version"]))
    return {"model_name": MODEL_NAME, "versions": versions}


def register(
    message: str = "", *, artifacts: Path = ARTIFACTS, report: Path = REPORT
) -> dict[str, Any]:
    """Log the weights as a new registry version, with the judgement attached.

    **판정을 태그로 같이 올립니다.** 통과하지 못한 버전도 등록은 합니다 — 올린 기록이 없으면
    "왜 승격하지 않았는지"를 되짚을 수 없습니다. 막는 것은 별칭 이동입니다.
    """
    load_env()
    import joblib
    import mlflow

    about = manifest(artifacts)
    verdict = judge(path=report)
    weights = Path(about["weights"])
    if not weights.exists():
        raise FileNotFoundError(f"가중치가 없습니다: {weights}. train을 먼저 돌리세요.")
    model = joblib.load(weights)

    from pipeline.tracking import EXPERIMENT

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=f"register-{RUNG}"):
        mlflow.log_params({
            "rung": RUNG,
            "snapshot_version": about["snapshot_version"],
            "digest": about["digest"],
        })
        for key, value in (about["metrics"] or {}).items():
            if isinstance(value, (int, float)):
                mlflow.log_metrics({f"manifest.{key}": float(value)})
        mlflow.log_artifact(str(weights))
        logged = mlflow.sklearn.log_model(
            sk_model=getattr(model, "model", model),
            name=RUNG,
            registered_model_name=MODEL_NAME,
            # MLflow가 `HistGradientBoosting`의 저장을 막습니다 - `TreePredictor`가 경계
            # 검사 없는 노드 인덱스를 담아서, 조작된 파일로 `predict()`를 부르면 프로세스가
            # 죽을 수 있습니다. 여기서 올리는 파일은 **이 저장소의 학습 실행이 방금 만든
            # 것**이므로(`models.link_prediction.train`) 신뢰합니다. 보고된 그 타입 하나만
            # 적습니다 - 전부 신뢰 목록에 넣는 것이 이 경고를 없애는 방법이 되면 안 됩니다.
            skops_trusted_types=TRUSTED_SKLEARN_TYPES,
        )

    client = _client()
    newest = max(
        client.search_model_versions(f"name='{MODEL_NAME}'"), key=lambda v: int(v.version)
    )
    for key, value in {
        "digest": about["digest"],
        "snapshot_version": about["snapshot_version"],
        "ranking_mrr": str((about["metrics"] or {}).get("ranking_mrr")),
        "promotion_passed": str(verdict["passed"]),
        "promotion_reasons": "; ".join(verdict["reasons"]) or "-",
    }.items():
        client.set_model_version_tag(MODEL_NAME, newest.version, key, value)
    if message:
        client.update_model_version(MODEL_NAME, newest.version, description=message)
    return {"version": newest.version, "uri": logged.model_uri, "verdict": verdict}


def promote(
    alias: str, version: str | None = None, *, report: Path = REPORT, force: bool = False
) -> dict[str, Any]:
    """Move the alias, but only when the judgement passes.

    `force`는 **시연용**입니다 - 게이트를 우회해 나쁜 버전이 올라간 상황을 만들어야 롤백을
    보일 수 있습니다. 사람이 손으로 별칭을 옮기는 일은 실제로 일어나고, 그래서 롤백이
    필요합니다(ADR-032).
    """
    client = _client()
    verdict = judge(path=report)
    if not verdict["passed"] and not force:
        return {"moved": False, "alias": alias, "verdict": verdict}
    target = version or max(
        (v.version for v in client.search_model_versions(f"name='{MODEL_NAME}'")),
        key=int,
        default=None,
    )
    if target is None:
        return {"moved": False, "alias": alias, "verdict": {
            "passed": False, "reasons": ["등록된 버전이 없습니다"],
        }}
    previous = None
    try:
        previous = client.get_model_version_by_alias(MODEL_NAME, alias).version
    except Exception:  # noqa: BLE001 - 별칭이 아직 없는 첫 승격입니다.
        previous = None
    client.set_registered_model_alias(MODEL_NAME, alias, target)
    return {"moved": True, "alias": alias, "from": previous, "to": target,
            "forced": force, "verdict": verdict}


def rollback(alias: str) -> dict[str, Any]:
    """Point the alias at the previous version, whatever the judgement says.

    롤백에는 판정을 걸지 않습니다. 나쁜 버전이 올라간 뒤에 되돌리는 것이 롤백이고, 그때
    판정을 요구하면 되돌릴 수 없습니다.
    """
    client = _client()
    versions = sorted(
        client.search_model_versions(f"name='{MODEL_NAME}'"), key=lambda v: int(v.version)
    )
    if len(versions) < 2:
        return {"moved": False, "reason": "되돌릴 이전 버전이 없습니다"}
    try:
        current = client.get_model_version_by_alias(MODEL_NAME, alias).version
    except Exception:  # noqa: BLE001
        return {"moved": False, "reason": f"별칭 {alias}가 없습니다"}
    earlier = [v.version for v in versions if int(v.version) < int(current)]
    if not earlier:
        return {"moved": False, "reason": f"{current}보다 이전 버전이 없습니다"}
    target = max(earlier, key=int)
    client.set_registered_model_alias(MODEL_NAME, alias, target)
    return {"moved": True, "alias": alias, "from": current, "to": target}


def main() -> int:
    parser = argparse.ArgumentParser(description="학습 모델의 버전·별칭을 다룹니다.")
    parser.add_argument("--manifest", action="store_true",
                        help="올릴 대상만 봅니다(네트워크 없음).")
    parser.add_argument("--judge", action="store_true",
                        help="승격 조건만 검사합니다(네트워크 없음).")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--register", action="store_true")
    parser.add_argument("--promote", metavar="ALIAS")
    parser.add_argument("--rollback", metavar="ALIAS")
    parser.add_argument("--version", help="--promote에서 특정 버전을 지정합니다.")
    parser.add_argument("--message", default="")
    parser.add_argument("--artifacts", type=Path, default=ARTIFACTS,
                        help="가중치 디렉터리. 시연에서 일부러 나쁜 모델을 가리킵니다.")
    parser.add_argument("--report", type=Path, default=REPORT,
                        help="판정에 쓸 학습 보고서.")
    parser.add_argument("--force", action="store_true",
                        help="판정을 무시하고 별칭을 옮깁니다. 롤백 시연용입니다.")
    args = parser.parse_args()

    use_utf8_stdout()
    if args.manifest:
        print(json.dumps(manifest(args.artifacts), ensure_ascii=False, indent=2))
        return 0
    if args.judge:
        verdict = judge(path=args.report)
        print(json.dumps(verdict, ensure_ascii=False, indent=2))
        return 0 if verdict["passed"] else 1
    if args.register:
        print(json.dumps(
            register(args.message, artifacts=args.artifacts, report=args.report),
            ensure_ascii=False, indent=2))
        return 0
    if args.promote:
        result = promote(args.promote, args.version, report=args.report, force=args.force)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["moved"] else 1
    if args.rollback:
        result = rollback(args.rollback)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["moved"] else 1
    print(json.dumps(status(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
