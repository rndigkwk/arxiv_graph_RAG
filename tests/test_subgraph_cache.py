"""그래프 조회는 캐시를 거칩니다(ADR-075).

화면은 다시 그릴 때마다 대화 기록의 답변을 전부 다시 그립니다. 그 자리에서 Aura를 직접 부르면
답변 수만큼 질의가 반복됩니다 - 한 번에 중앙값 0.40초였습니다.
"""

import ast
from pathlib import Path

SOURCE = Path("app.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def function(name):
    return next(node for node in TREE.body
                if isinstance(node, ast.FunctionDef) and node.name == name)


def calls_to(attr, node):
    return [call for call in ast.walk(node) if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute) and call.func.attr == attr]


def test_only_the_cached_wrapper_reaches_the_database():
    owners = [node.name for node in TREE.body if isinstance(node, ast.FunctionDef)
              and calls_to("fetch_subgraph", node)]
    assert owners == ["subgraph_of"]


def test_the_wrapper_is_cached_with_an_expiry():
    decorator = function("subgraph_of").decorator_list[0]
    assert ast.unparse(decorator.func) == "st.cache_data"
    assert {k.arg for k in decorator.keywords} >= {"ttl", "max_entries"}


def test_every_drawing_site_uses_the_wrapper():
    assert SOURCE.count("subgraph_of(tuple(") == 2

