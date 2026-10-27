"""번역은 모델이 아니라 앱이 정합니다 — 영어 질문에 번역 턴을 쓰지 않습니다(ADR-087).

`translate_to_english`는 에이전트 도구였고, 지시문은 "질문에 한국어(또는 영어가 아닌 글자)가
있으면 먼저 부르라"고 했습니다. 그런데 **질문이 전부 영어인 평가에서 30문항 중 8~13개가 이
도구를 불렀습니다**(저장된 실행 다섯 번). 모델은 영어 질문 전체를 넘기고 같은 문장을 돌려받았습니다.
부를 때마다 번역 호출(p50 약 1.7초)과 모델 턴 하나가 더해지고, 어느 문항이 부를지는 실행마다
달랐습니다.

영어가 아닌 글자가 있는지는 모델에게 물을 일이 아닙니다. 코드가 셉니다.

- **라틴 문자가 아닌 글자**(한글·한자·가나·키릴 등)가 있으면 그래프가 에이전트 전에 번역하고,
  번역을 이번 턴의 메시지로 붙입니다. `route`가 경로 힌트를 붙이는 것과 같은 자리입니다.
- 없으면 아무것도 하지 않습니다. `Müller`처럼 악센트가 붙은 이름은 라틴 문자라 번역하지 않습니다.

번역 결과는 **도구 호출 기록과 같은 모양**으로 붙입니다(`by: "app"`). 화면의 "한국어→영어 번역"
줄과 번역 패널, 평가 하네스가 그 기록을 읽기 때문입니다(ADR-086의 관련 논문과 같은 방식).

이 모듈은 `chatbot`을 임포트하지 않습니다. 접속 없이 테스트합니다.
"""

from __future__ import annotations

import unicodedata
from typing import Any

TOOL = "translate_to_english"
HINT = (
    "English translation of the question above, made by the app. Use these English terms in "
    "Cypher strings and search_papers queries: {english}"
)


def needs_translation(text: str) -> bool:
    """라틴 문자가 아닌 글자가 하나라도 있는가. 숫자·기호·악센트 붙은 라틴 문자는 세지 않습니다."""
    return any(
        char.isalpha() and not unicodedata.name(char, "").startswith("LATIN") for char in text
    )


def hint(output: dict[str, Any]) -> str:
    return HINT.format(english=output["english"])


def call_record(question: str, output: Any, seconds: float) -> dict[str, Any]:
    """앱이 부른 번역을 도구 호출 기록 모양으로. 실패는 `error` 상태로 남깁니다."""
    failed = isinstance(output, BaseException)
    return {
        "id": f"app:{TOOL}", "name": TOOL, "by": "app",
        "args": {"text": question},
        "status": "error" if failed else "success",
        "output": {"error": str(output)} if failed else output,
        "seconds": round(seconds, 3),
    }
