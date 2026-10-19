"""내보내기가 **없는 값을 만들지 않는지** 검증합니다.

서지 파일에서 추측은 조용한 오류입니다. 연도를 오늘로 채우거나 저자를 "Unknown"으로 적으면
그 파일을 쓰는 사람이 틀린 인용을 하고, 틀렸다는 사실이 아무 데도 남지 않습니다. 그래서
고정할 것이 넷입니다.

1. **없는 칸은 빠집니다.** 채우지도, `None`이라고 적지도 않습니다.
2. **빈 칸을 셉니다.** 비어 있다는 사실이 파일과 화면 양쪽에 남습니다.
3. **BibTeX가 깨지지 않습니다.** 제목의 `&`·`%`·중괄호가 escape됩니다.
4. **Streamlit을 가져오지 않습니다** — 그래서 이 테스트가 접속도 화면도 없이 돕니다.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

import citations

FULL = {
    "arxiv_id": "2301.12345",
    "title": "Attention & Scale: 100% {Better}",
    "authors": ["Ada Lovelace", "Alan Turing"],
    "published": "2023-01-15T00:00:00Z",
    "primary_category": "cs.LG",
    "categories": ["cs.LG", "cs.AI"],
    "pdf_url": "https://arxiv.org/pdf/2301.12345",
    "abstract": "초록입니다.",
}
BARE = {"arxiv_id": "cs/0701001", "title": "Old style id", "primary_category": "cs.DS"}


def test_the_module_does_not_import_streamlit():
    source = Path("citations.py").read_text(encoding="utf-8")

    assert not re.search(r"^\s*import streamlit", source, re.MULTILINE)
    assert not re.search(r"^\s*from streamlit", source, re.MULTILINE)


def test_a_missing_field_is_left_out_not_guessed():
    text = citations.entry(BARE)

    assert "author" not in text
    assert "year" not in text
    assert "None" not in text
    # 있는 것은 그대로 들어갑니다.
    assert "eprint        = {cs/0701001}" in text or "eprint = {cs/0701001}" in text


def test_the_year_comes_from_published_and_nowhere_else():
    assert citations.year_of(FULL) == "2023"
    assert citations.year_of({"published": None}) == ""
    assert citations.year_of({"published": "미상"}) == ""
    assert citations.year_of({}) == ""


def test_bibtex_special_characters_are_escaped():
    """제목의 `&` 하나가 파일 전체를 깨뜨립니다."""
    text = citations.entry(FULL)

    assert r"\&" in text and r"\%" in text
    assert r"\{Better\}" in text
    # escape되지 않은 `&`가 남아 있으면 안 됩니다.
    assert not re.search(r"(?<!\\)&", text)


def test_the_key_survives_both_arxiv_id_shapes():
    assert citations.bibtex_key(FULL) == "arxiv_2301_12345"
    assert citations.bibtex_key(BARE) == "arxiv_cs_0701001"
    assert citations.bibtex_key({}) == ""
    # 키에 BibTeX가 못 읽는 글자가 남으면 안 됩니다.
    for paper in (FULL, BARE):
        assert re.fullmatch(r"[A-Za-z0-9_]+", citations.bibtex_key(paper))


def test_an_entry_without_an_id_cannot_be_written_and_is_counted():
    """키를 만들 수 없는 항목은 조용히 사라지지 않고 머리말에 적힙니다."""
    text = citations.to_bibtex([FULL, {"title": "id가 없습니다"}])

    assert text.count("@misc{") == 1
    assert "arXiv ID가 없어 뺀 항목 1건" in text


def test_the_empty_fields_are_counted_in_the_file_and_by_the_caller():
    gaps = citations.missing([FULL, BARE])

    assert gaps == {"author": ["cs/0701001"], "year": ["cs/0701001"]}
    text = citations.to_bibtex([FULL, BARE])
    assert "author 칸이 빈 항목 1건: cs/0701001" in text
    assert "year 칸이 빈 항목 1건: cs/0701001" in text
    # 빈 칸이 없으면 그 줄도 없습니다.
    assert "칸이 빈 항목" not in citations.to_bibtex([FULL])


def test_the_namesake_caveat_travels_with_the_file():
    """파일은 앱 밖으로 나가므로 화면의 설명이 따라가지 않습니다."""
    assert "동명이인" in citations.to_bibtex([FULL])


def test_authors_are_read_from_a_list_or_a_string():
    assert citations.author_list({"authors": ["A B", " C D "]}) == ["A B", "C D"]
    assert citations.author_list({"authors": "A B; C D"}) == ["A B", "C D"]
    assert citations.author_list({"authors": None}) == []
    assert citations.author_list({}) == []


def test_csv_keeps_every_row_and_flattens_the_lists():
    rows = list(csv.DictReader(io.StringIO(citations.to_csv([FULL, BARE]))))

    assert [row["arxiv_id"] for row in rows] == ["2301.12345", "cs/0701001"]
    assert rows[0]["authors"] == "Ada Lovelace; Alan Turing"
    assert rows[0]["categories"] == "cs.LG; cs.AI"
    # 없는 값은 빈 칸입니다. "None"이라고 적히면 그 글자가 데이터가 됩니다.
    assert rows[1]["authors"] == "" and rows[1]["published"] == ""
    assert "None" not in citations.to_csv([BARE])


def saved_paper_query() -> str:
    """The `SAVED_PAPER_DETAILS` Cypher, read as text.

    `import chatbot`은 Aura와 OpenAI에 붙습니다. 조회문 하나를 보려고 붙을 이유가 없고,
    붙이면 `tests/test_input_tokens.py`가 지키는 성질("토큰을 세는 데 chatbot이 필요하면
    안 된다")을 **임포트 순서로** 깨뜨립니다. 실제로 한 번 깨뜨렸습니다.
    """
    source = Path("chatbot.py").read_text(encoding="utf-8")
    match = re.search(r'SAVED_PAPER_DETAILS = """(.*?)"""', source, re.DOTALL)
    assert match, "SAVED_PAPER_DETAILS를 찾지 못했습니다"
    return match.group(1)


def test_csv_columns_match_what_the_saved_list_holds():
    """저장 목록이 들고 있지 않은 열을 적으면 영원히 빈 열이 생깁니다."""
    returned = set(re.findall(r"AS (\w+)", saved_paper_query()))

    assert set(citations.CSV_COLUMNS) <= returned, set(citations.CSV_COLUMNS) - returned


def test_the_saved_paper_query_reads_the_authors():
    """저자가 없으면 BibTeX 항목이 서지 도구에서 쓸 수 없습니다(ADR-042)."""
    cypher = saved_paper_query()

    assert "ArxivAuthorName" in cypher
    assert "AS authors" in cypher
    # 저자가 없는 논문에서 조회가 0행을 돌려주면 저장 자체가 실패합니다.
    assert "OPTIONAL MATCH" in cypher
