"""콘솔 인코딩 때문에 결과를 못 보는 일을 막습니다.

이 PC의 기본 콘솔 코드페이지는 cp949입니다. 그래서 em dash나 이모지가 섞인 한 줄이
`UnicodeEncodeError`로 **명령 전체를 실패시킵니다.** 오늘 두 번 걸렸습니다.

- `evals.gate`가 요약 한 줄에 em dash를 넣어 판정 출력이 죽었습니다.
- `pipeline.register_run`은 MLflow가 run URL을 이모지와 함께 직접 `sys.stdout`에 쓰는데,
  그것이 `end_run` 안에서 터져서 **등록은 됐지만 run이 종료 처리되지 않았습니다.**

출력 글자 때문에 작업이 실패하는 것은 작업의 실패가 아닙니다. 쓸 수 없는 글자는 바꿔 쓰고
진행합니다. 환경변수(`PYTHONIOENCODING`)에 의존하면 사람이 매번 붙여야 합니다.
"""

from __future__ import annotations

import sys


def use_utf8_stdout() -> None:
    """Make stdout/stderr tolerate characters the console codepage cannot encode."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 파이프로 감싸인 스트림이면 건드리지 않습니다.
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
