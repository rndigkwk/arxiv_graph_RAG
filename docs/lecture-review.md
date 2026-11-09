# 교안 적용 정리 (Day 02~77, 2026-10-29 · Day 71~75는 2026-11-05 · Day 76은 2026-11-06 · Day 77은 2026-11-09)

엔코아 교안(`class_1`의 다섯 저장소, `enkoa-practice-deep-learning/내작업`의 Day 65~77, OpenAI Decisions
예제)을 day별로 열어 이 프로젝트 기준으로 나눴습니다. 교안의 목차를 읽고, 저장소의 코드와 ADR에서
그 기법이 실제로 있는지 확인했습니다.

- ✅ **적용함**: 앱이나 평가·학습 코드에 들어가 있습니다. 어디에 있는지.
- 🧪 **테스트 후 미적용**: 재 보고 넣지 않았습니다. 결과와 이유.
- 🟢 **적용할 것**: 아직 없고, 할 값이 있습니다. 조건.
- ➖ **적용 안 함**: 재지 않았습니다. 맞지 않는 이유.

이 프로젝트의 전제가 판정을 많이 정합니다. 데이터는 arXiv의 **제목·초록·저자·인용 메타데이터**이고(본문 PDF
없음), 생성·임베딩은 OpenAI API이며, 배포는 VM 한 대(e2-medium, 4GB)에 Neo4j와 API가 같이 돕니다
([ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)). 답변 지연 목표는 p50 10초입니다([ADR-084](adr/ADR-084-answer-p50-under-10s-measure-the-vm-first.md)).

자세한 기록은 [`ROADMAP.md`](../ROADMAP.md) 4·5·11·12절과 [ADR 색인](adr/README.md)에 있습니다. 이 문서가
그것들을 day별로 다시 묶은 것입니다. Day 24~26·43~45는 받은 폴더에 없어 다루지 않았습니다.

## 파이썬·데이터 수집 (Day 02~05)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 02~04 | 변수·제어문·함수·자료구조·예외·모듈·파일·클래스 | ✅ | 저장소 전체의 기초입니다. 모듈은 `src/`·`pipeline/`·`evals/`·`models/` 패키지로 나눴습니다([ADR-068](adr/ADR-068-modules-live-in-src.md)). |
| 05 | 공개 API로 데이터 수집 | ✅ | arXiv·Semantic Scholar API로 주간 수집합니다(`pipeline/ingest.py`, `pipeline/s2.py`). 429와 일시 장애는 재시도하고 처방을 따로 적습니다([ADR-054](adr/ADR-054-the-upstream-blip-is-a-third-category.md)). |
| 05 | HTML 파싱(매장 정보 크롤링) | ➖ | API가 구조화된 값을 줍니다. |

## 데이터 분석·통계 (Day 06~10)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 06~07 | pandas 조회·정제·결합·집계, 시각화 | ➖ | 분석은 Cypher 집계와 JSON 보고서로 합니다. 화면 차트는 Plotly(`src/metagraph.py`)입니다. |
| 08 | 평균의 함정, 중앙값·분위수 | ✅ | 지연과 비용은 평균이 아니라 p50·p90으로 적습니다(`scripts/measure_vm_answer_latency.py`). |
| 08~09 | 신뢰구간, 짝 검정 | ✅ | 링크 예측과 관련 논문 비교에 짝 부트스트랩 구간을 붙입니다(`models/link_prediction/train.py`, `evals/related_study.py`, [ADR-031](adr/ADR-031-the-model-that-wins-is-the-forecast.md)). |
| 09 | A/B 테스트 — 동시에 돌리고, 효과크기를 보기 | ✅ | 지연은 main과 **동시에** 돌린 짝으로만 비교합니다. 따로 돌린 실행끼리 견줬을 때 공급자 속도 차이를 효과로 읽을 뻔했습니다([ADR-090](adr/ADR-090-recent-is-a-date-filtered-search.md)). 효과크기는 절대값으로도 적습니다 - 라우팅의 입력 토큰 절감은 두 가족에서 −4,511이지만 전체로는 −141(약 1%)이었습니다([ADR-024](adr/ADR-024-route-by-question-type.md)). |
| 09 | 회귀분석, 다중공선성(VIF) | ➖ | 회귀 계수를 해석하는 과제가 없습니다. 링크 예측 피처의 기여는 절제(ablation)로 쟀습니다(`models/link_prediction/ablation.py`). |
| 10 | 종합 실습(전처리 규칙·EDA·리포트) | ➖ | 같은 습관을 수집 품질 게이트가 맡습니다(`pipeline/quality.py`, [ADR-004](adr/ADR-004-quality-gate-thresholds.md)). |

## 텍스트·임베딩 (Day 11~13)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 11 | 텍스트 전처리, TF-IDF, n-gram | ➖ | 키워드 쪽은 Neo4j 전문 색인이 맡고, 제목 해석에만 씁니다([ADR-028](adr/ADR-028-resolve-titles-with-a-fulltext-index.md)). |
| 12 | 문장 임베딩, 코사인 유사도, 최대 입력 길이 | ✅ | `text-embedding-3-large`, 제목+초록 한 덩어리로 넣습니다([ADR-009](adr/ADR-009-keep-openai-embedding.md)). |
| 12 | 허브에서 모델 고르기 | 🧪 | 로컬 임베딩 모델 셋과 BGE-M3를 같은 과제로 쟀고 모두 바를 넘지 못했습니다([ADR-046](adr/ADR-046-bge-m3-does-not-clear-the-bar.md), [ADR-049](adr/ADR-049-no-local-embedding-clears-the-bar.md)). |
| 12~13 | UMAP·KMeans·BERTopic 토픽 모델링 | ➖ | 주제는 arXiv 분류(`primary_category`)가 이미 줍니다. 묶음은 Leiden 커뮤니티로 화면에 보입니다(`src/metagraph.py`). |

