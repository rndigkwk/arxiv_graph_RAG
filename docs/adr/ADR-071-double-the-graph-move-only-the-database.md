# ADR-071: 두 배가 필요해지면 DB만 옮긴다 — 4GB 한 대면 되고, 먼저 닿는 것은 30초다

- **상태:** 채택 (두 배가 필요해질 때 적용합니다. 지금은 Aura Free를 유지합니다)
- **날짜:** 2026-10-26

## 맥락

"Aura Free 한도를 넘겨 그래프를 두 배로 키우려면 어디에 올리나"를 외부 모델(fable)에 물었고,
세 안 중 **Cloud Run 앱 + 별도 GCE VM의 Neo4j(월 ~$33)**를 권하는 답이 왔습니다. 근거는 셋이었습니다.

1. "Streamlit 잠들기가 노출돼 [ADR-016](ADR-016-stay-on-streamlit.md)의 전환 조건 1이 충족됐다."
2. 10-14 로컬 실측의 DB 컨테이너 피크 1.745GiB를 두 배 하면 **"DB 혼자 4GB"**가 필요하다.
3. 그러니 앱과 DB를 한 VM에 올리는 안은 메모리가 모자란다.

**전제는 맞습니다.** 2026-10-26 라이브 조회로 노드 151,627/200,000(75.8%), 관계
343,126/400,000(**85.8%**)이고, 두 배면 관계가 한도의 172%입니다. Aura Free로는 안 됩니다.

**근거는 셋 다 맞지 않았습니다.**

- 1은 두 번째 오인용입니다. 조건 원문은 "**keep-alive가 비활성화돼** 실제로 `Zzzz` 화면이 노출된
  적"이고 비활성화는 없었습니다(ADR-016에 10-25 대조를 적었습니다).
- 2의 1.745GiB는 데이터의 크기가 아니라 **설정의 크기**입니다. 그 측정은 heap 1G·page cache 1G로
  고정했고, [`LOCAL_DOCKER_RESULT.md`](../../LOCAL_DOCKER_RESULT.md)가 스스로 "단순히 두 배로
  계산하면 안 됩니다"라고 적어 두었습니다. `/data` 1,268MiB 중 771MiB는 트랜잭션 로그였습니다.
- 그래서 3도 근거를 잃습니다.

반대쪽 추정(원시 데이터 160MB의 두 배라 2GB급이면 된다)도 인덱스를 빼고 셌습니다. **두 추정이
열 배 차이로 맞섰고 둘 다 측정이 아니었습니다.** 그래서 쟀습니다.

## 측정

같은 컨테이너(heap 1G · page cache 1G · 한도 4GiB · 2 CPU, 10-14와 같은 설정)에서 1배를 적재해
재고, 그 위에 **거울 사본**을 얹어 2배를 쟀습니다. 다른 날의 수를 빼지 않습니다.
스크립트는 [`scripts/measure_double_graph.py`](../../scripts/measure_double_graph.py)이고,
Aura와 OpenAI에 접속하지 않습니다(0원).

