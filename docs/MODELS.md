# 쓰는 모델과 쓰지 않는 모델

배포된 앱이 실제로 호출하는 모델, 학습해서 등록까지 했지만 앱에 넣지 않은 모델, 재서
기각한 모델을 한자리에 적습니다. **무엇을 쓰는지만큼 무엇을 왜 안 쓰는지가 이 저장소의
내용**이어서, 셋을 같은 표에 둡니다.

각 줄의 근거는 결정 기록([`docs/adr/`](adr/))과 실험 기록([`EXPERIMENTS.md`](../EXPERIMENTS.md))에
있습니다.

## 1. 배포된 앱이 호출하는 것 — 둘뿐입니다

| 모델 | 어디서 | 무엇을 | 조정 방식 |
|---|---|---|---|
| **`gpt-6-luna`** (OpenAI chat) | [`chatbot.py`](../chatbot.py) — 에이전트, `translate_to_english`, 번역 버튼 | 도구 다섯 개를 가진 에이전트. 구조화 출력 `GroundedAnswer`(answer + evidence_ids) | **가중치 조정 없음.** 조정 대상은 프롬프트이고 MLflow에 버전으로 남깁니다([ADR-018](adr/ADR-018-prompt-versioning.md)) |
| **`text-embedding-3-large`** (768차원) | 같은 파일 — `search_papers`의 벡터 검색 | **질문만** 임베딩합니다. 논문 벡터는 적재 때 만든 것을 다시 쓰고 새로 만들지 않습니다 | 차원을 적재와 같은 768로 고정. 파인튜닝은 재고 기각했습니다(4절) |

| **`graph-text-gbm`** (저장된 가중치 380KB) | [`forecast.py`](../forecast.py) — "앞으로 함께 인용될 논문" 패널 | 후보 50편을 재정렬합니다. **버튼을 눌러야 돕니다** | 학습은 오프라인. 앱은 지문을 맞춰 보고 읽기만 합니다([ADR-038](adr/ADR-038-the-forecast-panel.md)) |

**답변 경로에는 자체 학습 모델이 없습니다.** 모델이 쓰이는 자리는 **표시 패널 하나**이고,
에이전트 도구가 아니어서 게이트가 보는 지표에 닿지 않습니다. 그것이 결론이지 누락이
아닙니다.

## 2. 앱이 쓰는 "모델이 아닌 것"

관련 논문·제목 해석·경로 선택은 전부 질의와 규칙입니다. 각 자리마다 모델을 세워 보고
**측정으로 졌기 때문에** 이 모양입니다.

| 방법 | 어디서 | 파라미터 | 왜 이것인가 |
|---|---|---|---|
| **공동 인용자 수 정렬**(Cypher) | `rank_related_papers` 1단계 | `shared_citers DESC`, DB에서 `top_k × 4`만 | recall@5 <!--n:reports/related_study.json#summary.cocited.recall_at_k-->0.8892<!--/n-->로 PPR의 2.2배([ADR-030](adr/ADR-030-related-papers-needs-a-query-not-a-model.md)·[ADR-034](adr/ADR-034-measure-every-eligible-seed.md)) |
| **개인화 PageRank** | 같은 함수 2단계(보충), 분류별 추천 | damping 0.85 · 200회 · 허용오차 1e-11 | 공동인용이 다섯 칸을 못 채울 때만([`pagerank.py`](../pagerank.py)) |
| **Lucene 전문 검색** | `resolve_paper` | 모호성 간격 0.10 · 커버리지 0.8 · 최소 단어 3자 | `CONTAINS`가 비슷한 제목에서 엉뚱한 논문을 집었습니다([ADR-028](adr/ADR-028-resolve-titles-with-a-fulltext-index.md)) |
| **규칙 기반 라우팅** | [`routing.py`](../routing.py) | 어휘 단서 목록 | 모델 분류는 **기각** — 아끼려는 것이 토큰인데 분류가 토큰을 더 씁니다([ADR-024](adr/ADR-024-route-by-question-type.md)) |

## 3. 학습하고 등록까지 했지만 앱에 없는 것

