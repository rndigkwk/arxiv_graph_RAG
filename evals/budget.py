"""예산이 바닥나 평가를 **돌리지 못한 것**을 품질 회귀와 구별합니다.

    uv run python -m evals.budget --explain      # 표식이 있으면 그 사유를 적습니다

2026-10-19에 CI의 `evaluate`가 빨갛게 떴습니다. 로그 40줄을 열어 보니 품질과 무관했습니다 —
OpenAI 프로젝트가 설정된 지출 한도에 닿아 첫 임베딩 호출이 429로 거부됐습니다. 화면에 보이는
것은 **품질이 회귀했을 때와 똑같은 빨간 X** 하나뿐이었습니다.

이 저장소는 이미 두 번 같은 구별을 했습니다.

| 구별 | 어디서 |
|---|---|
| 채점 함수가 죽은 것 ≠ 에이전트가 실패한 것 | `gate.scoring_error` vs `answer_failure_rate` |
| 프롬프트가 달라진 것 ≠ 점수가 내려간 것 | `gate.prompt_version` (ADR-041) |
| **평가를 돌리지 못한 것 ≠ 품질이 내려간 것** | 여기 (ADR-044) |

**통과로 바꾸지 않습니다.** 돌리지 못한 검사는 통과가 아닙니다. 바뀌는 것은 하나 — 왜
실패했는지가 로그 깊은 곳이 아니라 **요약 첫 줄에** 적힙니다.

## 일시적 429와 구별합니다

같은 HTTP 상태 코드가 두 가지를 뜻합니다. 분당 요청 한도(`rate_limit_exceeded`)는 **기다리면
풀리므로** 재시도가 맞는 대응이고, 이미 `neo4j_graphrag`와 `tenacity`가 그렇게 합니다. 예산
소진(`insufficient_quota` 계열)은 **기다려도 풀리지 않습니다** — 재시도는 시간만 씁니다.
그래서 예산 소진만 여기서 잡고, 일시적 한도는 건드리지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MARKER = Path("reports/eval_not_run.json")

# 0은 통과, 1은 보통의 실패입니다. 78을 따로 쓰는 이유는 워크플로가 **파일을 읽기 전에**
# 둘을 구별할 수 있게 하기 위함입니다(sysexits.h의 EX_CONFIG — "환경이 쓸 수 없는 상태").
EXIT_NOT_RUN = 78

# 공급자가 "예산이 없다"고 말하는 코드들입니다. 문자열로 보는 이유는 실제 예외가
# `tenacity.RetryError` -> `EmbeddingsGenerationError` -> `openai.RateLimitError`로 두 겹
# 싸여 오기 때문입니다 — 타입으로 잡으려면 감싸는 쪽을 전부 알아야 합니다.
EXHAUSTED_CODES = (
    "insufficient_quota",
    "project_spend_limit_exceeded",
    "billing_hard_limit_reached",
    "account_deactivated",
)


def _texts(exc: BaseException) -> list[str]:
    """This exception and everything it was raised from, as strings."""
    seen: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(seen) < 10:
        seen.append(str(current))
        code = getattr(current, "code", None)
        if isinstance(code, str):
            seen.append(code)
        body = getattr(current, "body", None)
        if isinstance(body, dict):
            seen.append(json.dumps(body, ensure_ascii=False))
        # tenacity는 실패한 Future를 들고 있고 그 안에 원래 예외가 있습니다.
        future = getattr(current, "last_attempt", None)
        if future is not None and getattr(future, "failed", False):
            inner = future.exception()
            if isinstance(inner, BaseException):
                seen.append(str(inner))
                seen.append(str(getattr(inner, "code", "")))
        current = current.__cause__ or current.__context__
    return seen


def exhausted(exc: BaseException) -> str | None:
    """The provider code when this means "the budget is gone", else None.

    일시적 한도(`rate_limit_exceeded`)에는 **None을 돌려줍니다.** 그것은 기다리면 풀리므로
    "돌리지 못함"이 아니라 보통의 실패입니다.
    """
    haystack = " ".join(_texts(exc))
    for code in EXHAUSTED_CODES:
        if code in haystack:
            return code
    return None


def record(code: str, exc: BaseException, *, stage: str, done: int = 0) -> dict[str, Any]:
    """Write the marker the workflow reads, and annotate the CI log.

    **어디까지 돌았는지 적습니다**(`done`). 0이면 한 건도 못 돌린 것이고, 그보다 크면 도중에
    떨어진 것이라 부분 결과가 보고서에 남아 있습니다 — 그 둘은 다시 돌릴 때 할 일이 다릅니다.
    """
    entry = {
        "not_run": True,
        "reason": "budget_exhausted",
        "provider_code": code,
        "stage": stage,
        "cases_done": done,
        "detail": f"{type(exc).__name__}: {exc}"[:500],
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    message = (
        f"평가를 돌리지 못했습니다(품질 회귀가 아닙니다): {stage}에서 예산이 바닥났습니다 "
        f"[{code}]. 돌린 사례 {done}건."
    )
    # GitHub가 이 줄을 잡아 검사 제목에 올립니다. 로컬에서는 평범한 줄로 보입니다.
    print(f"::error::{message}" if os.environ.get("GITHUB_ACTIONS") else message)
    return entry


def clear() -> None:
    """Drop a marker left by an earlier run, so a stale file cannot explain a new failure."""
    MARKER.unlink(missing_ok=True)


def explain(path: Path = MARKER) -> str:
    """The Markdown the workflow puts at the top of its step summary."""
    if not path.exists():
        return ""
    try:
        entry = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return "\n".join([
        "## 평가를 돌리지 못했습니다 — 품질 회귀가 아닙니다",
        "",
        f"- 단계: `{entry.get('stage')}`",
        f"- 공급자 코드: `{entry.get('provider_code')}`",
        f"- 돌린 사례: {entry.get('cases_done')}건",
        "",
        "지출 한도를 올리거나 다음 예산 주기를 기다린 뒤 이 작업을 다시 돌리세요. "
        "**게이트는 통과로 바뀌지 않습니다** — 돌리지 못한 검사는 통과가 아닙니다"
        "(ADR-044).",
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="평가를 돌리지 못한 사유를 적습니다.")
    parser.add_argument("--explain", action="store_true")
    parser.add_argument("--marker", type=Path, default=MARKER)
    args = parser.parse_args()
    if args.explain:
        print(explain(args.marker), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
