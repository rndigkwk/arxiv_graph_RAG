# ADR-114: 답의 한국어 번역을 흘려 보낸다 — 첫 조각부터 한국어로 보여 준다

- **상태:** 채택
- **날짜:** 2026-11-07

## 맥락

[ADR-108](ADR-108-the-question-screen-shows-the-answer-first.md)의 화면은 한국어 질문의 영어 답을
`/api/translate`로 옮깁니다. 이 길은 번역이 **다 끝나야** 한국어를 한꺼번에 돌려줍니다. 그동안 사람은
영어 원문과 "한국어로 옮기는 중…"을 봅니다.

[ADR-111](ADR-111-translation-skips-table-rows.md)이 긴 표 답의 번역을 7초에서 2초대로 줄였습니다. 그래도
한국어가 보이기까지는 번역 전체를 기다렸습니다. 답 쪽은 이미 흘려 보내고 있습니다(`/api/ask`의
`partial`). 번역만 한 덩어리였습니다.

## 결정

**`POST /api/translate/stream`을 둡니다.** SSE로 `partial {"text"}`(지금까지 옮긴 글 전체),
`final {"text"}`, `error`를 보냅니다. 화면은 첫 조각이 오면 한국어로 넘기고 이어서 갱신합니다.

- **상한은 `/api/translate`와 같은 것을 셉니다**(`translate_limiter`). 한 번의 번역은 어느 길로 와도 한
  번입니다.
- **표 자리표시는 화면에 비치지 않습니다.** 흘려 받는 중에는 도착한 `[[TABLE_n]]`만 표로 바꾸고, 끝에 걸린
  반쪽(`[[TAB`)은 잘라 둡니다(`table_passthrough.restore_partial`). 끝난 글은 지금과 같은 `restore`를
  거칩니다. 자리표시가 깨지면 전체를 다시 흘려 옮깁니다. 이때 화면 글은 처음부터 다시 자랍니다.
- **한국어로 넘기는 것은 첫 조각 한 번뿐입니다.** 받는 동안 "원문"을 누른 사람을 다시 끌어오지 않습니다.
- **번역이 끊기면 반쯤 옮긴 글을 지웁니다.** 화면은 영어 답과 "번역하지 못했습니다 · 다시 시도"로
  돌아갑니다.
- **API가 아직 흘려 보내지 않으면(404) 예전 길로 돌아갑니다.** Vercel 배포가 VM보다 먼저 끝나도 화면이
  깨지지 않습니다.
- `/api/translate`는 그대로 둡니다. 측정 스크립트와 Streamlit이 씁니다.

## 측정

로컬에서 VM이 낸 답 3개를 4번씩 흘려 받아 옮겼습니다(`reports/translate_stream_first_text.json`). "첫
글"은 화면에 한국어가 처음 보이는 때입니다. "끝"은 지금 화면이 한국어를 보여 주는 때입니다.

| 답 | 지금 번역기: 첫 글 / 끝 | 추론 끈 번역기([ADR-112](ADR-112-translation-without-reasoning.md)): 첫 글 / 끝 |
| --- | ---: | ---: |
| 25행 표 (2,915자) | 2.04초 / 2.75초 | **0.71초** / 1.48초 |
| 5행 표 (590자) | 1.59초 / 2.03초 | **0.63초** / 1.23초 |
| 설명 열이 있는 표 (562자) | 0.78초 / 2.01초 | **0.73초** / 1.92초 |

**지금 번역기에서는 흘려 보내도 첫 글이 늦습니다.** 모델이 먼저 추론하고 나서 글을 내기 때문입니다.
추론을 끈 번역기와 함께여야 첫 글이 0.7초 안팎이 됩니다. 두 변경은 서로 독립이라 어느 쪽을 먼저 머지해도
됩니다.

모든 실행에서 자리표시가 화면 조각에 비치지 않았습니다. 마지막 조각은 끝난 글과 같았고, 표 행은 모두
그대로 돌아왔습니다.

**머지 뒤 VM에서 쟀습니다**(2026-11-08, 3ee8abb). 스트림의 첫 조각은 0.76~0.91초, 끝은 1.5~1.6초였습니다. 재시작 직후
첫 호출은 1.84초였습니다. 조각은 하나씩 도착해 Caddy가 모아 두지 않습니다. 운영 화면에서 예시 4를 물었을 때
한국어 첫 글은 영어 답 1.0초 뒤(9.1초), 끝은 9.8초였습니다. 그 답의 표 머리줄은 이번에는 영어로 남았습니다("Title").
머리줄을 옮길지는 모델이 정합니다.

## 고르지 않은 것

- **`/api/translate`를 흘려 보내는 응답으로 바꾸기.** 측정 스크립트와 Streamlit이 JSON 한 덩어리를 읽습니다.
  길을 하나 더 두는 편이 깨지는 곳이 없습니다.
- **답을 흘려 받는 동안 문단 단위로 미리 번역하기.** 답의 문장은 판정 전이라 바뀔 수 있습니다. 번역 호출이
  문단 수만큼 늘어 상한도 빨리 찹니다.

## 민감도

- **로컬 수치입니다.** 머지 뒤 VM에서 첫 글이 뜨는 때를 다시 잽니다. Caddy는 `/api/*` 전체를
  `flush_interval -1`로 모아 두지 않으므로 질문과 같은 길로 흐릅니다.
- **SSE 연결 하나가 번역 동안 열려 있습니다.** 질문 스트림과 같은 모양이라 새 부담은 없습니다.
- **자리표시가 깨지는 드문 경우** 화면 글이 처음부터 다시 자랍니다. 측정에서는 일어나지 않았습니다.
- **`src/`가 바뀌어 머지하면 VM API가 다시 뜹니다**([ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md)).

## 참고

- [`src/api.py`](../../src/api.py) (`/api/translate/stream`), [`src/chatbot.py`](../../src/chatbot.py) (`translate_to_korean`의 `on_partial`),
  [`src/table_passthrough.py`](../../src/table_passthrough.py) (`restore_partial`)
- [`web/lib/api.ts`](../../web/lib/api.ts) (`translateStream`), [`web/components/Workbench.tsx`](../../web/components/Workbench.tsx)
- [`tests/test_api.py`](../../tests/test_api.py), [`tests/test_table_passthrough.py`](../../tests/test_table_passthrough.py), [`web/lib/translate.test.ts`](../../web/lib/translate.test.ts)
