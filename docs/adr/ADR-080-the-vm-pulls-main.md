# ADR-080: VM이 main을 당겨 온다 — GitHub에는 VM에 닿는 자격을 두지 않는다

- **상태:** 채택
- **날짜:** 2026-10-26

## 맥락

1단계로 앱이 GCP VM에서 돕니다([ADR-079](ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)).
배포는 VM에서 `git pull` + 앱 재시작이었고 사람이 SSH로 했습니다. 머지마다 그 일을 잊으면 화면은 옛
커밋이고, 배포 점검(`deployed-app.yml`)은 10분을 기다린 뒤 실패합니다 — Streamlit Cloud에서 재부팅을
잊었던 것과 같은 병입니다([ADR-072](ADR-072-check-the-deployed-app-in-a-browser.md)).

자동으로 만드는 길은 둘입니다.

1. **밀기.** GitHub Actions가 VM에 SSH(또는 GCP 서비스 계정)로 들어가 배포합니다. GitHub Secrets에 VM에
   쓸 수 있는 자격이 생깁니다. 이 저장소는 Aura의 CLIENT_* 키도 GitHub에 두지 않았습니다.
2. **당기기.** VM의 타이머가 main을 보고 바뀌었으면 배포합니다. 자격은 VM 밖으로 나가지 않고, 공개
   저장소라 VM에서 fetch하는 데도 자격이 필요 없습니다.

## 결정

- **당깁니다.** `deploy/vm/deploy.sh`를 systemd 타이머(`arxiv-deploy.timer`)가 2분마다 부릅니다.
  `setup.sh`가 타이머를 설치합니다.
- **fast-forward만 합니다.** VM에서 누가 커밋했으면 덮어쓰지 않고 실패합니다(journal에 남고, 배포
  점검이 실패해 alert 이슈가 열립니다).
- **배포마다 앱을 재시작합니다.** 코드는 마운트돼 있고 파일 감시를 껐으므로 재시작해야 새 모듈을
  읽습니다. 이미지는 Dockerfile이 복사하는 의존성 목록이 바뀐 때만 다시 굽습니다.
- **타이머가 겹치면 뒤의 것이 나갑니다**(`flock`). 의존성 빌드는 2분을 넘을 수 있습니다.
- **배포 점검은 VM 주소를 봅니다.** 워크플로는 그대로 push마다 돌고, 새 커밋이 화면에 뜰 때까지
  10분을 기다립니다. VM에는 아무것도 하지 않습니다.

## 민감도

- **main에 들어간 것은 2분 안에 공개 주소에서 돕니다.** 리뷰 없이 머지하면 그대로 나갑니다 —
  Streamlit Cloud도 같았으므로 새 위험은 아닙니다. 막아야 할 때는 VM에서 타이머를 멈춥니다
  (`sudo systemctl stop arxiv-deploy.timer`).
- **재시작 동안(앱 부팅 약 25초) 접속이 끊깁니다.** 사용자가 많아지면 두 컨테이너를 번갈아 띄우는 쪽을
  다시 봅니다. 지금은 한 사람입니다.
- **10분 안에 끝나지 않는 배포는 점검이 실패로 셉니다.** 의존성 빌드가 VM에서 그보다 오래 걸리면
  `COMMIT_WAIT_S`를 늘립니다.
- **GitHub가 VM을 모릅니다.** VM이 죽어 있으면 배포 점검이 실패해서 알려 줍니다. 그 이상의 상태(어느
  커밋에서 왜 멈췄는지)는 VM의 `journalctl -u arxiv-deploy`에만 있습니다.

## 참고

- `deploy/vm/deploy.sh`, `deploy/vm/setup.sh`, `tests/test_vm_deploy.py`
- [GCP VM 실행 안내](../GCP_VM.md)
