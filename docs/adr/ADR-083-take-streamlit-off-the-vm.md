# ADR-083: VM에서 Streamlit을 내린다 — 예전 주소는 Vercel로 보내고, 배포 점검은 API를 읽는다

- **상태:** 채택
- **날짜:** 2026-10-27

## 맥락

이전 계획 5단계입니다([MIGRATION_PLAN](../MIGRATION_PLAN.md)). 시안의 다섯 화면이 Next.js로 Vercel에
올라갔습니다([ADR-082](ADR-082-the-browser-calls-the-vm-api-directly.md)). 질문 작업대, 논문 페이지, 탐색,
품질이고, 휴대폰은 같은 화면의 좁은 배치입니다.

VM(4GB)에는 Streamlit과 FastAPI가 나란히 돌았습니다([ADR-081](ADR-081-fastapi-beside-streamlit-on-the-vm.md)).
두 프로세스가 같은 모듈을 열고 **인용망을 한 벌씩** 올립니다. 두 프로세스가 다 올라온 뒤 available이
1,160 MiB였습니다. 같은 화면을 두 벌 두면 메모리를 두 번 쓰고, 배포 점검도 두 곳을 봐야 합니다.

## 결정

- **compose에서 `app`(Streamlit)을 뺍니다.** VM에는 Neo4j, API, Caddy만 남습니다. `deploy.sh`는
  `up -d --remove-orphans`로 내린 컨테이너를 지우고, 배포마다 API만 재시작합니다.
- **예전 주소는 Vercel로 보냅니다.** Caddy가 `/api/*` 밖의 요청을 `https://arxiv-graphrag.vercel.app{uri}`로
  302 넘깁니다. README와 예전 링크로 온 사람이 빈 화면을 보지 않습니다. 302인 이유는 화면 주소가 아직
  바뀔 수 있어서입니다(브라우저가 301을 오래 기억합니다).
- **배포 점검은 브라우저 없이 `/api/health`를 읽습니다.** 두 커밋(디스크의 체크아웃, API가 임포트한
  모듈)이 모두 기대한 커밋이어야 합니다. ADR-072의 교훈("화면은 새것인데 모듈은 옛것")이 그대로
  적용되는 자리입니다. 이어서 `/api/stats`로 그래프가 답하는지, Vercel 주소가 뜨는지 봅니다.
  Playwright 설치가 빠져 점검이 몇 분 짧아집니다.
- **Streamlit 코드(`app.py`)는 남깁니다.** Streamlit Cloud의 백업 앱(0단계, Aura Free)이 그것을 돌립니다.
  그 앱은 `visit_deployed_app.py --streamlit`으로 손으로 점검합니다.
- **Dockerfile은 이번에 바꾸지 않습니다.** 이미지의 기본 명령은 아직 Streamlit이고, compose의 `api`가
  uvicorn으로 덮어씁니다. 이유는 아래 "머지 순간"입니다.

## 머지 순간 — 옛 deploy.sh가 돈다

배포 타이머는 `deploy.sh`를 연 채로 `git merge`를 합니다. git은 파일을 새로 만들어 바꾸므로, 그
실행은 **끝까지 옛 스크립트**입니다. 옛 스크립트는 Dockerfile이 바뀌면 `build -q app api`를 부르는데,
새 compose에는 `app`이 없어 거기서 멈춥니다. 그래서 Dockerfile을 건드리지 않았습니다.

그래도 옛 스크립트의 마지막 `restart app api`는 `app`이 없어 실패합니다. 그 전의 `up -d`는 새 compose로
돌지만 `--remove-orphans`가 없어 Streamlit 컨테이너가 남고, Caddy도 재시작되지 않습니다. **머지 뒤 VM에서
한 번 직접** 돌립니다.

    docker compose --env-file .env -f deploy/vm/compose.yml up -d --remove-orphans
    docker compose --env-file .env -f deploy/vm/compose.yml restart caddy

다음 배포부터는 새 스크립트가 돕니다.

## 민감도

- **새 화면이 지금 화면보다 낫다는 판단이 전제입니다.** 계획은 "확인한 뒤에 내린다"였고, 사용자가 네
  화면 PR과 함께 내리기로 정했습니다(2026-10-27). 새 화면에 없는 Streamlit 기능은 GDS 세션 엔진 선택,
  하한이 결함을 잡는지 보는 표, 프롬프트 지문 표, 관련 논문 변형 비교입니다. 그것이 필요하면 백업 앱에
  있고, 다시 VM에 올리려면 이 ADR을 뒤집습니다.
- **Vercel 주소가 바뀌면 Caddy의 넘김이 틀린 곳을 가리킵니다.** Caddyfile 한 줄입니다.
- **`/api/health`의 모듈 커밋은 API가 모듈을 불러온 뒤에만 있습니다.** 예열이 그것을 시작할 때
  부르므로 재시작 몇 초 뒤면 채워집니다. 비어 있으면 점검은 옛 모듈로 보고 기다립니다(10분).
- **Dockerfile의 기본 명령을 uvicorn으로 바꾸는 것은 다음 배포 뒤에 합니다.** 그때는 새 deploy.sh가 돌아
  `build -q api`만 부릅니다.

## 참고

- `deploy/vm/compose.yml`, `deploy/vm/Caddyfile`, `deploy/vm/deploy.sh`, `scripts/visit_deployed_app.py`,
  `.github/workflows/deployed-app.yml`, `tests/test_vm_deploy.py`, `tests/test_deployed_app.py`
