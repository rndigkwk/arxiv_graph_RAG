"""저장 목록을 BibTeX와 CSV로 내보냅니다. Streamlit을 가져오지 않습니다.

앱에 논문을 20편까지 저장할 수 있는데 **꺼낼 방법이 없었습니다** — 저장소 전체에
`st.download_button`이 0개였습니다. 논문 도구에서 그것은 기능이 하나 모자란 것이 아니라
목록이 세션과 함께 사라지는 것입니다.

**여기서 새 사실을 만들지 않습니다.** 저장된 dict에 있는 것만 적고, 없는 칸은 비워 두고
**몇 건이 비었는지 셉니다**(`missing`). 서지 항목에서 추측은 조용한 오류가 됩니다 — 연도를
오늘로 채우거나 저자를 "Unknown"으로 적으면 그 파일을 쓰는 사람이 틀린 인용을 하게 됩니다.

화면 코드와 나눠 둬 **접속도 Streamlit도 없이 테스트**합니다(`quality_board.py`와 같은 선).
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

# CSV 열 순서입니다. 저장 목록 화면이 보여 주는 것과 같은 순서로 두면 대조하기 쉽습니다.
CSV_COLUMNS = (
    "arxiv_id",
    "title",
    "authors",
    "published",
    "primary_category",
    "categories",
    "pdf_url",
    "abstract",
)

# BibTeX 키에 쓸 수 없는 글자입니다. arXiv ID의 점과 슬래시가 여기 걸립니다
# (`2301.12345` -> `2301_12345`, 옛 형식 `cs/0701001` -> `cs_0701001`).
_KEY_UNSAFE = re.compile(r"[^A-Za-z0-9]+")

# BibTeX에서 뜻을 갖는 글자입니다. 제목의 `&`나 `%`를 그대로 두면 파일이 깨집니다.
_ESCAPES = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def escape(text: str) -> str:
    r"""Make a value safe to put inside a BibTeX field.

    역슬래시를 먼저 바꾸면 뒤에 넣은 `\&`가 다시 escape되므로, 한 글자씩 한 번만 봅니다.
    """
    return "".join(_ESCAPES.get(char, char) for char in text)


def bibtex_key(paper: dict[str, Any]) -> str:
    """`arxiv:2301.12345` -> `arxiv_2301_12345`. ID가 없으면 빈 문자열입니다."""
    paper_id = str(paper.get("arxiv_id") or "").strip()
    if not paper_id:
        return ""
    return "arxiv_" + _KEY_UNSAFE.sub("_", paper_id).strip("_")


def author_list(paper: dict[str, Any]) -> list[str]:
    """Author names as a list, from either a list or a semicolon/comma string."""
    authors = paper.get("authors")
    if isinstance(authors, str):
        parts = re.split(r"[;,]", authors)
    elif isinstance(authors, (list, tuple)):
        parts = [str(name) for name in authors]
    else:
        return []
    return [name.strip() for name in parts if str(name).strip()]


def year_of(paper: dict[str, Any]) -> str:
    """The four digits at the front of `published`, or "" when it is not there.

    `toString(p.published)`는 `2023-01-15T00:00:00Z` 모양입니다. 앞 네 자리만 씁니다 —
    없는 값을 오늘 연도로 채우면 **틀린 인용**이 되고, 그 틀림은 조용합니다.
    """
    published = str(paper.get("published") or "")
    return published[:4] if re.fullmatch(r"\d{4}", published[:4] or "") else ""


def entry(paper: dict[str, Any]) -> str:
    """One `@misc` entry. Fields we do not have are left out, never guessed.

    arXiv 논문의 표준 모양입니다 — `eprint`·`archivePrefix`·`primaryClass`가 있으면 서지
    도구가 arXiv 항목으로 알아봅니다.
    """
    key = bibtex_key(paper)
    fields: list[tuple[str, str]] = []
    if authors := author_list(paper):
        fields.append(("author", escape(" and ".join(authors))))
    if title := str(paper.get("title") or "").strip():
        fields.append(("title", escape(title)))
    if year := year_of(paper):
        fields.append(("year", year))
    paper_id = str(paper.get("arxiv_id") or "").strip()
    if paper_id:
        fields.append(("eprint", escape(paper_id)))
        fields.append(("archivePrefix", "arXiv"))
    if primary := str(paper.get("primary_category") or "").strip():
        fields.append(("primaryClass", escape(primary)))
    if url := str(paper.get("pdf_url") or "").strip():
        fields.append(("url", escape(url)))
    width = max((len(name) for name, _ in fields), default=0)
    body = ",\n".join(f"  {name.ljust(width)} = {{{value}}}" for name, value in fields)
    return f"@misc{{{key},\n{body}\n}}"


def missing(papers: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Which entries lack a field a bibliography needs.

    **세어서 화면에 적습니다.** 비어 있다는 사실이 보이지 않으면, 쓰는 사람은 파일이 완전한
    줄 알고 그대로 인용합니다.
    """
    gaps: dict[str, list[str]] = {"author": [], "year": []}
    for paper in papers:
        paper_id = str(paper.get("arxiv_id") or "").strip()
        if not paper_id:
            continue
        if not author_list(paper):
            gaps["author"].append(paper_id)
        if not year_of(paper):
            gaps["year"].append(paper_id)
    return {name: ids for name, ids in gaps.items() if ids}


def to_bibtex(papers: list[dict[str, Any]], *, note: str = "") -> str:
    """The whole list as one `.bib` file, with what is missing stated at the top.

    저자 이름은 그래프의 `ArxivAuthorName` 표기이고 **동명이인을 구별하지 않습니다**. 그
    사실을 파일 안에 적습니다 — 파일은 앱 밖으로 나가므로 화면의 설명이 따라가지 않습니다.
    """
    usable = [paper for paper in papers if bibtex_key(paper)]
    head = ["% arxiv_graph_RAG 저장 목록", f"% 논문 {len(usable)}편"]
    if note:
        head.append(f"% {note}")
    head.append("% 저자 표기는 동명이인을 구별하지 않습니다(ArxivAuthorName).")
    for name, ids in missing(usable).items():
        head.append(f"% {name} 칸이 빈 항목 {len(ids)}건: {', '.join(ids)}")
    if skipped := len(papers) - len(usable):
        head.append(f"% arXiv ID가 없어 뺀 항목 {skipped}건 (키를 만들 수 없습니다)")
    return "\n".join(head) + "\n\n" + "\n\n".join(entry(paper) for paper in usable) + "\n"


def to_csv(papers: list[dict[str, Any]]) -> str:
    """The same list as CSV, one row per paper.

    **BibTeX와 달리 한 줄도 빼지 않습니다.** `.bib`은 키가 필요해서 arXiv ID가 없는 항목을
    낼 수 없지만, CSV는 저장된 것을 그대로 옮기는 자리입니다.

    `utf-8-sig`로 내려받게 하면 Excel이 한글 제목을 깨뜨리지 않습니다 — 그 결정은 화면
    쪽에 있습니다(`app.py`). 여기서는 문자열만 만듭니다.
    """
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for paper in papers:
        row = {column: paper.get(column) for column in CSV_COLUMNS}
        row["authors"] = "; ".join(author_list(paper))
        categories = paper.get("categories")
        if isinstance(categories, (list, tuple)):
            row["categories"] = "; ".join(str(item) for item in categories)
        writer.writerow({key: "" if value is None else value for key, value in row.items()})
    return buffer.getvalue()
