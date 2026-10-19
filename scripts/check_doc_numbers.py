"""문서에 적힌 수치가 출처 보고서와 아직 맞는지 확인합니다.

    uv run python scripts/check_doc_numbers.py
    uv run python scripts/check_doc_numbers.py --list

2026-10-18에 실제로 틀어졌습니다. [ADR-016](../docs/adr/ADR-016-stay-on-streamlit.md)이 앱
상주 메모리를 **259MB**라고 적고 있었는데 재 보니 **266.5MB**였습니다. Langfuse 비용을 재다가
**우연히** 발견했습니다. 측정값을 문서에 적는 프로젝트에서 그 숫자가 조용히 늙는 것은 링크가
깨지는 것보다 나쁩니다 — 링크는 CI가 잡아 줬고 숫자는 아무도 안 봤습니다.

그래서 숫자에 출처를 붙입니다.

    앱 피크 상주 <!--n:reports/app_memory.json#peak_mb-->266.5<!--/n-->MB

`#` 뒤는 점으로 잇는 경로입니다(`summary.graph_rag.hit_at_k`). 키 자체에 점이 들어 있으면
`/`로 끊습니다(`extra_imports/langfuse.langchain/delta`). 보고서가 갱신됐는데 문서가 그대로면
CI가 막습니다.

**전수 검사가 아닙니다.** 문서 7,400줄의 모든 숫자를 묶지 않습니다 — 묶은 것만 검사하고,
**몇 개를 묶었는지 출력합니다.** 늘려 가는 것이 목적이고, "전부 검증됨"처럼 읽히면 안 됩니다.

반올림은 **문서가 적은 자리수만큼** 허용합니다. 문서에 `266.5`라고 적혀 있으면 소수 첫째
자리까지 맞으면 통과이고, `0.9667`이면 넷째 자리까지 봅니다. 문서가 보고서보다 더 정확할
수는 없습니다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `pipeline`을 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_DOCS = (
    "README.md",
    "ROADMAP.md",
    "advanced.md",
    "EXPERIMENTS.md",
    "APP_README.md",
)
ADR_DIR = Path("docs/adr")
DOCS_DIR = Path("docs")
# <!--n:경로#점으로.잇는.키-->적어 둔 값<!--/n-->
ANCHOR = re.compile(
    r"<!--\s*n:(?P<path>[^#\s]+)#(?P<key>[^\s>]+?)\s*-->(?P<value>[^<]*)<!--\s*/n\s*-->"
)


def lookup(report: Any, key: str) -> Any:
    """Walk a key path, treating digits as list indexes.

    기본 구분자는 점입니다(`summary.graph_rag.hit_at_k`). 그런데 **키 자체에 점이 든**
    경우가 있습니다 — `extra_imports.langfuse.langchain.delta`에서 중간 키가
    `langfuse.langchain`입니다. 그때는 `/`로 끊습니다:
    `extra_imports/langfuse.langchain/delta`.
    """
    value = report
    parts = key.split("/") if "/" in key else key.split(".")
    for part in parts:
        if isinstance(value, list):
            value = value[int(part)]
        else:
            value = value[part]
    return value


def decimals(text: str) -> int:
    """How precise the document claims to be."""
    _, _, fraction = text.strip().replace(",", "").partition(".")
    return len(fraction)


def compare(written: str, actual: Any) -> str | None:
    """Return a complaint, or None when the document still matches the report."""
    cleaned = written.strip().replace(",", "")
    try:
        claimed = float(cleaned)
    except ValueError:
        return f"문서의 값이 숫자가 아닙니다: {written!r}"
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return f"보고서의 값이 숫자가 아닙니다: {actual!r}"
    places = decimals(cleaned)
    if round(float(actual), places) != round(claimed, places):
        # 반올림한 값만 보여 주면 "266"처럼 나와 실제 값을 숨깁니다. 둘을 함께 적습니다.
        return f"문서 {cleaned} ≠ 보고서 {round(float(actual), places)} (실제 {actual})"
    return None


def check(paths: tuple[str, ...]) -> tuple[list[str], int]:
    problems: list[str] = []
    cache: dict[str, Any] = {}
    anchored = 0
    for name in paths:
        doc = Path(name)
        if not doc.exists():
            continue
        text = doc.read_text(encoding="utf-8")
        for match in ANCHOR.finditer(text):
            anchored += 1
            # 경로는 문서 위치가 아니라 **저장소 최상위** 기준입니다. ADR이 `../../`로
            # 적는 링크와 달리, 출처는 한 가지로만 적게 해서 옮겨도 안 깨집니다.
            source = Path(match.group("path"))
            if not source.exists():
                problems.append(f"{name}: 출처가 없습니다 -> {source}")
                continue
            if str(source) not in cache:
                cache[str(source)] = json.loads(source.read_text(encoding="utf-8"))
            try:
                actual = lookup(cache[str(source)], match.group("key"))
            except (KeyError, IndexError, TypeError, ValueError):
                problems.append(f"{name}: 키가 없습니다 -> {source}#{match.group('key')}")
                continue
            complaint = compare(match.group("value"), actual)
            if complaint:
                problems.append(f"{name}: {source}#{match.group('key')} {complaint}")
    return problems, anchored


def main() -> int:
    parser = argparse.ArgumentParser(description="문서의 수치가 출처와 맞는지 확인합니다.")
    parser.add_argument("docs", nargs="*", default=[])
    parser.add_argument("--list", action="store_true", help="묶인 수치를 전부 출력합니다.")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    paths = tuple(args.docs) or (
        *DEFAULT_DOCS,
        *(str(adr) for adr in sorted(ADR_DIR.glob("*.md"))),
        # `docs/` 바로 아래 문서도 봅니다. 목록을 손으로 들고 있으면 새 문서가 조용히
        # 검사 밖에 남습니다 - "들고 올 키 목록"이 세 번 빠뜨린 것과 같은 모양입니다.
        *(str(doc) for doc in sorted(DOCS_DIR.glob("*.md"))),
    )

    if args.list:
        for name in paths:
            doc = Path(name)
            if not doc.exists():
                continue
            for match in ANCHOR.finditer(doc.read_text(encoding="utf-8")):
                print(f"{name}: {match.group('value').strip()} "
                      f"<- {match.group('path')}#{match.group('key')}")
        return 0

    problems, anchored = check(paths)
    for problem in problems:
        print(problem)
    print(f"출처에 묶인 수치 {anchored}개, 문제 {len(problems)}건")
    if anchored == 0:
        print("아직 묶인 수치가 없습니다. 전수 검사가 아니므로 늘려 가야 합니다.")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
