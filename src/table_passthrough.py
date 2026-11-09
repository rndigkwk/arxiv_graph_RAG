"""번역할 때 마크다운 표의 행을 빼 두었다가 되돌립니다(ADR-111).

답의 표는 논문 제목·저자·arXiv ID·분류 코드라, 번역 지시문(`prompts.TRANSLATE_TO_KOREAN`)이
어차피 영어로 두라고 합니다. 그런데 모델은 그 표를 한 글자씩 다시 써야 해서, 25행 표가 든 답은
번역이 7초까지 걸렸습니다(2026-11-07, `reports/vm_answer_latency_examples.json`). 표의 머리줄과
구분선은 번역에 보내 "Title"이 "제목"이 되게 두고, 그 아래 행만 자리표시로 바꿔 돌아온 번역에
그대로 끼웁니다.

머리줄의 칸이 모두 아래 사전(`HEADERS`)에 있으면 머리줄도 여기서 옮기고 표 전체를 자리표시로
바꿉니다(ADR-117). 모델에 맡기면 같은 "Primary category"가 실행마다 "주 카테고리", "1차 카테고리"가
되거나 영어로 남았습니다. 사전에 없는 칸이 하나라도 있으면 지금처럼 머리줄을 번역에 보냅니다.

자리표시가 번역에서 사라지거나 바뀌면 `restore`가 `None`을 돌려줍니다. 그때는 부르는 쪽이 예전처럼
전체를 번역합니다 - 표를 잃는 것보다 느린 편이 낫습니다.
"""

from __future__ import annotations

import re

MARKER = "[[TABLE_{}]]"
_MARKER = re.compile(r"\[\[TABLE_(\d+)\]\]")
# 모델이 머리줄과 자리표시 사이에 빈 줄을 넣으면 표가 끊깁니다. 되돌릴 때 그 빈 줄을 지웁니다.
# 바로 위가 표 줄일 때만입니다. 표 전체가 자리표시이면(ADR-117) 문단과 표 사이 빈 줄은 남깁니다.
_GAP_BEFORE_MARKER = re.compile(r"(?m)(^[ \t]*\|[^\n]*)\n(?:[ \t]*\n)+(?=\[\[TABLE_\d+\]\])")
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


# 답 쓰는 에이전트가 표에 쓰는 머리줄입니다(`prompts.AGENT_SYSTEM`의 "arXiv ID | Title | Primary
# category"와, 평가·측정 보고서에 나온 칸). 키는 소문자입니다. 뜻이 질문마다 다른 칸("Relationship",
# "Focus", "Why read it")은 넣지 않습니다 - 그런 표는 모델이 옮깁니다.
HEADERS = {
    "arxiv id": "arXiv ID",
    "title": "제목",
    "primary category": "주 분류",
    "category": "분류",
    "author": "저자",
    "authors": "저자",
    "shared author": "겹치는 저자",
    "shared author(s)": "겹치는 저자",
    "shared authors": "겹치는 저자",
    "citations": "인용 수",
    "citation count": "인용 수",
    "published": "발행일",
    "pdf": "PDF",
}


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _known_header(line: str) -> str | None:
    """머리줄의 칸이 모두 사전에 있으면 옮긴 머리줄, 하나라도 없으면 None."""
    cells = _cells(line)
    if not all(cell.lower() in HEADERS for cell in cells):
        return None
    return "| " + " | ".join(HEADERS[cell.lower()] for cell in cells) + " |"


def _is_table_line(line: str) -> bool:
    return line.lstrip().startswith("|")


def _head_size(block: list[str]) -> int:
    """번역에 보낼 앞줄 수 - 머리줄, 다음 줄이 구분선이면 그것까지."""
    return 2 if len(block) > 1 and _SEPARATOR.match(block[1]) else 1


def _has_sentences(rows: list[str]) -> bool:
    """설명 문장이 든 칸이 있는가 - 마침표로 끝나고 네 단어 이상인 칸.

    제목·ID·분류·숫자 칸은 마침표로 끝나지 않습니다. "GNN을 어떻게 쓰는가" 같은 설명 열은
    읽는 사람에게 옮겨야 하므로 그런 표는 통째로 번역에 보냅니다(예전과 같은 길).
    """
    for row in rows:
        for cell in row.strip().strip("|").split("|"):
            text = cell.strip()
            if text.endswith(".") and len(text.split()) >= 4:
                return True
    return False


def split_tables(text: str) -> tuple[str, list[str]]:
    """연속한 표 줄(`|`로 시작)에서 머리줄 아래 행을 빼고, 그 자리에 `[[TABLE_n]]` 한 줄을 둡니다.

    행이 없는 표(머리줄뿐)와 설명 문장이 든 표는 그대로 번역에 보냅니다.
    """
    lines = text.split("\n")
    out: list[str] = []
    tables: list[str] = []
    block: list[str] = []
    for line in lines + [""]:
        if _is_table_line(line):
            block.append(line)
            continue
        if block:
            head = _head_size(block)
            if _has_sentences(block[head:]):
                head = len(block)
            elif head == 2 and len(block) > 2 and (korean := _known_header(block[0])):
                # 머리줄을 사전으로 옮기면 번역에 보낼 줄이 없습니다. 표 전체가 자리표시입니다.
                block, head = [korean, *block[1:]], 0
            out.extend(block[:head])
            if block[head:]:
                tables.append("\n".join(block[head:]))
                out.append(MARKER.format(len(tables)))
            block = []
        out.append(line)
    return "\n".join(out[:-1]), tables


def has_prose(text: str) -> bool:
    """자리표시와 표 머리줄을 빼고도 옮길 글이 남는가. 표뿐인 답은 모델을 부르지 않습니다."""
    rest = [line for line in _MARKER.sub("", text).split("\n") if not _is_table_line(line)]
    return bool("".join(rest).strip())


def restore(translated: str, tables: list[str]) -> str | None:
    """자리표시를 표의 행으로 되돌립니다. 자리표시가 하나라도 빠지거나 늘면 `None`입니다."""
    found = [int(n) for n in _MARKER.findall(translated)]
    if sorted(found) != list(range(1, len(tables) + 1)):
        return None
    joined = _GAP_BEFORE_MARKER.sub(r"\1\n", translated)
    return _MARKER.sub(lambda m: tables[int(m.group(1)) - 1], joined)


# 흘려 받는 중에 끝에 걸린 반쪽 자리표시("[[TAB", "[[TABLE_1]")입니다.
# 화면에 비치지 않게 잘라 둡니다.
_PARTIAL_TAIL = re.compile(r"\[(?:\[(?:T(?:A(?:B(?:L(?:E(?:_\d*\]?)?)?)?)?)?)?)?$")


def restore_partial(translated: str, tables: list[str]) -> str:
    """흘려 받는 중인 번역에 이미 도착한 자리표시만 표로 바꿉니다(ADR-114).

    끝에 걸린 반쪽 자리표시는 지웁니다. 번호가 표 수를 넘는 자리표시는 그대로 둡니다 - 끝나고
    `restore`가 `None`을 돌려주면 부르는 쪽이 전체를 다시 번역합니다.
    """
    text = _PARTIAL_TAIL.sub("", _GAP_BEFORE_MARKER.sub(r"\1\n", translated))

    def table(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        return tables[index] if 0 <= index < len(tables) else match.group(0)

    return _MARKER.sub(table, text)
