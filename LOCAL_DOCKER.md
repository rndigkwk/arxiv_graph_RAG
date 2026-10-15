# 로컬 Docker 실험

현재 Aura 데이터와 기존 임베딩을 복제해 로컬 Neo4j의 적재 크기·자원 사용·검색 지연을 측정합니다.
데이터 확장이나 유료 호스팅 여부를 판단하기 위한 기준 실험입니다.

## 격리 범위

- Aura는 읽기 전용으로 조회합니다. 삭제·재적재·인덱스 변경을 하지 않습니다.
- 쓰기 대상은 `bolt://127.0.0.1:17687`, 데이터베이스 `neo4j`로 고정했습니다.
- 컨테이너: `arxiv-graphrag-local`, 이미지: Neo4j Community 5.26.32 (digest 고정).
- CPU 2개, DB 컨테이너 메모리 한도 4GiB, heap 1GiB, page cache 1GiB.
- 영구 볼륨: `arxiv-graphrag-local-data`, `arxiv-graphrag-local-logs`.
- 비밀번호·원본 스냅샷·측정 JSON은 Git에서 제외한 `.local/`에 저장합니다.
- `.env`를 수정하지 않습니다. 앱 실행 자식 프로세스에만 로컬 접속 정보를 전달합니다.
- 내보내기·적재·기본 측정은 OpenAI API를 호출하지 않습니다.

## 실행 순서 (PowerShell, 저장소 루트)

Docker Desktop의 Linux 엔진을 먼저 실행합니다.

```powershell
.\.venv\Scripts\python.exe scripts\local_graph_test.py init
docker compose --env-file .local\neo4j.env -f compose.local.yml up -d

# 처음 한 번: Aura에서 이미 계산된 임베딩까지 읽어옵니다.
.\.venv\Scripts\python.exe -u scripts\local_graph_test.py export

# 로컬 적재. MERGE를 사용하므로 같은 스냅샷을 재적재할 수 있습니다.
.\.venv\Scripts\python.exe -u scripts\local_graph_test.py import
.\.venv\Scripts\python.exe -u scripts\local_graph_test.py benchmark
.\.venv\Scripts\python.exe scripts\local_graph_test.py app-smoke

# 기존 앱을 로컬 DB에 연결합니다.
.\.venv\Scripts\python.exe scripts\local_graph_test.py app
```

앱: <http://127.0.0.1:18501>

Neo4j Browser: <http://127.0.0.1:17474> (Bolt 주소: `bolt://127.0.0.1:17687`).
사용자명은 `neo4j`이며 로컬 비밀번호는 `.local/neo4j.env`에 있습니다. Aura 비밀번호와 다릅니다.

스냅샷이 이미 있으면 `export`는 덮어쓰기를 거부합니다. `import`와 `benchmark`부터 재실행하세요.
스냅샷은 일련의 읽기 쿼리로 얻는 복사본입니다. 내보내기 중 원본을 수정하지 않아야 하며,
전후 개수가 같은지 검사하지만 시점 일관성을 보장하는 DB 백업을 대체하지 않습니다.

## 측정 파일

| 파일 | 내용 |
|---|---|
| `.local/snapshot.json` | 원본 개수, 파일별 SHA-256·행 수·압축 크기, 내보내기 시간 |
| `.local/import_report.json` | 로컬 개수 확인, 단계별 적재 시간 |
| `.local/benchmark_report.json` | 검색 지연, 임베딩 차원, 인덱스 상태, PageRank 결과, Docker 자원·DB 크기 |
| `.local/app_smoke_report.json` | 실제 로컬 DB에 연결한 Streamlit AppTest의 홈/챗봇/PageRank 탭 렌더링 |

이번 실행의 결과는 [LOCAL_DOCKER_RESULT.md](LOCAL_DOCKER_RESULT.md)에 요약합니다.

기본 측정은 대표 분류/부가 분류 집계, 피인용 정렬, 저자 검색, 저장된 논문 벡터를 이용한 인덱스 검색,
기존 앱의 전역/개인화 PageRank를 실행합니다. 각 DB 질의는 첫 실행과 반복 12회의 p50/p95를 기록합니다.

첫 실행은 캐시를 비운 cold-start 측정이 아닙니다. 12회 p95도 작은 표본의 참고값입니다.
동시 사용자 부하, OpenAI 응답 시간, 클라우드 VM 성능을 검증한 수치로 해석하지 마세요.
Docker stats는 해당 시점의 DB 컨테이너 측정이며 호스트에서 실행하는 Python/Streamlit 메모리는 별도입니다.
벡터 인덱스 측정은 저장된 벡터를 사용하므로 한국어 질문 번역과 새 질의 임베딩의 품질 검증과 구분합니다.

앱에서 실제 질문하거나 번역·비교 버튼을 누르면 기존과 같이 OpenAI API 사용료가 발생합니다.
홈의 커뮤니티 그림은 기존 품질 리포트 스냅샷입니다. 이번 로컬 DB에서 Leiden을 재실행한 결과가 아닙니다.
GDS 옵션은 Aura 세션용이므로 이번 실험에서는 직접 계산 옵션을 사용합니다.

## 중지·재시작

```powershell
# 앱은 실행한 터미널에서 Ctrl+C로 중지합니다.
docker compose --env-file .local\neo4j.env -f compose.local.yml stop
docker compose --env-file .local\neo4j.env -f compose.local.yml start
```

중지해도 영구 볼륨 데이터는 남습니다. 이 안내에는 데이터 볼륨을 삭제하는 명령을 포함하지 않습니다.

## 확장 판단

현재 크기의 적재/질의/재시작이 통과하고 메모리 여유가 확인되면 추가 데이터를 별도 스냅샷으로 실험합니다.
데이터를 복제해 두 배로 만드는 합성 부하는 실제로 다른 논문을 수집한 데이터와 구분합니다.
현재 결과가 통과하더라도 두 배 데이터나 클라우드 배포의 성능이 보장되는 것은 아닙니다.
