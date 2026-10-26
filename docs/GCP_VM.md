# GCP VM 실행 안내 — 이전 계획 1단계

[이전 계획](MIGRATION_PLAN.md)의 1단계입니다: VM 한 대에 Neo4j와 지금 Streamlit 앱을 올리고,
인터넷에는 Caddy의 HTTPS 하나만 엽니다. 설계와 리허설 결과는
[ADR-079](adr/ADR-079-one-vm-mounts-the-repo-and-opens-only-https.md)에 있습니다.

**사람이 할 일은 1·2번뿐입니다.** 계정 가입·결제 수단·로그인은 대신할 수 없습니다. 2번이 끝나면
이 PC의 `gcloud`로 나머지를 진행합니다.

## 1. 가입 (사람)

1. <https://cloud.google.com/free>에서 무료 체험을 시작합니다. 결제 수단을 넣지만 체험 기간에는
   청구되지 않습니다. **90일은 가입한 날부터** 셉니다.
2. 프로젝트를 하나 만듭니다(예: `arxiv-graphrag`). 프로젝트 ID를 적어 둡니다.
3. 결제 → 예산 및 알림에서 예산 알림을 겁니다(예: 월 $50). 크레딧을 다 쓰기 전에 메일이 옵니다.

## 2. 이 PC에 gcloud (사람)

```powershell
winget install Google.CloudSDK
```

새 터미널에서 로그인합니다. 브라우저가 열리고, 로그인은 사람이 합니다.

```powershell
gcloud auth login
gcloud config set project <프로젝트 ID>
```

## 3. VM 만들기

서울 리전, 4GB(e2-medium), Debian 12, 디스크 30GB. 고정 IP와 80·443 방화벽을 함께 만듭니다.

```bash
gcloud services enable compute.googleapis.com
gcloud compute addresses create arxiv-graphrag-ip --region=asia-northeast3
gcloud compute firewall-rules create arxiv-graphrag-web --allow=tcp:80,tcp:443 --target-tags=arxiv-graphrag
gcloud compute instances create arxiv-graphrag --zone=asia-northeast3-a --machine-type=e2-medium --image-family=debian-12 --image-project=debian-cloud --boot-disk-size=30GB --boot-disk-type=pd-balanced --address=arxiv-graphrag-ip --tags=arxiv-graphrag
```

Neo4j의 7474·7687은 열지 않습니다. compose도 호스트 포트를 열지 않습니다
(`tests/test_move_graph.py`가 막습니다).

VM 안에서 Docker·스왑 2GB·저장소를 준비합니다.

```bash
gcloud compute ssh arxiv-graphrag --zone=asia-northeast3-a --command "curl -fsSL https://raw.githubusercontent.com/rndigkwk/arxiv_graph_RAG/main/deploy/vm/setup.sh | bash"
```

## 4. 비밀 옮기기

도메인은 고정 IP를 [sslip.io](https://sslip.io)에 붙여 씁니다(`34.64.12.34` → `34-64-12-34.sslip.io`).
도메인을 사지 않아도 Caddy가 Let's Encrypt 인증서를 받습니다.

```bash
gcloud compute addresses describe arxiv-graphrag-ip --region=asia-northeast3 --format="value(address)"
uv run python scripts/make_vm_env.py --domain 34-64-12-34.sslip.io
gcloud compute scp .local/vm.env arxiv-graphrag:arxiv_graph_RAG/.env --zone=asia-northeast3-a
```

`.local/vm.env`에는 OpenAI 키와 **새로 만든** Neo4j 비밀번호만 있습니다. Aura 비밀번호와 GDS 세션
키는 VM에 두지 않습니다(`scripts/make_vm_env.py`의 설명).

## 5. 그래프 옮기기

**옮기는 날 다시 내보냅니다.** 주간 수집이 Aura에 계속 쓰므로 며칠 전 스냅샷은 낡습니다.
Aura는 읽기만 합니다(약 5분, 217MB).

```bash
uv run python scripts/move_graph.py export --out .local/move
gcloud compute scp --recurse .local/move arxiv-graphrag:arxiv_graph_RAG/deploy/vm/snapshot --zone=asia-northeast3-a
```

VM에서 싣고 검증합니다(리허설에서 싣기 약 3분 반).

```bash
cd ~/arxiv_graph_RAG
docker compose --env-file .env -f deploy/vm/compose.yml up -d --wait neo4j
docker compose --env-file .env -f deploy/vm/compose.yml run --rm app python scripts/move_graph.py load --dir /snapshot
docker compose --env-file .env -f deploy/vm/compose.yml run --rm app python scripts/move_graph.py verify --dir /snapshot
docker compose --env-file .env -f deploy/vm/compose.yml up -d
```

`verify`가 `"problems": []`가 아니면 앱을 열지 않습니다. `load`는 빈 DB에만 씁니다. 다시 실으려면
VM의 Neo4j 볼륨을 사람이 직접 비웁니다.

## 6. 그날 잴 것

- **동시 메모리**(ADR-071 결정 4). 질문을 몇 개 보내면서 `docker stats --no-stream`과 `free -m`을 봅니다.
  리허설 피크는 셋을 합쳐 2.7GiB였습니다. 스왑을 쓰기 시작하면 8GB(e2-standard-2)로 올립니다.
- **검색이 Aura와 같은지.** 같은 사례를 양쪽에서 돌려 비교합니다(리허설: 순위 지표 같음, 겹침 0.894).

```bash
uv run python -m evals.run --mode retrieval --limit 31 --top-k 10 --output .local/move/retrieval_aura.json
uv run python scripts/move_graph.py diff --before .local/move/retrieval_aura.json --after .local/move/retrieval_vm.json
```

  `retrieval_vm.json`은 VM에서 `docker compose ... run --rm app python -m evals.run ...`으로 만들어
  `gcloud compute scp`로 가져옵니다.
- **배포 점검.** `scripts/visit_deployed_app.py`에 새 주소를 넣어 돌립니다. 통과하면 워크플로의
  주소를 바꿉니다.

## 배포와 재시작

**자동입니다**([ADR-080](adr/ADR-080-the-vm-pulls-main.md)). VM의 타이머가 2분마다 main을 보고,
바뀌었으면 fast-forward한 뒤 앱을 재시작합니다(의존성이 바뀌었으면 이미지도 다시 굽습니다).
배포 점검 워크플로가 새 커밋이 화면에 뜨는지 봅니다.

```bash
journalctl -u arxiv-deploy --since today
sudo systemctl stop arxiv-deploy.timer
sudo systemctl start arxiv-deploy.timer
```

첫 줄은 배포 기록, 둘째·셋째 줄은 자동 배포를 멈추고 다시 켭니다. VM이 재부팅되어도 세 컨테이너는
`restart: unless-stopped`로 다시 뜨고 타이머도 다시 켜집니다.

## 1단계에서 아직 아닌 것

- **주간 수집은 Aura에 씁니다.** 1단계의 VM은 옮긴 날의 사본을 보여 줍니다. 수집을 VM의 cron으로
  옮기는 것은 2단계이고, 그때 한 번 더 내보내 싣습니다.
- 백업 앱(0단계)과 Aura `keep-alive`는 그대로 둡니다.
