"""검색 모듈이 **옛 코드**인지는 커밋이 아니라 `src/`의 내용으로 가립니다(ADR-072 고침).

처음에는 커밋을 비교했습니다 - 화면이 읽은 HEAD와 `chatbot`이 임포트될 때 읽은 HEAD. 그런데
Streamlit Cloud는 머지 뒤 `app.py`만 다시 돌리고 `src/` 모듈은 **바뀌었든 아니든** 다시
임포트하지 않습니다. 그래서 `src/`를 건드리지 않은 머지(2026-10-26 PR #55)에서도 두 커밋이
달랐고, 배포 점검이 재부팅하라는 이슈(#56)를 열었습니다. 모듈은 옛 커밋에서 왔지만 **코드는
같았습니다.**

`chatbot`이 임포트될 때 `src/*.py`의 지문을 남기고(`chatbot.LOADED_SOURCE`), 화면이 디스크의
지문과 비교합니다. 같으면 커밋이 달라도 옛 코드가 아닙니다.

**이 파일이 `release.py`와 따로인 이유:** 머지 뒤에는 `src.release`도 옛 모듈로 남습니다.
거기에 함수를 더하고 `app.py`가 부르면, 더한 직후의 배포에서 `AttributeError`로 화면이 깨집니다.
새 모듈은 아직 임포트된 적이 없으므로 처음 불릴 때 디스크에서 읽힙니다.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(root: Path = ROOT) -> str | None:
    """`src/*.py`의 이름과 내용으로 만든 지문. 읽을 수 없으면 `None`입니다."""
    sha = hashlib.sha256()
    try:
        for path in sorted((root / "src").glob("*.py")):
            sha.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    except OSError:
        return None
    return sha.hexdigest()[:16]


def same_code(loaded: str | None, root: Path = ROOT) -> bool:
    """임포트할 때의 지문과 지금 디스크의 지문이 같은가. **모르면 같지 않다고 봅니다.**

    지문이 없는 모듈은 이 기능보다 오래된 것이고, 읽지 못한 지문은 확인하지 못한 것입니다.
    둘 다 "같다"로 넘기면 옛 모듈 사고를 놓칩니다 - 커밋 비교의 경고를 그대로 둡니다.
    """
    return loaded is not None and loaded == digest(root)
