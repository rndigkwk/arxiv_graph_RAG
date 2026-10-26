"""배포된 코드가 어느 커밋인지 읽습니다. 화면과 배포 점검(`scripts/check_deployed_app.py`)이 씁니다.

Streamlit Cloud가 **옛 모듈을 돌린 일이 두 번** 있었습니다. `app.py`는 새로 실행됐는데 이미
임포트된 `chatbot`이 그대로 남았고, 재부팅해야 고쳐졌습니다. 그런데 화면에 배포 여부를 확인할
표시가 없어서 매번 소거법으로 갈랐습니다.

**커밋을 두 번 읽습니다.** `app.py`가 그릴 때마다 디스크에서 한 번(`commit()`), 그리고
`chatbot`이 임포트될 때 한 번(`chatbot.LOADED_COMMIT`). 둘이 다르면 화면은 새 코드인데 검색
모듈은 옛 코드라는 뜻입니다. 디스크만 읽으면 이 경우를 못 잡습니다 - 파일은 이미 새것입니다.
dograg의 같은 점검이 그 차이를 다루지 않는 이유는 그쪽 사고가 `ImportError`로 드러났기 때문입니다.

하위 프로세스를 띄우지 않고 `.git`을 직접 읽습니다. 배포 환경에 `git`이 있다는 보장이 없습니다.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHORT = 7
# 검색 모듈에 `LOADED_COMMIT`이 아예 없을 때입니다. 이 표시보다 **오래된** 모듈이라는 뜻이므로
# 그 자체로 옛 코드입니다 - 없다고 조용히 넘기면 이 기능의 첫 배포에서 바로 놓칩니다.
OLDER = "이전 코드"


def commit(root: Path = ROOT) -> str | None:
    """체크아웃된 커밋의 앞 일곱 글자. `.git`이 없거나 읽을 수 없으면 `None`입니다."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref: "):
            return head[:SHORT] or None  # 분리된 HEAD는 커밋 자체입니다
        ref = head[5:]
        loose = git / ref
        if loose.exists():
            return loose.read_text(encoding="utf-8").strip()[:SHORT] or None
        for line in (git / "packed-refs").read_text(encoding="utf-8").splitlines():
            if line.endswith(" " + ref):
                return line.split(" ", 1)[0][:SHORT]
    except OSError:
        pass
    return None


def stamp(app_commit: str | None, module_commit: str | None) -> tuple[str, str | None]:
    """화면에 붙일 배포 표시와, 둘이 어긋났을 때의 경고.

    표시의 모양은 배포 점검이 읽으므로 바꾸면 `check_deployed_app.parse_stamp`도 바꿔야
    합니다 - `tests/test_deployed_app.py`가 둘을 맞물려 시험합니다.
    """
    caption = f"배포 {app_commit or '확인 불가'}"
    if app_commit and module_commit and app_commit != module_commit:  # OLDER 포함
        return caption, (
            f"검색 모듈이 옛 커밋입니다: 화면 {app_commit}, 검색 모듈 {module_commit}. "
            "Streamlit Cloud에서 앱을 재부팅해야 새 코드가 돕니다."
        )
    return caption, None
