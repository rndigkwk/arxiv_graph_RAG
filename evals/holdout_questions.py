"""심판 판정용 **보류 질문**을 그래프에서 기계로 생성합니다(ADR-059).

    uv run python -m evals.holdout_questions --output evals/judge_holdout.json

[ADR-058](../docs/adr/ADR-058-the-independent-judge-only-means-something-with-labels.md)이
공급자를 교차해 `requires_content`가 공급자 의존이라는 것을 쟀습니다. 그런데 그 61문항은
**프롬프트를 맞춰 넣은 바로 그 집합**입니다. 그래서 `gpt-6-luna`의 `61/61`이 프롬프트의
성질인지 튜닝 집합의 성질인지 아직 구별되지 않습니다.

이 생성기가 그 구별을 가능하게 합니다. 라벨 규칙이 **질문 문장에서 기계적으로** 나오므로
(`judge_study.labelled_questions`) 사람 라벨 없이 보류 집합을 만들 수 있습니다 - 라벨이 사람
판단이면 이 과제는 비용에서 막힙니다.

**이것은 평가 사례가 아닙니다.** 스냅샷 Cypher로 센 정답(`expected_ids`)이 없고 심판 판정의
라벨만 있습니다. 회귀 게이트에 쓰면 안 됩니다 - 그래서 파일 이름이 `cases*.json`이 아니고,
매니페스트에도 적습니다.

질문의 **모양**은 기존 사례의 템플릿을 그대로 씁니다. 모양이 다른 질문을 만들면 다른 것을 재게
됩니다. 템플릿이 사례 파일의 문장과 갈라지지 않는지는 테스트가 확인합니다.

판정용 Cypher는 **`evals/cases_graph.json`에서 읽습니다.** 여기에 베껴 두면 그 목록이 조용히
늙습니다 - 이 저장소에서 이미 아홉 번 그랬습니다.
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

GRAPH_CASES = Path("evals/cases_graph.json")
BASE_CASES = Path("evals/cases.json")
OUTPUT = Path("evals/judge_holdout.json")

# 씨앗을 고정합니다. 고정하지 않으면 "다시 돌려 보니 괜찮았다"가 가능해집니다.
SEED = 20261022
# 거부를 감당할 만큼 넉넉히 뽑습니다.
OVERSAMPLE = 6
# 짧은 제목은 주제 구절로 읽힙니다(`Graph Neural Networks`). 그러면 라벨이 의심스러워집니다.
MIN_TITLE_WORDS = 5
# 진단 2/3, 최종 확인 1/3. 최종 조각은 프롬프트를 고치는 동안 보지 않습니다.
FINAL_SHARE = 1 / 3
TARGETS = {
    # `expect_content=True`인 가족입니다. ADR-058의 실패가 여기서 났으므로 가장 큽니다.
    "exact-title": 60,
    "author": 12,
    "citing": 12,
    "coauthor": 12,
    "reference": 12,
    "topcited": 12,
}
# `topcited`의 N입니다. 튜닝 집합이 쓴 (분류, N) 쌍은 거부합니다.
TOPCITED_LIMITS = (3, 5, 10)

# 질문의 모양입니다. 사례 파일의 문장과 갈라지지 않는지 테스트가 확인합니다.
TEMPLATES = {
    "exact-title": "Find the paper titled: {title}",
    "author": "Which papers in this collection list an author named {name}?",
    "citing": "Which papers in this collection cite the paper titled {title}?",
    "coauthor": (
        "Which other papers in this collection share an author with {title}?"
    ),
    "reference": (
        "Among the papers cited by {title}, which ones have primary category "
        "{category}?"
    ),
    "topcited": (
        "Within primary category {category}, which {limit} papers are cited most "
        "often by other papers in this collection?"
    ),
}

# 라벨입니다. 제목·주제만 묻는 질문은 내용 조건, 저자·인용·분류·집계만 묻는 질문은 그래프
# 조건입니다(`judge_study`의 머리말과 같은 규칙).
LABELS = {
    "exact-title": (True, False),
    "author": (False, True),
    "citing": (False, True),
    "coauthor": (False, True),
    "reference": (False, True),
    "topcited": (False, True),
}
WHY = {
    True: "정확한 제목만 묻습니다. 그래프 조건이 없습니다.",
    False: (
        "질문의 제목·이름·분류는 관계의 상대편을 가리킵니다. 돌려줄 논문의 주제 조건이 "
        "아닙니다."
    ),
}

# 후보를 뽑는 질의입니다. `reverse(arxiv_id)`로 정렬하는 이유는 `arxiv_id` 순서가 **발표
# 시점 순서**이기 때문입니다 - 그대로 자르면 보류 집합이 오래된 논문으로 쏠립니다. 뒤집으면
# 뒷자리가 먼저 와서 시점과 상관이 끊기고, 그래도 결정적입니다.
CANDIDATES = {
    "exact-title": (
        "MATCH (p:ArxivPaper) "
        "WHERE p.title IS NOT NULL AND size(split(p.title, ' ')) >= $min_words "
        "RETURN p.arxiv_id AS arxiv_id, p.title AS title "
        "ORDER BY reverse(p.arxiv_id) LIMIT $limit"
    ),
    "author": (
        "MATCH (a:ArxivAuthorName)-[:AUTHORED]->(p:ArxivPaper) "
        "WITH a.name AS name, count(p) AS papers "
        "WHERE papers >= 2 "
        "RETURN name, papers ORDER BY reverse(name) LIMIT $limit"
    ),
    "citing": (
        "MATCH (:ArxivPaper)-[:REFERENCES]->(q:ArxivPaper) "
        "WITH q, count(*) AS citations "
        "WHERE citations >= 2 AND q.title IS NOT NULL "
        "  AND size(split(q.title, ' ')) >= $min_words "
        "RETURN q.arxiv_id AS arxiv_id, q.title AS title, citations "
        "ORDER BY reverse(q.arxiv_id) LIMIT $limit"
    ),
    "coauthor": (
        "MATCH (p:ArxivPaper)<-[:AUTHORED]-(:ArxivAuthorName)-[:AUTHORED]->"
        "(other:ArxivPaper) "
        "WHERE p <> other AND p.title IS NOT NULL "
        "  AND size(split(p.title, ' ')) >= $min_words "
        "WITH p, count(DISTINCT other) AS siblings "
        "WHERE siblings >= 2 "
        "RETURN p.arxiv_id AS arxiv_id, p.title AS title, siblings "
        "ORDER BY reverse(p.arxiv_id) LIMIT $limit"
    ),
    "reference": (
        "MATCH (p:ArxivPaper)-[:REFERENCES]->(q:ArxivPaper) "
        "WHERE q.primary_category IS NOT NULL AND p.title IS NOT NULL "
        "  AND size(split(p.title, ' ')) >= $min_words "
        "WITH p, q.primary_category AS category, count(*) AS hits "
        "WHERE hits >= 1 "
        "RETURN p.arxiv_id AS arxiv_id, p.title AS title, category, hits "
        "ORDER BY reverse(p.arxiv_id) LIMIT $limit"
    ),
    "topcited": (
        "MATCH (p:ArxivPaper) WHERE p.primary_category IS NOT NULL "
        "WITH p.primary_category AS category, count(*) AS papers "
        "WHERE papers >= 100 "
        "RETURN category, papers ORDER BY category"
    ),
}


def cypher_by_family(path: Path) -> dict[str, str]:
    """판정용 Cypher를 사례 파일에서 읽습니다. 여기 베껴 두면 조용히 늙습니다."""
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    found: dict[str, str] = {}
    for case in cases:
        family, query = case["category"], case["cypher"]
        if found.setdefault(family, query) != query:
            raise SystemExit(
                f"{family} 가족의 Cypher가 사례마다 다릅니다. 하나를 고를 수 없으므로 "
                "생성기를 손으로 고쳐야 합니다."
            )
    return found


def tuning_set(graph_cases: Path, base_cases: Path) -> dict[str, Any]:
    """튜닝 집합이 이미 쓴 것을 모읍니다. 겹치면 보류가 아닙니다."""
    base = json.loads(base_cases.read_text(encoding="utf-8-sig"))
    base_list = base["cases"] if isinstance(base, dict) else base
    graph = json.loads(graph_cases.read_text(encoding="utf-8"))["cases"]
    used: dict[str, Any] = {
        "arxiv_ids": set(), "names": set(), "topcited": set(), "topics": [],
        "titles": set(),
    }
    for case in base_list + graph:
        used["arxiv_ids"].update(case["expected_ids"])
        params = case.get("cypher_params", {})
        if "arxiv_id" in params:
            used["arxiv_ids"].add(params["arxiv_id"])
        if "name" in params:
            used["names"].add(params["name"])
        if "category" in params and "limit" in params:
            used["topcited"].add((params["category"], params["limit"]))
        if case["category"] == "topic":
            # 주제 구절을 질문에서 꺼냅니다. 제목이 이 구절을 품으면 같은 문장이 두 가족의
            # 라벨을 동시에 가집니다.
            phrase = re.sub(r"^Find arXiv papers about\s+", "", case["question"])
            used["topics"].append(phrase.rstrip(".").strip().lower())
        if case["category"] == "exact-title":
            used["titles"].add(
                case["question"].replace("Find the paper titled:", "").strip().lower()
            )
    return used


def title_is_safe(title: str, used: dict[str, Any]) -> str | None:
    """제목을 거부할 이유를 돌려줍니다. 없으면 `None`입니다."""
    text = (title or "").strip()
    if len(text.split()) < MIN_TITLE_WORDS:
        return "짧은 제목"
    lowered = text.lower()
    if lowered in used["titles"]:
        return "튜닝 집합의 제목"
    for phrase in used["topics"]:
        if phrase and (phrase in lowered or lowered in phrase):
            return "주제 구절과 겹침"
    return None


def build(run_read, used: dict[str, Any], cyphers: dict[str, str],
          targets: dict[str, int]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """가족별로 목표 개수를 채웁니다. 거부한 이유와 개수를 함께 돌려줍니다."""
    rng = random.Random(SEED)
    rejected: dict[str, int] = {}
    questions: list[dict[str, Any]] = []

    def reject(reason: str) -> None:
        rejected[reason] = rejected.get(reason, 0) + 1

    def papers_for(family: str, params: dict[str, Any]) -> list[str]:
        """사례와 같은 Cypher로 붙일 논문을 찾습니다. 비면 질문이 공허합니다."""
        rows = run_read(cyphers[family], params)
        return [row["arxiv_id"] for row in rows][:2]

    for family, want in targets.items():
        expect_content, expect_graph = LABELS[family]
        template = TEMPLATES[family]
        if family == "topcited":
            rows = run_read(CANDIDATES[family], {})
            pool = [
                (row["category"], limit)
                for row in rows for limit in TOPCITED_LIMITS
            ]
            rng.shuffle(pool)
            picked = 0
            for category, limit in pool:
                if picked >= want:
                    break
                if (category, limit) in used["topcited"]:
                    reject("튜닝 집합의 (분류, N)")
                    continue
                params = {"category": category, "limit": limit}
                papers = papers_for(family, params)
                if not papers:
                    reject("집계 결과 없음")
                    continue
                picked += 1
                questions.append({
                    "id": f"holdout-{family}-{picked:02d}",
                    "family": family,
                    "question": template.format(category=category, limit=limit),
                    "paper_ids": papers,
                    "expect_content": expect_content,
                    "expect_graph": expect_graph,
                    "why": WHY[expect_content],
                })
            continue

        rows = run_read(CANDIDATES[family],
                        {"min_words": MIN_TITLE_WORDS, "limit": want * OVERSAMPLE})
        rng.shuffle(rows)
        picked = 0
        for row in rows:
            if picked >= want:
                break
            if family == "author":
                name = row["name"]
                if name in used["names"]:
                    reject("튜닝 집합의 저자")
                    continue
                if len(name.split()) < 2:
                    # 한 토큰 이름은 이름인지 단어인지 갈립니다.
                    reject("한 토큰 이름")
                    continue
                params: dict[str, Any] = {"name": name}
                text = template.format(name=name)
            else:
                if row["arxiv_id"] in used["arxiv_ids"]:
                    reject("튜닝 집합의 논문")
                    continue
                reason = title_is_safe(row["title"], used)
                if reason:
                    reject(reason)
                    continue
                params = {"arxiv_id": row["arxiv_id"]}
                if family == "reference":
                    params["category"] = row["category"]
                    text = template.format(title=row["title"],
                                           category=row["category"])
                else:
                    text = template.format(title=row["title"])

            if family == "exact-title":
                # 제목으로 그 논문 하나를 찾는 질문입니다. 붙일 논문은 그 논문입니다.
                papers = [row["arxiv_id"]]
            else:
                papers = papers_for(family, params)
                if not papers:
                    # 관계가 실제로 없으면 심판이 보는 것이 달라집니다.
                    reject("관계 없음")
                    continue
            picked += 1
            questions.append({
                "id": f"holdout-{family}-{picked:02d}",
                "family": family,
                "question": text,
                "paper_ids": papers,
                "expect_content": expect_content,
                "expect_graph": expect_graph,
                "why": WHY[expect_content],
            })
        if picked < want:
            # 못 채운 것을 조용히 넘기지 않습니다. 집합이 기울었다는 뜻입니다.
            reject(f"{family} 목표 미달({picked}/{want})")
    return questions, rejected


def split(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """가족별로 진단/최종을 나눕니다.

    전체를 한 번에 자르면 가족 비율이 두 조각에서 달라지고, 그러면 두 조각의 정확도를
    비교할 수 없습니다.
    """
    rng = random.Random(SEED + 1)
    by_family: dict[str, list[dict[str, Any]]] = {}
    for item in questions:
        by_family.setdefault(item["family"], []).append(item)
    for family, items in by_family.items():
        order = list(items)
        rng.shuffle(order)
        final = max(1, round(len(order) * FINAL_SHARE)) if order else 0
        for index, item in enumerate(order):
            item["split"] = "final" if index < final else "diagnostic"
    return questions


def main() -> int:
    parser = argparse.ArgumentParser(
        description="심판 판정용 보류 질문을 그래프에서 생성합니다(ADR-059)."
    )
    parser.add_argument("--graph-cases", type=Path, default=GRAPH_CASES)
    parser.add_argument("--base-cases", type=Path, default=BASE_CASES)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    import chatbot

    used = tuning_set(args.graph_cases, args.base_cases)
    cyphers = cypher_by_family(args.graph_cases)
    questions, rejected = build(chatbot.run_read, used, cyphers, TARGETS)
    split(questions)

    counts: dict[str, dict[str, int]] = {}
    for item in questions:
        row = counts.setdefault(item["family"], {"diagnostic": 0, "final": 0})
        row[item["split"]] += 1

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "seed": SEED,
        "purpose": (
            "심판 판정(requires_content·requires_graph)의 보류 집합입니다. 정답 "
            "expected_ids가 없으므로 **회귀 게이트에 쓸 수 없습니다**(ADR-059)."
        ),
        "targets": TARGETS,
        "generated": counts,
        "rejected": dict(sorted(rejected.items())),
        "tuning_set": {
            "arxiv_ids": len(used["arxiv_ids"]),
            "names": len(used["names"]),
            "topcited_pairs": len(used["topcited"]),
            "topics": len(used["topics"]),
        },
        "questions": questions,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: report[key] for key in
                      ("generated", "rejected", "targets")},
                     ensure_ascii=False, indent=2))
    print(f"질문 {len(questions)}개 -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
