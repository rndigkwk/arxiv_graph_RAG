"""PageRank 탭이 분류를 **고를 수 있게** 되었는지 지킵니다.

`app.py`에 `CATEGORY = "cs.AI"`가 상수로 박혀 있었고 화면에 분류 선택이 없었습니다. 저장된
논문을 대표 분류로 세면 cs.AI는 **30.2%**입니다 - cs.CL 20.4%, cs.LG 16.7%, cs.CV 10.8%가
그 뒤에 있고 대표 분류는 131종입니다. 그동안 이 탭은 **저장된 논문의 30%만** 보여 주고
있었습니다.

목록은 손으로 적지 않습니다. 이 저장소에서 손으로 유지하는 목록이 조용히 갈라진 것이 여덟
번입니다(ADR-068). 수집 창이 바뀌면 분류 분포도 바뀌므로, 목록은 세어서 만듭니다.
"""

import re
from pathlib import Path

import app

SOURCE = Path("app.py").read_text(encoding="utf-8")


def test_the_category_is_not_a_fixed_constant_any_more():
    """상수 이름이 `DEFAULT_`로 바뀐 것이 핵심입니다 - 시작값이지 유일한 값이 아닙니다."""
    assert "DEFAULT_CATEGORY" in SOURCE
    assert not re.search(r"^CATEGORY = ", SOURCE, re.M)
    assert not re.search(r"^\s*code = CATEGORY\s*$", SOURCE, re.M)


def test_the_screen_offers_a_category_picker():
    body = SOURCE[SOURCE.index("def show_pagerank("):]

    assert 'st.selectbox("분류"' in body
    # 고른 값이 계산으로 들어가야 의미가 있습니다. 두 엔진 모두 `code`를 받습니다.
    assert body.index('st.selectbox("분류"') < body.index("ranking(code,")


def test_the_choices_are_counted_not_listed_by_hand():
    assert "def category_options(" in SOURCE
    assert "top_categories(" in SOURCE
    # 화면 쪽에 분류 코드를 늘어놓은 리터럴 목록이 없어야 합니다.
    assert not re.search(r'\[\s*"(?:cs|stat|math)\.[A-Z]', SOURCE)
    assert "category_options()" in SOURCE[SOURCE.index("def show_pagerank("):]


def test_the_scope_labels_follow_the_chosen_category():
    """범위 문구에 분류 이름이 들어갑니다.

    고정 문구로 두면 cs.AI를 고르지 않아도 cs.AI라고 적힙니다.
    """
    labels = list(app.scopes("cs.CV"))

    assert all("cs.CV" in label for label in labels)
    assert set(app.scopes("cs.CV").values()) == {"global", "subgraph"}


def test_the_counting_query_shape():
    """세는 쿼리는 쓰기 낱말이 없고 상한이 매개변수로 들어갑니다."""
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    query = source[source.index("TOP_CATEGORIES = "):]
    query = query[:query.index('"""', query.index('"""') + 3)]

    assert "LIMIT $limit" in query
    for word in ("CREATE", "MERGE", "DELETE", "SET ", "REMOVE", "CALL"):
        assert word not in query.upper()
