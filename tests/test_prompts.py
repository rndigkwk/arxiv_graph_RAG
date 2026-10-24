"""프롬프트가 한곳에 모여 있고, 버전을 매길 수 있는 상태인지 봅니다.

프롬프트를 손으로 고치다 두 번 회귀를 냈습니다. 이 테스트들이 막는 것은 회귀 자체가 아니라
**회귀를 추적할 수 없게 되는 상태**입니다 — 본문이 다시 코드 안으로 흩어지거나, 등록 목록에서
빠지거나, 지문이 보고서에 적히지 않는 경우.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pipeline.prompt_registry import DIGEST_LENGTH, digest, manifest
from src import prompts

ROOT = Path(__file__).resolve().parents[1]
# 프롬프트가 들어 있던 두 모듈입니다. 다시 리터럴이 생기면 여기서 걸립니다.
CONSUMERS = ("src/chatbot.py", "src/candidate_validation.py")
# 이 길이 아래의 문자열은 프롬프트가 아니라 오류 메시지나 라벨입니다.
PROMPT_LIKE_LENGTH = 300


def test_every_prompt_is_listed_for_registration():
    exported = {
        name
        for name in vars(prompts)
        if name.isupper() and isinstance(getattr(prompts, name), str)
    }
    # 상수를 더하고 REGISTERED에 넣지 않으면 그 프롬프트만 버전 없이 돌아갑니다.
    assert exported == set(prompts.REGISTERED)
    assert set(prompts.all_prompts()) == exported


def is_cypher(text: str) -> bool:
    """Cypher 질의는 DB에 보내는 코드입니다. 모델에게 보내는 지시문이 아닙니다."""
    return text.strip().upper().startswith(("MATCH", "OPTIONAL MATCH", "WITH", "RETURN", "CALL"))


def docstring_lines(tree: ast.AST) -> set[int]:
    """Where the docstrings are. They are documentation, not text sent to the model."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                found.add(first.value.lineno)
    return found


def test_no_long_prompt_literal_is_left_in_the_consuming_modules():
    """본문이 코드 안으로 돌아가면 지문과 실제로 보낸 글이 어긋납니다.

    이 확인이 실제로 하나를 찾아냈습니다 — 번역 도구의 지시문 두 개가 `chatbot.py`에 남아
    있었고, 그래서 ADR-010이 쟀던 번역 경로만 버전 없이 돌고 있었습니다.
    """
    offenders = []
    for name in CONSUMERS:
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        allowed = docstring_lines(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and len(node.value) > PROMPT_LIKE_LENGTH
                and node.lineno not in allowed
                and not is_cypher(node.value)
            ):
                offenders.append((name, node.lineno, node.value[:60]))
    assert not offenders, offenders


def test_the_prompts_the_app_sends_come_from_this_module():
    chatbot_source = (ROOT / "src/chatbot.py").read_text(encoding="utf-8")
    assert "cypher_rules = prompts.CYPHER_RULES" in chatbot_source
    assert "prompts.AGENT_SYSTEM" in chatbot_source
    candidate_source = (ROOT / "src/candidate_validation.py").read_text(encoding="utf-8")
    assert '("system", prompts.JUDGE_CONTENT)' in candidate_source
    assert "translate_system = prompts.TRANSLATE_TO_ENGLISH" in chatbot_source
    assert "korean_system = prompts.TRANSLATE_TO_KOREAN" in chatbot_source


def test_the_two_sentences_that_caused_regressions_are_still_there():
    """두 번의 회귀가 어느 문장에서 왔는지 본문에 남아 있어야 합니다.

    옮기는 과정에서 글이 잘리면 측정값과 프롬프트가 조용히 어긋납니다.
    """
    # 심판 정확도 21/21 -> 11/21 을 만든 구별입니다(EXPERIMENTS.md 8절).
    assert "requires_content is true only when" in prompts.JUDGE_CONTENT
    assert "belongs in graph_conditions instead" in prompts.JUDGE_CONTENT
    # 집계 순위가 문자열로 정렬되던 문제의 규칙입니다(EXPERIMENTS.md 9절).
    assert "toString()으로 감싼 값으로 정렬하면" in prompts.CYPHER_RULES


def test_the_agent_prompt_still_takes_the_values_the_app_fills_in():
    # 자리표시자가 사라지면 포맷이 조용히 빈 지시문을 만듭니다.
    assert "{schema}" in prompts.AGENT_SYSTEM
    assert "{cypher_rules}" in prompts.AGENT_SYSTEM


def test_a_digest_changes_when_one_character_changes():
    before = digest(prompts.JUDGE_CONTENT)
    after = digest(prompts.JUDGE_CONTENT + " ")
    assert before != after
    assert len(before) == DIGEST_LENGTH


def test_the_manifest_records_a_digest_for_every_registered_prompt():
    entries = manifest()
    assert set(entries) == set(prompts.REGISTERED)
    for name, entry in entries.items():
        assert entry["chars"] == len(getattr(prompts, name))
        assert entry["sha256"] == digest(getattr(prompts, name))


def test_an_evaluation_report_records_which_prompts_it_ran():
    """git_sha만으로는 부족합니다. 프롬프트를 안 고친 커밋에서도 점수는 흔들립니다."""
    from evals.telemetry import prompt_manifest

    recorded = prompt_manifest()
    assert recorded == {name: digest(text) for name, text in prompts.all_prompts().items()}
