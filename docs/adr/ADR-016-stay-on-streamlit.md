# ADR-016: Cloud Run으로 옮기지 않는다

- **상태:** 보류
- **날짜:** 2026-10-14

## 맥락

같은 저자의 `dograg`는 메모리 한도 때문에 Streamlit Community Cloud에서 Cloud Run으로 옮겼습니다.
같은 전환을 이 프로젝트에도 할 것인가.

## 결정

지금은 옮기지 않습니다.

## 근거

**dograg가 옮긴 이유가 여기엔 없습니다.** 앱 핵심 경로 메모리를 실측했습니다 — **259MB**,
Streamlit 보장치 690MB의 38%입니다. 넘칠 이유가 없습니다.

| dograg의 전환 이유 | 이 프로젝트 |
|---|---|
| 메모리 한도 초과 | 해당 없음 (259MB / 690MB) |
| 무거운 로컬 모델 | 해당 없음 (API 임베딩, [ADR-009](ADR-009-keep-openai-embedding.md)) |
| 커스텀 런타임 | 해당 없음 |
| **무인 깨우기** | **아직 실제 문제로 나타나지 않음** |

마지막 항목만 남습니다. Aura Free는 3일 미사용 시 멈추고, Streamlit 앱도 잠들면 사람이 눌러야
깨어납니다. 그런데 [`keep-alive.yml`](../../.github/workflows/keep-alive.yml)이 12시간마다 도는
한 잠들지 않습니다.

**그대로 옮기면 dograg 복제입니다.** 같은 문제를 같은 방법으로 푼 두 번째 사례는 포트폴리오에서
값이 없습니다.

## 전환 조건

둘 중 하나입니다.

1. keep-alive가 비활성화돼 실제로 `Zzzz` 화면이 노출된 적이 있을 때 (Actions 탭의 워크플로
   비활성화 알림으로 확인됩니다)
2. Day 77(FastAPI 검색 API 분리)을 하기로 결정했을 때 — 그 경우 Cloud Run이 자연스러운
   호스트이고, 검색을 API로 떼면 **dograg에 없는 구조**가 되며 평가기가 앱을 거치지 않게 됩니다

## 민감도

메모리 259MB는 **측정 시점(2026-10-14)의 의존성 기준**입니다. 앱 의존성이 늘면 이 값이 바뀌고
결론도 바뀝니다. 그래서 다시 재는 스크립트를 두었습니다 —
[`scripts/measure_app_memory.py`](../../scripts/measure_app_memory.py).

> **이 문서가 실제로 늙었습니다.** 2026-10-18까지 여기에 259MB라고 적혀 있었고 실제는
> 266.5MB였습니다. Langfuse 비용을 재다가 **우연히** 발견했습니다. 측정값을 적는 프로젝트에서
> 그 숫자가 조용히 늙는 것은 링크가 깨지는 것보다 나쁩니다 — 링크는 CI가 잡아 줬고 숫자는
> 아무도 보지 않았습니다. 그래서 아래 표의 수치는 출처 보고서에 묶여 있고
> [`scripts/check_doc_numbers.py`](../../scripts/check_doc_numbers.py)가 CI에서 대조합니다.

### 2026-10-18 재측정 — Langfuse를 넣어도 들어갑니다

| 구성 | 피크 상주 | 690MB 대비 |
|---|---:|---:|
| 2026-10-14 (ADR 작성 시점) | 259 MB | 38% |
| 2026-10-18 현재 | **<!--n:reports/app_memory.json#peak_mb-->266.5<!--/n--> MB** | 39% |
| 2026-10-18 + Langfuse 임포트 | **<!--n:reports/app_memory_langfuse.json#peak_mb-->271.7<!--/n--> MB** | 39% |

**Langfuse는 앱 피크에 +5.2 MB를 더합니다.** 여유가 <!--n:reports/app_memory_langfuse.json#headroom_mb-->418.3<!--/n--> MB 남습니다.

그런데 **빈 인터프리터에 Langfuse만 올리면 60.7 MB**입니다(`langfuse` <!--n:reports/app_memory_langfuse.json#extra_imports/langfuse/delta-->43.0<!--/n--> + `langfuse.langchain`
<!--n:reports/app_memory_langfuse.json#extra_imports/langfuse.langchain/delta-->17.7<!--/n-->). 두 숫자가 다른 이유는 **의존성이 이미 올라와 있기 때문**입니다 — `httpx`·`pydantic`·
`wrapt`·`packaging`은 `langchain`과 `openai`가 이미 가져옵니다. 새로 드는 것은 OpenTelemetry
세 패키지뿐이고, 그것이 5.2 MB입니다.

`ROADMAP.md` 5절이 "클라이언트 경량"이라고 **가정**했던 것이 이제 측정값이 됐습니다.

> **이 측정이 덮지 않는 것.** 임포트 비용만 쟀습니다. Langfuse는 스팬을 모아 백그라운드
> 스레드로 보내므로 **트레이스 양에 비례하는 런타임 비용**이 따로 있고, 그것은 재지 않았습니다.
> 배치 설정과 샘플링 비율에 달려 있습니다.

## 참고

- [`ROADMAP.md` 8-1-1·8-5절](../../ROADMAP.md)
