# ADR-113: 분류 코드와 주제를 함께 묻는 질문은 앱이 그 분류 안에서 먼저 찾는다 — 도구에 인자를 더하지 않는다

- **상태:** 채택
- **날짜:** 2026-11-07

## 맥락

화면 예시 "대규모 언어 모델 에이전트를 다루는 cs.CL 논문을 알려 줘"는 같은 질문인데 실행마다 크게
달랐습니다. VM에서 한 번은 모델 턴 2개(10.9초), 한 번은 5개(16.6초), 측정에서는 7개(19.9초)를 썼습니다.
같은 날 다른 측정에서는 빈 답이 왔습니다. 한국어 답 p90 22.67초가 이 질문 하나에서 나왔습니다.

트레이스를 보면 원인이 같습니다. 주제 검색(`search_papers`)에는 분류를 거르는 인자가 없습니다. 그래서
모델은 다음을 오가며 조회를 다시 씁니다.

- 벡터 검색 결과에서 분류를 손으로 거르기
- `toLower(p.abstract) CONTAINS "agent"` 같은 Cypher
- 분류 조건 `"cs.CL" IN p.categories`와 `p.primary_category = "cs.CL"` 사이

## 결정

**질문에 분류 코드가 하나 있고 주제 낱말이 남으면, 앱이 에이전트 전에 그 대표 분류 안에서 벡터 검색을
합니다**(`src/pre_category.py`, `category` 노드). 찾은 5편을 이번 턴의 메시지로 붙입니다. 메시지는
모델에게 두 가지를 시킵니다. 그 ID들로 `p.primary_category` 조회를 한 번 해 분류를 확인하고, 주제에 맞는
논문만 인용하라는 것입니다.

- **"최근"([ADR-090](ADR-090-recent-is-a-date-filtered-search.md))과 같은 자리, 같은 모양입니다.**
  지시문과 도구 스키마는 그대로입니다. 해당하지 않는 질문은 정규식 한 번만 거치고 노드를 지나갑니다.
- **순위·집계·저자 질문은 건너뜁니다.** "cs.AI 논문 중 피인용 많은 5편", "How many papers are in cs.CV?",
  "cs.CL papers by Yoshua Bengio"가 그런 질문입니다. 코드를 지우고 순위·집계 낱말을 지워도 남는 낱말이
  있어야 주제 질문으로 봅니다.
- **분류 코드가 둘이면 건너뜁니다.** 어느 쪽으로 거를지는 모델이 정합니다.
- **논문을 지칭했거나(`resolve`) "최근" 검색이 찾았으면 건너뜁니다.** 메시지를 둘 붙이면 두 힌트가 서로
  다른 조회를 시킵니다.
- **대표 분류로 거릅니다.** Cypher 규칙이 "대표 분류만 볼 때는 p.primary_category"라고 하고, 2턴으로
  끝난 실행도 그 조건을 썼습니다.

## 측정

로컬에서 main(4ddc1ba)과 이 브랜치를 두 프로세스로 **동시에** 돌렸습니다. 분류+주제 질문 6개를 3번씩
물었습니다(`reports/category_first_pair.json`).

| | main | 이 브랜치 |
| --- | ---: | ---: |
| 답한 실행 | 16/18 (보류 2) | **18/18** |
| p50 | 11.98초 | **8.35초** |
| p90 | 17.95초 | **13.30초** |
| 최장 | 101.12초 (모델 턴 26개) | 26.05초 (턴 2개, 첫 실행의 준비 시간) |
| 모델 턴 최대 | 26 | 5 |
| 다시 쓴 실행 | 4 | 2 |
| 근거 논문 수 | 0~25편 | 3~7편 |

main은 근거 0~1편으로 답한 실행이 다섯 번 있었습니다. "cs.CL에서 기계 번역 논문"은 한 번 `search_papers`를
40번 넘게 부르며 101초를 썼습니다.

**머지 뒤 VM에서 쟀습니다**(2026-11-08, 3ee8abb, `reports/vm_answer_latency_examples_3ee8abb.json`). 예시 4는 서버 19.91초
(턴 7개)에서 7.80초(턴 2개, `category` 노드 1.65초)가 되었습니다. 운영 화면에서 한 번 물었을 때도 8.1초였습니다.

## 고르지 않은 것

- **`search_papers`에 `category` 인자를 더하기.** 가장 곧은 고침입니다. 그러나 ADR-090에서 같은 도구에
  기간 인자를 더했을 때, 인자 하나와 지시문 다섯 줄이 **모든 질문의** 모델 턴을 0.5~1초 느리게
  했습니다(CI에서 main과 동시에 돌린 세 쌍). 일부 질문용 기능은 앱 노드로 둡니다.
- **화면 예시 문구를 바꾸기.** 예시는 빨라지지만, 사람들이 직접 치는 같은 모양의 질문은 그대로입니다.
- **분류 조건을 `IN p.categories`(부가 분류 포함)로.** 더 많이 걸리지만, 지시문 규칙과 어긋나 모델의
  확인 조회와 앱의 검색이 다른 조건을 쓰게 됩니다.

## 민감도

- **근거가 5편 안팎으로 줄었습니다.** 예전에는 `CONTAINS` 조회가 25편 표를 만들기도 했습니다. 분류 없는
  주제 질문(`search_papers` 기본 5편)과 같은 크기입니다. 더 보려면 이어 묻거나 탐색 화면을 씁니다.
- **주제 판별은 낱말 목록입니다.** 목록에 없는 순위 낱말("prominent")이 있으면 주제 질문으로 잘못 보고
  검색을 붙입니다. 메시지에 "If the question does not ask for {code} papers on a topic, ignore this
  message"를 넣었습니다. 틀리면 검색 한 번(0.5~1.9초)과 메시지 한 줄이 더해질 뿐입니다.
- **분류 안 검색은 전수 비교입니다.** neo4j-graphrag의 `filters`는 벡터 인덱스 대신 걸러진 노드 전부와
  코사인을 잽니다. 측정에서 노드 시간은 0.6~1.9초였습니다. cs.CL처럼 큰 분류에서 가장 깁니다.
- **회귀 평가(smoke)는 PR에서 돕니다.** 게이트 케이스에 분류+주제 질문이 적어 그 효과는 위 짝 측정으로
  봅니다.
- **`src/`가 바뀌어 머지하면 VM API가 다시 뜹니다**([ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md)).

## 참고

- [`src/pre_category.py`](../../src/pre_category.py), [`src/agent_graph.py`](../../src/agent_graph.py) (`category` 노드),
  [`src/chatbot.py`](../../src/chatbot.py) (`category_first`), [`tests/test_category_papers.py`](../../tests/test_category_papers.py)
- [ADR-090](ADR-090-recent-is-a-date-filtered-search.md) — 같은 모양의 "최근" 노드와 도구 인자를 기각한 측정
- [ADR-086](ADR-086-the-app-ranks-related-papers-not-the-model.md), [ADR-087](ADR-087-translate-before-the-agent.md),
  [ADR-088](ADR-088-resolve-the-named-paper-before-the-agent.md) — 앱이 에이전트 전에 하는 일
