# ADR-084: 답변 p50을 10초 안으로 — 먼저 VM에서 잰다

- **상태:** 채택
- **날짜:** 2026-10-27
- **후속:** 2026-10-28 — 목표를 맞췄습니다. VM 서버 p50 13.6초 → **7.70초**, p90 16.8초 → 10.87초(같은 15문항).
  조건 검사 겹치기([ADR-085](ADR-085-overlap-the-condition-check-with-the-answer.md)), 관련 논문·번역·제목 해석을 모델 턴 밖으로
  ([ADR-086](ADR-086-the-app-ranks-related-papers-not-the-model.md)·[087](ADR-087-translate-before-the-agent.md)·[088](ADR-088-resolve-the-named-paper-before-the-agent.md)).
  남은 가장 큰 대기는 판정 자체(p50 3.74초 중 2.36초를 기다림)입니다.

## 맥락

목표를 **p50 10초**로 정했습니다(2026-10-27). p90은 p50이 맞은 뒤에 봅니다.

지금 가진 지연 측정은 둘 다 지금 운영과 다릅니다.

- 로컬 측정([`inside_generate_2026-10-26.json`](../../reports/inside_generate_2026-10-26.json), 30문항)은
  **Aura**를 봤고, VM으로 옮기기 전입니다.
- VM 첫날([`vm_day1.json`](../../reports/vm_day1.json))은 질문 하나가 30~42초였습니다. 그때는 Streamlit이
  같은 VM에서 돌았고 스왑을 썼습니다([ADR-083](ADR-083-take-streamlit-off-the-vm.md)이 내리기 전).

지금 VM의 수를 모르는 채로 답 흐름을 고치면, 무엇이 효과였는지 가를 수 없습니다. 그래서 고치기 전에 잽니다.

## 결정

**운영 API에 로컬과 같은 질문을 보내 잽니다.**
[`scripts/measure_vm_answer_latency.py`](../../scripts/measure_vm_answer_latency.py)가
`evals/cases_graph.json`의 가족마다 앞 3문항(15문항)을 보내고, 응답의 `graph_trace`·`generate_trace`를
[`reports/vm_answer_latency.json`](../../reports/vm_answer_latency.json)에 남깁니다. 기준은
**서버가 잰 합계**입니다. 받는 쪽에서 잰 시간은 네트워크·SSE·Caddy의 몫을 보려고 같이 적습니다.
요청 사이는 21초를 띄웁니다(분당 3회 상한, ADR-081).

## 결과 (2026-10-27, 커밋 48af271)

| | p50 | p90 |
|---|---|---|
| 서버 합계 | <!--n:reports/vm_answer_latency.json#summary.server_seconds.p50-->13.6<!--/n-->초 | <!--n:reports/vm_answer_latency.json#summary.server_seconds.p90-->16.8<!--/n-->초 |
| 받는 쪽 | <!--n:reports/vm_answer_latency.json#summary.client_seconds.p50-->13.8<!--/n-->초 | <!--n:reports/vm_answer_latency.json#summary.client_seconds.p90-->16.8<!--/n-->초 |

- **VM이 로컬보다 빠릅니다.** 같은 15문항에서 로컬 p50
  <!--n:reports/vm_answer_latency.json#summary.vs_local.local_p50-->16.6<!--/n-->초, VM p50
  <!--n:reports/vm_answer_latency.json#summary.vs_local.vm_p50-->13.6<!--/n-->초입니다. 첫날의 30~42초는
  Streamlit과 스왑 때문이었고, 지금은 아닙니다. **VM을 고칠 일은 없습니다.**
- **네트워크는 거의 0입니다.** 받는 쪽과 서버의 차이가 p50에서 0.3초 안팎입니다.
- **시간은 두 노드에만 있습니다.** `generate`와 `check_conditions`입니다. 나머지 넷은 0초입니다(ADR-069와 같은 모양).
  - `generate` p50 9.0초: 모델 턴 평균
    <!--n:reports/vm_answer_latency.json#summary.inside_generate.model_turns_mean-->3.53<!--/n-->턴 × 턴당
    p50 <!--n:reports/vm_answer_latency.json#summary.inside_generate.seconds_per_turn_p50-->2.27<!--/n-->초입니다.
    모델이 `generate`의 <!--n:reports/vm_answer_latency.json#summary.inside_generate.model_share-->0.889<!--/n-->를
    차지하고, 도구는 <!--n:reports/vm_answer_latency.json#summary.inside_generate.tools_share-->0.109<!--/n-->입니다.
    VM의 Neo4j 조회(`search_graph` p50 0.24초)는 문제가 아닙니다.
  - `check_conditions` p50 4.6초: 합계의 35%이고, 모델 호출 하나입니다.
- 15문항 모두 답했고, 보류와 오류는 없었습니다.

**목표까지 3.6초가 남습니다.** 줄일 자리는 모델 턴과 조건 검사뿐입니다.

## 다음에 할 것

| 수 | 줄어드는 몫(추정) | 근거 |
|---|---|---|
| ① 조건 검사를 마지막 턴(답 쓰기)과 겹친다 | 약 2.3초 = 마지막 턴 하나 | 조건 검사는 질문과 후보 논문만 봅니다. 조회 결과가 오면 시작할 수 있습니다. 검사는 지우지 않습니다(ADR-069) |
| ② 모델 턴 하나를 줄인다 | 약 2.3초 = 턴당 p50 | 그래프 조회와 관련 논문 순위를 한 턴에 부르게 하거나, 번역을 에이전트 밖으로 뺍니다 |

①만으로는 약 11초라 모자라고, **둘을 합쳐야 약 9초**입니다. 둘 다 프롬프트나 답 흐름을 바꾸므로 머지 전에
유료 평가 한 번으로 품질을 확인하고 기준선을 다시 씁니다(ADR-041).

①은 겹치는 만큼만 줄입니다. 조건 검사(4.6초)가 마지막 턴(2.3초)보다 길어서, 검사 시간이 그대로 남습니다.

## 민감도

- **15문항입니다.** 가족마다 3문항이라 가족별 p50은 세 수의 가운데 값입니다. 가족끼리 비교하는 데 쓰지 않습니다.
- **영어 질문만 쟀습니다.** 평가 데이터셋이 영어라서입니다. 한국어 질문은 번역 도구가 한 번 더 돌아
  약 2초와 한 턴이 더해질 수 있습니다(로컬 측정에서 `translate_to_english` p50 1.97초). 화면의 예시 질문은
  한국어이므로, 사람이 쓰는 질문의 p50은 이보다 깁니다.
- **첫 부분 답 시각은 쓸 수 없습니다.** 요약의 `first_partial_seconds_p50`(0.08초)은 첫 `partial`
  이벤트의 시각입니다. 그 이벤트가 글자 없이 먼저 오므로 "첫 글자까지"가 아닙니다. 다음 측정에서는
  글자가 있는 첫 부분 답을 잽니다.
- **모델 쪽 속도가 바뀌면 수가 바뀝니다.** 시간의 89%가 모델 턴이라, OpenAI의 응답 속도가 하루 사이에 바뀌어도
  p50이 움직입니다. ①②를 넣은 뒤에도 같은 스크립트로 다시 잽니다.

## 참고

- [`scripts/measure_vm_answer_latency.py`](../../scripts/measure_vm_answer_latency.py), [`reports/vm_answer_latency.json`](../../reports/vm_answer_latency.json)
- ADR-065(노드별 분해), ADR-066(`generate` 안쪽), ADR-069(조건 검사는 유지)
