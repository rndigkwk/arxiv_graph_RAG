"""바꿔 말한 평가 질문(`evals/paraphrase_questions.py`, ADR-093).

정답을 다시 세지 않으므로, 정답을 정하는 값이 질문에 글자 그대로 남았는지가 유일한 보증입니다.
"""

from __future__ import annotations

import json
from pathlib import Path

from evals import paraphrase_questions as pq

SOURCE = json.loads(pq.SOURCE.read_text(encoding="utf-8"))
ORIGINALS = {case["id"]: case for case in SOURCE["cases"]}
COMMITTED = json.loads(Path(pq.OUTPUT).read_text(encoding="utf-8"))["cases"]


def test_the_committed_set_keeps_every_value_the_answer_depends_on():
    assert pq.check(COMMITTED, ORIGINALS) == []


def test_the_committed_set_is_what_the_templates_make():
    """템플릿만 고치고 파일을 다시 만들지 않으면 여기서 걸립니다."""
    assert COMMITTED == pq.paraphrase(SOURCE["cases"])


def test_every_question_has_one_english_and_one_korean_wording():
    for source_id in ORIGINALS:
        languages = sorted(c["language"] for c in COMMITTED if c["paraphrase_of"] == source_id)
        assert languages == ["en", "ko"], source_id


def test_a_dropped_name_is_caught():
    broken = [dict(COMMITTED[0], question="Show me everything this author has written.")]
    assert any("name" in problem for problem in pq.check(broken, ORIGINALS))


def test_a_dropped_category_is_caught():
    case = next(c for c in COMMITTED if c["category"] == "topcited")
    broken = [dict(case, question=case["question"].replace(case["cypher_params"]["category"],
                                                           "computer science"))]
    assert any("category" in problem for problem in pq.check(broken, ORIGINALS))


def test_a_changed_answer_is_caught():
    broken = [dict(COMMITTED[0], expected_ids=["0000.00000"])]
    assert any("정답" in problem for problem in pq.check(broken, ORIGINALS))
