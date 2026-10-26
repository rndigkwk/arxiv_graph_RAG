#!/usr/bin/env bash
# VM이 main을 따라갑니다 - systemd 타이머(arxiv-deploy.timer)가 2분마다 부릅니다(ADR-080).
# GitHub에서 VM으로 밀지 않고 VM이 당겨 오므로, GitHub에 SSH 키도 GCP 자격도 두지 않습니다.
# 배포 점검(.github/workflows/deployed-app.yml)은 새 커밋이 화면에 뜰 때까지 10분을 기다립니다.
set -euo pipefail
cd "$HOME/arxiv_graph_RAG"

# 빌드가 2분을 넘기면 다음 타이머가 겹칩니다. 겹친 쪽은 그냥 나갑니다.
exec 9>/tmp/arxiv-deploy.lock
flock -n 9 || exit 0

git fetch -q origin main
old=$(git rev-parse HEAD)
new=$(git rev-parse origin/main)
[ "$old" = "$new" ] && exit 0
# fast-forward만 합니다. VM에서 누가 커밋했다면 멈추고 사람을 기다립니다(실패가 journal에 남습니다).
git merge -q --ff-only origin/main

C="docker compose --env-file .env -f deploy/vm/compose.yml"
changed() { ! git diff --quiet "$old" "$new" -- "$@"; }
# 이미지는 의존성만 굽습니다. 의존성이 바뀌었을 때만 다시 굽습니다.
if changed pyproject.toml uv.lock .python-version .dockerignore deploy/vm/Dockerfile; then
  $C build -q app api
fi
$C up -d
# 코드는 마운트돼 있고 파일 감시를 껐으므로, 새 코드는 재시작해야 읽힙니다.
$C restart app api
if changed deploy/vm/Caddyfile; then
  $C restart caddy
fi
echo "배포 ${old:0:7} -> ${new:0:7}"
