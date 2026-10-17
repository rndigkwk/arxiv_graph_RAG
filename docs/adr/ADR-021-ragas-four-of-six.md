# ADR-021: RAGAS 지표는 여섯 중 넷만 쓰고, 두 개는 게이트에 넣지 않는다

- **상태:** 채택
- **날짜:** 2026-10-17 (같은 날 판정 모델 교체·결함 심기·30건 전체로 세 번 보강)

## 맥락

`answer_correct`는 **문구 포함 검사**입니다. 답변이 정해진 문구를 담고 있는지만 보므로
완전성도 사실성도 판정하지 못합니다. Day 59 교안의 RAGAS 지표로 바꾸면 "검색이 문제인가
생성이 문제인가"를 나눠 말할 수 있습니다.

## 결정

**네 지표를 측정하고, 그중 둘만 게이트 후보로 둡니다.**

| 지표 | 측정 | 게이트 후보 | 근거 |
|---|---|---|---|
| Faithfulness | ✅ | ✅ | 결함을 심으면 1.000 → **0.000**으로 내려갑니다. 판정 모델에도 둔감 |
| Context Precision (without reference) | ✅ | ✅ | 가족 단위로 `recall_at_k`와 방향 일치(30건, 5/5 가족). 판정 모델 교체 20건 전부 일치. 단 **검색 지표가 아닙니다**(아래) |
| Answer Relevancy | ✅ | ❌ | **답변 형식을 재고 있습니다**(아래) |
| ~~Context Relevance~~ | **❌ 제거** | ❌ | **50건 전부 정확히 1.0000**(원본 30 + 결함 20). 아래 참고 |
| Context Recall | ❌ | — | 골드 ID로 `recall_at_k`를 정확히 셉니다. LLM 추정은 더 나쁜 측정입니다 |
| Answer Correctness | ❌ | — | 정답 답변이 필요합니다. 작성은 사람 몫입니다(ROADMAP 3c) |

### 진단 축은 "검색/생성"이 아니라 "답변 의존 여부"입니다

**처음 분류가 틀렸습니다.** `ContextPrecisionWithoutReference`를 검색 쪽에 넣었는데, 결함을
심어 보니 **문맥이 한 글자도 바뀌지 않은 상태에서 답변만 바꿔도 0.8286 → 0.0000** 이었습니다.
그 지표는 "각 문맥이 **그 답변에** 쓸모 있었는가"를 묻기 때문입니다.

| | 지표 |
|---|---|
| 답변과 무관 | `context_relevance`(질문·문맥만 받습니다), 골드 ID의 `recall_at_k`·`hit_at_k` |
| 답변에 의존 | `faithfulness`, `answer_relevancy`, **`context_precision`** |

검색만 보려면 위쪽을 봅니다. 이 수정이 없으면 "검색이 나빠졌다"고 읽고 검색기를 고치려
들었을 것입니다.

### 네 지표는 누락을 보지 못합니다

다섯 편 중 한 편만 답한 변형에서 **Faithfulness가 1.0000**입니다. 말한 것이 전부 문맥에 있으니
맞는 판정이고, 그래서 이 네 개로는 누락을 잡을 수 없습니다. Context Recall과 Answer
Correctness가 존재하는 이유이며, 그 둘을 쓰지 않는 대신 **완전성은 `recall_at_k`가 센다**는
선택이 여기서 근거를 얻습니다.

## 문맥에 조회문을 담습니다

이것이 이 ADR의 핵심 설계 결정입니다.

처음엔 Cypher가 돌려준 **행만** 문맥으로 넘겼고, `citing-01`의 **faithfulness가 0.0**이
나왔습니다. 답변은 정확했습니다 — 근거 recall 1.000, 다섯 편의 제목·분류가 행과 일치.

답변의 핵심 주장은 "이 논문들이 X를 **인용한다**"인데 **그 관계가 행에 없습니다.** 관계는
조회문에 적혀 있습니다. 모델은 자기가 쓴 질의와 그 결과를 함께 근거로 삼았고, 우리가 절반만
넘긴 것입니다. 조회문을 같은 문맥에 담자 **0.0 → 1.0**이 됐습니다.

