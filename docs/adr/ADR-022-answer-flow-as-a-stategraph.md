# ADR-022: 답변 흐름을 StateGraph로 세우되, 행동은 바꾸지 않는다

- **상태:** 채택
- **날짜:** 2026-10-18

## 맥락

`ask()`는 이미 손으로 만든 CRAG였습니다 — 생성 → 검증 → 실패 시 1회 재작성 → 재검증. 2회 루프
안에 `try/except`가 들어 있어서 세 가지를 할 수 없었습니다.

1. **어느 단계가 느린지 말할 수 없었습니다.** "답변이 느리다"까지만 알 수 있었습니다.
2. **`condition_validation`이 보이지 않았습니다.** 계산돼서 응답에 담기기만 하고 화면에도
   추적에도 드러나지 않았습니다.
3. **보류가 경로가 아니었습니다.** 두 번 실패하면 예외만 올라갔습니다.

## 결정

Day 50~53 교안의 `StateGraph`로 흐름을 세웁니다
([`agent_graph.py`](../../agent_graph.py)). 노드는 일곱 개입니다.

```
START → generate → collect → check_format ─(ok)→ check_conditions ─(ok)→ accept → END
                                   │                      │
                                   └──(fail)──┬──(fail)───┘
                                              ↓
                                   attempt < 2 ? revise → generate
                                              : abstain → END
```

**행동은 바꾸지 않았습니다.** 같은 판정, 같은 횟수(한 번만 고쳐 봄), 같은 결과입니다.
`abstain`은 지금도 `AnswerValidationError`를 올립니다.

## 왜 보류에서 예외를 계속 올리는가

평가 하네스가 그 예외를 **"답하지 못함"으로 세고**, 게이트의 `answer_failure_rate` 기준선이
그 정의로 측정돼 있습니다([ADR-017](ADR-017-regression-gate.md)). 보류를 정상 반환으로 바꾸면
그 사례의 `failed`가 False가 되고, 품질 지표(완료 사례만 집계)에 0점이 섞여 **하한 0.99에
걸립니다.**

즉 사용자에게 보류 문장을 보여 주는 변경은 **기준선을 다시 재야 하는 별도 작업**입니다. 구조와
행동을 한 커밋에 섞지 않습니다 — 섞으면 회귀가 났을 때 어느 쪽 탓인지 말할 수 없습니다.

## 의존성 비용은 0입니다

`langgraph` 1.2.11이 **이미 설치돼 있습니다.** `langchain` 1.x의 `create_agent`가 그 위에
올라가 있어서 전이 의존성으로 들어옵니다. 그래서 [ADR-016](ADR-016-stay-on-streamlit.md)의 앱
메모리 실측 259MB에 영향이 없습니다.

## 바로 나온 측정값

노드별 지연이 `response["graph_trace"]`에, 그리고 평가 보고서의 행에 남습니다. 가족마다 한 건씩
5건(한 번 실행):

| 사례 | 총 | `generate` | `check_conditions` | 조건 판정 비중 |
|---|---:|---:|---:|---:|
| `author-01` | 24.7초 | 21.7초 | 3.0초 | 12% |
| `citing-01` | 10.2초 | 7.1초 | 3.1초 | 31% |
| `reference-01` | 13.6초 | 9.5초 | 4.1초 | 30% |
| `coauthor-01` | 14.1초 | 10.6초 | 3.5초 | 29% |
| `topcited-01` | 12.1초 | 6.5초 | 5.7초 | **47%** |

**복합 조건 검증이 체감 지연의 12~47%입니다.** `ROADMAP.md` 8-3절은 그 단계의 **토큰** 비용을
쟀는데, **지연** 비용은 지금까지 숫자가 없었습니다. 그리고 `collect`·`check_format`·`accept`는
전부 0.000초입니다 — 순수 검증 단계는 공짜이고, 비용은 LLM을 만지는 두 노드에만 있습니다.

> 5건 한 번 실행입니다. 임계값을 정할 표본이 아니고, 방향만 읽습니다.

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `ask()` 안에서 루프를 유지하고 타이머만 추가 | 세 번째 목표(보류를 경로로)가 안 됩니다. 그리고 단계 이름이 코드 주석에만 남습니다 |
| 에이전트까지 직접 `StateGraph`로 재구성 | `create_agent`가 이미 그래프입니다. 도구 선택·스트리밍을 다시 쓰면 바꾸는 범위가 커지고, 얻는 것은 없습니다 |
| 보류를 정상 반환으로 바꾸기 | 게이트 기준선이 무효가 됩니다(위) |

## 민감도

**`MAX_ATTEMPTS = 2`가 손으로 쓴 루프의 `range(2)`와 같은 값입니다.** 늘리면 토큰과 지연이
비례해 늘고, 실패율 기준선도 바뀝니다. 바꾸려면 재야 합니다.

`langgraph`가 전이 의존성이라는 점이 이 결정의 전제입니다. `langchain`이 그것을 떼어내면
명시 의존성으로 올려야 하고, 그때 앱 메모리를 다시 재야 합니다.

## 다음에 열리는 것

- **보류를 사용자에게 보여 주기** — 기준선 재측정과 묶어 별도로.
- **Langfuse 트레이싱**(ROADMAP 5절 Day 79) — 노드 경계가 생겼으므로 `trace`를 그쪽에 그대로
  보낼 수 있습니다. 지금은 노드 이름과 지연이 보고서에만 남습니다.

## 재현

[`tests/test_agent_graph.py`](../../tests/test_agent_graph.py)가 네트워크 없이 경로를
고정합니다 — 통과, 한 번 수정 후 통과, 두 번 실패 후 보류, 생성 미완(수정 대상이 아님),
수정 요청에 실패 사유가 실리는지, 과거 대화가 이번 턴의 근거에 섞이지 않는지.

```bash
uv run python -m evals.run --mode compare --cases evals/cases_graph.json \
  --case-ids author-01,citing-01,coauthor-01,reference-01,topcited-01 \
  --output reports/graph_restructure_smoke.json
uv run python -m evals.gate --report reports/graph_restructure_smoke.json --split smoke
```
