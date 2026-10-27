"""질문이 지칭한 논문을 앱이 먼저 찾습니다 — 제목 해석에 모델 턴을 쓰지 않습니다(ADR-088).

"X를 인용한 논문", "X가 인용한 논문 중 ...", "X와 공저자가 겹치는 논문"은 모델이 먼저
`resolve_paper`를 부르고, 결과를 받아 읽은 다음 턴에 Cypher를 씁니다. 그 한 턴이 VM에서 약
2.5초이고, p90을 만든 두 문항(ADR-087의 reference-01·coauthor-03)이 이 질문들입니다.

**질문 전체를 그대로 넘깁니다.** 제목을 잘라내는 규칙을 두면 평가 문장의 틀
("the paper titled X")에만 맞습니다. `resolve_paper`의 판정(`title_search.choose`)은 제목 쪽
적중률(`title_coverage`)을 보므로 덧붙은 낱말에 무너지지 않습니다.
VM에서 그대로 물어 본 결과(2026-10-27):

| 질문 | 맞게 특정 |
|---|---:|
| 평가 문장 그대로(citing·reference·coauthor) | 18/18 |
| 같은 제목, 다른 틀의 영어 문장 | 18/18 |
| 같은 제목, 한국어 문장 | 18/18 |
| 같은 제목, 전부 소문자 | 17/18 (1개는 `ambiguous`) |
| 제목이 없는 질문(저자·집계 12, 주제 10) | 잘못 특정 0 |

**확실할 때만 씁니다.** `arxiv_id`가 있고 `ambiguous`가 아닐 때만 힌트를 붙입니다. 아니면 아무것도
하지 않고, 모델이 지금처럼 `resolve_paper`를 부릅니다 - 도구는 남아 있습니다.

결과는 **도구 호출 기록과 같은 모양**으로 붙입니다(`by: "app"`). 관련 논문 시드(`related_by_app`)와
근거 허용 목록(`resolved`)이 그 기록을 읽습니다(ADR-086·087과 같은 방식).

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

from typing import Any

TOOL = "resolve_paper"
HINT = (
    "Paper named in the question, resolved by the app from its title: arxiv_id {arxiv_id} "
    "(\"{title}\"). Use this arxiv_id in the Cypher that follows and do not call resolve_paper "
    "for this paper. If the question is not about this paper, ignore this message."
)


def usable(output: Any) -> bool:
    """힌트로 쓸 만큼 확실한가. 실패·미발견·약한 일치·애매함은 모두 아닙니다."""
    return (
        isinstance(output, dict)
        and isinstance(output.get("arxiv_id"), str)
        and not output.get("ambiguous")
    )


def chosen_title(output: dict[str, Any]) -> str:
    for candidate in output.get("candidates") or []:
        if candidate.get("arxiv_id") == output["arxiv_id"]:
            return str(candidate.get("title") or "")
    return ""


def hint(output: dict[str, Any]) -> str:
    title = chosen_title(output).replace('"', "'")
    return HINT.format(arxiv_id=output["arxiv_id"], title=title)


def call_record(question: str, output: Any, seconds: float) -> dict[str, Any]:
    """앱이 부른 제목 해석을 도구 호출 기록 모양으로."""
    return {
        "id": f"app:{TOOL}", "name": TOOL, "by": "app",
        "args": {"title": question},
        "status": "success",
        "output": output,
        "seconds": round(seconds, 3),
    }