## LLM·RAG·에이전트 기초 (Day 14~23)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 14 | 메시지 역할, 모델 고르기와 비용 | ✅ | 질문마다 토큰과 비용을 보고서에 적고 평가 예산을 셉니다(`evals/budget.py`, `evals/ledger.py`). |
| 14 | 구조화 출력(pydantic) | ✅ | 답은 `GroundedAnswer`(근거 ID 먼저, 답 글 다음)를 strict로 받습니다. 근거가 먼저 나오므로 답 글을 쓰는 동안 조건 판정을 시작합니다([ADR-085](adr/ADR-085-overlap-the-condition-check-with-the-answer.md)). |
| 14 | 이미지 입력 | ➖ | 논문 그림을 다루지 않습니다. |
| 15 | 벡터 DB 비교(Chroma·Qdrant·Pinecone) | ✅ | 그래프와 벡터를 한 DB에 둡니다. PPR 시드를 추가 조회 없이 넘길 수 있어서입니다([ADR-001](adr/ADR-001-neo4j-graph-and-vector.md)). |
| 15 | ANN·HNSW | ✅ | Neo4j 벡터 색인입니다. VM으로 옮길 때 벡터 양자화 설정만 Aura와 같게 하지 못했습니다([ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)). |
| 15~16 | 메타데이터 필터 | ✅ | "최근" 질문은 `published` 필터를 **벡터 검색 안에서** 겁니다. 검색 뒤에 거르면 6문항 중 3개가 0편이 됐습니다([ADR-090](adr/ADR-090-recent-is-a-date-filtered-search.md)). |
| 15 | 모른다고 말하기, 출처 보여 주기 | ✅ | 근거가 없으면 보류하고 화면이 이유를 말합니다([ADR-026](adr/ADR-026-explain-the-abstention-in-the-app-only.md)). 답의 인용 ID는 이번 검색 결과 안에 있어야 합니다([ADR-019](adr/ADR-019-validate-evidence-alias-at-the-tool.md)). |
| 16 | PDF 파싱, 청킹 | ➖ | 본문이 없고 제목+초록이 한 단위입니다. |
| 16 | 평가셋과 지표, 설정 하나만 바꿔 다시 재기 | ✅ | 30문항 그래프 평가와 회귀 게이트([ADR-017](adr/ADR-017-regression-gate.md)). 변경은 하나씩 ADR로 남깁니다. |
| 17 | SQL·ERD·Supabase pgvector | ➖ | 관계형 DB가 없습니다([ADR-001](adr/ADR-001-neo4j-graph-and-vector.md)). |
| 18 | 프롬프트를 파일(YAML)로 관리 | ✅ 다르게 | 프롬프트 본문을 `src/prompts.py` 한 모듈에 모으고 버전은 MLflow에 남깁니다([ADR-018](adr/ADR-018-prompt-versioning.md)). 게이트가 지문을 읽습니다([ADR-041](adr/ADR-041-the-gate-reads-the-prompt.md)). |
| 18 | 대화 기록 트리밍 | ✅ | API가 받는 대화는 최근 6턴까지입니다(`src/api.py`의 `MAX_HISTORY_TURNS`). |
| 19 | 구조화 출력과 도구의 차이 | ✅ | 검색은 도구(`search_papers`·`search_graph` …), 최종 답은 구조화 출력입니다. |
| 19~20 | Text-to-SQL의 위험 | ✅ | 같은 위험이 Text-to-Cypher에 있습니다. 세션을 `READ_ACCESS`로 열고, 실행 전에 Neo4j 실행 계획으로 읽기 전용인지 묻고, 외부로 나가는 읽기(`LOAD CSV` 등)를 막습니다([ADR-048](adr/ADR-048-the-type-check-does-not-know-the-internet.md)). |
| 20 | ReAct 멀티툴 에이전트, 검색을 도구로 감싸기 | ✅ | `create_agent` 에이전트가 도구를 고릅니다. 다만 질문 유형으로 정해지는 경로는 코드가 정합니다([ADR-024](adr/ADR-024-route-by-question-type.md)). |
| 20 | 평가셋 구축 — 정답이 색인에 실제로 있는가 | ✅ | 정답은 고정한 스냅샷에서 Cypher로 셉니다([ADR-003](adr/ADR-003-snapshot-pinning.md)). 벡터 색인에 안 보이는 논문은 적재가 거부합니다([ADR-047](adr/ADR-047-the-loader-refuses-papers-the-vector-index-cannot-see.md)). |
| 20 | 인용한 문장이 근거 안에 있는가 | 🧪 | 인용 없는 주장에 기계적 검사를 붙여 봤더니 거짓 양성만 냈습니다([ADR-056](adr/ADR-056-no-mechanical-check-for-the-uncited-claim.md)). |
| 21 | MCP 서버(파일·웹·브라우저·SQLite·코드 실행) | ➖ | 답에 필요한 것은 그래프 하나이고, 외부 도구는 공격면만 늘립니다([ADR-048](adr/ADR-048-the-type-check-does-not-know-the-internet.md)). |
| 21~22 | 미들웨어, Plan-and-Execute | ➖ | 계획 단계는 모델 턴을 하나 더 씁니다. 지연 목표 때문에 반대로 턴을 앱으로 옮기고 있습니다([ADR-086](adr/ADR-086-the-app-ranks-related-papers-not-the-model.md), [ADR-087](adr/ADR-087-translate-before-the-agent.md), [ADR-088](adr/ADR-088-resolve-the-named-paper-before-the-agent.md)). |
| 21 | 비동기·동시 실행 | ✅ | 조건 판정을 답 쓰기와 겹쳐 돌립니다([ADR-085](adr/ADR-085-overlap-the-condition-check-with-the-answer.md)). 인용망은 부팅 때 배경에서 올립니다([ADR-067](adr/ADR-067-warm-the-citation-graph-at-boot.md)). |
| 22 | Self-reflection 루프와 두 종료 조건 | ✅ | 조건 판정이 거절하면 다시 쓰고, 시도 상한을 넘으면 보류합니다(`src/agent_graph.py`, [ADR-022](adr/ADR-022-answer-flow-as-a-stategraph.md)). |
| 22 | Langfuse 관측, 비용 계산 | ✅ | 추적은 평가에서만 켜고 하루 상한을 둡니다([ADR-023](adr/ADR-023-what-to-trace.md)). 실제로 저장됐는지 코드가 되읽습니다([ADR-062](adr/ADR-062-read-back-whether-langfuse-stored-it.md)). |
| 22 | 프롬프트를 서버에 올려 관리 | ✅ 다르게 | Langfuse가 아니라 MLflow 레지스트리입니다([ADR-018](adr/ADR-018-prompt-versioning.md)). |
| 23 | Streamlit 대시보드·챗봇 | ✅ → 뒤집힘 | 처음엔 Streamlit이었고([ADR-016](adr/ADR-016-stay-on-streamlit.md)), 지금은 Next.js(Vercel) 화면이 VM의 API를 부릅니다([ADR-082](adr/ADR-082-the-browser-calls-the-vm-api-directly.md), [ADR-083](adr/ADR-083-take-streamlit-off-the-vm.md)). |
| 23 | 제너레이터로 스트리밍 | ✅ | 답이 SSE로 부분부터 흘러갑니다(`src/api.py`). |

## 그래프 DB·지식 그래프 (Day 27~42)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 27~28 | LPG 모델링, 노드·관계·속성 고르기 | ✅ | `Paper`·`ArxivAuthorName`·`REFERENCES`·`AUTHORED`. 저자를 노드로 둘지 속성 목록으로 접을지 잰 값이 [ADR-063](adr/ADR-063-what-folding-authored-would-cost.md)입니다(목록으로 접으면 인덱스를 못 씁니다). |
| 27 | RDF·SPARQL·온톨로지 | ➖ | 외부 KB와 잇지 않습니다. |
| 29~30 | Cypher 기초·경로·`WITH` 파이프라인·`EXISTS` | ✅ | 에이전트가 쓰는 조회와 평가 정답 조회가 모두 Cypher입니다. 패턴 규칙은 지시문에 있습니다(`src/prompts.py`). |
| 30 | 최단 경로 | ➖ | 질문 가족에 "몇 단계 떨어져 있나"가 없습니다. |
| 29, 32 | `MERGE`는 키만, 나머지는 `SET`, 멱등 적재 | ✅ | 적재는 MERGE만 씁니다(`pipeline/ingest.py`). 다시 받기는 워터마크를 되감아서 합니다([ADR-074](adr/ADR-074-rewind-the-ingest-watermark.md)). |
| 31 | 집계가 조용히 틀리는 자리 | ✅ | 문자열로 바꾼 값으로 정렬해 9가 88보다 위에 오던 결함을 검사로 막습니다(`src/cypher_validation.py`, advanced.md). |
| 31 | 인덱스·제약, 실행 계획 | ✅ | 전문 색인으로 제목을 찾고([ADR-028](adr/ADR-028-resolve-titles-with-a-fulltext-index.md)), 실행 계획(EXPLAIN)으로 조회 유형을 판정합니다([ADR-048](adr/ADR-048-the-type-check-does-not-know-the-internet.md)). 리스트 원소는 RANGE 인덱스가 못 받는다는 것을 재서 저자 접기를 미뤘습니다([ADR-063](adr/ADR-063-what-folding-authored-would-cost.md)). |
| 32 | 배치 적재(`UNWIND`) | ✅ | 행 묶음을 `UNWIND $rows`로 보냅니다(`pipeline/ingest.py`). |
| 32 | `LOAD CSV` 파일 적재 | ➖ | API에서 받습니다. 모델이 쓰는 조회에서는 오히려 막습니다([ADR-048](adr/ADR-048-the-type-check-does-not-know-the-internet.md)). |
| 33 | GDS 투영, PageRank, `write`/`mutate` | ✅ | PageRank 탭(`src/pagerank.py`, `src/gds_pagerank.py`). Aura Free에는 GDS가 없어 별도 경로였습니다(ROADMAP 0-3절). |
| 34 | 커뮤니티 탐지(Leiden), 순도·NMI | ✅ | Leiden 커뮤니티 사이의 인용 흐름을 화면에 그립니다(`src/metagraph.py`). |
| 34 | 노드 유사도(이웃 겹침) | ✅ | 관련 논문은 공동인용 수로 정렬합니다 - 모델보다 질의가 답이었습니다([ADR-030](adr/ADR-030-related-papers-needs-a-query-not-a-model.md)). |
| 35 | 벡터·전문 색인을 같은 DB에 | ✅ | [ADR-001](adr/ADR-001-neo4j-graph-and-vector.md), [ADR-028](adr/ADR-028-resolve-titles-with-a-fulltext-index.md). |
| 35, 49 | 개체 PageRank를 문서 점수에 섞기 | 🧪 | 포화 함수로 섞어 재정렬해 봤고 넣지 않았습니다. 재정렬이 가져갈 여지 자체가 없었습니다([ADR-006](adr/ADR-006-reject-pagerank-rerank.md)). |
| 35 | 커뮤니티로 검색 범위 좁히기 | ➖ | 검색이 이미 k=5 허용값의 99.9%입니다(ROADMAP 11-8절). |
| 36~38 | NER, 트리플 추출, 추출 품질 지표 | ➖ | 그래프를 본문에서 추출하지 않고 메타데이터로 짓습니다. 추출 오류가 생길 자리가 없습니다. |
| 39 | 엔티티 정규화 — 같은 이름, 다른 사람 | 🟢 | 저자는 표기 문자열 하나가 한 노드라 동명이인을 구별하지 않습니다(ADR-042에 적어 둠). `coauthor` 가족의 거짓 공저자가 여기서 나옵니다. Semantic Scholar가 저자 ID를 주므로, 인용 논문 수집(이전 계획 2단계)이 들어올 때 함께 붙입니다. |
| 39~40 | 외부 KB 표준 ID(Wikidata) 연결 | ➖ | 논문은 arXiv ID가 이미 표준 키입니다([ADR-002](adr/ADR-002-arxiv-id-as-time-key.md)). |
| 40 | 문서에서 KG 자동 생성(KG Builder) | ➖ | 36~38과 같은 이유입니다. 예전 교안 노트북만 `notebooks/old/`에 남아 있습니다. |
| 41 | 증분 적재, 처리 이력, 이번 추가만 되돌리기 | ✅ | 주간 증분 수집과 실행 기록, 워터마크 되감기([ADR-074](adr/ADR-074-rewind-the-ingest-watermark.md)). 되돌리기가 아니라 다시 받기입니다 - MERGE만 쓰므로 지울 것이 없습니다. |
| 42 | Text2Cypher + 벡터 검색 도구를 고르는 에이전트 | ✅ | 이 앱의 중심입니다(`search_graph`·`search_papers`). 인용한 관계와 원문은 화면이 논문마다 보여 줍니다([ADR-065](adr/ADR-065-say-how-each-paper-was-found.md)). |

