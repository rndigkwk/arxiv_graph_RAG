"""운영 로그가 **어디로 가고 무엇을 담는지** 한 곳에서 정합니다.

로거는 두 개입니다 — `arxiv_graph_rag.validation`(답변 검증 실패의 사유 코드)과
`arxiv_graph_rag.graph`(그래프 읽기 재시도). 그런데 **어디에도 핸들러가 없었습니다.**

## 버려지고 있던 것은 아닙니다 — 시각이 없었습니다

핸들러가 없으면 파이썬의 `logging.lastResort`가 WARNING 이상을 stderr로 보냅니다. 그래서
줄은 보였고, Streamlit Cloud의 앱 로그에도 남았습니다. **없던 것은 형식입니다.**

| 없던 것 | 그래서 못 하던 것 |
|---|---|
| 시각 | **"언제, 얼마나 자주"를 셀 수 없습니다** |
| 로거 이름 | 검증 실패와 그래프 재시도가 섞입니다 |
| 수준 손잡이 | 조용히 하거나 더 보게 할 방법이 없습니다 |

## 왜 지금인가 — [ADR-052](../docs/adr/ADR-052-the-sixty-seconds-is-not-for-the-steady-state.md)

그 ADR이 `connection_acquisition_timeout` 하한을 **42초**로 정했는데, 근거가 2026-10-15의
**한 번**("90초 뒤 자체 회복")입니다. 분포 위에 세우려면 저하가 얼마나 오래 가는지를 모아야
하고, 그러려면 **시각이 찍힌 줄**이 필요합니다.

**그리고 예외 유형으로는 모드가 구별되지 않습니다.** 패킷이 버려지는 경우와 포트가 거부하는
경우가 **둘 다 `ServiceUnavailable`** 입니다(ADR-052에서 측정). 구별하는 것은 **경과
시간**입니다 — 블랙홀은 타임아웃을 꽉 채우고(≈60초) 거부는 ≈2초입니다. 그래서
`chatbot.read_with_retry`가 시도마다 `seconds=`를 함께 적습니다. 핸들러만으로는 부족합니다.

## 담지 않는 것 — 기존 선과 같습니다

질문 문장, 답변 본문, Cypher, 조회한 행을 **남기지 않습니다** — 추적에 대해
[ADR-023](../docs/adr/ADR-023-what-to-trace.md)이 정한 결정 3번과 같은 선입니다. 두 호출
지점이 넘기는 것은 요청 id·시도 번호·단계·사유 코드·초뿐이고, 테스트가 그 둘에 데이터가
섞이지 않는지 확인합니다.

## 설계

- **`arxiv_graph_rag`에만 붙입니다.** 루트 로거를 건드리면 Streamlit과 neo4j 드라이버의 줄까지
  형식이 바뀌고, 그건 우리가 정할 일이 아닙니다.
- **`propagate = False`.** Streamlit이 루트 로거를 설정하므로 올려보내면 **같은 줄이 두 번**
  찍힙니다.
- **두 번 불러도 핸들러가 늘지 않습니다.** Streamlit은 상호작용마다 스크립트를 다시 돌리므로
  이것이 필수입니다 - 안 막으면 재실행 수만큼 줄이 복제됩니다.
- **시각은 UTC입니다.** 두 기계의 줄을 나란히 놓고 읽어야 하고, 사고 기록(Aura 콘솔)도
  UTC입니다.
- **오타는 "전부 보기"가 아니라 기본값으로 떨어집니다.** `ARXIV_LOG_LEVEL=WARINNG`가
  DEBUG로 읽혀 운영 로그를 데이터로 채우면 안 됩니다 - `LANGFUSE_MAX_TRACES`에서 정한 것과
  같은 방향입니다(ADR-023).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import IO, Any

LOGGER_NAME = "arxiv_graph_rag"
LEVEL_KEY = "ARXIV_LOG_LEVEL"
FILE_KEY = "ARXIV_LOG_FILE"
DEFAULT_LEVEL = "WARNING"
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
# 시각·수준·로거 이름, 그리고 메시지. 메시지는 호출 지점이 `키=값`으로 적습니다.
FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"
# 파일로 남길 때의 상한. 한 파일 1MB, 세 개까지 - 디스크를 채우는 로그는 로그가 아닙니다.
FILE_BYTES = 1_000_000
FILE_BACKUPS = 3
# 우리가 붙인 핸들러를 알아보는 표식. 두 번 불러도 늘지 않게 하는 데 씁니다.
MARK = "_arxiv_graph_rag_handler"


def level(environ: dict[str, str] | None = None) -> int:
    """`ARXIV_LOG_LEVEL`. 모르는 값은 **기본값**으로 떨어집니다 - DEBUG로 가지 않습니다."""
    env = environ if environ is not None else os.environ
    raw = (env.get(LEVEL_KEY) or "").strip().upper()
    name = raw if raw in LEVELS else DEFAULT_LEVEL
    return getattr(logging, name)


def ours(logger: logging.Logger) -> list[logging.Handler]:
    """**우리가 붙인** 핸들러만. 표식으로 가립니다.

    pytest는 `propagate=False`인 로거에 자기 포착 핸들러를 직접 붙이고, 호스트도 무엇을
    붙일 수 있습니다. "몇 개 붙였나"를 전체 개수로 세면 그 환경에서 틀립니다.
    """
    return [handler for handler in logger.handlers if getattr(handler, MARK, False)]


def formatter() -> logging.Formatter:
    shaped = logging.Formatter(FORMAT, datefmt=DATE_FORMAT)
    # UTC. 두 기계의 줄을 나란히 읽어야 하고 Aura 콘솔도 UTC입니다.
    shaped.converter = time.gmtime
    return shaped


def configure(
    environ: dict[str, str] | None = None, *, stream: "IO[str] | None" = None
) -> dict[str, Any]:
    """핸들러를 붙이고 **무엇을 했는지 돌려줍니다.**

    돌려주는 까닭은 둘입니다 - 테스트가 확인할 수 있어야 하고, 앱이 "로그가 어디로 가는지"를
    화면에 적을 수 있어야 합니다. 조용히 설정하고 아무 말도 하지 않으면 안 붙은 것과
    구별되지 않습니다.
    """
    env = environ if environ is not None else os.environ
    logger = logging.getLogger(LOGGER_NAME)
    chosen = level(env)
    logger.setLevel(chosen)
    # Streamlit이 루트를 설정합니다. 올려보내면 같은 줄이 두 번 찍힙니다.
    logger.propagate = False

    # **우리가 붙인 것만 셉니다.** 다른 것이 붙어 있을 수 있고 그건 우리 일이 아닙니다 -
    # 예를 들어 pytest는 `propagate=False`인 로거를 보면 자기 포착 핸들러를 **직접**
    # 붙입니다(`LogCaptureHandler` 등). 전체 개수로 "이미 설정됐나"를 판단하면 그 환경에서
    # 거짓이 됩니다.
    existing = ours(logger)
    path_raw = (env.get(FILE_KEY) or "").strip()
    result: dict[str, Any] = {
        "logger": LOGGER_NAME,
        "level": logging.getLevelName(chosen),
        "stream": True,
        "file": None,
        "file_error": None,
        "already_configured": bool(existing),
        "handlers": len(existing),
    }
    if existing:
        # **두 번째 호출은 아무것도 더하지 않습니다.** Streamlit은 상호작용마다 스크립트를
        # 다시 돌리므로, 막지 않으면 재실행 수만큼 줄이 복제됩니다.
        return result

    shaped = formatter()
    # `stream`은 테스트가 줄을 받아 보기 위한 것입니다. 생략하면 stderr입니다.
    console = logging.StreamHandler(stream)
    console.setFormatter(shaped)
    setattr(console, MARK, True)
    logger.addHandler(console)

    if path_raw:
        try:
            from logging.handlers import RotatingFileHandler

            path = Path(path_raw)
            path.parent.mkdir(parents=True, exist_ok=True)
            rotating = RotatingFileHandler(
                path, maxBytes=FILE_BYTES, backupCount=FILE_BACKUPS, encoding="utf-8"
            )
            rotating.setFormatter(shaped)
            setattr(rotating, MARK, True)
            logger.addHandler(rotating)
            result["file"] = str(path)
        except OSError as exc:
            # **로그를 못 쓰는 것이 앱을 멈출 이유는 아닙니다.** 화면 쪽은 그대로 살아 있고,
            # 실패를 조용히 묻지 않고 돌려줍니다.
            result["file_error"] = f"{type(exc).__name__}: {exc}"[:200]

    result["handlers"] = len(ours(logger))
    return result


def describe(state: dict[str, Any]) -> str:
    """`configure()`가 돌려준 것을 한 줄로. 앱 화면과 CLI가 같은 문장을 씁니다."""
    where = ["stderr"]
    if state.get("file"):
        where.append(state["file"])
    line = f"로그 {state['level']} -> {' + '.join(where)}"
    if state.get("file_error"):
        line += f" (파일 실패: {state['file_error']})"
    return line
