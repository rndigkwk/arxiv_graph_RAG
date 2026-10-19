# 결정 기록 (ADR)

이 프로젝트가 **무엇을 쓰고, 무엇을 왜 안 쓰는지**를 한 장에서 답하기 위한 색인입니다.

[`advanced.md`](../../advanced.md)와 [`EXPERIMENTS.md`](../../EXPERIMENTS.md)에는 측정 과정이
서술로 남아 있습니다. 그 서술은 "어떻게 알았는가"에 답하지만 "그래서 지금 무엇을 쓰는가"에는
답하지 못합니다. ADR은 후자만 담고, 과정은 원문으로 링크합니다.

## 규칙

- **근거 없는 ADR은 쓰지 않습니다.** 측정값과 그 측정을 만든 스크립트 경로를 반드시 붙입니다.
- **뒤집힌 결정을 지우지 않습니다.** 상태를 `뒤집힘`으로 바꾸고 새 ADR이 무엇을 달리 측정했는지
  적습니다. 결론만 남기면 되짚을 수 없습니다.
- **민감도를 적습니다.** "어떤 가정이 바뀌면 이 결론이 바뀌는가". 한 수치에 매달린 결정과
  여러 측정이 같은 방향을 가리키는 결정은 신뢰도가 다릅니다.
- 설계를 바꿀 때 코드만 고치고 ADR을 빼놓지 않습니다.

## 상태

| 상태 | 뜻 |
|---|---|
| 채택 | 지금 코드가 이대로 돌아갑니다 |
| 기각 | 재 보고 넣지 않았습니다. 다시 제안될 때 이 문서가 답입니다 |
| 보류 | 측정상 할 만하지만 제약(메모리·용량·비용) 때문에 지금은 하지 않습니다 |
| 뒤집힘 | 뒤의 ADR이 대체했습니다 |

## 목록

### 데이터와 저장

| | 결정 | 상태 |
|---|---|---|
| [ADR-001](ADR-001-neo4j-graph-and-vector.md) | 그래프와 벡터를 한 DB(Neo4j Aura Free)에 둔다 | 채택 |
| [ADR-002](ADR-002-arxiv-id-as-time-key.md) | 시간 기준은 `published`가 아니라 arXiv ID의 `YYMM` | 채택 |
| [ADR-003](ADR-003-snapshot-pinning.md) | 모델과 평가는 라이브 DB가 아니라 버전이 박힌 스냅샷만 읽는다 | 채택 |
| [ADR-004](ADR-004-quality-gate-thresholds.md) | 품질 게이트 임계값을 실측값에 붙이지 않는다 | 채택 |
| [ADR-005](ADR-005-reject-second-aura-account.md) | Aura Free 계정 추가로 용량을 늘리지 않는다 | 기각 |

### 검색과 순위

| | 결정 | 상태 |
|---|---|---|
| [ADR-006](ADR-006-reject-pagerank-rerank.md) | PageRank 포화 점수 재정렬을 넣지 않는다 | 기각 |
| [ADR-007](ADR-007-defer-hybrid-and-rerank.md) | 하이브리드 검색과 리랭커를 붙이지 않는다 | 보류 |
| [ADR-008](ADR-008-ppr-over-full-graphrag-index.md) | 변형 비교는 전량 LLM 색인이 아니라 PPR 구현으로 한다 | 채택 |
| [ADR-009](ADR-009-keep-openai-embedding.md) | 임베딩은 `text-embedding-3-large`를 유지한다 | 채택 |
| [ADR-010](ADR-010-korean-translate-only-when-needed.md) | 한국어는 혼용은 그대로, 순수 한국어만 번역한다 | 채택 |

### 모델

| | 결정 | 상태 |
|---|---|---|
| [ADR-011](ADR-011-cocitation-link-prediction.md) | 링크 예측 과제를 직접 인용에서 공동인용으로 바꾼다 | 채택 |
| [ADR-012](ADR-012-graph-hard-negatives.md) | 링크 예측 네거티브는 그래프에서 뽑은 하드 네거티브로 한다 | 채택 |
| [ADR-013](ADR-013-linear-adapter-offline-only.md) | 인용 쌍 선형 어댑터는 오프라인까지만 한다 | 보류 |

### 운영

| | 결정 | 상태 |
|---|---|---|
| [ADR-014](ADR-014-remote-mlflow-tracking.md) | 학습 추적은 실행 VM이 아니라 원격 MLflow 서버에 남긴다 | 채택 |
| [ADR-015](ADR-015-retry-not-higher-timeout.md) | 그래프 적재는 타임아웃을 올리지 않고 재시도한다 | 채택 |
| [ADR-016](ADR-016-stay-on-streamlit.md) | Cloud Run으로 옮기지 않는다 | 보류 |
| [ADR-017](ADR-017-regression-gate.md) | 품질 회귀는 CI가 기준선과 대조해 막는다 | 채택 |
| [ADR-018](ADR-018-prompt-versioning.md) | 프롬프트 본문은 한 모듈에 모으고 버전은 MLflow에 남긴다 | 채택 |
| [ADR-019](ADR-019-validate-evidence-alias-at-the-tool.md) | 근거 별칭은 도구 경계에서 검사한다 | 채택 |
| [ADR-020](ADR-020-judge-called-once.md) | 심판은 질문당 한 번만 부른다 | 채택 |
| [ADR-021](ADR-021-ragas-four-of-six.md) | RAGAS 지표는 여섯 중 넷만 쓰고 둘은 게이트에 넣지 않는다 | 채택 |
| [ADR-022](ADR-022-answer-flow-as-a-stategraph.md) | 답변 흐름을 StateGraph로 세우되 행동은 바꾸지 않는다 | 채택 |
| [ADR-023](ADR-023-what-to-trace.md) | 추적은 평가에서만 켜고, 켠 뒤에도 프로세스당 상한을 둔다 | 채택 |
| [ADR-024](ADR-024-route-by-question-type.md) | 경로는 질문 유형으로 코드가 정하되 도구를 빼앗지는 않는다 | 채택 |
| [ADR-025](ADR-025-reject-context-compression.md) | 컨텍스트 압축을 넣지 않는다 — 압축할 것이 입력의 3% | 기각 |
| [ADR-026](ADR-026-explain-the-abstention-in-the-app-only.md) | 보류를 화면에서 설명하되 `ask()`는 그대로 예외를 올린다 | 채택 |
| [ADR-027](ADR-027-cocitation-gold-for-the-related-slot.md) | 관련 논문 슬롯의 정답은 공동인용으로 정하고 에이전트를 거치지 않고 잰다 | 채택 |
| [ADR-028](ADR-028-resolve-titles-with-a-fulltext-index.md) | 제목은 전문 검색 인덱스를 쓰는 도구로 해석하고 못 찾으면 못 찾았다고 한다 | 채택 |
| [ADR-029](ADR-029-ppr-beats-the-adapter-in-the-related-slot.md) | 관련 논문 슬롯은 PPR이 지킨다 — 어댑터는 그 자리에서 3.9배 진다 | 기각 |
| [ADR-030](ADR-030-related-papers-needs-a-query-not-a-model.md) | 관련 논문은 모델이 아니라 질의가 답이었다 — 공동인용 수로 정렬한다 | 채택 |
