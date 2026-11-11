#!/usr/bin/env bash
# 공동인용 예측 모델의 월간 감시(ADR-124). arxiv-forecast-monitor.timer가 매달 3일 04:19 UTC에 부릅니다 -
# 설치는 install_forecast_monitor_timer.sh.
#
#   deploy/vm/forecast_monitor.sh                  # 지난달
#   deploy/vm/forecast_monitor.sh --month 2026-10  # 뒤의 인자는 모듈에 그대로 넘깁니다
#
# api 이미지로 컨테이너를 한 번 띄웁니다. 그래프는 읽기만 하고(읽기 전용 세션), 유료 호출은 없습니다.
# 2026-11-11 시험 실행: 55초, 최대 메모리 775MB.
set -euo pipefail
cd "$HOME/arxiv_graph_RAG"
# API 컨테이너는 이 사용자로 돕니다(ADR-123, compose.yml의 user).
export APP_UID APP_GID
APP_UID=$(id -u)
APP_GID=$(id -g)

# 주간 수집과 같은 잠금을 기다립니다. 적재 중인 그래프를 읽지 않으려는 것입니다(최대 2시간).
exec 8>/tmp/arxiv-ingest.lock
flock -w 7200 8 || { echo "수집이 끝나지 않아 감시를 건너뜁니다"; exit 1; }

out="pipeline/state/reports/forecast-monitor-run-$(date -u +%Y%m%dT%H%M%SZ).json"
set +e
docker compose --env-file .env -f deploy/vm/compose.yml run --rm --no-deps -T api \
  python -m models.link_prediction.monitor --output "$out" "$@"
code=$?
set -e
echo "보고서: $out (종료 코드 $code)"

status=pipeline/state/last-forecast-monitor.json
printf '{"finished_at": "%s", "exit_code": %s, "report": "%s"}\n' \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$code" "$out" > "$status.tmp"
mv "$status.tmp" "$status"
exit "$code"
