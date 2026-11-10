# ADR-112: 번역도 추론을 끈다 — 더 작은 모델로 바꾸지는 않는다

- **상태:** 채택
- **날짜:** 2026-11-07

## 맥락

한국어 질문은 번역을 두 번 거칩니다([ADR-108](ADR-108-the-question-screen-shows-the-answer-first.md)).
서버가 질문을 영어 검색어로 옮기고([ADR-087](ADR-087-translate-before-the-agent.md)), 화면이 영어 답을
한국어로 옮깁니다. 2026-11-07 VM 측정(81a39c9)에서 질문 번역 노드는 p50 1.6초 안팎이었습니다. 한국어 답이
뜨기까지 p50은 11.24초로, 목표 10초를 넘었습니다.

두 번역은 같은 `translator`를 씁니다. 그 모델은 답과 같은 `gpt-6-luna`이고 추론 설정이 기본값입니다.
조건 판정([ADR-089](ADR-089-judge-without-reasoning.md))과 답 쓰는 에이전트([ADR-091](ADR-091-agent-without-reasoning.md))는
이미 추론을 끄고 정확도가 그대로였습니다. 번역만 남아 있었습니다.

## 결정

**`translator`의 추론을 끕니다**(`reasoning={"effort": "none"}`, Responses API). 모델은 그대로
`OPENAI_MODEL`입니다. 질문 번역과 답 번역이 함께 바뀝니다. 지시문은 바꾸지 않습니다.

## 측정

로컬에서 VM과 같은 OpenAI 모델을 불렀습니다(`reports/translate_reasoning_off.json`).

**질문 번역(한→영).** 문항은 `cases_korean.json`의 순수 한국어 13개와 화면 예시 7개입니다. 세 설정의
순서를 문항마다 돌렸습니다.

| 설정 | p50 | 최대 |
| --- | ---: | ---: |
| gpt-6-luna, 기본 추론 (지금) | 1.70초 | 5.88초 |
| **gpt-6-luna, 추론 끔** | **1.33초** | 3.66초 |
| gpt-4o-mini | 1.00초 | 1.24초 |

**검색이 같은가.** 한국어 검색 평가(`evals/language_study.py`, 61문항, hit@5) 결과입니다.

| 설정 | mixed 번역 hit | pure 번역 hit | pure 번역 MRR |
| --- | ---: | ---: | ---: |
| gpt-6-luna, 기본 추론 | 0.5246 | 0.5082 | 0.5000 |
| **gpt-6-luna, 추론 끔** | 0.5246 | 0.5082 | 0.5082 |
| gpt-4o-mini | 0.5246 | 0.4918 | 0.4918 |

**답 번역(영→한, [ADR-111](ADR-111-translation-skips-table-rows.md)의 표 행 건너뛰기 포함).** VM이 낸 답 3개를
대상으로 했습니다. 같은 답을 두 설정으로 번갈아 4번씩 옮겼습니다.

| 답 | 지금 p50 | 추론 끔 p50 |
| --- | ---: | ---: |
| 25행 표 (2,915자) | 2.46초 | 1.88초 |
| 5행 표 (590자) | 2.12초 | 1.43초 |
| 설명 열이 있는 표 (562자) | 2.46초 | 2.30초 |

자리표시가 깨진 적은 없었습니다. 번역문은 머리줄 낱말이 조금 달라졌습니다("주 카테고리" → "1차 카테고리").
설명 칸의 용어는 한국어로 옮겨졌습니다("graph neural networks" → "그래프 신경망").

**머지 뒤 VM에서 쟀습니다**(2026-11-08, 3ee8abb, `reports/vm_answer_latency_examples_3ee8abb.json`). 질문 번역 노드 p50은
1.6초 안팎에서 1.28초, 답 번역 p50은 2.30초에서 1.64초가 되었습니다. ADR-113·114와 같은 날 들어가 함께 잰 값입니다.

## 고르지 않은 것

- **질문 번역만 `gpt-4o-mini`로.** 가장 빨랐습니다(p50 1.00초). 그러나 "검색 증강 생성"을
  "search augmentation generation"으로 옮겨, RAG 문항의 적중을 잃었습니다. 질문 번역문은 검색어 그
  자체입니다. 0.33초를 더 얻으려고 용어를 잃지는 않습니다. 모델을 둘로 나누면 설정도 하나 늘어납니다.
- **답 번역만 추론을 끄기.** 질문 번역이 모든 한국어 질문의 앞길에 있어 더 큰 몫입니다. 둘을 나눌 이유가
  측정에서 나오지 않았습니다.

## 민감도

- **로컬 수치입니다.** 머지 뒤 `measure_vm_answer_latency.py --examples`로 VM의 질문 번역 노드와 한국어
  답이 뜨기까지를 다시 잽니다.
- **한국어 질문 세트는 모델이 만든 것입니다**(`language_study`의 한계). 실제 사용자의 한국어는 더 어렵습니다.
  추론을 끈 번역이 어려운 문장에서 더 약할 수 있습니다. 61문항에서는 차이가 없었습니다.
- **`OPENAI_MODEL`을 추론이 없는 모델로 바꾸면** `reasoning` 설정이 거부될 수 있습니다. 판정과
  에이전트도 같은 조건입니다.
- **`src/`가 바뀌어 머지하면 VM API가 다시 뜹니다**([ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md)).

## 참고

- [`src/chatbot.py`](../../src/chatbot.py) (`translator`, `TRANSLATE_REASONING`)
- [`reports/translate_reasoning_off.json`](../../reports/translate_reasoning_off.json)
- [ADR-089](ADR-089-judge-without-reasoning.md), [ADR-091](ADR-091-agent-without-reasoning.md) — 판정과 에이전트의 추론 끄기
