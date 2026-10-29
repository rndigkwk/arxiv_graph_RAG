# 교안 적용 정리 (Day 02~70, 2026-10-29)

엔코아 교안(`class_1`의 다섯 저장소, `enkoa-practice-deep-learning/내작업`의 Day 65~70, OpenAI Decisions
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
| LoRA(다음 시간 예고) | ➖ | 교안 02가 다음 시간으로 미뤘습니다. Day 71을 받으면 봅니다. |

## 🟢 적용할 것 모아 보기

| 우선 | 항목 | 왜 지금 | 조건 |
| --- | --- | --- | --- |
| 1 | ~~평가 질문 바꿔 말하기(Day 69)~~ ✅ [ADR-093](adr/ADR-093-paraphrased-eval-questions.md) | 60문항 모두 원문과 같은 점수 | 주간 게이트에 넣음(`paraphrase` 분할, 사례별 만점만) |
| 2 | ~~레드팀 질문 셋(Day 62)~~ ✅ [ADR-094](adr/ADR-094-red-team-through-the-whole-app.md) | 범위 밖 질문 2/5에 관계없는 근거 | 라우터로 고침([ADR-095](adr/ADR-095-out-of-scope-at-the-router.md)), 주입한 근거 ID도 막음([ADR-096](adr/ADR-096-dictated-evidence-ids.md)), 다시 돌려 19/19 |
| 3 | 저자 동명이인 구별(Day 39) | `coauthor` 가족의 거짓 공저자 | Semantic Scholar 저자 ID 수집과 함께 |
| 4 | 임베딩 퇴행 점검(Day 70) | ADR-097 학습은 논문 → 논문만 봅니다 | ~~`b256-e3`이 판정을 통과하면~~ 통과하지 못해 할 일이 없어졌습니다(2026-10-29, 미래 과제에서 동점 — ADR-097 기각) |
| 5 | 가중치 보간 WiSE-FT(Day 70) | 다시 학습하지 않고 퇴행을 줄임 | 4번과 함께 없어졌습니다 |
