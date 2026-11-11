#!/usr/bin/env bash
# VM이 main을 따라갑니다 - systemd 타이머(arxiv-deploy.timer)가 2분마다 부릅니다(ADR-080).
# GitHub에서 VM으로 밀지 않고 VM이 당겨 오므로, GitHub에 SSH 키도 GCP 자격도 두지 않습니다.
# 배포 점검(.github/workflows/deployed-app.yml)은 새 커밋이 화면에 뜰 때까지 10분을 기다립니다.
set -euo pipefail
cd "$HOME/arxiv_graph_RAG"
# API 컨테이너는 이 사용자로 돕니다(ADR-123, compose.yml의 user).
export APP_UID APP_GID
APP_UID=$(id -u)
APP_GID=$(id -g)

# 빌드가 2분을 넘기면 다음 타이머가 겹칩니다. 겹친 쪽은 그냥 나갑니다.
exec 9>/tmp/arxiv-deploy.lock
flock -n 9 || exit 0

git fetch -q origin main
new=$(git rev-parse origin/main)
# old는 HEAD가 아니라 **끝까지 성공한 마지막 배포**입니다(ADR-125). fast-forward 뒤에 빌드가
# 실패하면 HEAD는 이미 새 커밋인데 컨테이너는 옛것입니다. HEAD와 비교하면 다음 실행이
# old=new로 보고 그냥 나가서, 다음 머지까지 아무도 다시 굽지 않습니다.
mark=pipeline/state/deployed-commit
mkdir -p pipeline/state
old=$(cat "$mark" 2>/dev/null || true)
if [ -z "$old" ]; then
  # 기록이 없으면(이 장치 이전) 지금 HEAD가 배포돼 있다고 봅니다.
  old=$(git rev-parse HEAD)
  echo "$old" > "$mark"
fi
[ "$old" = "$new" ] && exit 0
# 기록이 깨졌으면 무엇이 바뀌었는지 모르므로 전부 다시 합니다.
full=0
git cat-file -e "$old^{commit}" 2>/dev/null || full=1
# fast-forward만 합니다. VM에서 누가 커밋했다면 멈추고 사람을 기다립니다(실패가 journal에 남습니다).
# 지난 실행이 merge 뒤에 실패했으면 HEAD가 이미 new라 아무 일도 하지 않습니다.
git merge -q --ff-only origin/main

C="docker compose --env-file .env -f deploy/vm/compose.yml"
changed() { [ "$full" = 1 ] || ! git diff --quiet "$old" "$new" -- "$@"; }
# 이미지는 의존성만 굽습니다. 의존성이 바뀌었을 때만 다시 굽습니다.
if changed pyproject.toml uv.lock .python-version .dockerignore deploy/vm/Dockerfile; then
  $C build -q api
fi
# 내린 서비스(2026-10-27의 app)는 --remove-orphans가 지웁니다. 남기면 메모리를 계속 씁니다.
$C up -d --remove-orphans
# 코드는 마운트돼 있고 파일 감시를 켜지 않았으므로, 새 코드는 재시작해야 읽힙니다.
# 다만 재시작은 진행 중인 답을 끊습니다(2026-11-06, 문서만 바꾼 머지가 답을 끊었습니다). 앱이 읽지
# 않는 파일만 바뀌었으면 건너뜁니다(ADR-107). 목록은 no-restart-paths 한 곳에 있습니다.
runtime=$(git diff --no-renames --name-only "$old" "$new" \
  | grep -vE -f <(grep -vE '^(#|$)' deploy/vm/no-restart-paths) || true)
[ "$full" = 1 ] && runtime=all
if [ -n "$runtime" ]; then
  $C restart api
else
  echo "앱이 읽는 파일이 바뀌지 않아 API를 재시작하지 않습니다"
fi
if changed deploy/vm/Caddyfile; then
  $C restart caddy
fi
# 여기까지 왔을 때만 기록합니다. 위에서 실패하면 다음 실행(2분 뒤)이 같은 old에서 다시 합니다.
echo "$new" > "$mark.tmp"
mv "$mark.tmp" "$mark"
echo "배포 ${old:0:7} -> ${new:0:7}"