## 고급 RAG·에이전트 (Day 46~57)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 46 | 청킹 전략, RAPTOR | ➖ | 청킹 대상이 없습니다. 요약 계층은 GraphRAG 커뮤니티 보고서와 같은 자리인데 부분 색인이 비교 불가로 끝났습니다(ROADMAP 11-8절). |
| 47 | BM25 + Dense 하이브리드 | 🧪 | 검색이 k=5 허용값의 99.9%라 가져갈 여지가 0.1%p뿐입니다. 보류([ADR-007](adr/ADR-007-defer-hybrid-and-rerank.md)). |
| 47 | Self-Query(질문에서 조건 뽑기) | ✅ | 모델이 아니라 코드가 뽑습니다 - "최근"은 정규식으로 가려 발표일 필터를 겁니다([ADR-090](adr/ADR-090-recent-is-a-date-filtered-search.md)). 질문이 지칭한 논문은 앱이 먼저 찾습니다([ADR-088](adr/ADR-088-resolve-the-named-paper-before-the-agent.md)). |
| 47 | HyDE, Multi-Query, Step-back, 질문 분해 | ➖ | 하이브리드와 같은 이유(검색 여유 0.1%p)이고, 질문마다 모델 호출이 늡니다. |
| 47 | Jev로 질의 분기 | ✅ 다르게 | 질문 유형으로 코드가 경로를 정합니다. 호출률 0.34 → 0.00, 근거 recall 0.956 → 1.000([ADR-024](adr/ADR-024-route-by-question-type.md)). |
| 48 | 후보와 순서를 나눠 평가 | ✅ | 재정렬이 노릴 여지를 먼저 쟀습니다(advanced.md "재정렬의 여지 측정"). |
| 48 | Cross-Encoder 리랭킹 | 🧪 | 호스팅을 사기 전에 바를 적고 쟀습니다([ADR-050](adr/ADR-050-measure-the-cross-encoder-before-buying-the-host.md)). 코사인을 못 이겼습니다([ADR-051](adr/ADR-051-the-cross-encoder-loses-to-cosine.md)). |
| 48 | 재정렬기 미세조정, 약한 자리에만 쓰기 | 🧪 | 공동인용으로 미세조정했지만 코사인에 졌고([ADR-057](adr/ADR-057-fine-tune-the-reranker-on-cocitation.md)), 코사인이 약한 자리만 고르는 규칙도 186개를 훑어 최선이 0이었습니다([ADR-061](adr/ADR-061-whether-the-reranker-is-worth-keeping-where-cosine-is-weak.md)). |
| 48 | ColBERT | ➖ | Cross-Encoder가 진 자리이고 CPU·메모리 부담이 더 큽니다. |
| 48 | 컨텍스트 압축 | 🧪 | 압축이 손댈 검색 문맥이 입력의 3%였고 캐시 할인을 깨뜨릴 수 있었습니다([ADR-025](adr/ADR-025-reject-context-compression.md)). |
| 49 | 벡터로 진입해 관계로 확장 | ✅ | `search_papers`로 찾고 `search_graph`로 관계를 조회합니다. 관련 논문은 앱이 부릅니다([ADR-086](adr/ADR-086-the-app-ranks-related-papers-not-the-model.md)). |
| 49 | 전문 검색을 같은 ID로 연결 | ✅ | 제목 해석([ADR-028](adr/ADR-028-resolve-titles-with-a-fulltext-index.md)). |
| 49 | 질문 분류로 검색 라우팅 | ✅ | [ADR-024](adr/ADR-024-route-by-question-type.md), 화면이 고른 분류와 경로를 말합니다([ADR-070](adr/ADR-070-the-screen-says-the-category-and-the-route.md)). |
| 50 | State·노드·엣지·조건부 분기 | ✅ | 답변 흐름이 StateGraph입니다(`src/agent_graph.py`, [ADR-022](adr/ADR-022-answer-flow-as-a-stategraph.md)). 노드마다 시간을 잽니다([ADR-066](adr/ADR-066-measure-inside-generate.md)). |
| 51 | 작성 → 검토 → 수정 사이클 + 상한 | ✅ | 조건 판정 → 다시 쓰기 → 보류. |
| 51 | HITL(`interrupt`) | ➖ | 논문 검색에 사람에게 되물을 것이 없습니다. |
| 51 | 스트리밍 | ✅ | SSE 부분 답(Day 23). |
| 51 | SQLite 체크포인터, 멈춘 곳에서 이어 하기 | ➖ | 답변은 한 번에 끝납니다. 증분 수집의 "틀린 지점부터 다시"는 아직 없습니다(ROADMAP 11-6절). |
| 52 | Store·Mem0 사용자 기억 | ➖ | 계정이 없습니다. 저장한 논문 목록은 앱 밖(브라우저)으로 내보냅니다([ADR-042](adr/ADR-042-the-saved-list-leaves-the-app.md)). |
| 52 | 컨텍스트 엔지니어링(요약·최근 대화) | ✅ | 최근 6턴 상한(Day 18). 대화가 짧아 요약은 두지 않았습니다. |
| 53 | CRAG(검색 평가 → 재검색 → 보류) | ✅ | 조건 판정이 그 자리입니다. 기다림의 26.7%를 쓰고 실행의 2.2%만 막지만, 그 2.2%를 대신 거를 것이 없어 남겼습니다([ADR-069](adr/ADR-069-the-condition-check-keeps-its-quarter.md)). |
| 53 | Self-RAG(답변 검토와 수정) | ✅ | 같은 판정이 답의 인용과 조건을 함께 봅니다. 추론을 끄고 부릅니다([ADR-089](adr/ADR-089-judge-without-reasoning.md)). |
| 54 | Adaptive·Modular RAG | ✅ | 질문 유형 라우팅([ADR-024](adr/ADR-024-route-by-question-type.md))과 앱이 먼저 하는 단계들 - 번역([ADR-087](adr/ADR-087-translate-before-the-agent.md)), 제목 해석([ADR-088](adr/ADR-088-resolve-the-named-paper-before-the-agent.md)), 최근 검색([ADR-090](adr/ADR-090-recent-is-a-date-filtered-search.md)). |
| 54 | RAG-Fusion | ➖ | 검색 여유 0.1%p. |
| 55 | 슈퍼바이저·워커, `Send` 병렬, 핸드오프 | ➖ | 같은 저자의 dograg 담당입니다. 논문 검색은 역할을 나눌 일이 적습니다. |
| 56 | Langfuse로 궤적 디버깅 | ✅ | [ADR-023](adr/ADR-023-what-to-trace.md), [ADR-062](adr/ADR-062-read-back-whether-langfuse-stored-it.md). |
| 56 | 재시도·타임아웃·재시도 예산 | ✅ | 적재는 타임아웃을 올리지 않고 재시도([ADR-015](adr/ADR-015-retry-not-higher-timeout.md)), 연결 획득 60초는 낮추지 않고 마감 시간을 둡니다([ADR-052](adr/ADR-052-the-sixty-seconds-is-not-for-the-steady-state.md)), 모델 호출 제한 시간은 저장된 180회에서 정했습니다([ADR-073](adr/ADR-073-say-what-the-model-failure-means.md)). |
| 56 | 부분 실패와 폴백 | ✅ | 모델이 실패하면 무엇을 할지 말합니다([ADR-073](adr/ADR-073-say-what-the-model-failure-means.md)). 상류의 일시 장애는 따로 셉니다([ADR-054](adr/ADR-054-the-upstream-blip-is-a-third-category.md)). |
| 57 | Microsoft GraphRAG·LightRAG | 🧪 | 전량 LLM 색인 대신 PPR 구현으로 변형을 비교했습니다. 부분 색인은 골드 커버리지 1.2%로 비교가 성립하지 않았습니다([ADR-008](adr/ADR-008-ppr-over-full-graphrag-index.md)). |
| 57 | HippoRAG(개체에서 점수 퍼뜨리기) | 🧪 → 바꿈 | 관련 논문 슬롯에서 PPR이 어댑터를 이겼고([ADR-029](adr/ADR-029-ppr-beats-the-adapter-in-the-related-slot.md)), 그 뒤 공동인용 수 질의가 PPR을 대신했습니다([ADR-030](adr/ADR-030-related-papers-needs-a-query-not-a-model.md)). |

