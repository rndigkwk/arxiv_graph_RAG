# GCP 이전과 프론트 교체 계획 (2026-10-26)

논문을 늘리기로 정했습니다. Aura Free의 관계 한도(지금 85.8%)로는 늘릴 수 없으므로 DB를 Google Cloud
VM으로 옮기고, 그 위에서 앱을 FastAPI + Next.js로 바꿉니다. 기간은 GCP 무료 체험 크레딧이 버티는
**3개월**입니다. 근거는 [ADR-071](adr/ADR-071-double-the-graph-move-only-the-database.md)(VM 한 대에
앱과 DB)과 [ADR-016](adr/ADR-016-stay-on-streamlit.md)(Streamlit을 떠나는 조건 2: FastAPI 분리)입니다.

**순서의 이유.** 논문 늘리기가 가장 오래 걸립니다(Semantic Scholar 조회·임베딩·주간 수집이 몇 주에
걸쳐 쌓입니다). 프론트는 DB가 어디 있든 접속 주소만 바꾸면 되므로 그동안 나란히 만듭니다. 그래서
DB가 먼저입니다.

## 0. 백업 — 끝남 (2026-10-26)

- 이 저장소에 태그 `streamlit-aura-v1`(`9016173`)을 달았습니다. Streamlit Cloud + Aura Free의 마지막 판입니다.
- 같은 커밋을 `rndigkwk/arxiv_graph_RAG_backup`의 `main`으로 올렸습니다(옛 판 `a3f611f`에서 fast-forward).
- 백업 저장소는 **Actions를 껐습니다.** 시크릿이 없어 배포 점검·주간 수집·회귀 평가가 매번 실패하고
  alert 이슈를 열기 때문입니다. 회귀 평가는 OpenAI 요금도 냅니다.
- 백업 저장소로 Streamlit 앱을 새로 만듭니다(사람이 Streamlit Cloud에서). 이 앱은 **계속 Aura Free**를 봅니다.

**백업 앱의 시크릿** — 이름만 적습니다. 값은 지금 앱의 시크릿과 같습니다.

| 이름 | 넣는가 | 이유 |
|---|---|---|
| `OPENAI_API_KEY` | 넣음 | 답변·번역·임베딩 |
| `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | 넣음 | Aura Free 접속 |
| `LANGFUSE_TRACING` | `"0"`으로 넣음 | 추적은 키가 없으면 어차피 꺼지지만(`pipeline/tracing.py`), 무료 한도를 두 앱이 나눠 쓰지 않게 명시합니다 |
| `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | **넣지 않음** | 위와 같음 |
| `CLIENT_ID`, `CLIENT_SECRET`, `CLIENT_NAME` | **넣지 않음** | PageRank 탭의 GDS 세션용입니다. Free 등급은 동시 세션 1개라 두 앱이 다툽니다. 기본 엔진(직접 계산)은 없어도 됩니다 |
| `AURA_INSTANCEID`, `AURA_INSTANCENAME` | 넣지 않음 | 앱 코드가 읽지 않습니다 |

**3개월 뒤의 출구.** 크레딧이 끝나면 VM이 멈춥니다. 그때도 포트폴리오 링크가 살아 있어야 하므로 이
백업 앱이 남습니다. 그래서 **Aura를 깨우는 `keep-alive` 워크플로는 이전 뒤에도 이 저장소에서 끄지
않습니다**(Aura Free는 쓰지 않으면 일시 중지됩니다).

## 1. GCP VM에 DB와 지금 앱 — 가동 (2026-10-26)

**<https://8-230-25-138.sslip.io>** 에서 돕니다(e2-medium, 서울, 프로젝트 `arxiv-512809`). 첫날 측정은
[ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)의 "GCP VM 첫날"입니다.

**실행 안내는 [GCP_VM.md](GCP_VM.md)입니다.** 같은 compose를 로컬 Docker에서 리허설했습니다
([ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)). 아래 "먼저 확인"의 답:
거울 사본은 **별도 볼륨**(`arxiv-graphrag-double-data`)에 실렸으므로 섞이지 않았습니다. 다만 로컬
스냅샷(10-14)이 낡아 쓰지 않고, 옮기는 날 Aura에서 다시 내보냅니다.

