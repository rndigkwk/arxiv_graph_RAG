"""프롬프트 본문에 버전을 매겨 MLflow 레지스트리에 올립니다.

    # 지금 코드의 프롬프트 지문을 적습니다 (네트워크 없음)
    uv run python -m pipeline.prompt_registry --manifest

    # 추적 서버에 버전으로 올리고 별칭을 옮깁니다
    uv run python -m pipeline.prompt_registry --register --alias production

프롬프트를 손으로 고치다 두 번 회귀를 냈고 **둘 다 고친 뒤에 재서** 알았습니다. 문제는 고친
것이 기록되지 않는다는 데 있었습니다 — 어느 문장이 어느 점수였는지가 제 기억에만 있었습니다.

**텍스트의 단일 출처는 `prompts.py`입니다.** 앱이 질문마다 레지스트리를 조회하게 만들지
않습니다. 그러면 배포 앱이 MLflow와 네트워크에 매이고, 추적 서버가 죽으면 챗봇이 죽습니다
(ADR-016의 메모리 실측 259MB도 그 가정 위에 있습니다). 레지스트리가 맡는 것은 **이력**입니다.

그래서 연결은 지문으로 합니다. 평가 보고서에 `prompt_versions`를 적어 두면, 어떤 점수가 어떤
프롬프트 본문에서 나왔는지 나중에 되짚을 수 있습니다. 지문이 다르면 다른 프롬프트입니다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from pipeline.console import use_utf8_stdout
from pipeline.tracking import EXPERIMENT, load_env
from src import prompts

MANIFEST = Path("reports/prompt_versions.json")
# 지문은 짧게 적습니다. 보고서에서 눈으로 비교할 수 있는 길이면 충분하고, 충돌을 막는 것이
# 목적이 아니라 "같은 글인가"를 묻는 것이 목적입니다.
DIGEST_LENGTH = 12


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def manifest() -> dict[str, Any]:
    """What the running code's prompts are, without touching the network."""
    return {
        name: {"sha256": digest(text), "chars": len(text)}
        for name, text in prompts.all_prompts().items()
    }


def register(alias: str = "production", message: str = "") -> dict[str, Any]:
    """Push each prompt to the MLflow prompt registry and move `alias` onto it.

    같은 본문을 다시 올리면 MLflow가 새 버전을 만듭니다. 그래서 이미 올라간 지문이면
    건너뜁니다 — 버전 번호가 커밋 수를 세는 것이 되면 이력으로 읽을 수 없습니다.
    """
    load_env()
    import mlflow
    from mlflow.exceptions import MlflowException

    results: dict[str, Any] = {}
    for name, text in prompts.all_prompts().items():
        fingerprint = digest(text)
        prompt_name = f"{EXPERIMENT}.{name.lower().replace('_', '-')}"
        try:
            existing = mlflow.genai.load_prompt(f"prompts:/{prompt_name}@{alias}")
        except (MlflowException, Exception):  # noqa: BLE001 - 없으면 처음 올리는 것입니다.
            existing = None
        if existing is not None and digest(str(existing.template)) == fingerprint:
            results[name] = {"sha256": fingerprint, "version": existing.version,
                             "action": "unchanged"}
            continue
        try:
            version = mlflow.genai.register_prompt(
                name=prompt_name,
                template=text,
                commit_message=message or f"{name} {fingerprint}",
                tags={"sha256": fingerprint, "source": "src/prompts.py"},
            )
            mlflow.genai.set_prompt_alias(prompt_name, alias=alias, version=version.version)
            results[name] = {"sha256": fingerprint, "version": version.version,
                             "action": "registered"}
        except Exception as exc:  # noqa: BLE001 - 호스트가 지원하지 않을 수 있습니다.
            results[name] = {"sha256": fingerprint, "error": f"{type(exc).__name__}: {exc}"[:200]}
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="프롬프트에 버전을 매깁니다.")
    parser.add_argument("--manifest", action="store_true", help="지문만 적습니다(네트워크 없음).")
    parser.add_argument("--register", action="store_true", help="추적 서버에 올립니다.")
    parser.add_argument("--alias", default="production")
    parser.add_argument("--message", default="")
    parser.add_argument("--output", type=Path, default=MANIFEST)
    args = parser.parse_args()
    use_utf8_stdout()

    if not (args.manifest or args.register):
        parser.error("--manifest 또는 --register 가 필요합니다.")

    report: dict[str, Any] = {"prompts": manifest()}
    if args.register:
        report["registry"] = register(alias=args.alias, message=args.message)
        report["alias"] = args.alias
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
