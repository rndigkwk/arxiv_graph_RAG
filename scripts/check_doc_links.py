"""문서의 상대 링크와 앵커가 실제로 해결되는지 확인합니다.

    uv run python scripts/check_doc_links.py
    uv run python scripts/check_doc_links.py README.md ROADMAP.md

앵커 규칙을 직접 구현해 두는 이유가 있습니다. 손으로 확인하면서 두 번 틀렸습니다 — 처음에는
구두점을 지우고 공백을 하나로 묶어서 `' — '`가 들어간 제목을 못 맞혔고, 고치려고 구두점을
공백으로 바꾸자 이번에는 `'1. '`의 마침표가 하이픈 두 개가 됐습니다.

GitHub 규칙은 둘을 분리합니다.

1. 소문자로 바꾸고 **구두점을 삭제**합니다(공백으로 바꾸지 않습니다).
2. 남은 **공백 문자 하나하나를 하이픈 하나**로 바꿉니다(여러 개를 하나로 묶지 않습니다).

그래서 `'1. A와'`는 `'1-a와'`가 되고, `'가능한가 — 기각'`은 `'가능한가--기각'`이 됩니다.
em dash가 지워진 자리의 공백 두 개가 하이픈 두 개로 남기 때문입니다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_DOCS = ("README.md", "ROADMAP.md", "advanced.md", "EXPERIMENTS.md", "APP_README.md")
LINK = re.compile(r"\[(?:[^\]]*)\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.*)$", re.M)
PUNCTUATION = re.compile(r"[^\w\s가-힣-]")


def slug(heading: str) -> str:
    """GitHub's heading anchor: delete punctuation, then one hyphen per whitespace character."""
    text = re.sub(r"[`*~]", "", heading).strip().lower()
    return re.sub(r"\s", "-", PUNCTUATION.sub("", text))


def anchors(markdown: str) -> set[str]:
    return {slug(heading) for heading in HEADING.findall(markdown)}


def check(paths: tuple[str, ...]) -> list[str]:
    documents = {
        path: Path(path).read_text(encoding="utf-8")
        for path in paths
        if Path(path).exists()
    }
    problems = []
    for name, text in documents.items():
        for target in LINK.findall(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, fragment = target.partition("#")
            if path and not Path(path).exists():
                problems.append(f"{name}: 파일 없음 -> {target}")
                continue
            host = documents.get(path) if path else text
            if path and host is None:
                continue  # 검사 대상 밖의 파일이면 앵커는 확인하지 않습니다.
            if fragment and fragment not in anchors(host):
                problems.append(f"{name}: 앵커 없음 -> {target}")
    return problems


def main() -> int:
    paths = tuple(sys.argv[1:]) or DEFAULT_DOCS
    problems = check(paths)
    for problem in problems:
        print(problem)
    print(f"검사한 문서 {len([p for p in paths if Path(p).exists()])}개, 문제 {len(problems)}건")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
