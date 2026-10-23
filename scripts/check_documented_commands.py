"""문서에 적힌 명령이 **실제로 파싱되는지** 확인합니다.

    uv run python scripts/check_documented_commands.py
    uv run python scripts/check_documented_commands.py --list

2026-10-24에 같은 함정에 **두 번** 걸렸습니다. `pipeline/ingest.py`의 docstring이 `--dry-run`
플래그를 쓰라고 안내하는데 그 플래그는 **없습니다** — 같은 docstring의 아래쪽이 "기본값이
그 동작입니다"로 맞게 적고 있으니, 위의 사용 예만 낡은 것입니다. 그리고 설계안에 아직 만들지
않은 `--rewind-to`를 적어 두었더니 그것을 그대로 치고 `unrecognized arguments`를 받았습니다.

**이 docstring은 그 명령을 그대로 적지 않습니다.** 적으면 이 검사기가 자기 자신을 잡습니다 —
자기 파일을 예외로 두는 쪽이 더 나쁩니다(그러면 이 파일의 진짜 문제를 숨깁니다).

링크가 깨지면 `check_doc_links.py`가 잡고, 수치가 늙으면 `check_doc_numbers.py`가 잡습니다.
**명령은 아무도 보지 않았습니다** — 문서 246줄에 모듈 60개가 걸려 있습니다.

## 임포트하지 않습니다

파서를 **AST로** 읽습니다. 모듈을 불러오면 `.env`가 필요한 것들이 있어(ADR-023의 키,
`chatbot`의 Neo4j) CI에서 못 돕니다. `add_argument("--x", ...)`의 문자열 리터럴만 모읍니다.

## 전수 검사가 아닙니다

파서를 찾지 못한 모듈은 **건너뛰고 그 수를 출력합니다**(`check_doc_numbers.py`와 같은 선).
"전부 검증됨"처럼 읽히면 안 됩니다 — `argparse`를 쓰지 않거나 플래그를 변수로 만드는 모듈이
있습니다.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 명령이 적히는 곳입니다. 마크다운과 **모듈 docstring** 둘 다 봅니다 - 오늘 틀린 쪽이
# 후자였습니다.
DOC_GLOBS = ("*.md", "docs/**/*.md")
CODE_DIRS = ("pipeline", "evals", "scripts", "models")
# argparse가 언제나 받는 것.
ALWAYS = frozenset({"--help"})
# 우리 모듈이 아닌 것. 플래그를 검사할 파서가 없습니다.
FOREIGN = frozenset({"pytest", "compileall", "mlflow", "streamlit", "ruff", "pip"})


def option_strings(path: Path) -> set[str] | None:
    """`add_argument`의 문자열 리터럴을 모읍니다. 파서가 없으면 `None`입니다."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return None
    found: set[str] = set()
    seen_parser = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "attr", None)
        if name == "ArgumentParser" or getattr(node.func, "id", None) == "ArgumentParser":
            seen_parser = True
        if name != "add_argument":
            continue
        seen_parser = True
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith("-"):
                    found.add(arg.value)
    return found | set(ALWAYS) if seen_parser else None


def target_path(target: str) -> Path | None:
    """`evals.judge_study` 또는 `scripts/foo.py`를 파일 경로로 바꿉니다."""
    if target.endswith(".py"):
        path = Path(target)
        return path if path.exists() else None
    path = Path(target.replace(".", "/") + ".py")
    return path if path.exists() else None


def commands(text: str) -> list[str]:
    """`uv run ... python ...` 줄을 모읍니다. **줄바꿈 백슬래시를 먼저 잇습니다.**

    잇지 않으면 여러 줄 명령의 둘째 줄부터를 못 봅니다 - 그리고 그 줄들이야말로 플래그가
    사는 곳입니다.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", text)
    out = []
    for line in joined.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if not stripped.startswith("uv run") or " python" not in stripped:
            continue
        out.append(stripped)
    return out


def flags_after_python(command: str) -> tuple[str, list[str]] | None:
    """`python` 뒤의 대상과 그 뒤의 플래그만 돌려줍니다.

    `uv run --group dev python ...`의 `--group`은 **uv의 것**입니다. `python` 앞의 플래그를
    섞으면 모든 명령이 틀린 것으로 보입니다.
    """
    match = re.search(r"\bpython\s+(-m\s+)?([\w./]+)(.*)$", command)
    if not match:
        return None
    target, rest = match.group(2), match.group(3)
    if target in FOREIGN or target.startswith("-"):
        return None
    flags = [
        token.split("=", 1)[0]
        for token in rest.split()
        if token.startswith("--")
    ]
    return target, flags


def check(docs: list[Path], code: list[Path]) -> tuple[list[str], int, int, set[str]]:
    problems: list[str] = []
    checked = 0
    unchecked: set[str] = set()
    cache: dict[str, set[str] | None] = {}
    for path in docs + code:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for command in commands(text):
            parsed = flags_after_python(command)
            if parsed is None:
                continue
            target, flags = parsed
            if target not in cache:
                resolved = target_path(target)
                cache[target] = option_strings(resolved) if resolved else None
            allowed = cache[target]
            if allowed is None:
                unchecked.add(target)
                continue
            checked += 1
            for flag in flags:
                if flag not in allowed:
                    problems.append(
                        f"{path.as_posix()}: {target}에 {flag}가 없습니다 "
                        f"({command[:90]})"
                    )
    return problems, checked, len(docs) + len(code), unchecked


def main() -> int:
    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="문서에 적힌 명령의 플래그가 실제로 있는지 확인합니다."
    )
    parser.add_argument("--list", action="store_true",
                        help="검사한 명령을 전부 출력합니다.")
    args = parser.parse_args()

    docs = sorted({path for pattern in DOC_GLOBS for path in Path().glob(pattern)})
    code = sorted(
        path for folder in CODE_DIRS for path in Path(folder).rglob("*.py")
    )
    if args.list:
        for path in docs + code:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for command in commands(text):
                print(f"{path.as_posix()}: {command[:110]}")
        return 0

    problems, checked, files, unchecked = check(docs, code)
    for problem in problems:
        print(problem)
    print(
        f"검사한 명령 {checked}개, 파일 {files}개, 파서를 못 찾아 건너뛴 대상 "
        f"{len(unchecked)}개, 문제 {len(problems)}건"
    )
    if unchecked:
        print(f"  건너뛴 대상: {', '.join(sorted(unchecked))}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