| | 1배 | 2배 |
|---|---:|---:|
| 그래프 저장소 | <!--n:reports/double_graph.json#stages.1x.data_mib.graph_store-->320.6<!--/n-->MiB | **<!--n:reports/double_graph.json#stages.2x.data_mib.graph_store-->640.6<!--/n-->MiB** |
| 스키마 인덱스(벡터 포함) | <!--n:reports/double_graph.json#stages.1x.data_mib.schema_index-->129.9<!--/n-->MiB | <!--n:reports/double_graph.json#stages.2x.data_mib.schema_index-->278.0<!--/n-->MiB |
| 트랜잭션 로그 | <!--n:reports/double_graph.json#stages.1x.data_mib.transaction_logs-->513.6<!--/n-->MiB | <!--n:reports/double_graph.json#stages.2x.data_mib.transaction_logs-->1025.7<!--/n-->MiB |
| DB 컨테이너 메모리(적재 중 샘플 최대) | <!--n:reports/double_graph.json#stages.1x.memory_during_load.max_gib-->1.791<!--/n-->GiB | **<!--n:reports/double_graph.json#stages.2x.memory_during_load.max_gib-->2.14<!--/n-->GiB** / 4GiB |
| `CITATION_EDGES` p95 (로컬) | <!--n:reports/double_graph.json#stages.1x.queries.citation_edges.p95-->2.579<!--/n-->초 | <!--n:reports/double_graph.json#stages.2x.queries.citation_edges.p95-->5.012<!--/n-->초 |
| `PAPER_ROWS` p95 (로컬) | <!--n:reports/double_graph.json#stages.1x.queries.paper_rows.p95-->1.817<!--/n-->초 | <!--n:reports/double_graph.json#stages.2x.queries.paper_rows.p95-->3.134<!--/n-->초 |
| 앱의 인용망 예열 (로컬) | <!--n:reports/double_graph.json#stages.1x.app_path.citation_graph_seconds-->5.77<!--/n-->초 | <!--n:reports/double_graph.json#stages.2x.app_path.citation_graph_seconds-->11.51<!--/n-->초 |
| 앱 Python 피크 메모리 | <!--n:reports/double_graph.json#stages.1x.app_path.peak_mb-->261.5<!--/n-->MB | <!--n:reports/double_graph.json#stages.2x.app_path.peak_mb-->297.9<!--/n-->MB |

세 가지가 읽힙니다.

**메모리는 모자라지 않습니다.** 2배의 그래프 저장소 <!--n:reports/double_graph.json#stages.2x.data_mib.graph_store-->640.6<!--/n-->MiB가
page cache 1GiB 안에 들어갑니다. 설정을 고정한 채 컨테이너가 2배에서 쓴 것은
<!--n:reports/double_graph.json#stages.2x.memory_during_load.max_gib-->2.14<!--/n-->GiB입니다. 벡터 인덱스
<!--n:reports/double_graph.json#stages.2x.data_mib.schema_index-->278.0<!--/n-->MiB는 page cache 밖(OS 파일 캐시)에 올라가므로 이것을
더해도 **4GB 한 대면 DB에 충분합니다.** "DB 혼자 4GB"는 틀렸고, "2GB급"도 인덱스와 OS를
빼면 빠듯합니다.

**앱 쪽도 한도가 아닙니다.** Streamlit Cloud 보장치 690MB에 2배에서
<!--n:reports/double_graph.json#stages.2x.app_path.streamlit_headroom_mb-->392.1<!--/n-->MB가 남습니다.

**먼저 닿는 것은 30초입니다.** 같은 69,438행의 `CITATION_EDGES`가 로컬에서는 p95
<!--n:reports/double_graph.json#stages.1x.queries.citation_edges.p95-->2.579<!--/n-->초, Aura에서는 10.25초입니다
([`reports/graph_load_latency.json`](../../reports/graph_load_latency.json), 10-16) — **행 수가
아니라 거리가 지연의 4분의 3입니다.** 다른 날 다른 배치의 두 값이라 비율은 대략입니다. Aura에서 잰 값에 같은 배치의 로컬 증가율을 곱하면 2배의
p95가 **<!--n:reports/double_graph.json#growth.aura_projection.citation_edges.aura_p95_projected-->19.89<!--/n-->초**, 30초까지 여유가
**<!--n:reports/double_graph.json#growth.aura_projection.citation_edges.headroom_ratio_projected-->1.51<!--/n-->배**입니다. 이것은
측정이 아니라 추정이고, "Aura만큼 먼 DB"를 가정한 값입니다.

## 결정