## RAG 설계·평가 (Day 58~61)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 58 | 우리 질문으로 임베딩 고르기 | ✅ | OpenAI 임베딩을 유지했습니다([ADR-009](adr/ADR-009-keep-openai-embedding.md)). 로컬 후보는 🧪([ADR-046](adr/ADR-046-bge-m3-does-not-clear-the-bar.md), [ADR-049](adr/ADR-049-no-local-embedding-clears-the-bar.md)). |
| 58 | 벡터 저장소 선정 | ✅ | [ADR-001](adr/ADR-001-neo4j-graph-and-vector.md). |
| 58 | 비용을 셋으로 나누기 | ✅ | ROADMAP 8절(호스팅·DB·API). |
| 58 | ADR, 지배 관계, 민감도 | ✅ | ADR 90개. 민감도 절은 검사기가 셉니다(`scripts/check_adr_rules.py`). 지배 관계는 규칙이 아니라 몇 곳에만 씁니다(ROADMAP 11-3절). |
| 59 | Faithfulness·Answer Relevancy·Context Precision·Relevancy | ✅ | 넷을 쟀고 둘은 게이트에 넣지 않습니다([ADR-021](adr/ADR-021-ragas-four-of-six.md)). 하한은 최악의 정상 실행과 심은 결함으로 검사했습니다([ADR-043](adr/ADR-043-the-floor-is-checked-against-the-worst-run.md)). |
| 59 | Answer Correctness, Context Recall | ➖ | 정답 답변 글이 없고, recall은 골드 ID로 이미 정확히 셉니다([ADR-021](adr/ADR-021-ragas-four-of-six.md)). |
| 60 | 판사 위치 편향 | ✅ | 120회 호출에서 두 판정이 한 번도 뒤집히지 않아 다수결을 넣지 않았습니다([ADR-020](adr/ADR-020-judge-called-once.md)). |
| 60 | 사람 판정과 대조, 판사 성적표 | ✅ | 라벨이 질문 문장에서 기계적으로 나오는 61문항으로 재고([ADR-058](adr/ADR-058-the-independent-judge-only-means-something-with-labels.md)), 프롬프트를 맞춘 집합과 다른 보류 질문으로 다시 잽니다([ADR-059](adr/ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md)). 사람이 직접 본 것은 3건뿐입니다. |
| 60 | 판사가 무너질 때 | ✅ | 게이트가 총 실패 4건부터 잡습니다([ADR-060](adr/ADR-060-what-the-gate-catches-when-the-judge-collapses.md)). |
| 60 | Pairwise 비교, DeepEval G-Eval·Toxicity | ➖ | 두 답 중 고르는 결정이 없고, 게이트가 자체 규칙 7개를 씁니다. |
| 60 | 판사 점수로 프롬프트 자동 최적화(GEPA) | ➖ | 조건 개수의 흔들림이 프롬프트 탓이 아니었으므로 최적화할 대상이 아직 없습니다(ROADMAP 11-5절). |
| 61 | `mlflow.genai.evaluate`, 평가 기록 | ✅ | 원격 MLflow(DagsHub)에 남깁니다([ADR-014](adr/ADR-014-remote-mlflow-tracking.md)). |
| 61 | 기준선 세 번 + pytest + CI 회귀 게이트 | ✅ | 지시문이 바뀌면 full 평가 3회로 기준선을 다시 씁니다. 게이트 규칙 7개와 게이트 자체를 시험하는 fixture([ADR-017](adr/ADR-017-regression-gate.md), [ADR-041](adr/ADR-041-the-gate-reads-the-prompt.md), [ADR-044](adr/ADR-044-not-run-is-not-a-regression.md)). |
| 61 | GitHub Actions, Ruff | ✅ | lint·test·회귀 평가 워크플로(`.github/workflows/`). |

## 가드레일 (Day 62~64)

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 62 | 위협 분석, 레드팀 질문 셋으로 공격 성공률 | ✅ | 레드팀 19문항을 앱 전체에 통과시킵니다(`evals/redteam.py`, [ADR-094](adr/ADR-094-red-team-through-the-whole-app.md)). 유출·쓰기·주입은 0건이었고, 범위 밖 질문 2개에 관계없는 근거가 붙었고, 라우터 자리에서 앱이 먼저 가리도록 고쳤습니다([ADR-095](adr/ADR-095-out-of-scope-at-the-router.md)). |
| 62 | NeMo Guardrails·Colang 대화 흐름 | ➖ | 대화 흐름이 없는 한 번짜리 질문입니다. 레일마다 모델 호출이 붙어 지연 목표와 맞서고, 의존성이 VM 메모리를 씁니다. |
| 63 | 입력 self check 레일 | ➖ | 같은 이유(호출 하나 더). 판정이 이미 답 뒤에 있습니다. |
| 63 | 민감정보 정규식 차단·가리기 | ➖ | 개인정보를 다루지 않습니다. |
| 63 | 검색 레일(근거만 쓰기) | ✅ | 인용 ID는 검색 결과 안에 있어야 하고([ADR-019](adr/ADR-019-validate-evidence-alias-at-the-tool.md)), 관련 논문 추천은 근거로 인정하지 않습니다(`src/answer_validation.py`). |
| 63 | Prompt Guard·Llama Guard 분류 모델 | ➖ | GPU 없는 4GB VM에 들어가지 않습니다(Day 68과 같은 계산). |
| 63 | 출력 검증(Guardrails AI) | ✅ 다르게 | strict 구조화 출력 + `validate_answer`·조건 판정이 그 자리입니다. |
| 64 | 실행 레일과 최소 권한 | ✅ | 조회 세션은 `READ_ACCESS`, 실행 전 유형 판정, 외부 요청 차단([ADR-048](adr/ADR-048-the-type-check-does-not-know-the-internet.md)). 돈이 드는 길(`/api/ask`)에만 IP당 분당 3회·하루 30회 문턱이 있습니다([ADR-081](adr/ADR-081-fastapi-beside-streamlit-on-the-vm.md)). VM에는 HTTPS 하나만 열었습니다([ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)). |

## OpenAI Decisions 예제

| 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- |
| 분류 전용 API로 선택지 고르기(게임 자동 플레이, 연령대 추정) | ➖ | 이 앱의 분기는 질문 유형 규칙이 0ms에 정합니다([ADR-024](adr/ADR-024-route-by-question-type.md)). 모델을 부르는 분류가 남아 있지 않습니다. dograg는 규칙이 못 정한 질문에 씁니다. |

## 딥러닝·오픈소스 LLM·파인튜닝 데이터 (Day 65~69)

