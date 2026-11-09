# ADR-117: 아는 표 머리줄은 사전으로 옮긴다 — 모델은 표를 보지 않는다

- **상태:** 채택
- **날짜:** 2026-11-09

## 맥락

[ADR-111](ADR-111-translation-skips-table-rows.md)은 답을 한국어로 옮길 때 표의 행만 자리표시로 빼고, 머리줄과 구분선은
번역에 보냈습니다. "Title"이 "제목"이 되게 하려는 것이었습니다. 그런데 모델은 같은 머리줄을 실행마다 다르게 옮겼습니다.

- 같은 "Primary category"가 "주 카테고리"(ADR-111 측정)와 "1차 카테고리"(ADR-112 측정)로 옮겨졌습니다.
- 2026-11-08 운영 화면의 예시 4 답에서는 "Title / Primary category"가 영어로 남았습니다(ADR-114 머지 뒤 VM 기록).
- 표뿐인 답은 옮길 문장이 없어 모델을 부르지 않으므로, 머리줄이 늘 영어였습니다.

머리줄은 답 쓰는 에이전트가 지시문(`prompts.AGENT_SYSTEM`)의 "arXiv ID | Title | Primary category"에 칸 하나를 더해 만듭니다.
저장된 평가·측정 보고서의 표가 있는 답 458개 가운데 418개(91%)는 머리줄의 칸이 모두 아래 사전에 있습니다.

## 결정

- **`table_passthrough.HEADERS`에 머리줄 사전을 둡니다.** 대소문자를 가리지 않고 칸 전체가 같아야 합니다.
  - arXiv ID → arXiv ID, Title → 제목, Primary category → 주 분류, Category → 분류
  - Author(s) → 저자, Shared author(s) → 겹치는 저자, Citations·Citation count → 인용 수, Published → 발행일, PDF → PDF
- **머리줄의 칸이 모두 사전에 있으면 머리줄을 여기서 옮기고, 머리줄·구분선·행을 한 자리표시로 바꿉니다.**
  번역 모델은 표를 보지 않습니다.
- **사전에 없는 칸이 하나라도 있으면 지금처럼 머리줄을 번역에 보냅니다**("Relationship", "Why read it"처럼 질문마다 뜻이 다른 칸).
  설명 문장이 든 표는 지금처럼 통째로 번역합니다.
- **표뿐인 답도 사전 머리줄은 한국어가 됩니다.** 모델은 부르지 않습니다.
- 머리줄 바로 아래 자리표시 앞의 빈 줄만 지웁니다. 표 전체가 자리표시이면 문단과 표 사이의 빈 줄을 남깁니다.
  지우면 표가 앞 문단에 붙습니다.
- 번역 지시문(`TRANSLATE_TO_KOREAN`)은 바꾸지 않습니다.

## 측정

로컬에서 VM과 같은 번역기로, 저장된 답(5행 표, "arXiv ID | Title | Primary category")을 네 번 옮겼습니다.
네 번 모두 머리줄이 `| arXiv ID | 제목 | 주 분류 |`였고, 1.63~3.09초 걸렸습니다(첫 호출이 3.09초).
머리줄 두 줄이 번역에서 빠지므로 번역 입력은 조금 짧아집니다. 시간은 재지 않았고, 줄었다고 주장하지 않습니다.

## 고르지 않은 것

- **번역 지시문에 "머리줄은 이렇게 옮겨라"를 더하기.** 번역은 모든 한국어 질문의 길 위에 있어 지시문이 길어지면 모든 번역이
  그만큼 읽습니다([ADR-090](ADR-090-recent-is-a-date-filtered-search.md)이 일부 질문용 기능을 지시문 대신 앱에 둔 것과 같은 이유).
  지시문을 더해도 모델이 따르는지는 실행마다 다릅니다.
- **에이전트가 처음부터 한국어 머리줄을 쓰게 하기.** 영어 질문의 답도 같은 에이전트가 씁니다.

## 민감도

- **사전에 없는 머리줄은 지금처럼 모델이 옮깁니다.** 새 칸이 자주 보이면 사전에 더합니다.
- **`src/`가 바뀌어 머지하면 VM API가 다시 뜹니다**([ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md)).

## 참고

- [`src/table_passthrough.py`](../../src/table_passthrough.py) (`HEADERS`, `_known_header`), [`src/chatbot.py`](../../src/chatbot.py) (`translate_to_korean`)
- [`tests/test_table_passthrough.py`](../../tests/test_table_passthrough.py)
