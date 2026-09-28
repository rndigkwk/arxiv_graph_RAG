# RAG 평가 품질·비용·재현성 개선 실행 계획

> 사용자가 승인한 개선 사항을 현재 원본 저장소에서 순서대로 구현한다. 백업 저장소는 수정하거나 원격으로 push하지 않는다.

**목표:** 답변의 직접 근거와 PageRank 관련 논문을 분리하고, 평가 데이터셋·실행 보고서·검증을 보강해 결과를 해석하고 재현할 수 있게 한다.

**구성:** `chatbot.py`의 답변 검증을 순수 모듈로 분리해 DB 연결 없이 단위 테스트한다. `evals`는 기존 케이스와 보강 후보 케이스를 같은 실행기에서 읽고, 근거 유형별 점수·사용량·지연시간·실험 정보를 버전이 명시된 JSON으로 저장한다. CI는 외부 API에 연결하지 않고 정적 검증과 단위 테스트를 수행한다.

**기술:** Python 3.12, pytest, Ruff, `tiktoken`, `uv`, GitHub Actions.

**설계 명세:** `docs/superpowers/specs/2026-09-27-rag-evaluation-quality-design.md`

## 공통 제약

- 기존 31건 자동 평가셋과 보고서는 보존한다.
- 새 평가 라벨은 사람 검토 전까지 `pending_human_review`로 표시한다.
- 민감한 환경값은 보고서와 로그에 기록하지 않는다.
- API 금액은 입력·출력·임베딩 단가와 가격 출처가 모두 설정된 경우에만 계산한다.
- Aura 비용은 산출하지 않으며, CI가 API 접속을 요구하지 않게 한다.
- 변경 범위는 `C:\Users\Playdata\Desktop\arxiv_graph_RAG`로 한정하고 백업 프로젝트는 수정하지 않는다.

## 검토할 실패 조건

- PageRank 관련 결과에만 있는 ID가 직접 답변 근거로 승인되지 않는지 확인한다 — 인용 검증 단위 테스트.
- 벡터 검색 논문과 그래프 조회 결과는 직접 근거로 계속 허용되는지 확인한다 — 직접 근거 테스트.
- 기존 케이스와 새 출처 정보가 있는 케이스를 같은 평가기에서 읽을 수 있는지 확인한다 — 데이터셋 호환성 테스트.
- 제공자 토큰 정보나 가격 단가가 없을 때 0으로 오인하지 않는지 확인한다 — 누락 정보 테스트.
- 측정값이 비었거나 하나뿐일 때, 또는 ID가 중복일 때 요약 결과가 유효한지 확인한다 — 경계 조건 테스트.

---

### 1단계: 직접 답변 근거와 관련 논문 분리

**대상 파일:**
- 생성: `answer_validation.py`
- 수정: `chatbot.py`
- 생성 또는 수정: `tests/test_chatbot_citations.py`

**함수와 동작:**
- `validate_evidence_ids(evidence_ids: list[str], direct_ids: set[str]) -> None`
- 직접 근거 ID는 현재 질문의 벡터 검색 논문 ID와 그래프 행의 `evidence_ids`를 합친다. PageRank `related` 결과는 여기에 포함하지 않는다.
- `chatbot.validate_answer()`는 순수 검증 함수를 호출하고, 기존 답변 비어 있음 검사와 그래프 행 형식 검사는 유지한다.

- [ ] **1단계: 먼저 실패 테스트 작성**
  - 벡터 검색 또는 그래프 행이 반환한 ID는 허용한다.
  - PageRank 관련 결과에만 존재하는 ID는 거부한다.
  - 잘못된 형식과 중복 ID를 거부하며 Neo4j 접속 정보는 요구하지 않는다.
- [ ] **2단계: 테스트를 실행해 관련 결과 전용 ID를 현재 검증기가 받아들이는 이유로 실패하는지 확인한다.**
- [ ] **3단계: 순수 검증기를 구현하고 프롬프트를 변경한다.** `evidence_ids`에는 답변 본문을 직접 뒷받침하는 ID만 넣는다. PageRank 이웃은 앱이 별도 표로 보여 주므로 답변 본문에서 ID와 함께 중복 나열하지 않는다.
- [ ] **4단계: 인용 테스트를 실행해 통과를 확인한다.**

### 2단계: 주제별 평가 후보셋 보강

**대상 파일:**
- 생성: `evals/cases_curated.json`
- 수정: `tests/test_eval_dataset.py`
- 수정: `evals/README.md`

**데이터 형식:**
- 각 케이스는 기존 필드 `id`, `category`, `question`, `search_query`, `expected_ids`, `expected_answer_contains`를 유지한다.
- 케이스마다 `review_status`, `label_rationale`, `source_evidence`(arXiv ID, 출처 필드, 짧은 원문 발췌)를 추가한다.
- `expected_related_ids`는 직접 답변 정답 ID와 별도 필드로 둔다.

- [ ] **1단계: 데이터 형식과 출처 검사를 위한 실패 테스트 작성** — 주제 케이스 최소 개수, 고유 ID, 비어 있지 않은 gold ID, 검토 상태, 출처 발췌를 검사한다.
- [ ] **2단계: 후보 데이터 파일이 없어 테스트가 실패하는지 확인한다.**
- [ ] **3단계: 체크인된 JSONL 원천에서 주제 케이스를 10건 이상 정리한다.** ID와 발췌문이 원천 레코드에 실제로 있는지 확인하고 모든 케이스를 `pending_human_review`로 표시한다.
- [ ] **4단계: 데이터셋 테스트를 실행하고 생성한 출처 정보를 모두 확인한다.**

