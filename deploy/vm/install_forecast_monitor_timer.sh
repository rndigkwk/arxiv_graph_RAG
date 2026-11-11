#!/usr/bin/env bash
# 공동인용 예측 모델의 월간 감시 타이머를 설치합니다(ADR-124). 첫 실행을 사람이 확인한 뒤에 한 번 부릅니다.
# 실행 기록은 `journalctl -u arxiv-forecast-monitor`, 보고서는 pipeline/state/reports/에 남습니다.
set -euo pipefail
sudo tee /etc/systemd/system/arxiv-forecast-monitor.service >/dev/null <<UNIT
[Unit]
Description=arxiv GraphRAG: 공동인용 예측 모델 월간 감시
[Service]
Type=oneshot
User=$USER
ExecStart=/bin/bash $HOME/arxiv_graph_RAG/deploy/vm/forecast_monitor.sh
# 수집 잠금을 2시간까지 기다리고, 실행 자체는 1분 안팎입니다.
TimeoutStartSec=3h
UNIT
sudo tee /etc/systemd/system/arxiv-forecast-monitor.timer >/dev/null <<UNIT
[Unit]
Description=arxiv GraphRAG: 매달 3일 04:19 UTC 공동인용 예측 모델 감시
[Timer]
OnCalendar=*-*-03 04:19:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now arxiv-forecast-monitor.timer
systemctl list-timers arxiv-forecast-monitor.timer --no-pager
