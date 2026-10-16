# ADR-015: 그래프 적재는 타임아웃을 올리지 않고 재시도한다

- **상태:** 채택
- **날짜:** 2026-10-16

## 맥락

2026-10-15에 `citation_graph()`가 간선 69,438개를 읽다 `TransactionTimedOutClientConfiguration`으로
끊겼습니다. `chatbot.run_read`가 모든 질의에 `timeout=30`을 걸기 때문입니다. 앱의 PageRank 탭이
같은 경로를 쓰므로 **운영에서 사용자가 보는 오류**입니다.

## 결정

타임아웃을 올리지 않습니다. `read_with_retry()`로 **재시도**합니다 — 3회까지, 2초→4초로 간격을
늘려 가며.

## 근거

고치기 전에 쟀습니다(각 8회).

| 질의 | 행 | p50 | 최대 | 실패 | 30초까지 여유 |
|---|---:|---:|---:|---:|---:|
| `paper_rows` | 37,854 | 5.58초 | 7.18초 | 0/8 | **4.18배** |
| `citation_edges` | 69,438 | 9.59초 | 10.25초 | 0/8 | **2.93배** |

**평상시 3배 여유가 이미 있으므로 30초가 원인이 아닙니다.** 올리면 진짜 장애에서 사용자를 더
오래 매달아 둡니다. 끊겼던 그때는 Aura가 Bolt 라우팅까지 실패하던 구간이었고 90초 뒤 자체
회복했습니다 — 만성 지연이 아니라 **일시적 저하**입니다.

## 설계 세부

| 규칙 | 이유 |
|---|---|
| 간격을 늘려 감(2초→4초) | 같은 간격으로 두 번 찔러 보는 것보다 회복할 시간을 주는 쪽이 라우팅 복구와 맞습니다 |
| 연결 계열 예외만 재시도 | 질의가 잘못된 것은 기다려도 낫지 않습니다 |
| 마지막 예외를 **삼키지 않음** | 빈 결과를 돌려주면 PageRank가 빈 그래프로 틀린 순위를 냅니다. 오류 화면보다 나쁩니다 |
| 로그에 질의 본문 없음 | 운영 로그에 데이터가 섞이지 않게 |

## 남긴 것

재시도는 *일시적* 저하만 덮습니다. 데이터가 늘어 `citation_edges`가 30초에 가까워지면 **전량
적재 자체를 바꿔야 합니다**(상위 N만 적재하거나 PageRank를 미리 계산). 지금은 2.93배 여유가
있으므로 하지 않았습니다.

## 민감도

**`citation_edges`의 여유 2.93배가 이 결정의 수명입니다.** 간선이 약 2.9배(20만 개)에 가까워지면
재시도로는 부족해집니다. [`scripts/measure_graph_load.py`](../../scripts/measure_graph_load.py)를
다시 돌리면 그 시점을 알 수 있습니다.

또한 3층 재시도가 겹쳐 있습니다 — Aura 드라이버 · `read_with_retry` · `ingest`의 `Retry-After`.
**최악의 총 시도 수와 총 대기 시간을 아직 계산하지 않았습니다**(`ROADMAP.md` 11-6절).

## 재현

[`scripts/measure_graph_load.py`](../../scripts/measure_graph_load.py) →
[`reports/graph_load_latency.json`](../../reports/graph_load_latency.json) ·
테스트 [`tests/test_graph_load_retry.py`](../../tests/test_graph_load_retry.py)

## 참고

- [`advanced.md` PageRank 적재 타임아웃](../../advanced.md)