| 모델 | 무엇 | 상태 | 하이퍼파라미터 |
|---|---|---|---|
| **`graph-text-gbm`** | `HistGradientBoostingClassifier`, 피처 12개(그래프 8 + 임베딩 코사인 · 분류 겹침 · 월 간격) | MLflow 레지스트리 **버전 1**, `production` 별칭 | `random_state=0` 외 **기본값**. 탐색하지 않았습니다 |
| **`graph-logistic`** | 그래프 피처 8개 | 저장만 | `StandardScaler` + `max_iter=2000` |

`graph-text-gbm`은 이 저장소에서 **학습 모델이 휴리스틱을 측정된 구간으로 이긴 첫 사례**
입니다 — 순위 MRR <!--n:reports/link_prediction.json#results.3.ranking_hard_negatives.mrr-->0.6513<!--/n-->,
최고 휴리스틱 `adamic-adar` 대비 +0.0818 [+0.0699, +0.0936]으로 구간이 0을 제외합니다
([ADR-031](adr/ADR-031-the-model-that-wins-is-the-forecast.md)).

**2026-10-19에 앱에 들어갔습니다 — 기존 패널을 고치는 것이 아니라 새 패널로**
([ADR-038](adr/ADR-038-the-forecast-panel.md)). 답하는 질문이 "**앞으로** 함께 인용될
논문"이고 관련 논문 패널의 "지금 같이 읽히는 논문"과 다른 질문이라, 섞지 않고 따로 둡니다.
에이전트 도구로 올리지는 않았습니다 — 답변 경로에 들어가면 게이트가 보는 지표가 움직이고,
그 판단은 기준선을 다시 재야 하는 일입니다.

> **승격은 "이 버전이 기준을 통과했다"까지이고 "앱이 이걸 쓴다"가 아닙니다.** 승격 조건은
> 둘입니다 — 최고 휴리스틱 대비 쌍체 구간이 0을 제외할 것, 순위 MRR이 0.60 이상일 것.
> 조건이 기준선에 없으면 통과가 아니라 **거부**합니다.

## 4. 재서 기각한 것 — 일곱 번

| 모델 | 어느 자리 | 결과 | 기록 |
|---|---|---|---|
| 선형 어댑터(인용 쌍 학습) | 관련 논문 | recall@5 0.1120 대 공동인용 0.8892 | [ADR-029](adr/ADR-029-ppr-beats-the-adapter-in-the-related-slot.md) |
| 선형 어댑터(공동인용 재학습) | 관련 논문 | 0.1064 — "목표가 달랐다"를 닫음 | ADR-030 |
| 로컬 임베딩(SPECTER 등) | 벡터 검색 | EXPERIMENTS 11-5 | — |
| GPU 전체 파인튜닝 | 임베딩 | EXPERIMENTS 11-6 | — |
| 선형 어댑터(미래 라벨) | 관련 논문, 그래프가 0점인 자리 | `vector` 대비 **다섯 지표 중 넷이 0 포함** | [ADR-036](adr/ADR-036-the-embedding-fills-what-the-graph-cannot.md) |
| **`graph-text-gbm`(재정렬기)** | 관련 논문, 미래 라벨 | 집계 <!--n:reports/related_future_study.json#summary.gbm.recall_at_k-->0.1861<!--/n-->로 **1위인데 양쪽 집단에서 다 짐** | [ADR-037](adr/ADR-037-the-gbm-wins-the-average-and-loses-both-halves.md) |
| **`BAAI/bge-m3`**(568M 다국어) | 임베딩, 간선 0개 자리 | OpenAI 대비 recall@5 <!--n:reports/embedding_slice_bge_m3.json#by_degree_slice.no_citation_edges.recall_at_k.mean-->-0.0334<!--/n-->, **작은 형제 `bge-base`보다도** <!--n:reports/embedding_slice_m3_vs_base.json#by_degree_slice.no_citation_edges.recall_at_k.mean-->-0.0232<!--/n--> (세 지표 모두 0 제외) | [ADR-046](adr/ADR-046-bge-m3-does-not-clear-the-bar.md) |

마지막 줄이 가장 조심스러운 기각입니다. 세 지표 모두 1위였고 **집단으로 나눈 뒤에야** 다수
집단(간선 0개, 80.8%)에서 자기가 품은 임베딩보다 나쁘고 둘째 집단에서 Cypher 한 줄보다
나쁘다는 것이 드러났습니다.

## 5. 조정 방식 — 다섯 가지 규율

