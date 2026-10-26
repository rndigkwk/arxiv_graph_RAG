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
- **아래 목록에 빠진 ADR이 없습니다.** 파일은 썼는데 색인에 줄을 안 넣는 것이 가장 흔한
  누락이고, 색인이 "무엇을 쓰는가"에 답하는 한 장이라 빠지면 그 한 장이 거짓이 됩니다.
  [`scripts/check_adr_rules.py`](../../scripts/check_adr_rules.py)가 양방향으로 셉니다.

> **2026-10-24 — 이 규칙도 글로만 있었습니다.** ADR-063·064·065가 색인에 없었습니다. 세 번
> 연속 빠뜨리고도 아무것도 막지 않았고, 손으로 세어 보고 알았습니다. 이 저장소에서 손으로
> 유지하는 목록이 조용히 갈라진 **일곱 번째**입니다 — 앞의 여섯처럼 검사기를 붙였습니다.

### 민감도 절의 제목 — 넷 중 하나를 씁니다

[`scripts/check_adr_rules.py`](../../scripts/check_adr_rules.py)가 이 넷을 찾습니다.
`민감도` · `바뀌는 조건` · `가정` · `말하지 않는 것`

**범위를 적는 절은 민감도가 아닙니다.** `넘지 않은 선`·`아직 아닌 것`·`하지 않은 것`은
"무엇을 안 했는가"이고, 민감도는 "무엇이 바뀌면 결론이 뒤집히는가"입니다. 둘을 섞으면
검사기가 통과만 시킵니다.

