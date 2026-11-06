#!/usr/bin/env bash
# 주간 수집(ADR-104). arxiv-ingest.timer가 월요일 03:19 UTC에 부릅니다 - 설치는 install_ingest_timer.sh.
#
#   deploy/vm/ingest.sh dry               # 시험 실행: 받고 세기만 합니다. 그래프에 쓰지 않습니다
#   deploy/vm/ingest.sh load              # 적재: 임베딩(상한 $0.50)·멱등 확인까지
#   deploy/vm/ingest.sh dry --days 14     # 뒤의 인자는 pipeline.ingest에 그대로 넘깁니다
#
# api 이미지로 컨테이너를 **한 번** 띄웁니다. 돌고 있는 api 컨테이너와 따로이고, compose 네트워크로
# DB에 붙으므로 DB 포트를 열지 않습니다. 키는 저장소 루트의 .env(OPENAI_API_KEY, NEO4J_PASSWORD)입니다.
set -euo pipefail
cd "$HOME/arxiv_graph_RAG"

exec 8>/tmp/arxiv-ingest.lock
flock -n 8 || { echo "수집이 이미 돌고 있습니다"; exit 0; }

mode=${1:-dry}
shift || true
case "$mode" in
  load) flags=(--load --verify-idempotent --embed --max-cost 0.50) ;;
  dry) flags=() ;;
  *) echo "dry 또는 load만 받습니다: $mode" >&2; exit 2 ;;
esac

# 보고서는 git이 무시하는 pipeline/state/ 아래에 둡니다. reports/ingest.json은 저장소가 추적하는
# 파일이라, VM에서 고치면 deploy.sh의 fast-forward가 막힙니다. 상태 파일(워터마크·대기열)도 여기입니다.
out="pipeline/state/reports/ingest-$(date -u +%Y%m%dT%H%M%SZ)-$mode.json"
# 적재는 성공·실패와 상관없이 끝에 상태 파일을 씁니다(ADR-106). 수집이 예외로 죽으면 워터마크
# 파일에는 아무것도 남지 않아서, `/api/health`가 종료 코드를 여기서 읽습니다.
set +e
docker compose --env-file .env -f deploy/vm/compose.yml run --rm --no-deps -T api \
  python -m pipeline.ingest "${flags[@]}" --output "$out" "$@"
ingest_exit=$?
set -e
echo "보고서: $out (종료 코드 $ingest_exit)"
[ "$mode" = load ] || exit "$ingest_exit"

# 첫 버전 날짜를 받지 못해 발표일이 빈 채로 실린 논문을 채웁니다(ADR-104). 빈 것만 받으므로
# 보통은 0편이고 요청도 없습니다. 적재가 실패했으면 건너뜁니다.
backfill_exit=null
if [ "$ingest_exit" -eq 0 ]; then
  set +e
  docker compose --env-file .env -f deploy/vm/compose.yml run --rm --no-deps -T api \
    python scripts/backfill_published.py --load \
    --output "pipeline/state/reports/backfill-$(date -u +%Y%m%dT%H%M%SZ).json"
  backfill_exit=$?
  set -e
fi

status=pipeline/state/last-ingest.json
printf '{"finished_at": "%s", "mode": "%s", "exit_code": %s, "backfill_exit_code": %s, "report": "%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$mode" "$ingest_exit" "$backfill_exit" "$out" > "$status.tmp"
mv "$status.tmp" "$status"
[ "$ingest_exit" -eq 0 ] && [ "$backfill_exit" = 0 ]
