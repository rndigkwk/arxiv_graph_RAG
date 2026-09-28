# 논문 비교 모드 구현 계획

> **구현 담당자 안내:** 작업은 `superpowers:executing-plans` 하위 스킬을 사용해 단계별로 진행합니다. 각 단계는 체크박스로 추적합니다.

**목표:** 현재 챗봇 응답의 벡터 검색 결과 중 사용자가 고른 2~4편의 논문을 근거에 맞춰 비교하는 Streamlit 흐름을 추가한다.

**구조:** `chatbot.py`에서 구조화된 비교 결과 형식을 정의하고, 선택한 논문의 ID·제목·초록만 프롬프트에 전달한 뒤 출력과 근거 ID를 검증한다. `app.py`는 각 답변별 선택 및 비교 상태를 관리하고, 검증된 결과를 arXiv 링크가 포함된 표로 표시한다.

**기술 스택:** Python 3.12+, Pydantic, LangChain 구조화 출력, Streamlit, pytest, Streamlit AppTest.

**명세:** `docs/superpowers/specs/2026-09-27-paper-comparison-design.md`

## 전체 제약

- 선택 논문의 저장된 제목과 초록만 사용하며, PDF 전문을 읽거나 전문을 확인한 것처럼 표현하지 않는다.
- 비교 대상은 2~4편이며 선택한 논문 순서를 결과에도 유지한다.
- 비교 항목은 연구 질문, 방법, 데이터/실험 설정, 주요 결과로 한다.
- 초록에서 근거를 찾지 못한 항목은 `초록에서 확인할 수 없음`과 빈 근거 ID 목록으로 표시한다.
- 각 항목은 해당 논문 행의 arXiv ID만 근거로 사용할 수 있다. 누락·중복·미선택 논문 행은 거부한다.
- 비교 결과를 원래 답변에 연결하고, 대화를 초기화하면 비교 상태도 초기화한다.
- 테스트는 Neo4j나 유료 모델 API를 호출하지 않는다. 관련 없는 포트폴리오 변경은 push하거나 commit하지 않는다.

## 검토 중점

- 논문이 2편 미만이면 비교 UI를 표시하지 않고 모델 호출도 하지 않는다 (작업 3 UI 테스트).
- 선택 ID가 2편 미만이거나 4편을 초과하면 모델 호출 전에 입력 검증에서 거부한다 (작업 1 검증기 테스트).
- 논문 행이 누락·중복되거나 선택하지 않은 논문이 포함되면 구조화 결과 전체를 거부한다 (작업 1 출력 검증 테스트).
- 초록에서 근거를 찾지 못한 항목은 지정된 미확인 문구와 빈 근거 ID를 유지한다 (작업 1 항목 검증 테스트).
- 모델/API 오류 또는 잘못된 결과가 있으면 일부 비교표를 표시하지 않고 다시 시도할 수 있는 오류를 안내한다 (작업 3 AppTest).

---

### 작업 1: 비교 결과 스키마와 근거 검증

**파일:**
- 수정: `chatbot.py`
- 생성: `tests/test_paper_comparison.py`

**인터페이스:**
- Pydantic 모델 `ComparisonField(text: str, evidence_ids: list[str])`, `ComparedPaper(arxiv_id: str, research_question: ComparisonField, method: ComparisonField, data_and_evaluation: ComparisonField, key_findings: ComparisonField)`, `PaperComparison(papers: list[ComparedPaper])`를 제공한다.
- `validate_paper_comparison(result: PaperComparison | dict, selected_papers: list[dict]) -> PaperComparison`를 제공한다. 선택 논문 ID가 2~4개의 고유 ID인지, 결과에 각 ID의 행이 정확히 하나씩 있는지, 중복·추가 행이 없는지, 각 항목의 근거 ID가 `[]` 또는 `[해당 행 arXiv ID]`인지 검증한다. 공백뿐인 문구는 거부한다. 근거 ID가 비어 있으면 문구는 `초록에서 확인할 수 없음`이어야 한다.
- 검증기는 결과 행을 선택 입력 순서에 맞춰 반환한다.

- [x] **1단계: 검증 실패 테스트 작성**

유효 행, 선택 논문 2편 미만 및 4편 초과, 누락·중복 행, 미선택 논문 행, 잘못된 근거 ID, 빈 문구, 인용이 붙은 미확인 문구, 근거가 없는 정상 미확인 문구, 선택 순서 유지 여부를 검사한다.

- [x] **2단계: 테스트를 실행해 비교 모델/검증기가 없어 실패하는지 확인**

`chatbot.py`를 가져오기 전에 실제 `.env` 없이도 테스트할 수 있도록 가짜 Neo4j/OpenAI 환경변수를 설정한다. 네트워크 요청은 하지 않는다.

실행: `uv run --locked python -m pytest tests/test_paper_comparison.py -q`
예상 결과: 비교 모델 또는 검증기를 찾을 수 없어 테스트 수집/실행이 실패한다.

- [x] **3단계: `chatbot.py`에 Pydantic 모델과 `validate_paper_comparison` 구현**

프롬프트 내용이나 인증 정보를 노출하지 않으면서 잘못된 비교 결과를 설명하는 검증 오류를 사용한다.

