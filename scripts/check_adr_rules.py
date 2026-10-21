"""ADR 색인이 선언한 규칙을 ADR 파일에서 기계적으로 확인합니다.

    uv run python scripts/check_adr_rules.py

[`docs/adr/README.md`](../docs/adr/README.md)의 "규칙"이 넷을 적어 두었는데, 그중 둘은
**글로만 있었습니다.** 그래서 지켜지는지 아무도 몰랐고, 실제로 하나는 깨져 있었습니다 —
`## 민감도` 절이 ADR-032 부근부터 사라집니다. 손으로 세어 보고 알았습니다.

**이 저장소에서 같은 병이 여섯 번째입니다.** 손으로 유지하는 목록이 조용히 갈라지는 것
(`DERIVED_KEYS`, 문서 검사기 대상, `compileall` 모듈 목록, ADR-032 경로 목록, 용량 한도).
다섯 번은 검사기를 붙여서 막았고, 이번도 같게 합니다.

## 무엇을 검사하고, 무엇을 검사하지 않는가

**내용의 품질은 검사할 수 없습니다.** "이 가정이 바뀌면 결론이 바뀐다"가 잘 적혔는지는
사람이 읽어야 압니다. 검사할 수 있는 것은 **그 절이 찾을 수 있는 제목으로 있는가**입니다.
그래서 이 검사기가 하는 일은 **제목 어휘의 표준화**이고, 그 이상을 주장하지 않습니다.

제목 어휘를 하나로 묶지 않고 **넷을 허용합니다.** 처음에 `민감도`라는 글자만 세었다가
**틀렸습니다** — ADR-046·049·051은 `## 이 결정이 바뀌는 조건`, ADR-030은
`## 한계 — 가장 큰 가정`, ADR-037은 `## 이 측정이 말하지 않는 것`으로 **같은 일을 합니다.**
글자를 세면 규칙이 아니라 어휘를 재게 됩니다.

**범위를 적는 절은 민감도가 아닙니다.** `넘지 않은 선`·`아직 아닌 것`·`하지 않은 것`은
"무엇을 안 했는가"이고, 민감도는 "무엇이 바뀌면 결론이 뒤집히는가"입니다. 둘을 섞으면
검사기가 통과만 시킵니다.

## 빚 목록이 스스로 낡는 것까지 잡습니다

지난 ADR 몇 개에는 그 절이 없습니다. 20개를 지금 손으로 채우는 것은 **없던 민감도를 뒤늦게
지어내는 일**이라 하지 않습니다(ADR 색인의 "뒤집힌 결정을 지우지 않습니다"와 같은 선입니다).
대신 `WITHOUT_SENSITIVITY`에 **명시적인 빚**으로 적습니다.

그런데 그 목록 자체가 손으로 유지하는 목록입니다 — 이 검사기가 막으려는 바로 그 모양입니다.
그래서 **양방향으로** 검사합니다.

| 상황 | 판정 |
|---|---|
| 목록에 없는 ADR에 절이 없다 | **실패** — 새 ADR이 규칙을 빠뜨렸습니다 |
| 목록에 있는 ADR에 절이 생겼다 | **실패** — 목록에서 지우라고 말합니다 |

두 번째가 핵심입니다. 빚을 갚으면 목록이 **줄어야** 하고, 줄지 않으면 검사기가 막습니다.
목록은 늘지 않고 줄기만 합니다.
"""

from __future__ import annotations

import re
from pathlib import Path

ADR_DIR = Path("docs/adr")
ADR_FILE = re.compile(r"^ADR-(\d{3})-")
HEADING = re.compile(r"^##\s+(.*)$", re.M)
STATUS = re.compile(r"^- \*\*상태:\*\*\s*(\S+)", re.M)

# 민감도 절의 제목으로 인정하는 말. 하나로 묶지 않는 이유는 모듈 docstring에 있습니다 -
# 글자를 세면 규칙이 아니라 어휘를 재게 됩니다.
SENSITIVITY_MARKERS = ("민감도", "바뀌는 조건", "가정", "말하지 않는 것")

# ADR 색인의 "상태" 표에 적힌 네 값입니다. 괄호로 덧붙인 설명은 뒤에 붙으므로 첫 낱말만 봅니다.
STATUSES = ("채택", "기각", "보류", "뒤집힘")

# **민감도 절이 없는 ADR의 빚 목록입니다. 늘지 않고 줄기만 합니다.**
# 절이 생기면 이 검사기가 "목록에서 지우라"고 실패합니다 - 그래서 이 목록은 낡을 수 없습니다.
WITHOUT_SENSITIVITY = frozenset({
    "023", "024", "026",
    "032", "033", "034", "035", "036", "038", "039", "040",
    "041", "042", "043", "044", "045", "047", "048", "050",
})


def adrs() -> list[tuple[str, Path]]:
    found = []
    for path in sorted(ADR_DIR.glob("ADR-*.md")):
        match = ADR_FILE.match(path.name)
        if match:
            found.append((match.group(1), path))
    return found


def has_sensitivity(text: str) -> bool:
    return any(
        marker in heading
        for heading in HEADING.findall(text)
        for marker in SENSITIVITY_MARKERS
    )


def check() -> list[str]:
    problems: list[str] = []
    found = adrs()
    if not found:
        return [f"{ADR_DIR}에서 ADR 파일을 찾지 못했습니다"]

    for number, path in found:
        text = path.read_text(encoding="utf-8")
        present = has_sensitivity(text)
        owed = number in WITHOUT_SENSITIVITY
        if not present and not owed:
            problems.append(
                f"{path.name}: 민감도 절이 없습니다. 제목에 "
                f"{' / '.join(SENSITIVITY_MARKERS)} 중 하나를 쓰세요 "
                "(docs/adr/README.md 규칙)"
            )
        elif present and owed:
            # **빚을 갚았습니다.** 목록에서 지워야 다음 사람이 같은 자리를 다시 셉니다.
            problems.append(
                f"{path.name}: 민감도 절이 생겼습니다. "
                f"scripts/check_adr_rules.py 의 WITHOUT_SENSITIVITY에서 '{number}'를 지우세요"
            )

        if match := STATUS.search(text):
            if match.group(1) not in STATUSES:
                problems.append(
                    f"{path.name}: 상태 '{match.group(1)}'가 색인의 네 값에 없습니다 "
                    f"({' / '.join(STATUSES)})"
                )
        else:
            problems.append(f"{path.name}: '- **상태:**' 줄이 없습니다")

    # 목록에 있는데 파일이 없는 경우. ADR을 지우거나 번호를 바꿨으면 여기서 걸립니다.
    numbers = {number for number, _ in found}
    for ghost in sorted(WITHOUT_SENSITIVITY - numbers):
        problems.append(
            f"ADR-{ghost}: WITHOUT_SENSITIVITY에 있는데 파일이 없습니다. 목록에서 지우세요"
        )
    return problems


def main() -> int:
    problems = check()
    for problem in problems:
        print(problem)
    found = adrs()
    owed = len(WITHOUT_SENSITIVITY)
    print(
        f"ADR {len(found)}개, 민감도 절 {len(found) - owed}개, "
        f"남은 빚 {owed}개, 문제 {len(problems)}건"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