- VM 한 대(4GB, 서울)에 Neo4j 5.26 Community + GDS Community. Neo4j는 `127.0.0.1`에만 묶고 인터넷에 열지 않습니다.
- 지금 그래프를 옮겨 싣습니다. 로컬 Docker 볼륨 `arxiv-graphrag-local-data`에 ADR-071 측정의 거울 사본(2배)이
  섞였는지 **먼저 확인**하고, 섞였으면 스냅샷 파이프라인으로 다시 짓습니다.
- **지금 Streamlit 앱도 같은 VM에 올립니다.** Streamlit Cloud에서 VM의 DB에 닿으려면 Bolt를 열어야 하고,
  ADR-071이 피한 위험이 그것입니다. 앱 하나만 HTTPS로 엽니다.
- 그날 잴 것(ADR-071 결정 4): 앱과 DB를 **동시에** 띄운 메모리(여유 1GB 안팎으로 추정만 있음),
  `CITATION_EDGES` p95. 메모리가 모자라면 8GB(e2-standard-2)로 올립니다.
- 배포 점검(`scripts/visit_deployed_app.py`)의 주소를 새 앱으로 바꿉니다.

## 2. 수집 확장

- 주간 수집에 Semantic Scholar 참고문헌 조회를 붙입니다. 지금의 "인용 보강"은 Aura 자신을 조회하므로
  새 논문에 참고문헌이 하나도 붙지 않습니다(`pipeline/ingest.py`의 `lookup`).
- 인용된 논문을 노드로 들입니다(원래 그래프를 지은 방식: 씨앗 6,467편 → 38,665편).
- 새 논문의 참고문헌은 Semantic Scholar에 늦게 올라옵니다. 1~2주 지난 논문을 다시 조회합니다.
- 수집은 VM의 cron에서 돕니다. GitHub Actions에서 돌리면 DB를 열어야 합니다.
- ~~먼저 확인할 것: "새 논문 103편/주"~~ → 풀렸습니다(EXPERIMENTS 11-56). 10-24 적재가 같은 창을 덮었던 것이고 실제는 주 약 1,000편입니다.
- **들일 범위**: 한 주에 그래프 밖 인용 논문이 3,297편, 두 번 이상 인용된 것은 115편입니다(EXPERIMENTS 11-56). 범위를 정해야 합니다.
- Aura 주간 수집 워크플로는 2026-10-27에 껐습니다(`gh workflow disable`). 백업 앱은 그 시점 그래프로 고정됩니다.

## 3. FastAPI 층 — 가동 (2026-10-27)

- `chatbot.ask`(스트리밍), 부분 그래프, 순위, 품질 데이터를 API로 엽니다. 같은 VM, 같은 코드.
- 이 단계에서 Streamlit은 그대로 둡니다. 둘이 같은 모듈을 씁니다.
- `src/api.py`, compose의 `api` 서비스, Caddy의 `/api/*`([ADR-081](adr/ADR-081-fastapi-beside-streamlit-on-the-vm.md)).
  질문은 SSE, 질문·번역에만 IP별·전체 상한.
- 배포 뒤 잰 것(ADR-081 "배포 첫날"): 두 프로세스가 다 올라온 뒤 available 1,160 MiB. SSE는 첫
  `partial`이 0.05초에 옵니다.

## 4. Next.js (Vercel)

- [프론트 시안](https://claude.ai/artifact/38K4WuVFbroXpW8xwB5i5w)의 다섯 화면: 질문 작업대, 논문 페이지,
  탐색, 품질, 휴대폰. **범위는 이 다섯으로 묶습니다.**
- Vercel 무료 플랜에 올립니다. VM에는 API와 DB만 남아 메모리 여유가 지켜집니다.
- 지금 Streamlit 화면에 묶인 테스트(93개 파일 중 10개)는 API 테스트와 프론트 테스트로 옮깁니다.

## 5. VM의 Streamlit 내리기

- 새 화면이 지금 화면보다 낫다고 확인한 뒤에 내립니다. 백업 앱(0단계)은 남깁니다.

## 요금

| 항목 | 월 | 3개월 |
|---|---:|---:|
| VM 4GB(e2-medium) + 디스크 + 고정 IP | 약 $37 | 약 $111 |
| 8GB로 올리면 | 약 $63 | 약 $190 |
| Vercel 무료 플랜 · Aura Free · Streamlit Cloud | $0 | $0 |

VM 가격은 제3자 집계입니다(ADR-071). 결제 전에 Google 가격표로 확인합니다. **OpenAI 요금은 GCP 크레딧과
별개**입니다. 무료 체험 90일은 가입한 날부터 세므로 1단계를 시작하는 날 가입합니다.