- [x] **4단계: 대상 테스트와 lint 실행**

실행: `uv run --locked python -m pytest tests/test_paper_comparison.py -q`
예상 결과: 비교 결과 검증 테스트가 모두 통과한다.

실행: `uv run --locked ruff check chatbot.py tests/test_paper_comparison.py`
예상 결과: 오류 없음.

### 작업 2: 초록만 근거로 비교문 생성

**파일:**
- 수정: `chatbot.py`
- 수정: `tests/test_paper_comparison.py`

**인터페이스:**
- `compare_papers(selected_papers: list[dict]) -> PaperComparison`를 제공한다.
- 작업 1의 `PaperComparison`, `validate_paper_comparison`을 사용한다.
- `with_structured_output(PaperComparison)`으로 모델을 호출한다. 시스템 지침은 제공된 제목·초록만 사용하고, 한국어로 간결하게 쓰며, 근거가 없으면 지정 미확인 문구를 반환하도록 한다.

- [x] **1단계: 가짜 구조화 모델을 사용하는 실패 테스트 작성**

모델 입력이 선택한 논문의 `arxiv_id`, `title`, `abstract`로만 구성되는지, 올바른 결과를 반환하는지, 잘못된 근거 ID가 거부되는지, 제공자 예외가 부분 결과 없이 그대로 전달되는지 검사한다.

- [x] **2단계: `compare_papers`가 없어 실패하는지 테스트**

실행: `uv run --locked python -m pytest tests/test_paper_comparison.py -q`
예상 결과: `compare_papers`를 찾을 수 없어 실패한다.

- [x] **3단계: `chatbot.py`에 `compare_papers` 구현**

모델 호출 전에 필수 입력 필드를 검사한다. 허용한 세 필드만 프롬프트에 넣고, 결과는 작업 1의 검증기를 통과시킨다.

- [x] **4단계: 비교 테스트와 lint 실행**

실행: `uv run --locked python -m pytest tests/test_paper_comparison.py -q`
예상 결과: 스키마·입력 범위·가짜 모델·출력 검증 테스트가 외부 호출 없이 통과한다.

실행: `uv run --locked ruff check chatbot.py tests/test_paper_comparison.py`
예상 결과: 오류 없음.

### 작업 3: 답변별 UI·상태·문서화

**파일:**
- 수정: `app.py`
- 수정: `tests/test_paper_comparison.py`
- 수정: `APP_README.md`

**인터페이스:**
- 기존 챗봇 답변 렌더링에서 호출할 `show_paper_comparison(chatbot, response, index)`를 제공한다.
- 유일한 선택 원천은 `response["papers"]`이며, Streamlit 키는 답변마다 고유하게 만든다. 비교 가능한 검색 결과가 2편 미만이면 선택 UI 대신 안내 문구를 표시한다.
- 검증된 결과를 답변 인덱스별 `st.session_state` 매핑에 저장한다. `clear_chat()`은 대화/기록/번역 상태와 함께 이를 초기화한다.

- [x] **1단계: 표시·실행·유지·오류의 AppTest 작성**

가짜 챗봇을 사용해 다음을 검사한다. 비교 가능한 논문이 2편 미만이면 조작부가 없다. 두 편 이상 선택하면 4개 이내를 고를 수 있다. 비교 버튼을 누르면 네 항목과 arXiv 링크가 표시된다. 재실행해도 해당 답변의 결과가 유지된다. 대화를 지우면 비교 상태도 지워진다. 예외가 발생하면 다시 시도할 수 있는 오류만 표시되고 부분 표는 나오지 않는다.

- [x] **2단계: 비교 UI/상태가 없어 실패하는지 AppTest 실행**

실행: `uv run --locked python -m pytest tests/test_paper_comparison.py -q`
예상 결과: 비교 UI 동작이 아직 없어 실패한다.

- [x] **3단계: `app.py`에 답변별 선택·실행·결과·초기화 구현**

각 답변의 검색 결과 아래에 비교 UI를 표시하고, 유효한 선택 뒤에만 `chatbot.compare_papers`를 호출한다. 논문별 행에 네 항목과 `https://arxiv.org/abs/{arxiv_id}` 링크를 표시한다. 검증되지 않은 모델 출력은 표시하지 않는다.

- [x] **4단계: `APP_README.md`에 기능 설명 추가**

선택 과정, 네 비교 항목, 초록 한정 근거, 미확인 처리, arXiv 출처 링크를 문서화한다.

- [x] **5단계: 최종 로컬 검증 실행**

실행: `uv run --locked python -m pytest -q`
예상 결과: 전체 테스트가 DB나 모델 자격 증명 없이 통과한다.

실행: `uv run --locked ruff check answer_validation.py evals tests chatbot.py app.py`
예상 결과: 오류 없음.

실행: `uv run --locked python -m compileall -q app.py chatbot.py answer_validation.py evals tests`
예상 결과: 성공 종료한다.

실행: `git diff --check`
예상 결과: 공백 오류가 없다.

별도 관측하지 않은 이상 실제 모델/Aura 연결 테스트나 원격 CI 실행을 완료했다고 주장하지 않는다.
