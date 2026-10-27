"""관련 논문 순위는 모델이 아니라 앱이 부릅니다 — 모델 턴 하나를 줄입니다(ADR-086).

`rank_related_papers`의 결과는 화면의 **별도 표**에만 쓰입니다. 지시문이 그 이웃을 답 글에
적지 말고 근거 ID에도 넣지 말라고 했으니, 모델은 그 결과를 읽을 일이 없습니다. 그런데도 모델이
도구로 부르면 결과를 받아 읽는 턴이 하나 생깁니다. 저장된 30문항 중 17문항이 그 턴을 따로
썼고(`reports/model_turns.json`), 턴당 p50은 2.81초였습니다.

그래서 시드를 앱이 고릅니다. 모델이 따르던 지시문의 규칙과 같습니다.

- 질문이 제목으로 지칭한 논문(`resolve_paper`가 확정한 arxiv_id)이 있으면 그것.
- 없으면 근거 논문의 앞 3편.
- 근거가 없으면(순수 집계) 부르지 않습니다.

결과는 **도구 호출 기록과 같은 모양**으로 붙입니다(`by: "app"`). 화면의 시드 표시·예측 패널·
경로 캡션과 평가 하네스가 그 기록을 읽기 때문입니다.

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# 지시문이 "topic search 뒤에는 top 3 arxiv_ids로 시드하라"고 하던 수입니다.
SEED_LIMIT = 3
TOOL = "rank_related_papers"


def resolved_ids(calls: Sequence[dict[str, Any]]) -> list[str]:
    """제목으로 확정한 논문. 후보가 비슷해 모델이 골라야 했던 것(`ambiguous`)은 뺍니다."""
    ids = []
    for call in calls:
        output = call.get("output")
        if call.get("name") != "resolve_paper" or call.get("status") != "success":
            continue
        if not isinstance(output, dict) or output.get("ambiguous"):
            continue
        if isinstance(output.get("arxiv_id"), str) and output["arxiv_id"] not in ids:
            ids.append(output["arxiv_id"])
    return ids


def seeds(calls: Sequence[dict[str, Any]], evidence_ids: Sequence[str]) -> list[str]:
    """관련 논문의 시드. 빈 목록이면 부르지 않습니다."""
    return resolved_ids(calls) or list(dict.fromkeys(evidence_ids))[:SEED_LIMIT]


def call_record(seed_ids: Sequence[str], output: Any, seconds: float) -> dict[str, Any]:
    """앱이 부른 결과를 도구 호출 기록 모양으로. 실패는 `error` 상태로 남깁니다."""
    failed = isinstance(output, BaseException)
    return {
        "id": f"app:{TOOL}", "name": TOOL, "by": "app",
        "args": {"arxiv_ids": list(seed_ids)},
        "status": "error" if failed else "success",
        "output": {"error": str(output), "related": []} if failed else output,
        "seconds": round(seconds, 3),
    }
