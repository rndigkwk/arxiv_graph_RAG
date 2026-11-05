#!/usr/bin/env bash
# 주간 수집 타이머를 설치합니다(ADR-104). 첫 적재를 사람이 확인한 **뒤에** 한 번 부릅니다.
# 실행 기록은 `journalctl -u arxiv-ingest`, 보고서는 pipeline/state/reports/에 남습니다.
set -euo pipefail
sudo tee /etc/systemd/system/arxiv-ingest.service >/dev/null <<UNIT
[Unit]
Description=arxiv GraphRAG: 주간 수집과 적재
[Service]
Type=oneshot
User=$USER
ExecStart=/bin/bash $HOME/arxiv_graph_RAG/deploy/vm/ingest.sh load
# 503 백오프가 겹치면 OAI 한 페이지에 20분까지 기다립니다. 그보다 길면 끊고 다음 주에 잇습니다.
TimeoutStartSec=2h
UNIT
sudo tee /etc/systemd/system/arxiv-ingest.timer >/dev/null <<UNIT
[Unit]
Description=arxiv GraphRAG: 월요일 03:19 UTC 주간 수집
[Timer]
OnCalendar=Mon *-*-* 03:19:00 UTC
Persistent=true
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now arxiv-ingest.timer
systemctl list-timers arxiv-ingest.timer --no-pager
