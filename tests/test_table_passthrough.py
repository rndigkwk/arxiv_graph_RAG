"""번역할 때 표의 행을 빼 두었다가 되돌리기(ADR-111). 모델 없이 돕니다."""

from __future__ import annotations

from src import table_passthrough as t

ANSWER = "\n".join([
    "The graph lists these papers:",
    "",
    "| arXiv ID | Relationship |",
    "|---|---|",
    "| 1706.03762 | Attention Is All You Need |",
    "| 1810.04805 | BERT |",
    "",
    "Both are about transformers.",
])
ROWS = "| 1706.03762 | Attention Is All You Need |\n| 1810.04805 | BERT |"


def test_the_header_is_sent_and_the_rows_become_one_marker_line():
    prose, tables = t.split_tables(ANSWER)
    assert tables == [ROWS]
    assert prose.split("\n") == ["The graph lists these papers:", "", "| arXiv ID | Relationship |",
                                 "|---|---|", "[[TABLE_1]]", "", "Both are about transformers."]


def test_a_table_at_the_very_end_is_kept():
    prose, tables = t.split_tables("Here:\n| a |\n|---|\n| b |")
    assert prose == "Here:\n| a |\n|---|\n[[TABLE_1]]" and tables == ["| b |"]


def test_a_header_without_rows_is_left_for_the_translator():
    assert t.split_tables("Here:\n| a | b |\n|---|---|") == ("Here:\n| a | b |\n|---|---|", [])


def test_translation_gets_the_rows_back_under_the_translated_header():
    prose, tables = t.split_tables(ANSWER)
    korean = (prose.replace("The graph lists these papers:", "그래프에 있는 논문입니다:")
              .replace("| arXiv ID | Relationship |", "| arXiv ID | 관계 |")
              .replace("Both are about transformers.", "둘 다 트랜스포머 논문입니다."))
    restored = t.restore(korean, tables)
    assert "| arXiv ID | 관계 |\n|---|---|\n| 1706.03762 | Attention Is All You Need |" in restored
    assert restored.startswith("그래프에 있는 논문입니다:")


def test_a_blank_line_the_model_slips_above_the_marker_does_not_break_the_table():
    restored = t.restore("| ID | 제목 |\n|---|---|\n\n[[TABLE_1]]\n\n끝.", ["| 1 | A |"])
    assert restored == "| ID | 제목 |\n|---|---|\n| 1 | A |\n\n끝."


def test_a_table_with_description_sentences_is_translated_whole():
    """설명 열은 읽는 사람에게 옮겨야 합니다. 제목 칸은 마침표로 끝나지 않습니다."""
    described = "\n".join([
        "Papers:", "| arXiv ID | Title | How they use GNNs |", "|---|---|---|",
        "| 2104.06378 | QA-GNN | Connects the question context with a knowledge graph. |",
    ])
    assert t.split_tables(described) == (described, [])
    titled = "| ID | Title |\n|---|---|\n| 1 | Attention Is All You Need for Graph Reasoning |"
    assert t.split_tables(titled)[1] == ["| 1 | Attention Is All You Need for Graph Reasoning |"]


def test_a_lost_or_extra_marker_falls_back_to_the_whole_text():
    _, tables = t.split_tables(ANSWER)
    assert t.restore("자리표시가 사라졌습니다", tables) is None
    assert t.restore("[[TABLE_1]] [[TABLE_2]]", tables) is None
    assert t.restore("[[TABLE_1]][[TABLE_1]]", tables) is None


def test_an_answer_that_is_only_a_table_has_nothing_to_translate():
    prose, tables = t.split_tables("| a | b |\n|---|---|\n| 1 | 2 |")
    assert tables and not t.has_prose(prose)
    assert t.has_prose(t.split_tables(ANSWER)[0])


def test_text_without_tables_is_unchanged():
    assert t.split_tables("No table here.\nTwo lines.") == ("No table here.\nTwo lines.", [])