### 3단계: 답변 근거 점수와 관련 논문 점수 분리

**대상 파일:**
- 수정: `evals/metrics.py`
- 수정: `evals/run.py`
- 수정: `tests/test_evaluation_metrics.py`
- 수정: `tests/test_eval_runner.py`

**함수와 동작:**
- 답변 근거 지표는 `evidence_ids`와 직접 답변용 `expected_ids`만 비교한다.
- 관련 논문 지표는 `related_ids`와 `expected_related_ids`를 비교한다. 관련 정답 목록이 없으면 0 대신 `None`으로 둔다.
- 새 보고서는 `schema_version: 2`와 `answer_evidence_precision/recall`, `related_retrieval_precision/recall` 필드를 사용한다. 기존 평가 케이스와 보고서의 로딩 호환성은 유지한다.

- [ ] **1단계: 먼저 실패 테스트 작성** — 직접 인용 precision/recall, 관련 ID 별도 평가, 관련 gold 누락 시 `None`을 검사한다.
- [ ] **2단계: 현재 통합 점수 방식에서 실패하는지 확인한다.**
- [ ] **3단계: 점수를 분리해 구현하고 기존 케이스도 평가할 수 있도록 유지한다.**
- [ ] **4단계: 모든 지표와 실행기 테스트를 수행한다.**

### 4단계: 사용량·가격·지연시간·실험 정보 계측

**대상 파일:**
- 생성: `evals/telemetry.py`
- 수정: `evals/run.py`, `pyproject.toml`, `uv.lock`, `.env.example`
- 생성 또는 수정: `tests/test_eval_telemetry.py`, `tests/test_eval_runner.py`

**함수와 동작:**
- `summarize_latency(samples: Sequence[float]) -> dict[str, float | None]`는 평균·p50·p95를 반환하며 표본이 없으면 각 값은 `None`이다.
- `estimate_api_cost(*, chat_input_tokens, chat_output_tokens, embedding_tokens, rates) -> float | None`는 필요한 사용량·요금·가격 출처가 모두 있을 때만 USD를 계산한다.
- `build_run_metadata(*, cases_path, case_ids, mode, top_k, model, embedding_model) -> dict[str, object]`는 UTC 시각, 데이터셋 SHA-256, Git SHA·변경 여부, OS/Python·패키지 버전, 실행 인수를 기록한다.
- 임베딩 토큰 수는 실제 검색 질의에 모델 호환 `tiktoken` 인코딩을 적용해 추정치로 표시한다. 제공자 사용량 정보가 필요한 호출에서 누락되면 해당 합계와 비용을 `null`로 둔다.
- 선택 가격 환경변수는 `EVAL_CHAT_INPUT_USD_PER_1M`, `EVAL_CHAT_OUTPUT_USD_PER_1M`, `EVAL_EMBEDDING_USD_PER_1M`, `EVAL_PRICING_SOURCE`이다.

- [ ] **1단계: 먼저 실패 테스트 작성** — p50/p95, 빈 값과 단일 표본, 가격 계산식, 토큰·단가 누락, 데이터 해시·모델 정보, 민감정보 미포함을 검사한다.
- [ ] **2단계: 계측 함수와 필드가 없어 테스트가 실패하는지 확인한다.**
- [ ] **3단계: 계측 기능을 구현하고 케이스별 JSON과 요약에 채팅 토큰, 임베딩 토큰 추정치, 지연시간 백분위, 선택적 비용, 실행 정보를 추가한다.**
- [ ] **4단계: 모의 제공자와 합성 케이스로 검증한다. 단위 테스트에서는 유료 API를 호출하지 않는다.**

### 5단계: 문서 및 CI 검증

**대상 파일:**
- 수정: `.github/workflows/ci.yml`, `advanced.md`, `evals/README.md`
- 필요할 때만 수정: `README.md`

- [ ] **1단계: CI에 평가 후보 데이터 형식 검사와 계측 테스트를 추가한다.** Aura/OpenAI 인증정보를 요구하지 않는다.
- [ ] **2단계: CI 명령을 로컬에서 그대로 실행한다.** 잠금 파일 검사, 잠금 기준 dev 설치, Ruff, pytest, compileall을 수행한다.
- [ ] **3단계: 새 데이터 형식, 결과 해석, 가격 설정, 실행 정보 및 개인정보 보호 규칙을 문서화한다.**
- [ ] **4단계: 공개 Actions 상태를 확인한다.** 인증 또는 공개 실행 기록이 없으면 동일한 로컬 검증을 수행하고 원격 CI는 미확인으로 기록한다.

## 최종 검증

- Ruff로 `evals`, `tests`, `answer_validation.py`를 검사한다.
- 오프라인 전체 pytest를 실행한다.
- 쓰기 가능한 임시 캐시를 지정해 `uv lock --check`를 실행한다.
- 앱·챗봇·검증·평가 모듈에 Python `compileall`을 실행한다.
- 로컬 Markdown 링크, `git diff --check`, 보고서 스키마 버전, 민감정보 제거, 모든 평가 발췌문의 출처를 확인한다.
- 같은 exact-title 테스트 케이스에서 기존·변경 후 인용 점수를 비교한다. 구체적인 차이를 조사할 필요가 없다면 유료 전체 벤치마크는 다시 실행하지 않는다.
