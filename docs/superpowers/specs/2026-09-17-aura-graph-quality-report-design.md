# Aura Graph RAG 품질 리포트 설계 명세

## 목표

로컬 JSONL 원본과 적재된 arXiv 그래프를 대조하고, 품질 지표·PageRank·커뮤니티·커뮤니티 시각화로 인용 그래프의 중심을 설명하는 읽기 전용 Jupyter 노트북 하나를 만듭니다.

## 입력 데이터와 그래프 규약

`data/ai/arxiv_cs_recent_3months_oai_part*.jsonl` 및 `data/ai_references/arxiv_ai_references_api/*_part*.jsonl`을 읽습니다. 기존 `.env`의 Aura 접속 변수명을 사용하며, 원본 파일이나 Aura의 영속 데이터는 수정하지 않습니다.

대상 그래프 스키마는 `(:ArxivAuthorName)-[:AUTHORED]->(:ArxivPaper)` 및 `(:ArxivPaper)-[:REFERENCES]->(:ArxivPaper)`입니다. `ArxivPaper.arxiv_id`는 논문의 표준 키이고, `ArxivAuthorName.name`은 저자명 키입니다. 저자명은 실존 인물 식별자가 아니므로, 저자 ER 결과가 인물 단위의 동명이인 해소를 증명하지 않는다고 명시합니다.

## 측정 지표

스키마 준수율은 Aura의 모든 `AUTHORED`, `REFERENCES` 관계를 검사합니다. 지정된 양 끝 노드 레이블, 비어 있지 않은 키 필드, 자기참조가 아닌 관계를 통과 조건으로 합니다. 통과 수·검사 수·비율·기각 사유별 건수·오류 예시를 표시합니다.

정밀도 채점표는 스키마를 통과한 `REFERENCES` 관계에서 `random.Random(42)`로 50건을 뽑습니다. 인용 논문/피인용 논문 ID, 제목, 원본 파일 포함 여부, 빈 `score`·`note` 열을 함께 제공합니다. 검토자가 의미 적합성을 0 또는 1로 채운 뒤에만 정밀도를 계산합니다. 원본 파일 포함 여부는 검토 보조 정보이며, 의미 정답의 자동 판정으로 사용하지 않습니다.

ER 일관성은 중복 논문 ID, 정규화 후 같은 논문 ID, 비어 있거나 중복된 정규화 저자명, 하나의 정규화 저자 키에 대응하는 여러 저장 저자명을 보고합니다. 이 노트북은 노드를 병합하지 않습니다.

## GDS와 시각화

Aura Free DB에서 직접 GDS 프로시저를 호출하지 않습니다. `.env`의 `CLIENT_ID`, `CLIENT_SECRET`, 선택 `PROJECT_ID`를 `AuraAPICredentials`로 전달해 `GdsSessions`를 만들고, 기존 `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `AURA_INSTANCEID`로 `DbmsConnectionInfo`를 구성합니다. `sessions.estimate()`의 계산 결과로 메모리를 정한 뒤 TTL이 있는 attached GDS 세션을 `get_or_create()`로 엽니다.

열린 세션 안에서 `ArxivPaper`와 `REFERENCES`만 이용한 임시 메모리 투영 `arxiv_citation_quality_report`를 만듭니다. Leiden을 위해 관계 방향은 무방향으로 처리합니다. 같은 이름의 세션 내부 그래프만 제거하며, `finally`에서 정리합니다. GDS 세션 자체는 기본적으로 재사용하고, 사용자가 `DELETE_GDS_SESSION = True`로 명시한 경우에만 `sessions.delete()`로 종료해 불필요한 세션 비용을 막습니다.

PageRank를 스트리밍으로 실행해 허브 논문 10개를 표시합니다. Leiden은 `randomSeed: 42`, `concurrency: 1`로 실행하고 모듈러리티·커뮤니티 수·규모 상위 5개 커뮤니티의 대표 허브와 분류 분포를 표시합니다. GDS가 없거나 권한이 없으면 품질 지표는 계속 보여 주되, 원인을 명확히 안내합니다.

한글 폰트에 안전한 커뮤니티 규모 막대그래프와 상위 5개 커뮤니티만 표시하는 NetworkX 메타그래프를 그립니다. 점 크기는 커뮤니티 논문 수, 선 굵기는 커뮤니티 간 인용 수이며, 배치에는 시드 42를 사용합니다.

## 검증

`nbformat`으로 노트북을 파싱하고 모든 Python 코드 셀을 컴파일하며, 필수 제목을 확인합니다. 픽스처를 사용한 순수 헬퍼 테스트를 실행합니다. 유효한 Aura API 자격 증명과 활성 GDS 세션 없이 실시간 실행 성공을 주장하지 않습니다.
