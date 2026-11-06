# ADR-106: VM 주간 수집이 멈추면 배포 점검이 알린다 — 상태는 `/api/health`가 파일에서 읽는다

- **상태:** 채택
- **날짜:** 2026-11-06

## 맥락

주간 수집은 VM의 타이머가 돌립니다([ADR-104](ADR-104-weekly-ingest-runs-on-the-vm.md), 월요일 03:19 UTC). 첫 정기
실행은 11-09입니다. 그런데 실패를 알려 주는 길이 없었습니다.

- 실패 알림(`alert-on-failure.yml`)은 GitHub Actions 실행만 봅니다. VM 타이머는 그 대상이 아닙니다.
- Actions의 "Weekly ingest"는 아직 돌지만, 백업 DB(Aura)를 상대로 한 시험 실행이라 VM이 적재했는지와 상관이 없습니다.
- 키가 만료되거나 디스크가 차거나 타이머가 멈추면 그래프는 옛 논문에 머뭅니다. "최근 2주" 질문이 빈 답을
  낼 때에야 드러납니다([ADR-105](ADR-105-recent-uses-the-period-the-question-names.md)가 이 실패를 민감도에 적었습니다).
- 하루가 페이지 한도를 넘으면 워터마크가 멈춥니다(ADR-104의 `truncated_on`). 적재는 성공으로 끝나므로 종료 코드로는
  보이지 않습니다.

## 결정

**VM이 남긴 파일을 `/api/health`가 읽어 `ingest`로 내고, 8시간마다 도는 배포 점검이 그것을 실패로 셉니다.**
실패는 기존 경로(`alert-on-failure.yml`)로 `alert` 이슈를 엽니다.

- **`deploy/vm/ingest.sh load`가 끝에 `pipeline/state/last-ingest.json`을 씁니다.** 성공·실패와 상관없이 종료 코드,
  발표일 채우기의 종료 코드, 보고서 경로를 적습니다. 수집이 예외로 죽으면 워터마크 파일(`pipeline/state/ingest.json`)에는
  아무것도 남지 않아서 이 파일이 필요합니다. 적재가 실패하면 발표일 채우기는 건너뜁니다.
- **`src/ingest_status.summary`가 두 파일을 읽어 문제를 짧은 이름으로 적습니다.**

  | 문제 | 기준 |
  |---|---|
  | `load_stale` | 마지막 적재가 8일보다 오래됨 - 월요일 실행이 늦어도 하루는 기다립니다 |
  | `watermark_behind` | 워터마크가 오늘보다 12일 넘게 뒤 - 월요일 직후에도 주말만큼 뒤라 다음 실행 직전에는 열흘쯤입니다 |
  | `ingest_failed` · `backfill_failed` | `last-ingest.json`의 종료 코드가 0이 아님 |
  | `stopped_at_<이유>` | 보고서가 `completed`·`harvest_empty` 말고 다른 이유로 멈춤(비용 상한, 용량 등) |
  | `not_idempotent` | 같은 배치를 다시 적재했을 때 수가 늘어남 |
  | `no_state` · `never_loaded` · `no_watermark` | 상태 파일이 없거나 비어 있음 |

  `last-ingest.json`이 없으면 워터마크 파일만으로 봅니다. 11-06 첫 적재는 이 파일이 생기기 전이었습니다.
- **배포 점검(`deployed-app.yml`)의 둘째 단계가 `visit_deployed_app.py --ingest`로 읽습니다.** 첫 단계(배포 확인)가
  실패해도 돌아, 한 알림에 둘 다 적힙니다.

**파일만 읽습니다.** 그래프에 묻지 않고, 모델도 부르지 않고, 비밀값도 다루지 않습니다. 저장소가 공개라 CI 로그도
공개입니다 - 보고서의 오류 문구(`stop_error`)는 내보내지 않고 편수·멈춘 이유·잘린 날만 냅니다.

## 고르지 않은 것

- **VM에서 GitHub 이슈를 직접 열기.** VM에 GitHub 토큰을 둬야 합니다. VM의 `.env`에는 지금 키 두 개와 설정만 있습니다.
- **수집을 GitHub Actions로 옮기기.** ADR-104가 이미 기각했습니다 - DB 포트를 인터넷에 열어야 합니다.
- **systemd의 `OnFailure=`로 알리기.** 실패는 잡지만 타이머가 아예 돌지 않은 경우와 워터마크가 멈춘 경우는 못 잡고,
  알릴 곳(메일, 토큰)이 따로 필요합니다.

## 민감도

- 배포 점검은 8시간마다라, 문제가 생기고 알림이 오기까지 최대 8시간입니다. 주간 수집에는 충분합니다.
- 실패한 실행은 그 뒤 첫 점검에서 잡힙니다. 타이머가 멈춘 경우는 마지막 적재 8일 뒤(다음 화요일 03:19 UTC쯤)입니다.
- `harvest_empty`(한 주 동안 받은 논문이 0편)는 실패로 세지 않습니다. 그런 주가 이어지면 `load_stale`로 잡힙니다.

## 참고

- [`src/ingest_status.py`](../../src/ingest_status.py), [`deploy/vm/ingest.sh`](../../deploy/vm/ingest.sh),
  [`scripts/visit_deployed_app.py`](../../scripts/visit_deployed_app.py), [`tests/test_ingest_status.py`](../../tests/test_ingest_status.py)
- [ADR-104](ADR-104-weekly-ingest-runs-on-the-vm.md) — 주간 수집은 VM에서
