"""한국어 질문 세트가 번역 손실 측정에 쓸 수 있는 형태인지 봅니다.

이 세트는 커밋해서 고정합니다. 측정할 때마다 모델로 다시 생성하면 수치가 생성 변동만큼
흔들려서, 번역 손실인지 생성 차이인지 구별할 수 없습니다.
"""

import json
import re
from pathlib import Path

from evals.language_study import CASES, load_cases

KOREAN = Path("evals/cases_korean.json")
HANGUL = re.compile(r"[가-힣]")


def load():
    return json.loads(KOREAN.read_text(encoding="utf-8"))


def test_every_english_case_has_a_korean_counterpart():
    dataset = load()
    korean_ids = {row["id"] for row in dataset["cases"]}
    english_ids = {case["id"] for case in load_cases(CASES)}

    assert korean_ids == english_ids


def test_the_english_query_is_stored_next_to_the_korean_ones():
    # 원문을 함께 적어 두지 않으면 영어 경로를 다른 파일에서 다시 찾아야 하고,
    # 두 파일이 어긋나면 상한이 틀어집니다.
    for row in load()["cases"]:
        assert row["search_query"].strip(), row["id"]
        assert row["search_query_ko_mixed"].strip(), row["id"]
        assert row["search_query_ko_pure"].strip(), row["id"]


def test_the_pure_variant_is_korean_for_most_cases():
    rows = load()["cases"]
    hangul = [row for row in rows if HANGUL.search(row["search_query_ko_pure"])]

    # 저자 이름만으로 된 질의는 한글로 옮길 것이 없어 영어로 남습니다.
    assert len(hangul) / len(rows) > 0.8


def test_the_mixed_variant_keeps_arxiv_category_codes_in_english():
    for row in load()["cases"]:
        if "cs." not in row["search_query"]:
            continue
        # 분류 코드가 번역되면 Cypher 조건과 맞지 않게 됩니다.
        code = re.search(r"cs\.[A-Z]{2}", row["search_query"])
        assert code and code.group(0) in row["search_query_ko_mixed"], row["id"]


def test_the_set_records_that_the_korean_was_machine_generated():
    dataset = load()

    # 모델이 만든 한국어라 왕복이 실제 사용자보다 쉽고, 측정값은 하한입니다.
    assert "하한" in dataset["note"]
    assert dataset["generated_by"]
    assert dataset["generated_at"]