def test_a_streaming_translation_shows_tables_once_their_marker_arrives():
    """흘려 받는 중에는 도착한 자리표시만 표가 됩니다(ADR-114). 반쪽 자리표시는 비치지 않습니다."""
    rows = ["| 1 | A |"]
    assert t.restore_partial("그래프 [[TAB", rows) == "그래프 "
    assert t.restore_partial("| ID |\n|---|\n[[TABLE_1]", rows) == "| ID |\n|---|\n"
    restored = t.restore_partial("| ID |\n|---|\n\n[[TABLE_1]]\n끝", rows)
    assert restored == "| ID |\n|---|\n| 1 | A |\n끝"
    assert t.restore_partial("[보통 괄호] 글", rows) == "[보통 괄호] 글"
    # 번호가 넘치면 그대로 둡니다. 끝나고 `restore`가 None이면 전체를 다시 번역합니다.
    assert t.restore_partial("[[TABLE_2]]", rows) == "[[TABLE_2]]"


def test_a_known_header_is_translated_here_and_the_whole_table_is_kept():
    """에이전트의 기본 표는 머리줄까지 사전으로 옮깁니다(ADR-117). 모델은 표를 보지 않습니다."""
    table = "| arXiv ID | Title | Primary category |\n|---|---|---|\n| 1 | A | cs.CL |"
    prose, tables = t.split_tables("Found:\n" + table + "\n\nDone.")
    assert prose == "Found:\n[[TABLE_1]]\n\nDone."
    korean_table = "| arXiv ID | 제목 | 주 분류 |\n|---|---|---|\n| 1 | A | cs.CL |"
    assert tables == [korean_table]
    restored = t.restore("찾았습니다:\n[[TABLE_1]]\n\n끝.", tables)
    assert restored == "찾았습니다:\n" + korean_table + "\n\n끝."


def test_the_blank_line_between_a_paragraph_and_a_whole_table_marker_is_kept():
    """빈 줄을 지우는 것은 머리줄 바로 아래 자리표시뿐입니다. 문단에 붙으면 표가 문단이 됩니다."""
    tables = ["| arXiv ID | 제목 |\n|---|---|\n| 1 | A |"]
    restored = t.restore("다섯 편입니다.\n\n[[TABLE_1]]\n\n끝.", tables)
    assert restored == "다섯 편입니다.\n\n" + tables[0] + "\n\n끝."
    partial = t.restore_partial("다섯 편입니다.\n\n[[TABLE_1]]", tables)
    assert partial == "다섯 편입니다.\n\n" + tables[0]


def test_category_words_in_translated_prose_match_the_screen():
    """모델은 같은 말을 실행마다 다르게 옮겼습니다. 머리줄 "주 분류"와 맞춥니다(ADR-119)."""
    for said in ("주 카테고리가", "기본 카테고리가", "1차 카테고리가", "주카테고리가"):
        assert t.korean_terms(f"{said} cs.CL인 논문") == "주 분류가 cs.CL인 논문"
    assert t.korean_terms("인용, 카테고리를 봅니다") == "인용, 분류를 봅니다"
    assert t.korean_terms("Category Theory 논문") == "Category Theory 논문"  # 영어는 그대로


def test_header_words_are_matched_whole_and_without_case():
    assert t._known_header("| ARXIV ID | title | Citations |") == "| arXiv ID | 제목 | 인용 수 |"
    assert t._known_header("| arXiv ID | Title | Why read it |") is None


def test_a_table_only_answer_gets_its_known_header_in_korean():
    """옮길 문장이 없는 표뿐인 답도 머리줄은 한국어가 됩니다. 모델은 부르지 않습니다."""
    prose, tables = t.split_tables("| arXiv ID | Title |\n|---|---|\n| 1 | A |")
    assert not t.has_prose(prose)
    assert t.restore(prose, tables) == "| arXiv ID | 제목 |\n|---|---|\n| 1 | A |"
