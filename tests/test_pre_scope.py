"""범위 밖 질문을 앱이 먼저 가리기(`src/pre_scope.py`, ADR-095). 접속 없이 봅니다."""

from __future__ import annotations

import json
from pathlib import Path

from src import pre_scope


def _questions(*paths):
    for path in paths:
        for case in json.loads(Path(path).read_text(encoding="utf-8-sig"))["cases"]:
            yield case["id"], case["question"]


def test_every_regression_question_has_a_domain_cue():
    """여기서 걸리면 평소 질문에도 벡터 검색 한 번이 더 붙고, 거절될 수도 있습니다."""
    missing = [cid for cid, q in _questions("evals/cases_graph.json",
                                            "evals/cases_graph_paraphrase.json",
                                            "evals/cases.json", "evals/cases_curated.json")
               if not pre_scope.has_domain_cue(q)]
    assert missing == []


def test_the_red_team_out_of_scope_questions_have_no_cue():
    cases = dict(_questions("evals/cases_redteam.json"))
    for cid in ("scope-01", "scope-02", "scope-04"):
        assert not pre_scope.has_domain_cue(cases[cid]), cid
    # "FIFA"는 약어라 단서가 됩니다 - 알고 받은 대가입니다(ADR-095 후속).
    assert pre_scope.has_domain_cue(cases["scope-03"])


def test_acronyms_are_cues_but_ordinary_words_are_not():
    for q in ("What is RLHF?", "Explain MoE routing", "How do GANs work?", "best LLMs"):
        assert pre_scope.has_domain_cue(q), q
    for q in ("What's the weather in Seoul?", "Tell me a joke", "I like Python", "A B C"):
        assert not pre_scope.has_domain_cue(q), q


def test_the_english_translation_also_counts():
    assert pre_scope.has_domain_cue("요즘 뭐가 유행이야", "recent papers on diffusion")


def test_a_low_score_is_declined_and_a_high_one_is_passed():
    low = {"papers": [{"score": pre_scope.DECLINE_BELOW - 0.01, "title": "x"}]}
    high = {"papers": [{"score": pre_scope.DECLINE_BELOW + 0.01, "title": "x"}]}
    assert pre_scope.decide(low) == "decline"
    assert pre_scope.decide(high) == "pass"


def test_an_unknown_score_is_never_declined():
    assert pre_scope.decide({"papers": []}) == "pass"
    assert pre_scope.decide({"papers": [{"title": "x"}]}) == "pass"
    assert pre_scope.decide(None) == "pass"


def test_names_are_offered_for_the_author_lookup():
    assert "Yann LeCun" in pre_scope.name_candidates("What has Yann LeCun done lately?")
    three = pre_scope.name_candidates("anything by Maria Garcia Lopez")
    assert {"Maria Garcia Lopez", "Maria Garcia", "Garcia Lopez"} <= set(three)
    assert pre_scope.name_candidates("what's the weather in seoul") == []


def test_no_hint_is_left_to_steer_the_agent():
    # 메시지 경로는 VM에서 "What is RLHF?"를 거절하게 했습니다(ADR-095 후속).
    assert not hasattr(pre_scope, "hint") and not hasattr(pre_scope, "HINT")
    assert '"hint"' not in Path("src/chatbot.py").read_text(encoding="utf-8").split(
        "def scope_first")[1].split("\n\n")[0]


def test_the_app_wires_the_scope_check():
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    assert "scope=scope_first," in source
    assert "pre_scope.has_domain_cue(asked, english)" in source
    assert "MATCH (a:ArxivAuthorName) WHERE a.name IN $names" in source
