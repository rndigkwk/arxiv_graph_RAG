#!/usr/bin/env bash
# GCP VM(Debian 12)에서 한 번 돌립니다 - docs/GCP_VM.md의 3단계.
# Docker 공식 apt 저장소에서 Engine과 compose 플러그인을 깔고, 스왑 2GB를 만들고, 저장소를 clone합니다.
# 다시 돌려도 됩니다(있는 것은 건너뜁니다). 지우는 명령은 없습니다.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y ca-certificates curl git
if ! command -v docker >/dev/null; then
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
    "https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" |
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  sudo usermod -aG docker "$USER"
fi

# 리허설의 동시 피크가 2.7GiB였습니다(reports/vm_rehearsal.json). 4GB VM에서 질문이 겹치면 모자랄 수
# 있으므로 OOM으로 Neo4j가 죽기 전에 스왑이 받게 합니다. 스왑을 쓰기 시작하면 8GB로 올릴 때입니다.
if [ ! -f /swapfile ]; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

[ -d "$HOME/arxiv_graph_RAG" ] || git clone https://github.com/rndigkwk/arxiv_graph_RAG.git "$HOME/arxiv_graph_RAG"

# main을 따라가는 타이머(ADR-080). 처음 올린 뒤 2분 간격으로 deploy.sh를 부릅니다.
sudo tee /etc/systemd/system/arxiv-deploy.service >/dev/null <<UNIT
[Unit]
Description=arxiv GraphRAG: main을 따라 배포
[Service]
Type=oneshot
User=$USER
ExecStart=/bin/bash $HOME/arxiv_graph_RAG/deploy/vm/deploy.sh
UNIT
sudo tee /etc/systemd/system/arxiv-deploy.timer >/dev/null <<UNIT
[Unit]
Description=arxiv GraphRAG: 2분마다 main 확인
[Timer]
OnBootSec=2min
OnUnitActiveSec=2min
[Install]
WantedBy=timers.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now arxiv-deploy.timer
echo "끝났습니다. docker 그룹이 적용되도록 SSH를 다시 연결하세요."
