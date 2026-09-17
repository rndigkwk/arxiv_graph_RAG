# Aura Graph RAG 품질 리포트 구현 계획

> **에이전트 작업자용:** 이 계획은 작업별로 `superpowers:subagent-driven-development`(권장) 또는 `superpowers:executing-plans` 하위 스킬을 사용해 실행해야 합니다. 단계 진행은 체크박스(`- [ ]`)로 관리합니다.

**목표:** Aura의 arXiv 그래프를 검사하고, 50건 정밀도 채점표와 인용 허브·커뮤니티 분석을 제공하는 읽기 전용 노트북을 만듭니다.

**구조:** 하나의 노트북이 순수 헬퍼, 로컬 JSONL 원본 인덱싱, 읽기 전용 Cypher, 임시 GDS 스트리밍, 정적 시각화를 맡습니다. Aura의 영속 데이터는 바꾸지 않습니다.

**기술 스택:** Python 3.12, Jupyter, nbformat, pandas, matplotlib, NetworkX, Neo4j Python 드라이버, GDS

**설계 명세:** `docs/superpowers/specs/2026-09-17-aura-graph-quality-report-design.md`

## 공통 제약

- `notebooks/aura_graph_rag_quality_report.ipynb`를 새로 만들고, 기존 노트북은 유지합니다.
- 로컬 JSONL은 읽기만 하며 수정하지 않습니다.
- Aura Free DB에서 GDS를 직접 호출하지 않고, `GdsSessions` attached 세션에서만 GDS를 실행합니다.
- `.env`의 `CLIENT_ID`, `CLIENT_SECRET`, `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `AURA_INSTANCEID`를 확인하되 값은 출력하지 않습니다.
- 정확히 지정한 세션 내부 임시 GDS 그래프를 삭제하는 경우를 제외하고 읽기 Cypher만 사용합니다.
- 표본 시드 42, GDS 시드 42, GDS 동시성 1, 표본 50건을 사용합니다.
- 저자명 ER 결과를 실존 인물의 동명이인 해소 결과로 해석하지 않습니다.

### 작업 1: 순수 헬퍼와 테스트 셀 추가

**파일:** `notebooks/aura_graph_rag_quality_report.ipynb` 생성, `tests/test_aura_graph_rag_quality_report.py` 생성

- [ ] arXiv ID 정규화, 관계 자기참조 기각, 수동 점수 완결성, 양방향 메타그래프 간선 가중치 합산 테스트를 작성합니다.
- [ ] `uv run pytest tests/test_aura_graph_rag_quality_report.py -v`를 실행해 헬퍼가 없어서 실패함을 확인합니다.
- [ ] 노트북에 `normalize_arxiv_id`, `check_relation_schema`, `score_sample`, `build_meta_graph` 순수 헬퍼를 추가하고, 테스트 로더는 해당 셀만 실행하게 합니다.
- [ ] 테스트 명령을 다시 실행해 모든 테스트가 통과하는지 확인합니다.

### 작업 2: 원본 대조와 품질 분석 셀 추가

**파일:** `notebooks/aura_graph_rag_quality_report.ipynb` 수정, `tests/test_aura_graph_rag_quality_report.py` 수정

- [ ] 루트 탐색, 비밀값을 출력하지 않는 `.env` 검증, JSONL 로더, 매개변수화한 읽기 전용 Cypher 실행기를 추가합니다.
- [ ] `cit_arxiv_id`를 인덱싱하고 Aura 관계를 조회해 준수율·ER 일관성을 계산하며, 빈 점수 칸을 갖는 50건 채점표를 만듭니다.
- [ ] 통과/기각 사유 집계와 `None` 수동 점수 보존을 테스트하고, 전체 헬퍼 테스트를 실행합니다.

### 작업 3: GDS·리포트 표·시각화 셀 추가

**파일:** `notebooks/aura_graph_rag_quality_report.ipynb` 수정

- [ ] `AuraAPICredentials`, `GdsSessions`, `DbmsConnectionInfo`로 attached GDS 세션을 열고, 추정 메모리와 TTL을 적용합니다.
- [ ] 세션 안에서 `arxiv_citation_quality_report`만 만들고 정리하며, `DELETE_GDS_SESSION`이 참일 때만 세션을 삭제합니다.
- [ ] PageRank 상위 10개와 재현 가능한 Leiden을 실행하고, 규모 상위 5개 커뮤니티를 요약합니다.
- [ ] 커뮤니티 규모 및 5개 커뮤니티 메타그래프 시각화를 그리고, 헬퍼 테스트를 다시 실행합니다.

### 작업 4: 노트북 정적 검증

**파일:** `tests/validate_aura_graph_rag_quality_report.py` 생성, 필요할 때만 `notebooks/aura_graph_rag_quality_report.ipynb` 수정

- [ ] `nbformat`으로 노트북을 검증하고 Python 코드 셀 전체를 컴파일하며, 세 품질 지표·PageRank 10·커뮤니티 5·시각화 제목을 확인합니다.
- [ ] `uv run pytest tests/test_aura_graph_rag_quality_report.py -v`와 `uv run python tests/validate_aura_graph_rag_quality_report.py`를 실행합니다.
- [ ] 완료 보고 전에 `git diff --check`와 노트북 JSON을 점검합니다.
