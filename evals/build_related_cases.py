"""관련 논문 슬롯의 정답을 만듭니다. 정답은 스냅샷의 공동인용이 정합니다.

    uv run python -m evals.build_related_cases
    uv run python -m evals.build_related_cases --verify-against-db

**왜 이 파일이 필요한가.** 앱에서 자체 학습 모델이 들어갈 수 있는 자리는 `rank_related_papers`
하나입니다(ADR-013의 선형 어댑터, ADR-011의 링크 예측이 모두 "시드 논문 → 관련 논문" 과제).
그런데 그 자리는 **평가가 전혀 재지 않는 자리**였습니다 — 30건 중 `expected_related_ids`가 있는
사례가 0건이고, 보고서의 `related_retrieval_recall`은 전부 `None`이며, 기준선에 `related`라는
낱말이 한 번도 나오지 않습니다. 게이트가 보지 않는 자리에는 "통과 시 승격, 실패 시 롤백"을
걸 수 없습니다.

**에이전트를 통과시켜 재지 않습니다.** 어떤 논문을 시드로 넣을지는 모델이 그때그때 정합니다 —
실측에서 `citing-01`은 기준 논문을 시드로 썼고(정답 5건이 전부 `related_ids`에 나타남)
`citing-03`은 답변 논문을 시드로 썼습니다(기준 논문이 `related_ids`에 나타남). 에이전트를
거치면 순위가 아니라 **모델의 시드 선택**을 재게 됩니다. 관련 논문은 답변 경로가 아니라 표시
패널이고, PPR과 어댑터 모두 `시드 → 순위` 함수이므로 그 함수를 직접 잽니다.

**"관련"의 정의는 공동인용입니다.** [ADR-011](../docs/adr/ADR-011-cocitation-link-prediction.md)이
링크 예측을 직접 인용에서 공동인용으로 바꾼 것과 같은 이유입니다. 직접 인용은 Cypher 한 줄로
정확히 답해지므로 모델이 겨룰 과제가 아니고, 공동인용은 공통 이웃 평균이 59.4로 신호가
있습니다. 도구 자신의 설명도 "같이 읽히는 논문"입니다.

**정답 크기를 묶습니다.** 공동 인용자 1명만 요구하면 정답이 중앙값 35편, 최대 5,928편이 되어
`topic` 가족과 같은 함정에 빠집니다(k=5 도달 상한 0.012). 공동 인용자 **2명 이상**을 요구하고
정답이 2~10편인 시드만 남기면 3,740개가 적격이고 정답 크기 중앙값이 4편입니다.

**어댑터가 점수를 줄 수 있는 범위로 한정합니다.** 시드와 정답을 모두 임베딩이 있는 37,854편
안에서만 고릅니다. PPR은 인용 그래프 149k 노드 전체를 보지만 어댑터는 임베딩 행렬만 보므로,
한정하지 않으면 어댑터가 구조적으로 못 맞히는 정답이 섞여 비교가 불공정해집니다. ADR-011이
cold-start 74%를 떼어 낸 것과 같은 규율입니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
from pathlib import Path
from typing import Any

from pipeline import features
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import Snapshot

OUTPUT = Path("evals/cases_related.json")
# 공동 인용자가 한 명이면 우연일 수 있습니다. 둘을 요구하면 정답 크기가 중앙값 35 -> 4로
# 내려가고, 그래야 recall@5가 움직입니다.
MIN_COCITERS = 2
MIN_GOLD = 2
MAX_GOLD = 10
# 0이면 **적격 시드 전부**입니다. 처음에는 300을 뽑았는데, 뽑을 이유가 없었습니다 - 이
# 과제는 LLM을 부르지 않아 전수가 공짜이고(PPR 3,740 시드에 11분), 표본을 쓰면 구간에
# 표본추출 변동이 섞입니다(ADR-034).
CASES = 0
SEED = 20261018

# DB 대조용. 저장한 정답과 **같은 뜻**이어야 하므로 임베딩 한정까지 적습니다 - 임베딩이 없는
# 논문은 어댑터가 점수를 줄 수 없어 애초에 정답에서 뺐습니다.
COCITATION_CYPHER = (
    "MATCH (s:ArxivPaper {arxiv_id: $arxiv_id})<-[:REFERENCES]-(c:ArxivPaper)"
    "-[:REFERENCES]->(p:ArxivPaper) "
    "WHERE p <> s AND p.embedding IS NOT NULL "
    "WITH p, count(DISTINCT c) AS shared_citers "
    "WHERE shared_citers >= $min_cociters "
    "RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id"
)


def load_tables(snap: Snapshot) -> dict[str, Any]:
    cache = features.Cache(snap)
    meta = {row["arxiv_id"]: row for row in cache.meta()}
    citing_of: dict[str, list[str]] = collections.defaultdict(list)
    cited_of: dict[str, list[str]] = collections.defaultdict(list)
    for row in snap.rows("references"):
        citing_of[row["cited"]].append(row["citing"])
        cited_of[row["citing"]].append(row["cited"])
    return {"meta": meta, "citing_of": citing_of, "cited_of": cited_of}


def cocited(seed: str, tables: dict[str, Any]) -> collections.Counter:
    """How many distinct papers cite the seed and each other paper together."""
    embedded = tables["meta"]
    partners: collections.Counter = collections.Counter()
    for citer in set(tables["citing_of"].get(seed, ())):
        for paper in tables["cited_of"].get(citer, ()):
            if paper != seed and paper in embedded:
                partners[paper] += 1
    return partners


def eligible(tables: dict[str, Any]) -> list[tuple[str, list[str]]]:
    """Seeds whose co-citation gold set is small enough for recall@5 to move."""
    picks = []
    for seed in sorted(tables["citing_of"]):
        if seed not in tables["meta"]:
            continue
        partners = cocited(seed, tables)
        gold = sorted(
            paper for paper, shared in partners.items() if shared >= MIN_COCITERS
        )
        if MIN_GOLD <= len(gold) <= MAX_GOLD:
            picks.append((seed, gold))
    return picks


def build(snap: Snapshot, *, count: int = CASES) -> dict[str, Any]:
    tables = load_tables(snap)
    picks = eligible(tables)
    chosen = (
        picks if not count or len(picks) <= count
        else random.Random(SEED).sample(picks, count)
    )
    meta = tables["meta"]
    cases = []
    for index, (seed, gold) in enumerate(sorted(chosen), start=1):
        partners = cocited(seed, tables)
        cases.append({
            # 네 자리입니다. 세 자리면 `related-1000`이 `related-999` **앞**에 와서
            # 사전순과 번호순이 어긋납니다(3,740건으로 늘리고 드러났습니다).
            "id": f"related-{index:04d}",
            "category": "related",
            # 질문 문장이 없습니다. 이 과제는 에이전트를 거치지 않고 `시드 -> 순위` 함수를
            # 직접 재므로 시드 자체가 입력입니다.
            "seed_id": seed,
            "seed_title": " ".join((meta[seed].get("title") or "").split()),
            "expected_related_ids": gold,
            # 공동 인용자 수. 순위를 매기는 변형을 nDCG 같은 지표로 재고 싶을 때 필요합니다.
            "shared_citers": {paper: partners[paper] for paper in gold},
            "gold_size": len(gold),
            # `cypher`와 `label_method`는 **데이터셋 수준**에 한 번 적습니다. 사례마다
            # 반복하면 3,740건에서 파일의 49%가 같은 문자열이 됩니다(2.2MB 중 1.1MB).
            "cypher_params": {"arxiv_id": seed, "min_cociters": MIN_COCITERS},
        })
    sizes = [case["gold_size"] for case in cases]
    return {
        # 2: 사례별 `cypher`·`label_method`를 데이터셋 수준으로 올리고 id를 네 자리로
        # 바꿨습니다. 정답 자체(`seed_id`·`expected_related_ids`)는 그대로입니다.
        "dataset_version": 2,
        "snapshot_version": snap.version,
        "snapshot_cut_month": snap.manifest["cut"]["month"],
        "source": f"snapshots/{snap.version}/manifest.json",
        "label_method": "snapshot_cocitation",
        # DB 대조용 조회. 사례는 `cypher_params`만 들고 있습니다.
        "cypher": COCITATION_CYPHER,
        "task": (
            "시드 논문 하나를 받아 공동인용 관계가 깊은 논문을 순위로 돌려주는 과제입니다. "
            "앱에서는 rank_related_papers(개인화 PageRank)가 이 자리를 맡고 있고, "
            "ADR-013의 선형 어댑터와 ADR-011의 링크 예측이 같은 자리를 겨룹니다."
        ),
        "note": (
            f"정답은 공동 인용자 {MIN_COCITERS}명 이상인 논문이고, 정답이 "
            f"{MIN_GOLD}~{MAX_GOLD}편인 시드만 골랐습니다(1명만 요구하면 중앙값 35편, "
            "최대 5,928편이 되어 recall@5가 움직이지 않습니다). 시드와 정답 모두 임베딩이 "
            "있는 논문으로 한정해 어댑터와 PPR을 같은 범위에서 비교합니다. "
            f"{'적격 시드 전부입니다' if not CASES else '무작위 추출입니다'} - "
            "LLM을 부르지 않는 과제라 전수가 공짜이고, 표본을 쓰면 구간에 표본추출 변동이 "
            "섞입니다(ADR-034)."
        ),
        "eligible_seeds": len(picks),
        "gold_size_median": statistics.median(sizes) if sizes else 0,
        "cases": cases,
    }


def verify_against_db(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    """Run each recorded Cypher on the live DB and compare it to the stored gold set."""
    from src import chatbot

    problems = []
    for case in dataset["cases"]:
        rows = chatbot.run_read(dataset["cypher"], case["cypher_params"])
        found = sorted(row["arxiv_id"] for row in rows)
        if found != case["expected_related_ids"]:
            problems.append({
                "id": case["id"],
                "stored": case["expected_related_ids"],
                "db": found,
                "only_in_db": sorted(set(found) - set(case["expected_related_ids"])),
                "only_in_stored": sorted(set(case["expected_related_ids"]) - set(found)),
            })
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="관련 논문 과제의 정답을 만듭니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷을 씁니다.")
    parser.add_argument("--count", type=int, default=CASES,
                        help="0이면 적격 시드 전부입니다. 양수면 그만큼 무작위 추출합니다.")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--verify-against-db", action="store_true")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    dataset = build(snap, count=args.count)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    sizes = [case["gold_size"] for case in dataset["cases"]]
    print(json.dumps({
        "output": str(args.output).replace("\\", "/"),
        "snapshot": dataset["snapshot_version"],
        "eligible_seeds": dataset["eligible_seeds"],
        "cases": len(dataset["cases"]),
        "gold_size": {
            "min": min(sizes), "median": statistics.median(sizes), "max": max(sizes),
            "mean": round(statistics.fmean(sizes), 2),
        },
    }, ensure_ascii=False, indent=2))

    if args.verify_against_db:
        problems = verify_against_db(dataset)
        print(f"DB 대조: 사례 {len(dataset['cases'])}개, 불일치 {len(problems)}건")
        for problem in problems[:5]:
            print(json.dumps(problem, ensure_ascii=False))
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
