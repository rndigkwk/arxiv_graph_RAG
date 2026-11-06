# ADR-107: 앱이 읽는 파일이 바뀐 배포만 API를 재시작한다 — 재시작이 진행 중인 답을 끊는다

- **상태:** 채택
- **날짜:** 2026-11-06

## 맥락

2026-11-06 웹 화면을 점검하다 "Yoshua Bengio가 쓴 논문이 있어?"를 물었더니 "API에 닿지 못했습니다"가 떴습니다.
같은 순간 PR #128(평가 카드 문서) 머지가 VM에 배포되고 있었습니다.

- API 로그: `POST /api/ask` 다음 줄이 `Shutting down`입니다. Caddy는 `aborting with incomplete response`를 남겼고,
  재시작하는 동안 들어온 요청은 `connection refused`였습니다.
- `deploy/vm/deploy.sh`는 main의 커밋마다 `restart api`를 했습니다([ADR-080](ADR-080-the-vm-pulls-main.md)).
  PR #128은 문서와 VM 측정 보고서만 바꿨는데도 그랬습니다.
- 답은 6~20초 걸리고, docker의 기본 종료 대기는 10초입니다. 재시작이 진행 중인 답을 끊습니다.

최근 머지 여덟 개 중 넷(#123, #125, #127, #128)이 API가 읽지 않는 파일만 바꿨습니다. 그 머지들이 재시작할 이유가
없었습니다.

## 결정

**바뀐 파일 중 하나라도 앱이 읽는 파일이면 재시작하고, 모두 목록에 맞으면 건너뜁니다.** 목록은
[`deploy/vm/no-restart-paths`](../../deploy/vm/no-restart-paths) 한 곳입니다(`grep -E` 정규식).

| 재시작하지 않음 | 재시작함 |
|---|---|
| `docs/`, `*.md`, `tests/`, `web/`(Vercel), `notebooks/`, `scripts/`, `evals/`, `.github/`, `reports/vm_*`, `deploy/vm/{ingest,install_ingest_timer,setup}.sh` | 그 밖의 전부 - `src/`, `pipeline/`, `models/`, `baselines/`, `reports/`, 의존성 파일, `compose.yml`, `Caddyfile`, `Dockerfile`, `deploy.sh` |

- **목록은 "안 읽는 쪽"을 적습니다.** 모르는 새 경로는 재시작으로 떨어집니다. 틀려도 지금과 같은 쪽입니다.
- **`scripts/`와 `evals/`는 API가 임포트하지 않습니다**(확인함). `reports/` 중 앱이 여는 것(`related_study.json` 등)이
  있어 `reports/`는 통째로 넣지 않고, VM 측정 보고서(`reports/vm_*`)만 넣었습니다.
- **배포 점검도 같은 목록을 읽습니다.** 재시작하지 않으면 `/api/health`의 `module_commit`이 옛 커밋으로 남습니다.
  `scripts/visit_deployed_app.py`의 `settle`이 git으로 두 커밋 사이를 보고, 목록에 맞는 파일만 바뀌었으면 새 코드로
  셉니다. git이 그 커밋을 모르면(얕은 체크아웃) 옛 모듈로 셉니다 - 점검은 `fetch-depth: 100`으로 받습니다.
- **시험이 목록을 지킵니다.** `src/`가 `Path("…")`로 여는 경로가 목록에 걸리면 실패합니다
  (`tests/test_vm_deploy.py`). 빈 줄은 모든 파일에 맞아 재시작을 늘 건너뛰므로 그것도 막습니다.

실제 머지로 확인했습니다: 9041c5d→adf124b(#128), 539ef44→9041c5d(#127), c93ee83→5e4da28(#125),
1fab991→1155114(#123)는 건너뛰고, 5e4da28→539ef44(#126, `src/api.py`)와 1155114→c93ee83(#124)는 재시작합니다.
bash(`deploy.sh`)와 Python(`restart_free`)의 판단이 같았습니다.

## 고르지 않은 것

- **재시작 전에 진행 중인 답을 기다리기**(`stop_grace_period`, uvicorn의 정상 종료). uvicorn은 종료 신호를 받으면
  새 연결을 바로 받지 않습니다. 답이 끝날 때까지(최대 20초쯤) 새 질문이 모두 거절되어, 끊기는 사람이 한 명에서
  여럿으로 바뀔 수 있습니다.
- **두 컨테이너를 번갈아 띄우기.** VM은 4GB이고, API는 프로세스마다 인용망을 한 벌씩 올립니다. ADR-080이 사용자가
  늘면 다시 보기로 한 자리입니다.
- **파일 감시(`--reload`)로 바꾸기.** 재시작이 사라지지 않고 시점만 바뀌며, 감시가 VM CPU를 계속 씁니다.

## 민감도

- **앱 코드를 바꾼 머지는 여전히 진행 중인 답을 끊습니다.** 이 결정은 그런 머지를 줄일 뿐입니다. 끊긴 답은 화면에서
  다시 물으면 됩니다.
- `src/`가 경로를 문자열 조합으로 열면 시험이 못 봅니다. 그런 파일을 목록의 경로에 두지 않습니다.

## 참고

- [`deploy/vm/deploy.sh`](../../deploy/vm/deploy.sh), [`deploy/vm/no-restart-paths`](../../deploy/vm/no-restart-paths),
  [`scripts/visit_deployed_app.py`](../../scripts/visit_deployed_app.py), [`tests/test_vm_deploy.py`](../../tests/test_vm_deploy.py)
- [ADR-080](ADR-080-the-vm-pulls-main.md) — VM이 main을 당겨 온다
