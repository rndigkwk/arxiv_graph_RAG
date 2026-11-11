"""VM 이미지 안에서 앱이 쓰는 외부 패키지를 모두 불러 봅니다(ADR-128).

`.github/workflows/vm-image.yml`이 부릅니다. `src.chatbot`은 불러지는 순간 Neo4j에 접속하므로
CI에서 직접 불러 볼 수 없습니다. 대신 `src/`의 모든 모듈이 **모듈 맨 위에서** 가져오는 외부
패키지를 AST로 모아 하나씩 불러 보고, 접속하지 않는 `src.api`는 직접 불러 봅니다. 목록을 손으로
적지 않으므로 새 의존성을 쓰는 모듈이 생기면 저절로 들어옵니다.

함수 안의 import는 보지 않습니다. `src/gds_pagerank.py`의 `graphdatascience`처럼 앱이 쓰지 않는
선택 도구를 그렇게 불러오기 때문입니다.

    python deploy/vm/check_image_imports.py   # 저장소 맨 위에서
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

OURS = {"src", "pipeline", "models", "evals", "scripts"}


def third_party(root: Path = Path("src")) -> list[str]:
    names: set[str] = set()
    for path in sorted(root.glob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Import):
                names |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return sorted(n for n in names if n not in sys.stdlib_module_names and n not in OURS)


def main() -> int:
    sys.path.insert(0, str(Path.cwd()))
    failed = []
    for name in [*third_party(), "uvicorn", "src.api"]:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - 무엇이 왜 안 되는지 전부 보여 줍니다
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
    for line in failed:
        print(line)
    print(f"불러 본 것 {len(third_party()) + 2}개, 실패 {len(failed)}개")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