**0.0은 모델의 잘못이 아니라 우리 입력 실수였습니다.** 그래서 문맥의 단위를 **조회 하나**로
정했습니다 — 질의문과 그 결과 행이 한 문맥입니다. 그렇게 묶어야 Context Precision의 "쓸모
있는 근거가 위에 있는가"가 뜻을 갖습니다.

ID로 초록을 되찾아 문맥이라고 넘기는 방식은 기각했습니다. 그래프 조건 질문에서 모델이 본 것은
초록이 아닙니다. 그래서 `evals.run`이 **그때 본 것을 그대로** 보고서에 적고(`contexts`),
`contexts`가 없는 옛 보고서는 **건너뛰고 그 사실을 적습니다.**

## Answer Relevancy를 게이트에 넣지 않는 이유

가장 낮은 `author-01`(0.346)의 답변은 **문장이 없는 Markdown 표**입니다. 이 지표는 답변에서
질문을 거꾸로 만들어 비교하므로 표만 있는 답변은 재료가 없습니다. 그런데 그 답변은
정확합니다(`recall_at_k` 1.0).

앱의 지시문은 **일부러** 표로 답하게 합니다(`prompts.AGENT_SYSTEM`: "When you list papers,
give a Markdown table"). 점수를 올리려면 지표를 위해 문장을 덧붙여야 하고, 그것은 제품이 아니라
**지표를 최적화하는 일**입니다. ADR-006·007에서 "재 보고 넣지 않는다"를 했던 것과 같은 판단입니다.

## 설치 대가

`ragas`는 `langchain_community.chat_models.vertexai`를 조건 없이 임포트하고, 그 모듈은
langchain-community 0.4에서 사라졌습니다. `langchain-community<0.4`로 내려 해결했고 앱의
`langchain` 1.3.16과 `langchain-core` 1.6.0은 그대로입니다.

`ragas==0.4.3`이 `instructor` 때문에 `openai`를 3.8.0 → 3.3.0으로 내립니다. 앱 요구사항
(`>=2.45`)은 만족하고 배포 환경은 dev 그룹을 설치하지 않으므로 운영 영향은 없지만, **로컬
테스트가 운영과 다른 `openai`로 돈다**는 뜻입니다. dev 그룹에만 두는 이유가 여기 있습니다
([ADR-016](ADR-016-stay-on-streamlit.md)의 앱 메모리 259MB도 같은 가정 위에 있습니다).

## 민감도

**표본은 30건이 됐지만 실행은 한 번입니다.** ADR-017에서 2회 실행으로 정한 허용폭이 3회차에서
깨졌습니다. 같은 함정을 피하려면 **같은 설정으로 여러 번** 돌려 변동폭을 재야 하므로, 아직
**임계값은 정하지 않았습니다.** 지금 말할 수 있는 것은 상한뿐입니다 — Faithfulness는 1.0이
아니어야 하고, Context Precision은 가족마다 분포가 완전히 다르므로(1.0000 대 0.2183) **전체
평균이 아니라 가족별로** 봐야 합니다.

**자기채점 편향은 쟀고, 보이지 않았습니다.** 같은 입력을 `gpt-6-sol`(같은 세대, 다른 계열)로
다시 채점했습니다. 5건 쌍체 비교입니다.

| 지표 | 바뀐 사례 | 평균 차이 (다른 판정 − 자기 판정) |
|---|---:|---:|
| Faithfulness | **0 / 5** | 0.0000 |
| Context Precision | **0 / 5** | 0.0000 |
| Context Relevance | **0 / 5** | 0.0000 |
| Answer Relevancy | 5 / 5 | **+0.0111** |

자기가 더 높게 줬다면 차이가 음수여야 하는데, 세 지표는 0이고 Answer Relevancy는 양수입니다.

**게이트 후보로 고른 둘이 마침 판정 모델에 둔감한 쪽입니다.** 그 셋은 속이 이산적이기
때문입니다 — Faithfulness는 받쳐진 주장의 비율, Context Precision은 이진 관련 판정의
precision@k입니다. Answer Relevancy만 임베딩 코사인을 쓰므로 연속값이고 조금씩 움직입니다.

~~**천장 효과를 적어 둡니다.**~~ **결함을 심어 해소했습니다.** 답변만 바꾼 20건을 두 판정
모델로 채점했더니, 두 모델이 `contradiction`·`off_topic`에서 **똑같이 0.0000**을 줬습니다.
자기가 만든 답을 자기가 채점해도 봉합해 주지 않습니다.

| 지표 | 바뀐 사례 | 평균 차이 (독립 판정 − 자기 판정) |
|---|---:|---:|
| Faithfulness | 3 / 20 | +0.0167 |
| Context Precision | **0 / 20** | 0.0000 |
| Context Relevance | 0 / 20 | 0.0000 |
| Answer Relevancy | 19 / 20 | −0.0222 |

갈린 것은 `fabricated_id`뿐이고(0.610 대 0.677) 주장 몇 개를 "받쳐지지 않음"으로 셀지의
차이입니다. 방향도 자기채점 쪽이 **더 낮습니다** — 편향의 반대입니다.

`gpt-6-sol`도 같은 공급자입니다. 진짜 독립 판정은 다른 공급자의 모델로 재야 합니다. 점수를
적을 때는 **반드시 판정 모델 이름을 함께** 적습니다.

**이전 세대 모델로는 못 쟀습니다.** `gpt-5.5`는 ragas/instructor가 보내는 `max_tokens`를
거부하고 `max_completion_tokens`를 요구합니다(400). 교안 6-5절의 "최상위 프론티어 모델로 기준을
잡는다"는 방법이 이 조합에서는 막힙니다.

~~**Context Relevance가 포화돼 있습니다.**~~ **조건이 충족돼 뺐습니다.** 원본 30건과 결함
20건, **50건 전부 정확히 1.0000**입니다. 우리 파이프라인에서 문맥은 항상 **그 질문으로 검색한
것**이므로 구조적으로 1에 붙습니다. 기본에서 끄고 `--with-context-relevance`로 다시 켭니다.

**Faithfulness를 1.0으로 게이트하면 맞는 답변이 막힙니다.** 30건 전체에서 평균이 0.9833인데,
`author-01` 한 건이 0.5000이기 때문입니다. 그 답변은 **정확하고 문맥에 받쳐집니다** — 제목·분류·ID가
모두 행과 일치하고 근거 recall도 1.000입니다. 행으로 직접 확인되지 않는 것은 **"두 편"이라는
집계**뿐이고, 모델이 행을 세어 만든 맞는 값입니다.

Faithfulness는 주장을 하나씩 대조하므로 **모델이 수행한 집계를 깎을 수 있습니다.** 같은 사례가
5건 실행에서는 1.0000이었는데(그때는 답변에 문장이 없었습니다) 문장이 붙자 0.5000이 됐습니다.
**임계값을 정할 때 1.0을 요구하면 안 됩니다.**

## 재현

```bash
# 판정 모델을 바꿔 쌍체 비교
uv run --group dev python -m evals.ragas_study --report reports/ragas_input_families.json   --model gpt-6-sol --compare-to reports/ragas_study.json   --output reports/ragas_study_judge_gpt-6-sol.json
```

```bash
# 문맥이 담긴 보고서를 만들고
uv run python -m evals.run --mode compare --cases evals/cases_graph.json \
  --case-ids author-01,citing-01,coauthor-01,reference-01,topcited-01 \
  --output reports/ragas_input_families.json
# 네 지표로 채점합니다
uv run --group dev python -m evals.ragas_study --report reports/ragas_input_families.json
```

## 참고

- [`EXPERIMENTS.md` 11-9절](../../EXPERIMENTS.md) · [`evals/ragas_study.py`](../../evals/ragas_study.py)
