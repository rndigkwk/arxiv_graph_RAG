"""평가 하네스가 앱과 같은 조건에서 재는지 봅니다 - 인용망 예열(ADR-067, ADR-077).

2026-10-26 실행의 첫 `rank_related_papers` 호출은 26.1초였고, 나머지 24번은 p50 0.68초였습니다.
앱은 그 값을 부팅 때 배경에서 치르므로 사용자는 겪지 않습니다. 하네스만 그 값을 한 문항에
몰아 주면, 그 문항의 지연과 실패 판정이 앱에는 없는 일로 정해집니다.
"""

import ast
from pathlib import Path

from evals import run


class FakeChatbot:
    def __init__(self):
        self.built = 0

    def citation_graph(self):
        self.built += 1
        return {"ids": []}


def test_agent_modes_build_the_citation_graph_before_the_first_case():
    for mode in ("agent", "compare"):
        bot = FakeChatbot()
        seconds = run.warm_citation_graph(bot, mode)
        assert bot.built == 1
        assert isinstance(seconds, float) and seconds >= 0


def test_retrieval_mode_does_not_pay_for_a_graph_it_never_uses():
    bot = FakeChatbot()
    assert run.warm_citation_graph(bot, "retrieval") is None
    assert bot.built == 0


def main_body():
    tree = ast.parse(Path("evals/run.py").read_text(encoding="utf-8"))
    return next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "main")


def test_main_warms_up_before_the_case_loop_and_records_it():
    """예열이 문항 고리 **안**이나 뒤에 있으면 첫 문항이 여전히 그 값을 냅니다."""
    body = main_body()
    warm = next(node.lineno for node in ast.walk(body) if isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "warm_citation_graph")
    loop = next(node.lineno for node in body.body if isinstance(node, ast.For))
    assert warm < loop
    source = ast.unparse(body)
    assert "metadata['citation_graph_warmup_seconds'] = warmup_seconds" in source
