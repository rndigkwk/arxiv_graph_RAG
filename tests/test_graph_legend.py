"""그래프 범례가 **그린 것만, 빠뜨리지 않고** 설명하는지 지킵니다.

두 탭이 같은 `make_graph`를 쓰는데 범례는 챗봇 쪽에만 있었습니다. PageRank 탭은 표시 수를
5로 내리면 저자 점선이 그려지는데 그 말이 없었습니다(`AUTHOR_LIMIT`이 6).

그리고 챗봇 쪽에는 더 조용한 결함이 있었습니다. 논문이 6편을 넘으면 범례 문장을 **갈아
끼우면서** 테두리 설명까지 버렸습니다 — 답변 그래프는 보통 6편을 넘으므로, "후보를 지우지
않고 테두리로 가린다"는 ADR-065의 설명이 **거의 항상 사라지고 있었습니다.**

그래서 이 파일이 지키는 것은 두 가지입니다. 그리지 않은 선을 설명하지 않는 것과, 조건이
겹칠 때 한쪽이 다른 쪽을 덮지 않는 것.
"""

import re
from pathlib import Path

import app

BORDER = "테두리가 실선인 논문"
AUTHORED = "AUTHORED"
MECHANISM = {"1111.1111": "cocitation"}


def subgraph(papers):
    return {"papers": [{"arxiv_id": f"{index}"} for index in range(papers)],
            "references": []}


def test_a_small_graph_explains_the_author_dashes():
    line = app.graph_legend(subgraph(app.AUTHOR_LIMIT), 40)

    assert AUTHORED in line
    assert "REFERENCES" in line


def test_a_large_graph_does_not_explain_lines_it_did_not_draw():
    """6편을 넘으면 `make_graph`가 저자를 그리지 않습니다. 범례가 앞서 말하면 거짓입니다."""
    line = app.graph_legend(subgraph(app.AUTHOR_LIMIT + 1), 40)

    assert AUTHORED not in line
    assert "저자는 생략" in line


def test_the_border_explanation_survives_a_large_graph():
    """이것이 고친 결함입니다. 두 조건이 겹칠 때 한쪽이 다른 쪽을 덮고 있었습니다."""
    large = app.graph_legend(subgraph(app.AUTHOR_LIMIT + 1), 40, MECHANISM)

    assert BORDER in large
    # 덮이지 않았는지 확인하려면 작은 그래프에도 같은 설명이 있어야 합니다 - 둘 다 있어야
    # "겹칠 때만 사라졌다"를 가릴 수 있습니다.
    assert BORDER in app.graph_legend(subgraph(2), 40, MECHANISM)


def test_without_a_mechanism_there_is_no_border_explanation():
    """PageRank 탭은 후보를 섞지 않습니다. 테두리를 설명하면 없는 구분을 만듭니다."""
    assert BORDER not in app.graph_legend(subgraph(20), 40)


def test_the_node_cap_is_the_one_the_caller_passes():
    assert "최대 7개" in app.graph_legend(subgraph(2), 7)


def test_both_tabs_use_the_same_legend():
    """같은 그래프를 그리는 자리가 둘인데 설명이 하나만 있던 것이 이 결함의 시작입니다."""
    source = Path("app.py").read_text(encoding="utf-8")

    assert len(re.findall(r"st\.graphviz_chart\(", source)) == 2
    assert len(re.findall(r"st\.caption\(graph_legend\(", source)) == 2


def test_the_legend_is_one_function_not_two_copies():
    source = Path("app.py").read_text(encoding="utf-8")

    assert source.count("def graph_legend(") == 1
    # 덮어쓰던 옛 분기의 문장입니다. 남아 있으면 한쪽만 고친 것입니다.
    assert "저자 수는 아래 표에서 확인하세요." not in source