이 프로젝트는 신경망을 직접 학습하지 않습니다(학습하는 모델은 닫힌 해 ridge 어댑터와 로지스틱 회귀).
자세한 근거는 [ROADMAP 12절](../ROADMAP.md#12-교안-day-6569-검토-2026-10-28)입니다.

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 65 | 표준화는 학습 통계로 | ✅ | 링크 예측이 `make_pipeline(StandardScaler(), ...)`로 학습 구간에만 맞춥니다(`models/link_prediction/baselines.py`). |
| 65 | 경사하강, 학습률·스케줄러, DNN | ➖ | 어댑터는 닫힌 해라 학습률이 없습니다([ADR-013](adr/ADR-013-linear-adapter-offline-only.md)). |
| 66 | 검증으로 설정 고르기, 테스트는 한 번 | ✅ | 어댑터는 학습 구간 안 검증 분할로 ridge·negative 가중치를 고르고, 제목 해석은 학습·검증·평가 세 창에서 임계를 검증 창에서만 고릅니다. |
| 66 | 분할 단위의 누수 | ✅ | 인용하는 논문 단위·발행월 단위로 나눕니다([ADR-002](adr/ADR-002-arxiv-id-as-time-key.md), [ADR-011](adr/ADR-011-cocitation-link-prediction.md)). |
| 66 | CNN·ResNet·GAN·Lightning | ➖ | 이미지와 학습 루프가 없습니다. |
| 67 | 트랜스포머 인코더, 텐서 모양 | ➖ | 임베딩은 API입니다. |
| 68 | 추론 모드 켜고 끄기 — 판정 | ✅ | 판정에서 추론을 껐습니다. 판정 연구 61/61 그대로, VM 판정 대기 2.36 → 1.00초([ADR-089](adr/ADR-089-judge-without-reasoning.md)). |
| 68 | 추론 모드 켜고 끄기 — 답 쓰는 에이전트 | ✅ | 에이전트도 추론을 껐습니다. CI에서 main과 동시에 짝 3회씩 돌려 턴당 p50 2.30 → 1.80초, 품질 그대로입니다. 대신 판정이 경로 위로 올라와 끝까지는 문항 중앙값 −0.59초만 줄었습니다([ADR-091](adr/ADR-091-agent-without-reasoning.md)). |
| 68 | 구조화 출력·도구 호출 | ✅ | Day 14·19. |
| 68 | 오픈 웨이트 LLM 로컬 실행, 양자화 | ➖ | 4B 모델을 4비트로 줄여도 가중치만 약 2GB이고, 4GB VM을 Neo4j·API와 나눠 씁니다. 공유 vCPU 2개라 번역(1.4~3.5초)을 옮겨도 느려질 쪽입니다. |
| 69 | 생성한 답의 숫자 대조 | 🧪 | 저장된 GraphRAG 답 120개 중 두 자리 이상 숫자가 있는 답 28개, 검색 문맥에 없는 숫자 0개. 막을 것이 없습니다. |
| 69 | 오염 검사 | ✅ | 판정 보류 질문([ADR-059](adr/ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md)), 재정렬 학습 데이터의 `leak_check`(`evals/export_rerank_training.py`). |
| 69 | 데이터 카드 | ✅ | 평가 보고서마다 데이터셋 SHA-256·Git SHA·패키지 버전을 적습니다. |
| 69 | 패러프레이즈 증강 + 뜻 보존 확인 | ✅ | 30문항을 영어·한국어로 바꿔 말한 60문항을 따로 둡니다(`evals/cases_graph_paraphrase.json`, [ADR-093](adr/ADR-093-paraphrased-eval-questions.md)). 정답을 정하는 값이 글자 그대로 남는지 테스트가 봅니다. 60문항 모두 원문과 같은 점수였습니다. |
| 69 | 역번역 | ✅ | 한국어 질문의 번역 손실을 다섯 경로로 쟀습니다([ADR-010](adr/ADR-010-korean-translate-only-when-needed.md), ROADMAP 10-7절). |
| 69 | LLM으로 학습 데이터 생성(로컬 Gemma 부록 포함) | ➖ | 라벨이 인용 관계로 이미 공짜입니다([ADR-011](adr/ADR-011-cocitation-link-prediction.md)). |

## SFT 지도 파인튜닝 (Day 70)

교안은 `Qwen3.5-0.8B`를 노무 상담 데이터로 가중치 전체 SFT하고(교안 01), 에폭별 체크포인트를 Base와 같은 지표로
채점해 하나를 고른 뒤 일반 능력 퇴행과 가중치 보간을 봅니다(교안 02). 이 프로젝트는 생성 모델을 학습하지 않지만,
**학습한 모델을 고르고 퇴행을 재는 규율**은 임베딩 파인튜닝([ADR-097](adr/ADR-097-fine-tune-at-scale-on-a-rented-gpu.md))에
그대로 맞습니다.

| 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- |
| 생성 모델 SFT(답변 토큰만 손실, 대화 끝 토큰까지 학습, `max_length`) | ➖ | 답은 OpenAI API가 씁니다. 학습한 0.8B 모델을 돌릴 곳이 없습니다 — VM은 4GB를 Neo4j·API와 나눠 쓰고 GPU가 없습니다(ROADMAP 12-5). |
| "말투·형식·거절은 SFT로, 사실은 RAG로" | ✅ | 사실은 모델 가중치가 아니라 그래프 조회 결과에서 옵니다. 답의 근거 ID는 도구가 찾은 논문이어야 하고, 아니면 막습니다(`answer_validation`). 교안의 Tuned가 학습한 질문에도 숫자를 틀린 것이 이 설계가 막는 실패입니다. |
| 검증 손실은 학습 기록이지 모델 고르는 기준이 아님 | ✅ | ADR-097 노트북은 epoch마다 인용 과제 점수를 찍지만 진단이고, 최종 모델은 마지막 epoch로 미리 정했습니다. 판정은 다른 과제(ADR-039 자리)에서 합니다. |
| 고르는 규칙을 결과 보기 전에 적기 | ✅ | 바와 주 후보를 측정 전에 ADR에 적습니다([ADR-057](adr/ADR-057-fine-tune-the-reranker-on-cocitation.md)·[ADR-061](adr/ADR-061-whether-the-reranker-is-worth-keeping-where-cosine-is-weak.md)·ADR-097). ADR-097은 변형 셋 중 `b256-e3` 하나만 판정합니다 — 셋 중 하나라도 통과하면 채택하는 방식은 우연한 통과를 늘립니다. |
| 고른 평가셋으로 비교도 하면 낙관적 → 마지막 확인용 셋 | ✅ | 판정 프롬프트는 보류 질문으로 쟀고([ADR-059](adr/ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md)), 선택 규칙은 진단 2/3·최종 1/3으로 나눴습니다(ADR-061). |
| 에폭마다 체크포인트 저장, 이어서 학습 | ✅ | ADR-097 노트북이 epoch 안에서도 저장하고 `get_last_checkpoint`로 이어 돕니다. 저장된 모델은 설정 지문이 같을 때만 다시 씁니다. |
| 학습 손실과 평가 지표를 같은 MLflow run에 | ✅ | 학습 손실은 `report_to=["mlflow"]`로, epoch별 진단 점수는 같은 run에 `diag_` 지표로 남깁니다(DagsHub, [ADR-014](adr/ADR-014-remote-mlflow-tracking.md)). |
| 거절률과 과잉 거절률을 따로 | ✅ | 범위 기준 0.70을 범위 밖 50문항과 범위 안 50문항으로 쟀습니다. 범위 밖은 점수를 매긴 46개를 모두 거절했고, 범위 안 거절은 0개였습니다([ADR-095](adr/ADR-095-out-of-scope-at-the-router.md)). |
| 고정 문장은 키워드로, 정답은 판사로 | ✅ | 거절·막음은 사유 코드로 결정적으로 셉니다(`src/abstain.py`). 내용이 맞는지만 판정 모델이 봅니다. |
| 재현 문항과 적용 문항을 나눠 채점 | ➖ | 외웠는지 적용했는지를 가르는 지표입니다. 이 앱의 답은 가중치에서 꺼내지 않고 Cypher가 계산하므로 외울 것이 없습니다. 표현만 바꾼 질문(재현에 해당)은 Day 69에서 넣었습니다([ADR-093](adr/ADR-093-paraphrased-eval-questions.md)). |
| 일반 능력 퇴행 점검 | 🟢 | 임베딩을 바꾸면 같은 벡터를 쓰는 다른 두 자리가 움직입니다 — 질문 → 논문 검색(`search_papers`)과 범위 기준 0.70. ADR-097 학습은 논문 → 논문만 보고, 판정 자리도 둘을 보지 않습니다. ADR-097에 조건으로 적었습니다. |
| 가중치 보간(WiSE-FT) | 🟢 | 다시 학습하지 않고 퇴행을 줄입니다. 임베딩 모델도 같은 구조 두 개(튜닝 전 bge-base, `b256-e3`)라 그대로 됩니다. `b256-e3`이 판정을 통과하고 위 퇴행이 보일 때만 하고, α 후보는 그때 미리 적습니다. |
| 일반 데이터 섞기(replay) | ➖ | 보간이 다시 학습하지 않고 같은 일을 하므로 먼저입니다. 섞을 일반 쌍도 따로 만들어야 합니다. |
| perplexity | ➖ | 다음 토큰을 예측하는 모델이 없습니다. 임베딩의 일반 능력은 위의 질문 → 논문 검색으로 잽니다. |
| GPU 메모리를 파라미터당 바이트로 어림 | ✅ | 어림 대신 실측합니다. ADR-097 노트북이 `mini_batch` 후보를 몇 스텝씩 돌려 최대 메모리로 고릅니다. T4에서 어림으로 두 번 틀렸습니다(EXPERIMENTS 11-6). |
| LoRA(다음 시간 예고) | ➖ | 아래 Day 71에서 봅니다. |

## PEFT·QLoRA·DPO (Day 71~73)

세 날 모두 생성 모델(`Qwen3.5-0.8B` 노무 상담)을 적은 메모리로 학습하고(LoRA·QLoRA) 거절을 가르치는(DPO) 내용입니다.
이 프로젝트의 답은 OpenAI API가 쓰므로 학습 기법 자체는 들어올 자리가 없습니다(Day 70과 같은 이유). 가져올 것은
**고르는 규칙과 무너진 답을 보는 법**입니다.

| Day | 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- | --- |
| 71 | LoRA 어댑터 학습(`r`·`lora_alpha`·`target_modules`), DoRA | ➖ | 학습할 생성 모델이 없습니다. 임베딩 학습도 가중치 전체를 갱신했고(배치 256이 24분, [ADR-097](adr/ADR-097-fine-tune-at-scale-on-a-rented-gpu.md)), 지금은 닫혔습니다([ADR-098](adr/ADR-098-fine-tune-on-co-citation-pairs.md)). |
| 71 | 어댑터를 끄고 켜서 같은 모델로 전후 비교(`disable_adapter`) | ✅ 다르게 | 바꾼 것 하나만 켜고 끈 두 상태를 한 실행 안에서 같은 시드로 비교합니다. 빠진 인용 간선 23만 개는 `Graph(extra_references=...)` 하나로 얹고 빼서 쟀습니다([ADR-100](adr/ADR-100-dont-backfill-old-references.md)). |
| 71·72 | 에폭별 어댑터를 같은 지표로 채점해 고르기 | ✅ 다르게 | 고르지 않습니다. 최종 모델을 측정 전에 "3 epoch 끝"으로 정했고, epoch별 점수는 진단입니다(ADR-097). 고르는 데 쓴 점수로 판정까지 하면 낙관적이 되기 때문입니다(Day 70). |
| 71 | 병합(`merge_and_unload`)과 어댑터 유지 | ➖ | 배포할 어댑터가 없습니다. |
| 72 | 학습 메모리 네 가지, 설정을 측정으로 고르기 | ✅ | ADR-097 노트북이 `mini_batch` 후보를 몇 스텝씩 돌려 최대 메모리로 골랐습니다(Day 70 행). |
| 72 | OOM 순서 — 배치를 줄이고 누적으로 유효 배치 지키기 | ✅ | 같은 생각을 대조 학습에 맞춰 썼습니다. MNR 손실은 배치 안의 다른 문서가 음성이라 누적으로는 음성이 늘지 않습니다. 그래서 `CachedMultipleNegativesRankingLoss`로 미니 배치 64씩 계산하면서 음성은 255개를 지켰습니다(ADR-097). |
| 72 | 4비트 양자화(NF4·이중 양자화), QLoRA | ➖ | 학습할 생성 모델이 없고, VM에는 GPU가 없습니다(Day 68 행). |
| 72 | 양자화하면 같은 모델도 다시 평가 | ✅ | 같은 이유로 VM으로 옮길 때 벡터 색인 설정 차이를 적고 다시 쟀습니다([ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)). |
| 73 | DPO 선호 쌍, 참조 모델, beta | ➖ | 정렬할 자체 모델이 없습니다. 거절은 학습이 아니라 라우터 규칙과 범위 기준이 맡습니다([ADR-095](adr/ADR-095-out-of-scope-at-the-router.md)). |
| 73 | 거절을 가르칠 때 과잉 거절 쌍을 함께 넣기 | ✅ | 거절률과 과잉 거절률을 따로 잽니다(범위 밖 50·범위 안 50, ADR-095). 거절을 유도하는 문구가 RLHF 질문까지 거절한 적이 있어, 거절 유도는 범위 밖 확률이 높을 때만 씁니다. |
| 73 | 결과를 보기 전에 정한 규칙으로 고르기 | ✅ | ADR-098은 측정 전에 "떨어지면 셋째 변형은 없다"고 적었고, 졌으므로 로컬 임베딩 학습을 닫았습니다. |
| 73 | 지표로는 퇴행이 안 보여도 답 하나가 단어를 되풀이하며 길게 무너짐 → 길이와 실제 답으로 따로 확인 | ✅ | 같은 실패를 조건 판정에서 만났습니다. 11-02 정기 평가에서 판정 한 번이 출력 128,424토큰·1,442초를 썼고, 평가는 근거 재현율로만 그것을 알았습니다. 판정 출력에 4,000토큰 상한을 두고 깨진 판정은 다시 부르며([ADR-101](adr/ADR-101-retry-a-broken-condition-verdict.md)), 깨질 때마다 길이와 같은 항목의 되풀이 횟수를 남깁니다([ADR-102](adr/ADR-102-log-the-shape-of-a-broken-verdict.md)). |
| 73 | 글자 모양으로 판정할 수 있는 목표는 판사 없이 규칙 함수로 | ✅ | 거절·막음은 사유 코드로 셉니다(Day 70 행, `src/abstain.py`). |
| 73 | IPO·ORPO·KTO·GRPO | ➖ | 정렬할 모델이 없습니다. |

## 임베딩 파인튜닝 (Day 74)

교안은 노무 질의와 정답 조문 쌍으로 임베딩을 MNR 학습하고(교안 01), 어려운 오답을 캐서 거짓 오답을 거른 뒤 Triplet으로
다시 학습합니다(교안 02). 이 프로젝트가 **가장 오래 붙들었던 날**입니다. 인용 쌍과 공동인용 쌍으로 두 번 학습했고 두 번
다 기각했습니다.

| 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- |
| 공개 모델을 먼저 같은 평가로 비교하고, 학습 모델은 그 기준과 견주기(부록) | ✅ | 로컬 공개 모델 넷과 BGE-M3를 먼저 쟀고 모두 OpenAI를 못 넘었습니다([ADR-046](adr/ADR-046-bge-m3-does-not-clear-the-bar.md), [ADR-049](adr/ADR-049-no-local-embedding-clears-the-bar.md)). 학습 모델의 바도 자기 베이스가 아니라 OpenAI였습니다. |
| 도메인 쌍으로 MNR 학습, 평가 질의는 학습에 넣지 않기 | 🧪 | `bge-base`를 인용 쌍으로 학습한 `b256-e3`은 미래 공동인용 과제에서 OpenAI와 동점이었습니다(ADR-097). 정답이 아직 생기지 않은 미래 간선이라 학습 쌍과 겹치지 않습니다. |
| `NO_DUPLICATES`로 같은 정답 문서가 한 배치에 두 번 들어오지 않게 | ✅ | ADR-097부터 넣었습니다. 한 논문이 여러 쌍에 나오는 인용 쌍에서는 그대로 두면 정답이 음성으로 들어갑니다. |
| Hard Negative 마이닝 | 🧪 | 같은 분류인데 인용이 없는 논문을 어려운 오답으로 썼고, 정규화 수준 6개 모두에서 음성 가중치를 0으로 둔 쪽이 나았습니다([ADR-012](adr/ADR-012-graph-hard-negatives.md), [ADR-013](adr/ADR-013-linear-adapter-offline-only.md)). 교안의 거짓 오답 문제가 그대로입니다 - 인용하지 않았다고 무관한 논문은 아닙니다. |
| 최적 에폭을 검증 Recall@5로 복원 | ✅ 다르게 | 고르지 않습니다(Day 71·72 행). |
| 평가 질의를 "학습에서 본 문서인가"로 나눠 보기 | ✅ | ADR-098의 판정이 정확히 이것이었습니다. 공동인용 학습은 시드가 학습 쌍에 나온 간선 1~4개 집단에서 recall@5 +0.0159로 이겼지만, 처음 보는 간선 0개 집단(판정 자리)에서는 hit@5와 MRR이 졌습니다. 본 것을 외운 것이지 일반화가 아니었습니다. |
| 다른 분야 검색의 퇴행 점검 | ➖ | Day 70 행의 🟢였고, 학습 모델이 판정을 통과하지 못해 할 일이 없어졌습니다. |
| 모델을 바꾸면 문서 벡터를 다시 만든다 | ✅ | 후보마다 말뭉치 전체를 다시 인코딩해 쟀습니다(`notebooks/encode_corpus_colab.ipynb`). |

## vLLM 추론 엔진·서빙 (Day 75)

| 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- |
| 오픈소스 LLM을 OpenAI 호환 API로 서빙(`vllm serve`) | ➖ | 답·번역·판정 모두 OpenAI API입니다. 자체 서빙은 GPU가 계속 켜져 있어야 하는데 VM(e2-medium)에는 GPU가 없고, 논문 검색 앱에 자체 LLM 서빙을 붙일 이유가 없다고 로드맵에서 정했습니다(ROADMAP 6절 표, Day 75~76). 코드는 `ChatOpenAI`라 주소만 바꾸면 붙지만, 바꿀 이유가 없습니다. |
| 생성 상한을 지정해 비교하기 | ✅ | 조건 판정에 출력 상한 4,000토큰을 두었습니다(ADR-101). 흘려 받는 호출은 조각이 계속 오는 동안 제한 시간(`timeout=60`)이 끊지 못해서, 시간이 아니라 길이로 막아야 했습니다. |
| KV 캐시·PagedAttention·연속 배치 | ➖ | 서버를 운영하지 않습니다. 같은 문제(앞부분이 같은 입력의 재계산)는 OpenAI의 프롬프트 캐시가 맡고, 압축을 넣지 않은 이유 중 하나가 그 캐시 할인이었습니다([ADR-025](adr/ADR-025-reject-context-compression.md)). |
| 어댑터 여러 개를 베이스 하나로(`model` 이름으로 선택), AWQ·GPTQ 서빙 | ➖ | 서빙할 어댑터와 모델이 없습니다. |
| RunPod Pod 만들기와 외부 접속 | ✅ | 임베딩 학습 두 번을 RunPod에서 돌렸습니다(`notebooks/finetune_scale_runpod.ipynb`, `finetune_cocite_runpod.ipynb`). 토큰은 `getpass`로 받고 찍지 않으며, 가중치는 비공개 저장소에만 올립니다. |

## 서빙 벤치마킹·추론 가속 (Day 76)

교안은 RunPod의 vLLM 서버에 `vllm bench serve`로 부하를 걸어 지연·처리량·비용을 재고, 서버 옵션(접두어 캐싱, KV 캐시 자료형,
동시 실행 상한, 투기적 디코딩)을 `vllm bench sweep serve`로 비교합니다. 이 프로젝트는 서버를 운영하지 않으므로 옵션 쪽은 대부분 ➖이고,
**재는 방법** 쪽이 남습니다.

| 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- |
| 지연 지표: TTFT·요청 지연 | ✅ | `scripts/measure_vm_answer_latency.py`가 VM에 실제 질문을 보내 서버 시간(요청 지연)과 첫 조각까지의 시간(`first_partial_seconds`, TTFT에 해당)을 따로 남깁니다. 노드별 시간과 비중도 적습니다([ADR-084](adr/ADR-084-answer-p50-under-10s-measure-the-vm-first.md)). |
| TPOT·ITL | ➖ | 토큰 생성은 OpenAI 쪽이라 토큰 간격을 우리가 바꿀 수 없습니다. 답의 지연은 모델 턴 수와 도구 호출이 정하므로 그쪽을 잽니다. |
| 평균 대신 백분위로 목표 정하기 | ✅ | 목표는 p50 10초이고 보고서에 p90과 최댓값을 함께 적습니다. "최근" 질문은 평균에 가려지지 않게 따로 잽니다(`--recent`, EXPERIMENTS 11-60·11-61). |
| goodput·목표 달성률(목표를 지킨 요청의 비율) | 🟢 | 지금 보고서의 판정은 p50 하나뿐이라, 10초를 넘긴 문항이 몇 개인지는 표에서 세어야 합니다. "10초 안에 끝난 비율"을 한 줄 더 적으면 느린 꼬리가 보입니다. 다만 15문항이면 한 문항이 6.7%라, 교안의 0.95 경계로 판정하지는 않습니다(아래 🟢 7번). |
| 접두어 캐싱: 공통 부분을 앞에, 요청마다 다른 부분을 뒤에 | ✅ | OpenAI 프롬프트 캐시가 같은 일을 합니다. 지시문을 앞에 두고, 질문마다 다른 것(번역·제목 해석·"최근" 힌트)은 뒤의 턴 메시지로 붙입니다(ADR-087·088·090). 평가가 캐시에서 읽은 입력 토큰(`cache_read`)을 기록하고(`evals/run.py`, `scripts/measure_agent_output.py`), 입력의 96%가 이미 캐시에서 읽힌다는 것이 압축을 넣지 않은 이유였습니다([ADR-025](adr/ADR-025-reject-context-compression.md)). |
| 동시 요청 수를 바꿔 가며 재기(1·16·64) | ➖ | API는 질문 하나를 실행기 스레드에서 돌리고(`src/api.py`), 동시 부하는 재 본 적이 없습니다. 사용자가 적고 IP당 분당 3번으로 막아 두어 지금은 잴 이유가 약합니다. 사용자가 늘면 VM 안에서 동시 1·2·4로 재고, 목표 달성률이 떨어지는 지점을 상한으로 정합니다. |
| 100만 토큰당 비용 | ✅ | GPU 시간 대신 API 요금으로 셉니다. 평가가 문항당·실행당 추정 비용을 기록합니다(`estimated_api_cost_usd`, `evals/gate.py`). |
| KV 캐시 fp8, 동시 실행 상한(`--max-num-seqs`), 투기적 디코딩(MTP), `bench sweep` | ➖ | 서버를 운영하지 않습니다(Day 75와 같은 이유). |
| 한 번의 측정으로 단정하지 않기(측정 예의 결론) | ✅ | 지연 비교는 main과 같은 시각에 짝으로 돌리고 부트스트랩 구간을 적습니다. 예: ADR-103은 18쌍, 짝 차이 −2.50초 [−4.89, −0.19]. |
| 서버 로그의 처리량은 구간 평균, Running·Waiting은 순간값(부록) | ➖ | vLLM 서버 로그가 없습니다. 우리 로그는 요청마다 한 줄이라 구간 평균과 섞일 일이 없습니다. |

## FastAPI 모델 서빙 API (Day 77)

교안은 RunPod의 vLLM 앞에 FastAPI 서빙 API를 세웁니다. 기초(경로·쿼리 매개변수, pydantic 양식과 422, `def`와 `async def`,
`StreamingResponse`, lifespan, CORS)부터 vLLM 중계, 서비스 키 인증·요청 한도·한 양식의 오류 응답, SQLite 키 관리와
사용량 기록까지 다룹니다. 이 프로젝트의 `src/api.py`가 같은 자리에 있어서(VM, Caddy 뒤, [ADR-081](adr/ADR-081-fastapi-beside-streamlit-on-the-vm.md))
대부분 이미 들어가 있습니다. 재 보지 않은 두 가지가 남습니다.

| 교안 내용 | 상태 | 어떻게 / 결과 / 이유 |
| --- | --- | --- |
| pydantic 양식, 입력 제약과 422 | ✅ | 질문 2,000자, 대화 6턴, 번역 8,000자 상한을 둡니다(`MAX_QUESTION_CHARS` 등). 순위·인용 목록의 잘못된 인자도 422로 돌려줍니다. |
| 막는 일은 스레드에서(`def`와 `async def`) | ✅ | 답과 번역은 LangChain의 동기 호출이라 `run_in_executor`로 돌리고, 이벤트 루프는 SSE만 흘립니다. `/api/translate`는 `def`라 FastAPI가 스레드 풀에서 돌립니다. |
| lifespan에서 한 번 준비하기 | ✅ | 부팅 때 인용망과 모델을 배경에서 깨웁니다([ADR-067](adr/ADR-067-warm-the-citation-graph-at-boot.md)). 모델 클라이언트는 프로세스에 하나입니다. |
| SSE 스트리밍 중계 | ✅ | `/api/ask`와 `/api/translate/stream`이 `partial`·`final`·`error` 이벤트를 보냅니다. Caddy는 `flush_interval -1`로 조각을 모아 두지 않습니다(ADR-114 측정에서 조각이 하나씩 도착). |
| 첫 조각 뒤의 실패: 끝 신호(`[DONE]`)를 받았는지로 완료를 판단 | 🟢 | 번역은 그렇게 합니다(`translateStream`이 `final` 없이 끝나면 오류). **질문은 그렇지 않습니다.** `web/lib/api.ts`의 `ask()`는 스트림이 `final`·`abstain`·`error` 없이 닫히면 아무것도 내지 않고 끝납니다. 그러면 그 턴은 `streaming` 상태에 머물고, `busy` 때문에 입력창과 "새 질문"이 새로고침할 때까지 막힙니다. API 재시작이 진행 중인 답을 끊는 일은 [ADR-107](adr/ADR-107-restart-the-api-only-when-it-reads-the-change.md)이 이미 적어 두었습니다. 연결이 깔끔하게 닫히는 경우에만 생기고(리셋이면 `catch`가 오류로 바꿉니다), 실제로 멈춘 것을 본 적은 없습니다. 아래 🟢 8번. |
| 서버가 시스템 메시지를 정하고 사용자는 질문만 보냄 | ✅ | 지시문은 서버에 있고, 사용자는 질문과 앞선 대화만 보냅니다. 주입 시도는 레드팀 셋으로 막습니다([ADR-094](adr/ADR-094-red-team-through-the-whole-app.md)·[096](adr/ADR-096-dictated-evidence-ids.md)). |
| 백엔드 시간 제한, 다시 시도는 서빙 API가 정함(`max_retries=0`) | ✅ | 모델 호출은 `timeout=60, max_retries=1`입니다. 시간 제한을 늘리는 대신 한 번 다시 시도하기로 쟀습니다([ADR-015](adr/ADR-015-retry-not-higher-timeout.md)). 흘려 받는 호출은 시간 제한이 끊지 못해 출력 상한으로 막습니다(Day 75). |
| 백엔드 실패를 종류별로 알리기(시간 초과 504, 연결 실패 502) | 🟢 | 화면에는 상태 코드보다 **할 일**이 중요합니다. `src/model_outage.py`가 이미 예외를 "한도가 다 됨(초기화를 기다림)"과 "제공자가 응답하지 않음(잠시 뒤 다시)"으로 나누고, Streamlit은 그 문장을 보여 줍니다. **웹 API는 쓰지 않습니다.** `/api/ask`의 `error` 이벤트는 늘 "답변을 만들지 못했습니다. 다시 시도해 주세요."이고 예외 이름만 붙입니다. 한도가 다 된 날에는 다시 물어도 같은 실패가 납니다. 아래 🟢 9번. |
| 요청 한도: 60초 창과 `Retry-After` | ✅ | IP마다 분당 3번·하루 30번, 전체 하루 300번이고 429에 `Retry-After`를 붙입니다. CORS가 그 헤더를 화면에 열어 주고(`expose_headers`), 화면이 "N초 뒤"를 알려 줍니다. 번역은 따로 셉니다(ADR-108). |
| CORS: 허용한 출처만 | ✅ | `API_ALLOWED_ORIGINS`와 Vercel 미리보기용 `API_ALLOWED_ORIGIN_REGEX`만 받고, 메서드는 GET·POST뿐입니다. |
| 상태 확인 주소는 키 없이(`/healthz`) | ✅ | `/api/health`가 커밋·모듈 커밋·인용망·주간 수집 상태를 돌려주고, 배포 점검이 읽습니다(ADR-106). |
| 한 양식의 오류 응답(`{"error": {"code", "message"}}`) | ➖ | 오류를 읽는 곳이 우리 화면 하나입니다. 질문·번역은 SSE의 `error` 이벤트로, 그 밖은 FastAPI 기본 `detail`로 옵니다. 화면은 429를 상태 코드로 먼저 가르고 나머지는 상태 코드를 적어 보여 줍니다. 양식을 바꾸면 화면도 함께 바꿔야 하는데, 얻는 것이 없습니다. |
| `response_model`로 내부 항목 빼기 | ➖ | `final`에는 진단 항목(호출 기록, 노드별 시간)이 들어 있고, 그것을 화면의 "근거·과정"과 지연 측정 스크립트가 읽습니다. 저장소가 공개라 감출 내부 값도 아닙니다. 키나 비밀번호는 응답에 넣지 않습니다. |
| `/docs` 문서 화면 | ➖ | Caddy가 `/api/*`만 API로 보내므로 `/docs`는 밖에서 열리지 않습니다. |
| 서비스 키(`X-API-Key`), 해시로 저장, 관리자 발급·폐기(SQLite) | ➖ | 로그인 없이 누구나 쓰는 공개 데모입니다. 키를 화면에 넣으면 브라우저에서 보여 키가 아닙니다. 남용은 IP 한도와 전체 하루 상한이 막습니다. 다른 서비스가 이 API를 부르게 되면 그때 키와 키별 한도를 붙입니다. |
| 키별 토큰 사용량 기록 | ➖ | 키가 없고, 비용은 전체 하루 300번 상한이 묶습니다. 문항당 토큰과 추정 비용은 평가가 기록하고(`estimated_api_cost_usd`), VM 실제 답의 토큰은 `final`의 호출 기록에 있어 지연 측정 보고서가 읽습니다. |
| vLLM 서빙, 생각 켜기·끄기(`enable_thinking`) | ➖ / ✅ | 서빙은 Day 75와 같은 이유로 ➖. 추론을 끄는 쪽은 판정·에이전트·번역 모두 했습니다([ADR-089](adr/ADR-089-judge-without-reasoning.md)·[091](adr/ADR-091-agent-without-reasoning.md)·[112](adr/ADR-112-translation-without-reasoning.md)). |

**Day 69 교안 02는 정리한 뒤(11-02) 고쳐졌습니다.** 다시 열어 보니 목차(패러프레이즈, 역번역, 뜻 보존, 품질 필터, 오염 검사, 원본 단위 분할,
데이터 카드, chat template)가 위 Day 65~69 표와 같습니다. 중복 제거와 chat template은 SFT 데이터를 만들지 않으므로 ➖입니다.

## 🟢 적용할 것 모아 보기

| 우선 | 항목 | 왜 지금 | 조건 |
| --- | --- | --- | --- |
| 1 | ~~평가 질문 바꿔 말하기(Day 69)~~ ✅ [ADR-093](adr/ADR-093-paraphrased-eval-questions.md) | 60문항 모두 원문과 같은 점수 | 주간 게이트에 넣음(`paraphrase` 분할, 사례별 만점만) |
| 2 | ~~레드팀 질문 셋(Day 62)~~ ✅ [ADR-094](adr/ADR-094-red-team-through-the-whole-app.md) | 범위 밖 질문 2/5에 관계없는 근거 | 라우터로 고침([ADR-095](adr/ADR-095-out-of-scope-at-the-router.md)), 주입한 근거 ID도 막음([ADR-096](adr/ADR-096-dictated-evidence-ids.md)), 다시 돌려 19/19 |
| 3 | 저자 동명이인 구별(Day 39) | `coauthor` 가족의 거짓 공저자 | Semantic Scholar 저자 ID 수집과 함께 |
| 4 | 임베딩 퇴행 점검(Day 70) | ADR-097 학습은 논문 → 논문만 봅니다 | ~~`b256-e3`이 판정을 통과하면~~ 통과하지 못해 할 일이 없어졌습니다(2026-10-29, 미래 과제에서 동점 — ADR-097 기각) |
| 5 | 가중치 보간 WiSE-FT(Day 70) | 다시 학습하지 않고 퇴행을 줄임 | 4번과 함께 없어졌습니다 |
| 6 | 문항당 출력 길이 경보(Day 73·75) | 11-02의 12.8만 토큰 판정을 평가는 근거 재현율이 떨어진 뒤에야 알았습니다 | 회귀 평가 보고서에 문항당 출력 토큰 최댓값을 적고, 정상 실행의 최대(1,085토큰)보다 훨씬 크면 경고만 냅니다. 실패로 막을지는 몇 주 값을 본 뒤에 정합니다 |
| 7 | ~~지연 보고서에 목표 달성률(Day 76)~~ ✅ bb8ad2b | 판정이 p50 하나라 10초를 넘긴 문항이 몇 개인지 보이지 않았습니다 | `measure_vm_answer_latency.py`의 요약에 "10초 안에 끝난 비율"과 넘긴 문항 ID만 더합니다. 문항이 15개라 판정 기준으로 쓰지 않고, p50 목표는 그대로 둡니다 |
| 8 | 질문 스트림이 끝 신호 없이 닫히면 오류로(Day 77) | 재시작이 끊은 답은 화면을 새로고침할 때까지 "답하는 중…"에 묶어 둘 수 있습니다 | `ask()`가 `final`·`abstain`·`error` 없이 끝나면 `error`("답이 중간에 끊겼습니다. 다시 물어 주세요.")를 냅니다. `translateStream`과 같은 처리이고, vitest로 확인합니다. 웹만 바뀌어 VM API는 다시 뜨지 않습니다 |
| 9 | 웹 API 오류에 할 일 문장(Day 77) | 한도가 다 된 날에는 "다시 시도해 주세요"를 따라도 같은 실패가 납니다 | `/api/ask`·번역의 `error` 이벤트 메시지에 `model_outage.message(exc)`를 먼저 씁니다. Streamlit과 같은 문장입니다. `src/`가 바뀌므로 API가 다시 뜹니다(ADR-107) |