**1. 격자 탐색은 검증에서만, 한 번만.** 어댑터의 ridge를 11-3절에서 격자로 고르고, 이후
실험에서는 **다시 탐색하지 않고 그 값을 씁니다**(`RIDGE_MULTIPLIER = 0.1`). 다시 고르면 평가
시드로 하이퍼파라미터를 고르는 셈입니다.

**2. 점 추정으로 올리지 않습니다.** 변동의 단위(질의·시드·순위 그룹)로 쌍체 부트스트랩을
돌리고, **구간이 0을 포함하면 "측정되지 않았다"**고 적습니다. 승격 조건에도 구간이 들어가
있습니다.

**3. 시간으로 자릅니다.** W1→W2로 학습하고 W3→W4로 평가합니다. 미래 라벨 과제에서는 평가용
그래프를 `feature_cut` 이전 인용자만으로 만들고, **자르기 전후를 테스트가 비교해** 누수가
실제로 닫혔는지 확인합니다(자르기 전에는 공동인용이 미래 정답을 전부 담고 있었고, 자른 뒤에는
0건입니다).

**4. 하이퍼파라미터보다 과제 정의를 먼저 봅니다.** 일곱 번의 기각 중 넷이 "모델이 나빠서"가
아니라 **정답의 정의를 데이터 질의가 그대로 계산할 수 있어서**였습니다. 그래서 모델을 세우기
전에 판별 기준 셋을 먼저 통과시킵니다(ADR-031).

| 기준 | 내용 |
|---|---|
| 1 | 라벨이 **데이터 질의로 계산되지 않아야** 합니다 |
| 2 | **양성이 충분해야** 합니다 |
| 3 | 배포 제약에 맞아야 합니다(torch 불가, Streamlit 690MB, Aura 관계 한도) |

**5. 조정한 것이 무엇인지 게이트가 압니다.** 이 표의 첫 줄처럼 가중치를 조정하지 않는 모델도
**프롬프트는 조정합니다.** 그 조정을 손으로 하다 심판 정확도 21/21 → 11/21을 냈고, 둘 다 고친
뒤에 알았습니다. 그래서 프롬프트 본문의 지문을 평가 보고서에 적고([ADR-018](adr/ADR-018-prompt-versioning.md)),
**게이트가 기준선과 대조합니다**([ADR-041](adr/ADR-041-the-gate-reads-the-prompt.md) — 적어 두기만
하고 보지 않던 칸입니다). 같은 선에서 하한의 근거도 "평균이 하한보다 큰가"가 아니라 **최악의
정상 실행과 심은 결함**으로 검사합니다([ADR-043](adr/ADR-043-the-floor-is-checked-against-the-worst-run.md)).

## 6. 측정을 반영한 것과 아직 아닌 것

**간선이 없는 논문의 패널은 비어 있었고, 2026-10-19에 채웠습니다**
([ADR-040](adr/ADR-040-fill-the-empty-panel.md)). 공동인용도 PPR도 점수를 내지
못하는 자리이고, 벡터로 채우면 상위 5 안에 나중에 함께 인용될 논문이
<!--n:reports/related_future_study.json#by_degree_slice.no_citation_edges.vector.hit_at_k-->0.4739<!--/n-->의
비율로 들어갑니다(ADR-036).

**근거가 대리 지표라는 사실은 그대로입니다** — 0.4739는 "열에 다섯이 나중에 같이 인용된다"이고
"읽을 만하다"가 아닙니다. 붙이는 쪽을 고른 이유는 하나입니다: **빈 화면보다 나쁠 수는
없습니다.** `ranked_by`에 `embedding`을 적어 무엇으로 뽑힌 논문인지 화면에서 구별됩니다.

**아직 아닌 것은 사용자 신호입니다.** 클릭·저장을 모으면 대리 지표가 아닌 것으로 다시 잴 수
있는데, 그 장치가 없습니다.

## 재현

```bash
uv run --extra ml python -m models.link_prediction.train          # 사다리 + 가중치 저장
uv run --extra ml python -m pipeline.model_registry --judge       # 승격 판정(네트워크 없음)
uv run python -m evals.related_study                               # 관련 논문 변형 비교
uv run python -m evals.related_study --cases evals/cases_related_future.json \
    --output reports/related_future_study.json                     # 미래 라벨 과제
```
