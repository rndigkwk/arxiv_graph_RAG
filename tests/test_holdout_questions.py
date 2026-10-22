"""보류 질문 생성기가 지켜야 할 것들입니다(ADR-059).

보류 집합의 가치는 **라벨이 맞다**는 것에 전부 달려 있습니다. 라벨이 틀린 질문이 섞이면 두
공급자가 같이 틀리고, 그것을 "프롬프트가 약하다"로 읽게 됩니다 - 실제로는 우리가 만든 질문이
틀린 것인데요. 그래서 거부 조건과 템플릿을 테스트가 지킵니다.
"""

import json
import re
from pathlib import Path

import pytest

from evals import holdout_questions as hq

GRAPH_CASES = Path("evals/cases_graph.json")
BASE_CASES = Path("evals/cases.json")
GENERATED = Path("evals/judge_holdout.json")


def template_pattern(template: str) -> re.Pattern[str]:
    """`{구멍}`이 있는 템플릿을 정규식으로 바꿉니다."""
    parts = re.split(r"\{(\w+)\}", template)
    built = "".join(
        re.escape(part) if index % 2 == 0 else "(.+)"
        for index, part in enumerate(parts)
    )
    return re.compile("^" + built + "$", re.S)


def tuning_questions():
    base = json.loads(BASE_CASES.read_text(encoding="utf-8-sig"))
    cases = base["cases"] if isinstance(base, dict) else base
    cases += json.loads(GRAPH_CASES.read_text(encoding="utf-8"))["cases"]
    return cases


# ── 템플릿이 사례 파일과 갈라지지 않습니다 ──────────────────────────────

def test_every_template_still_matches_the_cases_it_came_from():
    """**이 테스트가 이 파일의 이유입니다.**

    보류 질문의 모양이 튜닝 질문과 달라지면 다른 것을 재게 됩니다. 사례 파일의 문장을
    누가 다듬으면 여기서 터져야 합니다 - 생성기를 조용히 늙게 두지 않습니다.
    """
    checked = 0
    for case in tuning_questions():
        template = hq.TEMPLATES.get(case["category"])
        if template is None:
            continue  # `topic`은 생성하지 않습니다.
        assert template_pattern(template).match(case["question"]), (
            f"{case['id']}의 문장이 {case['category']} 템플릿과 어긋납니다: "
            f"{case['question'][:90]}"
        )
        checked += 1
    # 가족 다섯 + exact-title = 생성하는 가족 전부가 실제로 검사됐는지 봅니다.
    assert checked >= 51


def test_the_family_tables_do_not_drift_apart():
    """템플릿·라벨·목표가 세 군데 손으로 유지되는 목록입니다. 키가 갈라지면 막습니다."""
    assert set(hq.TEMPLATES) == set(hq.LABELS) == set(hq.TARGETS) == set(hq.CANDIDATES)


def test_the_topic_family_is_left_out_on_purpose():
    """`topic`은 주제 구절이 기계적으로 안 나옵니다. 빠진 것을 사고로 두지 않습니다."""
    assert "topic" not in hq.TEMPLATES
    adr = Path(
        "docs/adr/ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md"
    ).read_text(encoding="utf-8")
    assert "`topic` 가족은 생성하지 않습니다" in adr


# ── 거부 조건 ────────────────────────────────────────────────────────────

def test_a_short_title_is_refused_because_it_reads_as_a_topic():
    used = {"titles": set(), "topics": []}
    assert hq.title_is_safe("Graph Neural Networks", used) == "짧은 제목"


def test_a_title_that_is_also_a_topic_phrase_is_refused():
    """같은 문장이 두 가족의 라벨을 동시에 가지면 라벨이 의심스러워집니다."""
    used = {"titles": set(), "topics": ["retrieval augmented generation"]}
    assert hq.title_is_safe(
        "Retrieval Augmented Generation", used
    ) is not None
    # 품는 방향 양쪽 다 봅니다.
    assert hq.title_is_safe(
        "A Survey of Retrieval Augmented Generation Methods", used
    ) == "주제 구절과 겹침"


def test_a_tuning_set_title_is_refused():
    used = {"titles": {"attention is all you need, really"}, "topics": []}
    assert hq.title_is_safe(
        "Attention Is All You Need, Really", used
    ) == "튜닝 집합의 제목"


def test_a_long_distinctive_title_passes():
    used = {"titles": set(), "topics": ["graph neural network"]}
    assert hq.title_is_safe(
        "PromptLink: Leveraging Large Language Models for Concept Linking", used
    ) is None


def test_the_tuning_set_pulls_the_topic_phrase_out_of_the_question():
    used = hq.tuning_set(GRAPH_CASES, BASE_CASES)
    assert used["topics"], "주제 구절을 하나도 못 꺼냈습니다"
    assert all("Find arXiv papers about" not in phrase for phrase in used["topics"])
    assert all(phrase == phrase.lower() for phrase in used["topics"])
    assert used["names"] and used["topcited"] and used["arxiv_ids"]


# ── Cypher는 사례 파일에서 읽습니다 ─────────────────────────────────────

def test_the_cypher_comes_from_the_case_file_not_from_a_copy():
    found = hq.cypher_by_family(GRAPH_CASES)
    assert set(found) == {"author", "citing", "coauthor", "reference", "topcited"}
    source = Path("evals/holdout_questions.py").read_text(encoding="utf-8")
    # 판정용 Cypher를 베껴 두면 그 목록이 조용히 늙습니다.
    assert "AUTHORED]->(p:ArxivPaper) RETURN p.arxiv_id" not in source