> **2026-10-21 — 이 규칙이 한동안 지켜지지 않았습니다.** 글로만 적혀 있어서 아무도 셈하지
> 않았습니다. 세어 보니 51개 중 **32개**에 있고 **19개**에 없습니다(023·024·026, 032~036,
> 038~045, 047·048·050). 없는 19개를 지금 채우지 않습니다 — **없던 민감도를 뒤늦게 지어내는
> 일**이고, 위의 "뒤집힌 결정을 지우지 않습니다"와 같은 선입니다. 대신 검사기의
> `WITHOUT_SENSITIVITY`에 **명시적인 빚**으로 적어 두고, 그 목록은 **늘지 않고 줄기만**
> 합니다. 절이 생겼는데 목록에 남아 있으면 검사기가 막습니다.
>
> 처음에는 `민감도`라는 **글자만 세어 24개가 빠진 것으로 봤고, 그것이 틀렸습니다** —
> ADR-046·049·051은 `이 결정이 바뀌는 조건`, ADR-030은 `한계 — 가장 큰 가정`, ADR-037은
> `이 측정이 말하지 않는 것`으로 같은 일을 합니다. 그래서 검사기는 제목 **넷**을 허용합니다.

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
| [ADR-063](ADR-063-what-folding-authored-would-cost.md) | AUTHORED를 접는 값을 잰다 — 지연은 그 값의 일부일 뿐이었다 | 채택 |
| [ADR-064](ADR-064-the-pipeline-cannot-finish-without-leaving-the-pipeline.md) | 파이프라인이 파이프라인 밖으로 나가야 끝난다 — 임베딩 단계를 안으로 가져온다 | 채택 |

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
| [ADR-052](ADR-052-the-sixty-seconds-is-not-for-the-steady-state.md) | 연결 획득 60초는 평상시용이 아니다 — 낮추지 않고 마감 시간을 둔다 | 채택 |
| [ADR-053](ADR-053-the-log-needed-a-clock-and-a-field.md) | 로그에 필요한 것은 핸들러가 아니라 시각과 `seconds=` 필드였다 | 채택 |
| [ADR-054](ADR-054-the-upstream-blip-is-a-third-category.md) | 상류의 일시적 장애는 세 번째 범주다 — 재시도하고 처방을 다르게 적는다 | 채택 |
| [ADR-055](ADR-055-the-stable-case-rule-is-not-resolved.md) | `stable_case`가 변동으로 터지는지는 측정되지 않았다 — 규칙을 고치지 않는다 | 채택 |
| [ADR-056](ADR-056-no-mechanical-check-for-the-uncited-claim.md) | 인용 없는 주장에 기계적 검사를 붙이지 않는다 — 붙이려던 검사가 거짓 양성만 냈다 | 기각 |
| [ADR-057](ADR-057-fine-tune-the-reranker-on-cocitation.md) | 재정렬기를 공동인용으로 미세조정한다 — **재 보니 코사인에 졌다**(격차는 절반으로 줄었다) | 기각 |
| [ADR-058](ADR-058-the-independent-judge-only-means-something-with-labels.md) | 독립 공급자 판정은 라벨이 있는 조건 판정에서만 한다 — 재 보니 61/61은 프롬프트가 아니라 그 모델의 성질이었다 | 채택 |
| [ADR-059](ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md) | 심판 프롬프트는 보류 질문에서 잰다 — 라벨이 기계적이라 사람 없이 집합을 만들 수 있다 | 채택 |
| [ADR-060](ADR-060-what-the-gate-catches-when-the-judge-collapses.md) | 심판이 무너지면 게이트는 총 실패 4건부터 잡는다 — 옳은 결정 둘이 곱해져 3건의 구멍을 만들었다 | 채택 |
| [ADR-061](ADR-061-whether-the-reranker-is-worth-keeping-where-cosine-is-weak.md) | 재정렬기를 코사인이 약한 자리에만 쓰는 규칙은 없다 — 186개를 훑어 최선이 0이었다 | 기각 |
| [ADR-062](ADR-062-read-back-whether-langfuse-stored-it.md) | 추적이 실제로 저장되는지 코드가 되읽는다 — 재 보니 저장되고 있었고 기한이 사라졌다 | 채택 |
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
| [ADR-031](ADR-031-the-model-that-wins-is-the-forecast.md) | 이기는 모델은 예측 쪽이었다 — 가중치를 저장하고 구간을 붙인다 | 채택 |
| [ADR-032](ADR-032-close-the-model-lifecycle.md) | 모델 생명주기를 닫는다 — 등록·판정·승격·롤백, 그리고 main 보호 | 채택 |
| [ADR-033](ADR-033-rank-metrics-measure-the-alphabet.md) | 순위 지표는 알파벳을 재고 있었다 — 순서에 의존하지 않는 지표를 더한다 | 채택 |
| [ADR-034](ADR-034-measure-every-eligible-seed.md) | 표본을 버리고 적격 시드 전부를 잰다 — 하네스의 현직을 배포된 것으로 맞춘다 | 채택 |
| [ADR-035](ADR-035-the-fill-slice-cannot-be-labelled.md) | 보충 구간의 정답은 만들 수 없다 — 대신 예측 시점에 얇은 시드를 잰다 | 채택 |
| [ADR-036](ADR-036-the-embedding-fills-what-the-graph-cannot.md) | 그래프가 비는 자리는 임베딩이 채운다 — 그러나 학습은 여전히 보태지 않는다 | 채택 |
| [ADR-037](ADR-037-the-gbm-wins-the-average-and-loses-both-halves.md) | GBM은 평균을 이기고 양쪽 집단에서 다 진다 | 채택 |
| [ADR-038](ADR-038-the-forecast-panel.md) | 예측 패널을 붙인다 — 모델이 쓰이는 첫 자리 | 채택 |
| [ADR-039](ADR-039-measure-embeddings-where-the-signal-is-only-the-embedding.md) | 임베딩은 임베딩만이 신호인 자리에서 잰다 | 채택 |
| [ADR-040](ADR-040-fill-the-empty-panel.md) | 빈 패널을 임베딩으로 채운다 — GBM의 집계 우위가 사라졌다 | 채택 |
| [ADR-041](ADR-041-the-gate-reads-the-prompt.md) | 게이트가 프롬프트를 읽는다 — 지문을 적어 두기만 하고 보지 않았다 | 채택 |
| [ADR-042](ADR-042-the-saved-list-leaves-the-app.md) | 저장 목록이 앱 밖으로 나간다 — 빈 칸은 추측하지 않고 센다 | 채택 |
| [ADR-043](ADR-043-the-floor-is-checked-against-the-worst-run.md) | RAGAS 하한은 최악의 정상 실행과 심은 결함으로 검사한다 | 채택 |
| [ADR-044](ADR-044-not-run-is-not-a-regression.md) | 돌리지 못한 것은 회귀가 아니다 — 그러나 통과도 아니다 | 채택 |
| [ADR-045](ADR-045-what-to-delete-before-adding-a-month.md) | 한 달을 더하기 전에 무엇을 지울지 — 지금은 지울 것이 없다 | 채택 |
| [ADR-046](ADR-046-bge-m3-does-not-clear-the-bar.md) | BGE-M3는 바를 넘지 못했다 — 5.2배 큰 모델이 작은 형제보다 나쁘다 | 기각 |
| [ADR-047](ADR-047-the-loader-refuses-papers-the-vector-index-cannot-see.md) | 적재가 벡터 인덱스에 안 보이는 논문을 거부한다 | 채택 |
| [ADR-048](ADR-048-the-type-check-does-not-know-the-internet.md) | 유형 검사는 인터넷을 모른다 — 읽기로 분류되는 외부 요청을 막는다 | 채택 |
| [ADR-049](ADR-049-no-local-embedding-clears-the-bar.md) | 로컬 임베딩 셋 다 바를 넘지 못했다 — 크기가 아무것도 사지 않았다 | 기각 |
| [ADR-050](ADR-050-measure-the-cross-encoder-before-buying-the-host.md) | 교차 인코더를 호스팅을 사기 전에 잰다 — 바를 먼저 적는다 | 채택 |
| [ADR-051](ADR-051-the-cross-encoder-loses-to-cosine.md) | 교차 인코더도 코사인을 못 이겼다 — 후보 풀은 핑계가 되지 않는다 | 기각 |
| [ADR-065](ADR-065-say-how-each-paper-was-found.md) | 화면이 논문마다 어떻게 찾았는지 말한다 — 후보를 지우지 않는다 | 채택 |
| [ADR-066](ADR-066-measure-inside-generate.md) | `generate` 안쪽을 잰다 — 괄호로 적어 둔 고백을 분해로 바꾼다 | 채택 |
| [ADR-067](ADR-067-warm-the-citation-graph-at-boot.md) | 인용망을 부팅 때 배경에서 올린다 — 첫 방문자가 15초를 혼자 내지 않는다 | 채택 |
| [ADR-068](ADR-068-modules-live-in-src.md) | 모듈은 `src/`에 산다 — 손으로 적은 목록을 지우는 것이 목적이다 | 채택 |
| [ADR-069](ADR-069-the-condition-check-keeps-its-quarter.md) | 조건 판정은 4분의 1을 계속 받는다 — 2.2%를 바꾸고, 그 2.2%가 유일한 거름망이다 | 기각 |
| [ADR-070](ADR-070-the-screen-says-the-category-and-the-route.md) | 화면이 고른 분류와 보낼 경로를 말한다 — 재검토 제안 셋 중 둘을 그대로, 하나를 약하게 | 채택 |
| [ADR-071](ADR-071-double-the-graph-move-only-the-database.md) | 두 배가 필요해지면 앱과 DB를 VM 한 대에 올린다 — 4GB면 되고, 30초는 거리에서 온다 (결정 1을 같은 날 뒤집음) | 채택 |
| [ADR-072](ADR-072-check-the-deployed-app-in-a-browser.md) | 배포 앱을 브라우저로 확인하고, 실패하면 이슈를 연다 — HTTP 방문은 깨우지도, 옛 코드를 알아보지도 못한다 | 채택 |
| [ADR-073](ADR-073-say-what-the-model-failure-means.md) | 모델이 실패하면 무엇을 할지 말한다 — 제한 시간은 저장된 180회에서 정한다 | 채택 |
| [ADR-074](ADR-074-rewind-the-ingest-watermark.md) | 수집 워터마크를 되감는 명령을 둔다 — 되돌리기가 아니라 다시 받기다 | 채택 |
| [ADR-075](ADR-075-cache-the-drawn-subgraph.md) | 그린 부분 그래프를 캐시한다 — 다시 그릴 때마다 답변 수만큼 조회하고 있었다 | 채택 |
| [ADR-076](ADR-076-the-latest-answer-is-a-workbench.md) | 최신 답변은 세 칸 작업대로, 지난 답변은 접는다 — 접은 패널도 안쪽은 실행된다 | 채택 |
| [ADR-077](ADR-077-the-harness-warms-up-like-the-app.md) | 평가 하네스도 앱처럼 인용망을 데운다 — 동시에 돈 도구는 한 번만 센다 | 채택 |
| [ADR-078](ADR-078-show-each-family-with-its-n.md) | 품질 게이트 탭에 분야별 표를 두되, 수 옆에 n을 둔다 — 지금은 천장에 붙어 있다 | 채택 |
| [ADR-079](ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md) | VM은 저장소를 마운트하고 HTTPS 하나만 연다 — 스키마는 Aura에서 읽어 옮기고, 벡터 양자화만 같게 못 한다 | 채택 |
| [ADR-080](ADR-080-the-vm-pulls-main.md) | VM이 main을 당겨 온다 — GitHub에는 VM에 닿는 자격을 두지 않는다 | 채택 |
| [ADR-081](ADR-081-fastapi-beside-streamlit-on-the-vm.md) | FastAPI 층은 같은 VM에서 Streamlit 옆에 둔다 — 돈이 드는 길에만 문턱을 둔다 | 채택 |
