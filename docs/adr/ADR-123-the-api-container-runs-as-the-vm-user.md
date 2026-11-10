# ADR-123: API 컨테이너는 root가 아니라 저장소를 clone한 VM 사용자로 돈다

- **상태:** 채택
- **날짜:** 2026-11-10

## 맥락

VM의 API 이미지(`deploy/vm/Dockerfile`)에는 `USER`가 없어서, API와 주간 수집 컨테이너가 root로 돌았습니다.
코드는 VM에 clone한 저장소를 `/app`에 마운트합니다([ADR-080](ADR-080-the-vm-pulls-main.md)).
그래서 컨테이너가 쓴 파일이 저장소 안에 root 소유로 남았습니다. 11-10에 VM에서 확인한 것입니다.

- 수집 상태 파일 `pipeline/state/ingest.json`, `ingest_dead_letter.jsonl`, `ingest_references.json`이 root 소유였습니다.
- `evals/`, `pipeline/` 등의 `__pycache__`도 root 소유였습니다.
- VM 사용자는 `uid=1000 gid=1003`입니다. gid가 1000이 아닙니다.

컨테이너 안의 프로세스가 뚫리면 root 권한으로 마운트된 저장소(`.env` 포함)를 고칠 수 있습니다.
포트폴리오 검토에서도 "Dockerfile이 root로 실행된다"는 지적을 받았습니다.

## 결정

- **이미지에 `app`(uid 1000) 사용자를 만들고 `USER app`으로 끝냅니다.** 이미지의 기본값이 root가 아니게 됩니다.
- **compose의 `api` 서비스가 `user: "${APP_UID:-1000}:${APP_GID:-1000}"`로 실제 사용자를 정합니다.**
  `deploy.sh`와 `ingest.sh`가 compose를 부르기 전에 `id -u`·`id -g`를 넘깁니다.
  그래서 컨테이너가 쓰는 파일은 저장소를 clone한 VM 사용자 소유가 됩니다.
- **VM에 남아 있던 root 소유 파일은 한 번 넘겼습니다**(11-10, `sudo find . -user root -exec chown Playdata:Playdata {} +`). 남은 root 파일은 0개입니다.
- 의존성 venv(`/opt/venv`)는 root 소유 그대로입니다. 읽고 실행만 하므로 고칠 필요가 없습니다.

## 측정

머지 전에 VM에서 따로 태그를 붙인 이미지로 시험하고 지웠습니다.

| 확인 | 결과 |
|---|---|
| `id` | `uid=1000(app) gid=1003` |
| `HOME` | `/home/app`, 쓸 수 있음 |
| 저장소를 읽기 전용으로 마운트하고 `import src.api` | 성공 |
| 수집 상태 파일 세 개와 `pipeline/state/reports`에 쓸 수 있는가 | 모두 쓸 수 있음(소유자를 넘긴 뒤) |

## 고르지 않은 것

- **이미지의 uid·gid를 1000:1000으로 고정하기.** VM 사용자의 gid가 1003이라, 수집이 만든 파일의 그룹이 어긋납니다.
  다른 VM에서 다시 세우면 uid도 다를 수 있습니다.
- **root로 시작해 소유자를 고친 뒤 권한을 내리는 진입 스크립트(gosu 등).** 시작할 때마다 root가 필요해서 목적에 맞지 않습니다.
- **(보류) 커밋마다 이미지를 구워 레지스트리에 올리기.** 같은 검토에서 나온 지적입니다.
  지금은 VM이 main을 당겨 오고 GitHub에 VM 자격을 두지 않습니다(ADR-080).
  레지스트리로 가려면 GitHub에 GCP 쓰기 권한(Workload Identity 등)이 필요하고, 배포 방식 자체가 바뀝니다. 따로 다룹니다.

## 민감도

- **머지 직후 첫 배포는 VM에 있던 옛 `deploy.sh`가 돕니다.** 옛 스크립트는 `APP_UID`를 넘기지 않습니다.
  그래서 이 첫 배포에서만 컨테이너가 compose 기본값인 `1000:1000`으로 뜹니다. uid가 같으니 쓰기는 됩니다.
  다음 배포부터는 `1000:1003`입니다.
- **`docker compose ... run api`를 손으로 부를 때**(`docs/GCP_VM.md`의 적재 명령 등) `APP_UID`를 넘기지 않으면 기본값 1000:1000입니다.
  VM 사용자의 uid가 1000이라 그대로 동작합니다.
- **`Dockerfile`이 바뀌었으므로 머지하면 이미지를 다시 굽고 API가 다시 뜹니다.**

## 참고

- [ADR-080](ADR-080-the-vm-pulls-main.md): VM이 main을 당겨 옵니다.
- [ADR-104](ADR-104-weekly-ingest-runs-on-the-vm.md): 주간 수집이 같은 이미지로 돕니다.
- [ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md): 앱이 읽는 파일이 바뀌면 API를 재시작합니다.