def test_a_family_whose_cases_disagree_on_cypher_is_refused(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"cases": [
        {"category": "author", "cypher": "MATCH (a) RETURN a"},
        {"category": "author", "cypher": "MATCH (b) RETURN b"},
    ]}), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        hq.cypher_by_family(path)
    assert "author" in str(caught.value)


# ── 나누기 ───────────────────────────────────────────────────────────────

def test_the_split_keeps_the_family_mix_in_both_pieces():
    """한 번에 자르면 두 조각의 가족 비율이 달라지고, 정확도를 비교할 수 없습니다."""
    items = [{"family": fam, "id": f"{fam}-{i}"}
             for fam, count in (("exact-title", 60), ("author", 12))
             for i in range(count)]
    hq.split(items)
    for family, total in (("exact-title", 60), ("author", 12)):
        picked = [i for i in items if i["family"] == family]
        final = sum(1 for i in picked if i["split"] == "final")
        assert final == round(total * hq.FINAL_SHARE)


def test_every_question_lands_in_exactly_one_piece():
    items = [{"family": "author", "id": f"a{i}"} for i in range(5)]
    hq.split(items)
    assert all(item["split"] in ("diagnostic", "final") for item in items)
    assert any(item["split"] == "final" for item in items)


def test_the_split_is_seeded_so_a_rerun_cannot_change_the_answer():
    def run():
        items = [{"family": "author", "id": f"a{i}"} for i in range(12)]
        hq.split(items)
        return [(i["id"], i["split"]) for i in items]

    assert run() == run()


# ── 생성 ─────────────────────────────────────────────────────────────────

def test_build_refuses_a_question_whose_relationship_is_empty():
    """관계가 없으면 질문이 공허해지고 심판이 보는 것이 달라집니다."""
    used = {"arxiv_ids": set(), "names": set(), "topcited": set(), "topics": [],
            "titles": set()}

    def run_read(query, params=None):
        if "WITH a.name AS name" in query:
            return [{"name": "Ada Lovelace", "papers": 3}]
        return []  # 판정용 Cypher가 아무것도 안 돌려줍니다.

    questions, rejected = hq.build(
        run_read, used, {"author": "MATCH (a) RETURN a"}, {"author": 2})
    assert questions == []
    assert rejected.get("관계 없음") == 1
    # 못 채운 것을 조용히 넘기지 않습니다.
    assert any("목표 미달" in reason for reason in rejected)


def test_build_refuses_a_one_token_author_name():
    used = {"arxiv_ids": set(), "names": set(), "topcited": set(), "topics": [],
            "titles": set()}

    def run_read(query, params=None):
        if "WITH a.name AS name" in query:
            return [{"name": "Li", "papers": 9}]
        return [{"arxiv_id": "2601.00001"}]

    questions, rejected = hq.build(
        run_read, used, {"author": "MATCH (a) RETURN a"}, {"author": 1})
    assert questions == []
    assert rejected.get("한 토큰 이름") == 1


def test_build_skips_what_the_tuning_set_already_used():
    used = {"arxiv_ids": {"2601.00001"}, "names": set(), "topcited": set(),
            "topics": [], "titles": set()}

    def run_read(query, params=None):
        if "p.title IS NOT NULL" in query:
            return [{"arxiv_id": "2601.00001",
                     "title": "A Rather Long And Distinctive Title Here"}]
        return [{"arxiv_id": "2601.00001"}]

    questions, rejected = hq.build(
        run_read, used, {}, {"exact-title": 1})
    assert questions == []
    assert rejected.get("튜닝 집합의 논문") == 1


# ── 커밋된 결과물 ────────────────────────────────────────────────────────

def test_the_generated_set_says_it_is_not_an_eval_case_file():
    """정답이 없는 집합을 회귀 게이트에 쓰면 게이트가 거짓말을 합니다."""
    report = json.loads(GENERATED.read_text(encoding="utf-8"))
    assert "회귀 게이트에 쓸 수 없습니다" in report["purpose"]
    assert all("expected_ids" not in item for item in report["questions"])
    assert "cases" not in GENERATED.name


def test_the_generated_set_is_internally_consistent():
    report = json.loads(GENERATED.read_text(encoding="utf-8"))
    items = report["questions"]
    assert len({item["id"] for item in items}) == len(items)
    for item in items:
        content, graph = hq.LABELS[item["family"]]
        assert item["expect_content"] == content
        assert item["expect_graph"] == graph
        assert item["split"] in ("diagnostic", "final")
        assert item["paper_ids"], item["id"]
        assert template_pattern(hq.TEMPLATES[item["family"]]).match(item["question"])


def test_the_generated_set_records_what_it_refused():
    """거부가 많으면 집합이 한쪽으로 기울었다는 뜻입니다. 모르면 결과를 잘못 읽습니다."""
    report = json.loads(GENERATED.read_text(encoding="utf-8"))
    assert "rejected" in report
    assert report["seed"] == hq.SEED


def test_the_generated_set_does_not_reuse_the_tuning_set():
    """보류가 아니면 이 연구가 답하려는 질문에 답하지 못합니다."""
    report = json.loads(GENERATED.read_text(encoding="utf-8"))
    used = hq.tuning_set(GRAPH_CASES, BASE_CASES)
    for item in report["questions"]:
        if item["family"] == "exact-title":
            title = item["question"].replace("Find the paper titled:", "").strip()
            assert title.lower() not in used["titles"], item["id"]
            assert item["paper_ids"][0] not in used["arxiv_ids"], item["id"]
        if item["family"] == "author":
            name = item["question"].split("named ")[1].rstrip("?")
            assert name not in used["names"], item["id"]
