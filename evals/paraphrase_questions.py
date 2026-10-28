"""회귀 평가 질문을 바꿔 말한 셋을 만듭니다 — 정답은 원문 그대로, 문형만 다릅니다(ADR-093).

    uv run --locked python -m evals.paraphrase_questions
    uv run --locked python -m evals.paraphrase_questions --check

`evals/cases_graph.json`의 30문항은 가족마다 한 문형입니다("Which papers in this collection
list an author named X?"). "최근"을 담은 질문이 하나도 없어 24~44초 걸리던 문제를 회귀 평가가
못 봤습니다(ADR-090). 정답은 Cypher에서 나오므로 **뜻이 같으면 정답도 같습니다** - 바꿔 말한
질문에 정답을 다시 셀 필요가 없습니다.

뜻이 같은지는 기계적으로 봅니다(`check`). 정답을 정하는 값 - 저자 이름, 대상 논문 제목, 분류,
개수 - 이 바꾼 질문에 **글자 그대로** 남아 있어야 합니다. 한국어 질문도 제목과 이름은 영어
그대로 둡니다(사용자가 실제로 그렇게 묻습니다).

문형은 가족마다 영어 하나, 한국어 하나입니다. 손으로 썼습니다 - 모델로 만들면 모델이 잘 알아듣는
문형만 나올 수 있습니다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SOURCE = Path("evals/cases_graph.json")
OUTPUT = Path("evals/cases_graph_paraphrase.json")

# 가족마다 두 문형. `{name}`·`{title}`·`{category}`·`{limit}`는 원문 문항의 값입니다.
TEMPLATES: dict[str, list[tuple[str, str]]] = {
    "author": [
        ("en", "Show me everything {name} has written that's in your database."),
        ("ko", "{name}이(가) 저자로 참여한 논문을 모두 찾아줘"),
    ],
    "citing": [
        ("en", "I'm looking for work that references \"{title}\" — what do you have?"),
        ("ko", "\"{title}\" 논문을 인용한 논문들 알려줘"),
    ],
    "reference": [
        ("en", "\"{title}\" has a reference list; which entries there are primarily {category} "
               "papers?"),
        ("ko", "\"{title}\"이 인용한 논문 중에 주 분류가 {category}인 것만 골라줘"),
    ],
    "coauthor": [
        ("en", "The authors of \"{title}\" — what else have they published here?"),
        ("ko", "\"{title}\"의 저자들이 쓴 다른 논문은 뭐가 있어?"),
    ],
    "topcited": [
        # "주 분류"를 빼면 보조 분류로 그 분야인 논문까지 들어가 정답이 달라집니다.
        ("en", "Top {limit} most-cited papers whose primary category is {category}, counting "
               "only citations within your graph, please."),
        ("ko", "주 분류가 {category}인 논문 중 이 그래프 안에서 가장 많이 인용된 {limit}편은?"),
    ],
}


def values_of(case: dict[str, Any]) -> dict[str, str]:
    """정답을 정하는 값. 바꾼 질문에 이 값들이 글자 그대로 있어야 합니다."""
    params = case["cypher_params"]
    values: dict[str, str] = {}
    if "name" in params:
        values["name"] = params["name"]
    if case["category"] in ("citing", "reference", "coauthor"):
        values["title"] = case["search_query"]
    if "category" in params:
        values["category"] = params["category"]
    if "limit" in params:
        values["limit"] = str(params["limit"])
    return values


def paraphrase(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for case in cases:
        for index, (language, template) in enumerate(TEMPLATES[case["category"]], start=1):
            out.append({
                **case,
                "id": f"{case['id']}-p{index}",
                "question": template.format(**values_of(case)),
                "paraphrase_of": case["id"],
                "language": language,
            })
    return out


def check(cases: list[dict[str, Any]], originals: dict[str, dict[str, Any]]) -> list[str]:
    """뜻이 바뀌었을 수 있는 문항. 빈 목록이면 통과입니다."""
    problems = []
    for case in cases:
        source = originals.get(case.get("paraphrase_of", ""))
        if source is None:
            problems.append(f"{case['id']}: 원문 문항이 없습니다")
            continue
        if case["question"] == source["question"]:
            problems.append(f"{case['id']}: 원문과 같은 문장입니다")
        for key, value in values_of(source).items():
            if value not in case["question"]:
                problems.append(f"{case['id']}: {key} {value!r}가 질문에 없습니다")
        if case["expected_ids"] != source["expected_ids"]:
            problems.append(f"{case['id']}: 정답이 원문과 다릅니다")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="파일을 쓰지 않고 검사만 합니다.")
    args = parser.parse_args()

    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    originals = {case["id"]: case for case in source["cases"]}
    if args.check:
        cases = json.loads(OUTPUT.read_text(encoding="utf-8"))["cases"]
    else:
        cases = paraphrase(source["cases"])
    problems = check(cases, originals)
    for problem in problems:
        print(problem)
    if problems:
        return 1
    if not args.check:
        value = {**{key: source[key] for key in source if key != "cases"},
                 "paraphrase_of": str(SOURCE), "cases": cases}
        OUTPUT.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8", newline="\n")
        print(f"{len(cases)}문항 -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
