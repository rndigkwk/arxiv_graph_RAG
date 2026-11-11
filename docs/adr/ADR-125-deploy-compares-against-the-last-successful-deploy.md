# ADR-125: VM 배포는 HEAD가 아니라 마지막으로 성공한 배포와 비교한다

- **상태:** 채택
- **날짜:** 2026-11-11

## 맥락

VM은 2분마다 `deploy/vm/deploy.sh`로 main을 따라갑니다([ADR-080](ADR-080-the-vm-pulls-main.md)).
이 스크립트는 `HEAD`와 `origin/main`을 비교했고, 같으면 바로 나갔습니다. 그런데 fast-forward를 먼저 하고 빌드·재시작을 그 뒤에 합니다.

그래서 이런 일이 생길 수 있었습니다.

1. 의존성이 바뀐 머지가 들어옵니다. fast-forward로 `HEAD`가 새 커밋이 됩니다.
2. `docker compose build`가 실패합니다(디스크 부족, PyPI 장애 등). `set -e`로 스크립트가 끝납니다.
3. 2분 뒤 다음 실행은 `HEAD`가 이미 `origin/main`과 같다고 보고 나갑니다.
4. 컨테이너는 옛 이미지와 옛 코드인 채로, **다음 머지까지 아무도 다시 굽지 않습니다.**

실제로 일어난 적은 없습니다. 강의 자료(Day 78, CI/CD)를 다시 보다가 코드를 읽어 찾았습니다.
이 상태는 배포 점검이 알아챕니다. health의 `commit`은 git의 `HEAD`를 읽어 새 커밋이지만, `module_commit`은 API가 뜰 때 읽은 값이라 옛 커밋으로 남습니다([ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md)).
알림은 오지만 **VM이 스스로 다시 하지 않으므로** 사람이 VM에 들어가야 풀렸습니다.

## 결정

- **끝까지 성공한 배포의 커밋을 `pipeline/state/deployed-commit`에 적고, 그것과 `origin/main`을 비교합니다.**
  기록은 스크립트의 마지막 줄 직전에만 씁니다. 빌드·재시작에서 실패하면 기록이 옛것으로 남고, 다음 실행이 같은 차이를 다시 합니다.
- 지난 실행이 merge 뒤에 실패했으면 `HEAD`는 이미 새 커밋이라 `git merge --ff-only`는 아무 일도 하지 않습니다.
  무엇을 다시 굽고 재시작할지는 기록과 새 커밋의 차이로 정합니다. 그래서 의존성 변경도 다시 보입니다.
- **기록이 없으면** 지금 `HEAD`가 배포돼 있다고 보고 적습니다. 이 장치가 처음 들어갈 때는 옛 `deploy.sh`가 머지를 배포한 뒤입니다.
- **기록이 깨졌으면**(그런 커밋이 없으면) 차이를 모르므로 이미지 빌드·API 재시작·Caddy 재시작을 모두 합니다.
- fast-forward만 한다는 원칙은 그대로입니다. VM에 누가 커밋했으면 여전히 멈추고 사람을 기다립니다.

## 시험

`tests/test_vm_deploy.py`가 가짜 `docker`·`flock`과 임시 git 저장소로 `deploy.sh`를 **실제로 돌립니다.**

- 의존성 변경 머지에서 빌드가 실패하면 기록은 옛것으로 남고, 다음 실행이 다시 빌드하고 API를 재시작합니다. 그 뒤 실행은 아무것도 하지 않습니다.
- 기록이 깨졌으면 전부 다시 합니다.
- 고치기 전의 `deploy.sh`로 돌리면 두 시험 모두 실패합니다.

## 고르지 않은 것

- **빌드를 merge 앞으로 옮기기.** 빌드는 작업 트리의 `pyproject.toml`·`uv.lock`을 읽으므로 merge 뒤여야 합니다.
  `git worktree`로 따로 굽는 방법은 있지만 VM 디스크를 더 쓰고, 재시작 실패는 여전히 못 잡습니다.
- **실패하면 `git reset`으로 되돌리기.** fast-forward만 한다는 원칙([ADR-080](ADR-080-the-vm-pulls-main.md))과 어긋나고, 되돌린 뒤에도 같은 빌드가 다시 실패합니다.
- **health가 `deployed-commit`을 보이게 하기.** 알림은 이미 `module_commit`으로 옵니다. 모자란 것은 알림이 아니라 다시 하는 동작입니다. 실패가 되풀이되면 `journalctl -u arxiv-deploy`에 2분마다 남습니다.

## 민감도

- 빌드가 계속 실패하면 2분마다 다시 시도합니다. 실패가 조용히 묻히지 않는 대신, journal이 시끄러워지고 빌드 시간만큼 CPU를 씁니다. 타이머가 겹치면 `flock -n`으로 뒤의 실행이 나갑니다.
- 이 PR을 머지한 첫 배포는 **옛** `deploy.sh`가 합니다. 새 스크립트는 그다음 실행부터 돌고, 그때 기록을 처음 씁니다.

## 참고

- [ADR-080](ADR-080-the-vm-pulls-main.md): VM이 main을 당겨 옵니다.
- [ADR-107](ADR-107-restart-the-api-only-when-it-reads-the-change.md): 앱이 읽는 파일이 바뀔 때만 재시작합니다.
