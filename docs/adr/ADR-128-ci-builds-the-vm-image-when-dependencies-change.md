# ADR-128: 의존성이 바뀐 PR은 CI가 VM 이미지를 먼저 굽고, 그 안에서 앱을 불러 본다

- **상태:** 채택
- **날짜:** 2026-11-11

## 맥락

VM의 API 이미지는 의존성만 굽고, VM이 머지 뒤에 직접 굽습니다([ADR-080](ADR-080-the-vm-pulls-main.md)).
CI는 `uv sync --locked --group dev --extra api`로 테스트를 돌리지만, **VM과 같은 이미지는 아무도 머지 전에 굽지 않았습니다.**

둘은 다릅니다. VM 이미지는 `python:3.12-slim` 위에 `--no-dev`로 설치하고, `.dockerignore`가 의존성 세 파일만 빌드 문맥에 보내며, root가 아닌 사용자로 돕니다([ADR-123](ADR-123-the-api-container-runs-as-the-vm-user.md)).
그래서 이런 변경은 CI를 통과하고 VM에서 처음 깨질 수 있습니다.

- dev 그룹에만 있던 패키지를 앱이 import하게 되는 변경
- slim 이미지에 없는 시스템 라이브러리가 필요한 휠
- Dockerfile 자체의 실수

VM 빌드가 실패하면 배포가 다시 시도하도록 고쳤지만(ADR-125, PR #156), 그것은 실패 뒤의 복구입니다. 이것은 실패를 머지 전에 막습니다.
교안(Day 78)의 "이미지를 빌드해 컨테이너로 띄우고 확인한 뒤에만 통과"와 같은 자리입니다.

## 결정

- **`.github/workflows/vm-image.yml`**이 `deploy/vm/Dockerfile`을 VM과 같은 문맥(저장소 맨 위)으로 굽습니다.
- 그 이미지에 저장소를 **읽기 전용으로** 마운트하고, 이미지의 사용자로 `deploy/vm/check_image_imports.py`를 돌립니다. `src/`의 모든 모듈이 **모듈 맨 위에서** 가져오는 외부 패키지를 AST로 모아 하나씩 불러 보고, `uvicorn`과 `src.api`를 불러 봅니다. 목록을 손으로 적지 않으므로 새 의존성이 저절로 들어옵니다. 비밀은 주지 않습니다.
  - `src.chatbot`을 직접 부르지 않는 이유: 불러지는 순간 연결 값을 요구하고(첫 실행 `ValueError`), 가짜 값을 주면 **Neo4j에 접속합니다**(둘째 실행 `ServiceUnavailable`). 로컬에서는 저장소의 `.env`와 그래프가 있어 이것을 몰랐습니다.
- **의존성 파일이 바뀐 PR과 main push에서만 돕니다.** 경로 목록은 `deploy.sh`가 이미지를 다시 굽는 목록(`pyproject.toml`, `uv.lock`, `.python-version`, `.dockerignore`, `deploy/vm/Dockerfile`)에 이 워크플로 파일을 더한 것입니다.
  `tests/test_vm_deploy.py`가 두 목록이 같은지 확인합니다. 한쪽에만 파일을 더하면 CI는 건너뛰었는데 VM 빌드가 깨지는 길이 다시 생깁니다.

## 고르지 않은 것

- **모든 PR에서 굽기.** 코드만 바뀐 PR은 VM도 다시 굽지 않습니다. 빌드에 1~2분이 더 들 뿐 막는 것이 없습니다.
- **구운 이미지를 레지스트리에 올리고 VM이 그것을 받기.** GitHub에 GCP 쓰기 권한이 필요하고 배포 방식이 바뀝니다(ADR-123에서 보류).
- **컨테이너로 서버를 띄워 `/api/health`까지 확인하기.** 기동할 때 인용망을 그래프에서 미리 읽는데(ADR-067), CI에는 Neo4j가 없습니다. 불러지는지까지가 이 검사가 확실히 말할 수 있는 범위입니다.

## 민감도

- **`src/`의 모듈 맨 위 import만 봅니다.** 함수 안의 import는 보지 않습니다. `graphdatascience`(GDS 세션 도구, 이미지에 없음)를 그렇게 불러오기 때문입니다. 앱이 함수 안에서 새 패키지를 부르거나 `src/` 밖(`pipeline/`·`models/`)의 패키지를 맨 위에서 쓰게 되면 여기서 잡히지 않습니다. 그런 일이 생기면 범위를 넓힙니다.
- VM은 `linux/amd64`(e2-medium), GitHub 러너도 `amd64`입니다. VM을 Arm으로 바꾸면 이 검사가 같은 휠을 보지 않습니다.
- 이 워크플로가 필수 검사가 아니라서, 실패해도 머지 버튼은 막히지 않습니다. 실패가 PR에 빨갛게 보이는 것까지입니다.

## 참고

- [ADR-080](ADR-080-the-vm-pulls-main.md): VM이 main을 당겨 오고 직접 굽습니다.
- `docs/lecture-review.md` Day 78 표.