**1. 두 배가 필요해지면 DB만 자체 호스팅으로 옮기고 앱은 Streamlit Cloud에 둡니다.**
[ROADMAP 8-6절](../../ROADMAP.md#8-6-합계)의 "Streamlit Cloud + 자체 호스팅 Neo4j" 행입니다.
heap 1G · page cache 1G로 시작하고 메모리 4GB 한 대면 됩니다. 앱과 DB는 독립적인 선택이고,
필요한 것은 DB 용량뿐입니다.

**2. Cloud Run 앱 + GCE VM(fable 안)은 기각합니다.** VM 크기(e2-medium 4GB)는 결과적으로
맞았지만 이유가 틀렸고, 앱 이사는 필요가 없습니다. 이사하면 잃는 것이 있습니다 —
`min=0`이면 컨테이너가 식을 때마다 인용망 예열을 다시 내는데
([ADR-067](ADR-067-warm-the-citation-graph-at-boot.md)), 그 값이 2배에서 로컬로도
<!--n:reports/double_graph.json#stages.2x.app_path.citation_graph_seconds-->11.51<!--/n-->초입니다. 그리고 ADR-016의 전환 조건은
충족되지 않았습니다.

**3. Aura Professional은 계속 기각입니다.** 1GB에 월 ~$66을 내도 메모리가 늘지 않고(지금이 이미
1GB), PageRank·Leiden에 쓰는 GDS가 여전히 없습니다. 자체 호스팅은 GDS Community가 내장입니다.

**4. 두 배를 적재하기 전에 DB와 앱 사이의 거리를 잽니다.** 30초는 `chatbot.run_read`가 모든
질의에 거는 타임아웃입니다. 새 호스트에서 `CITATION_EDGES`의 p95를 먼저 재고, 1.5배보다 여유가
작으면 적재보다 그 질의(한 번에 전부 읽기)를 먼저 고칩니다.

## 민감도

- **거울 사본은 실제 두 배가 아닙니다.** 저자가 전부 새 사람이라 노드·AUTHORED의 상한이고,
  인용망이 서로 이어지지 않는 두 덩어리이며, 임베딩이 원본과 똑같습니다. 읽는 행 수와 저장소
  크기는 맞고, 검색 품질과 PageRank 의미는 재지 않았습니다.
- **로컬 지연은 루프백입니다.** 클라우드 VM, 인터넷 왕복, 동시 접속이 들어 있지 않습니다. 결정 4가
  있는 이유입니다. 30초 추정은 "Aura만큼 먼 DB"를 **가정**한 값이고, DB가 더 가까우면 여유가 늘고
  더 멀면 줄어듭니다.
- **e2-medium은 shared-core입니다.** 이 측정은 전용 2 CPU로 했습니다. 공유 코어에서의 지연은 재지
  않았습니다.
- **벡터 검색은 2배에서 오히려 빨라 보이지만 순서 효과입니다.** 1배 p95
  <!--n:reports/double_graph.json#stages.1x.queries.vector_top5.p95-->0.153<!--/n-->초는 첫 실행이 식은 인덱스를 만난 값입니다. 증가율로
  읽으면 안 됩니다.
- **`db.checkpoint()`가 이 판에서 거부됐습니다**(ClientError). 그래도 저장소 크기가 정확히
  <!--n:reports/double_graph.json#growth.data_mib.store-->2.04<!--/n-->배로 나와 비교는 성립합니다. 트랜잭션 로그는 보존 설정에 따라
  달라지며 page cache가 담을 대상이 아닙니다.
- **비용은 확인하지 않았습니다.** 4GB VM의 2026-10 가격은 출처마다 다릅니다(Seoul e2-medium 약
  $31, 유럽 저가 VPS는 한 자릿수 유로). 결제 전에 공급자 페이지를 봐야 합니다.

## 넘지 않은 선

- 실제 두 배를 수집하지 않았습니다. 유료 클라우드 자원도 만들지 않았습니다.
- 자체 호스팅의 진짜 비용 — 공개 TLS 엔드포인트, 인증서·방화벽·인증, 백업 — 은 그대로
  남습니다(ROADMAP 8-2-1절). 이 측정은 그 비용을 줄이지 않습니다.
- 앱의 `CITATION_EDGES`를 나눠 읽는 수정은 하지 않았습니다. 결정 4의 측정이 먼저입니다.

## 참고

- [ADR-001](ADR-001-neo4j-graph-and-vector.md) — 관계 한도가 이 결정을 흔든다
- [ADR-016](ADR-016-stay-on-streamlit.md) — 앱을 옮기는 조건
- [ADR-067](ADR-067-warm-the-citation-graph-at-boot.md) — 부팅 때의 인용망 예열
- [`LOCAL_DOCKER_RESULT.md`](../../LOCAL_DOCKER_RESULT.md) — 10-14 1배 실측
