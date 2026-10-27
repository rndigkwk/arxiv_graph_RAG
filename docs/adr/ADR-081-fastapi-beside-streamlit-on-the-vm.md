# ADR-081: FastAPI 층은 같은 VM에서 Streamlit 옆에 둔다 — 돈이 드는 길에만 문턱을 둔다

- **상태:** 채택
- **날짜:** 2026-10-27

## 맥락

이전 계획 3단계입니다([MIGRATION_PLAN](../MIGRATION_PLAN.md)). 4단계의 Next.js 화면(시안 다섯 화면)이
부를 HTTP 층이 필요합니다. 지금 앱은 `src.chatbot`을 Streamlit에서 직접 부르고, 부분 답변은
`on_answer` 콜백으로 그립니다.

[ADR-016](ADR-016-stay-on-streamlit.md)의 전환 조건 2는 "FastAPI 검색 API 분리를 하기로 결정했을 때"이고,
그때는 Cloud Run을 호스트로 보았습니다. 지금은 DB 때문에 VM이 이미 있습니다
([ADR-071](ADR-071-double-the-graph-move-only-the-database.md), [ADR-079](ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)).

## 결정

- **같은 VM, 같은 이미지, 같은 모듈.** compose에 `api` 서비스를 더합니다(`uvicorn src.api:app`, 워커
  하나). 앱과 같은 이미지·마운트·`.env`이고, Streamlit은 그대로 둡니다. 둘 다 `src.chatbot`을 엽니다.
  FastAPI와 uvicorn은 `api` extra로 두어 Streamlit Cloud 백업 앱에는 설치되지 않습니다.
- **Caddy가 `/api/*`만 넘깁니다.** 호스트 포트는 없습니다. 질문은 Server-Sent Events라 Caddy가
  모아 두지 않게 하고(`flush_interval -1`) 압축하지 않습니다.
- **질문은 SSE입니다.** `partial`(부분 답변) 여러 개 뒤에 `final`·`abstain`·`error` 중 하나로 끝납니다.
  보류(`AnswerValidationError`)는 오류가 아니라 자기 이벤트이고 진단을 싣습니다. 예상 못 한 오류는
  화면에 종류만 보내고 메시지는 로그에만 남깁니다.
- **API는 대화 상태를 들지 않습니다.** 화면이 앞 문답(최대 6개)을 보내면 그것으로 맥락을 만듭니다.
- **돈이 드는 길에만 문턱을 둡니다.** 질문과 번역은 OpenAI를 부릅니다. Streamlit은 사람이 버튼을
  눌러야 하지만 API는 스크립트 한 줄로 수천 번 부를 수 있습니다. IP마다 분당 3·하루 30, 전체 하루
  300(환경변수로 바꿈). IP는 Caddy가 새로 쓰는 `X-Forwarded-For`입니다. 그래프만 읽는 길(통계·논문·
  부분 그래프·순위·품질)에는 두지 않습니다.
- **로그인은 두지 않습니다.** 지금 앱에도 없고, 문턱이 비용을 묶습니다.
- **CORS는 비워 둡니다.** Vercel 주소가 생기면 `API_ALLOWED_ORIGINS`에 넣습니다.

## 민감도

- **메모리가 가장 큰 가정입니다.** 1단계 측정은 앱 혼자일 때 호스트 최대 2,673 MiB, 남은 1,280 MiB
  였습니다(`reports/vm_day1.json`). API는 두 번째 파이썬 프로세스이고 인용망도 한 벌 더 올립니다.
  배포 뒤에 다시 재고, 여유가 500 MiB 밑이면 API의 부팅 예열을 끄거나 5단계(Streamlit 내리기)를
  앞당깁니다. 5단계 뒤에는 이 걱정이 사라집니다.
- **상한은 프로세스 메모리에 있습니다.** 재시작(배포)마다 비워집니다. 막으려는 것은 스크립트로 수천 번
  부르는 경우이고 그것은 재시작 사이에도 걸립니다. 워커를 늘리면 상한이 워커 수만큼 느슨해지므로,
  그때는 상한을 Neo4j나 파일로 옮깁니다.
- **IP 상한은 IP를 바꾸는 사람을 막지 못합니다.** 전체 하루 300이 그 경우의 천장입니다. 질문 하나에
  드는 비용이 바뀌면(모델 변경) 이 값을 다시 봅니다.
- **SSE를 Caddy가 모아 두면 스트리밍이 아닙니다.** 테스트는 가짜 백엔드로 이벤트 순서만 봅니다.
  실제로 흘러오는지는 배포 뒤 VM 주소에서 확인합니다.

## 배포 첫날 (2026-10-27)

- **자동 배포가 이미지를 다시 구웠습니다.** 머지 23:48:01, 타이머 23:49:07 시작, 배포 끝 23:52:13 —
  의존성 빌드까지 약 3분입니다.
- **메모리.** 두 프로세스가 다 올라온 뒤(API 예열 끝, 배포 점검이 Streamlit 화면을 연 뒤) 호스트
  available 1,160 MiB, swap 521 MiB 사용입니다. 컨테이너: Neo4j 1.43 GiB, API 384 MiB, 앱 349 MiB,
  Caddy 54 MiB. 1단계의 1,280 MiB에서 약 120 MiB 줄었고 500 MiB 선 위입니다. 질문이 몰릴 때의 최대는
  재지 않았습니다.
- **SSE는 흘러옵니다.** VM 주소에서 질문 하나: 첫 `partial` 0.05초, `partial` 240개 뒤 `final`, 전체
  50.4초. Caddy가 모아 두지 않습니다. 전체 시간이 리허설(27.2초)보다 긴 이유는 재지 않았습니다(한 번이고,
  배포 직후입니다).
- **배포 점검이 한 번 거짓으로 실패했습니다.** 마운트된 저장소라 merge 순간 화면 커밋이 먼저 바뀌고
  모듈은 재시작 뒤에 바뀝니다. 점검이 화면 커밋만 기다리다 그 사이를 옛 모듈로 읽었습니다. 다시 돌리니
  통과했고, 이제 모듈까지 기다립니다(`still_deploying`).

## 참고

- `src/api.py`, `tests/test_api.py`, `deploy/vm/compose.yml`, `deploy/vm/Caddyfile`
- [이전 계획](../MIGRATION_PLAN.md) 3단계
