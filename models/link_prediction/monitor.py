"""배포된 `graph-text-gbm`을 지난달 새로 생긴 공동인용으로 다시 채점합니다(ADR-124).

    python -m models.link_prediction.monitor                  # 지난달
    python -m models.link_prediction.monitor --month 2026-10  # 그 달

**재학습이 아니라 감시입니다.** 학습한 모델은 스냅샷(2026-10-14)에 고정돼 있고, 그래프는
주간 수집으로 자랍니다. 그 사이에 모델이 나빠졌는지를, 승격할 때와 **같은 판정**
(`pipeline.model_registry.judge`)으로 봅니다. 바를 넘지 못하면 보고서에 `below_bar`가 남고
`/api/health`와 배포 점검이 알립니다.

## 학습 평가와 같은 과제, 창만 한 달

| | 학습 평가(`train.py`) | 여기 |
|---|---|---|
| 피처 그래프 | 평가 라벨 달보다 앞선 인용자 | 같음 - 그 달보다 앞선 인용자 |
| 라벨 | `test_cut` 이후 전부 | **그 달** 인용자가 새로 만든 공동인용 |
| 순위 과제 | 앵커 1 + 같은 분야 미끼 9, 그룹 3,000 | 같은 함수(`dataset.ranking_groups`) |
| 비교 | 최고 휴리스틱 대비 쌍체 부트스트랩 | 같은 함수(`train.paired_bootstrap`) |

피처는 `dataset.group_features`가 만들어 학습과 열 순서까지 같습니다. 모델은 앱이 읽는 파일
(`models/artifacts/`)을 지문을 맞춰 읽습니다(`src.forecast.load_model`).

## 살아 있는 그래프를 읽어도 되는 이유

학습은 스냅샷만 읽습니다 - S2 보강이 과거 쌍에 간선을 역채움해 학습 음성이 평가 양성으로 바뀌기
때문입니다(`pipeline/snapshot.py`). 여기는 학습하지 않습니다. 그 달의 라벨과 그보다 앞선 피처를
**같은 시점의 그래프**에서 함께 읽으므로, 역채움은 둘에 똑같이 들어갑니다.

**그래프에는 읽기만 합니다**(읽기 전용 세션). 임베딩은 순위 과제에 나온 논문 것만 받습니다 -
전부 받으면 파이썬 객체로 800MB가 넘습니다.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import random
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np

from models.link_prediction import dataset as dataset_module
from models.link_prediction import metrics as metrics_module
from models.link_prediction.baselines import ladder
from models.link_prediction.train import paired_bootstrap, parse_month
from pipeline.snapshot import month_index, month_label

REPORTS = Path("pipeline/state/reports")
LAST_RUN = Path("pipeline/state/last-forecast-monitor.json")
# 그룹이 이보다 적으면 판정하지 않습니다. 구간이 넓어 "바 아래"가 변동인지 하락인지
# 가를 수 없습니다.
MIN_GROUPS = 300
MAX_POSITIVES = 30000
EMBEDDING_BATCH = 500


def previous_month(now: dt.datetime) -> int:
    """지난달의 월 번호. 이번 달은 아직 인용자가 다 들어오지 않았습니다."""
    first = now.date().replace(day=1)
    last = first - dt.timedelta(days=1)
    return last.year * 12 + last.month - 1


def feature_adjacency(
    references: dict[str, set[str]], citers: Iterable[str]
) -> dict[str, set[str]]:
    """`adjacency_of(cocitation_pairs(...))`와 같은 결과를, 쌍 집합 없이 바로 만듭니다.

    VM 그래프의 공동인용 쌍은 200만 개 가까이 됩니다. 쌍 집합과 인접 목록을 함께 들면 메모리가
    두 배라서, 인접 목록만 만듭니다. 같다는 것은 테스트가 봅니다.
    """
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    for citer in citers:
        targets = references.get(citer)
        if not targets or len(targets) > dataset_module.MAX_REFERENCES_FOR_COCITATION:
            continue
        ordered = sorted(targets)
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                adjacency[left].add(right)
                adjacency[right].add(left)
    return adjacency


def new_labels(
    references: dict[str, set[str]], citers: Iterable[str], feature: dict[str, set[str]]
) -> set[tuple[str, str]]:
    """그 달 인용자가 만든 공동인용 중 피처 그래프에 아직 없던 쌍. `build_windows`와 같은 정의."""
    labels = dataset_module.cocitation_pairs(references, set(citers))
    return {(left, right) for left, right in labels if right not in feature.get(left, ())}


class LiveTable:
    """`dataset.PaperTable`과 같은 모양. 임베딩은 필요한 논문 것만 나중에 채웁니다."""

    def __init__(self, papers: Iterable[dict[str, Any]]) -> None:
        self.position: dict[str, int] = {}
        self.primary: dict[str, str] = {}
        self.categories: dict[str, frozenset[str]] = {}
        self.month: dict[str, int | None] = {}
        for row in papers:
            arxiv_id = row["arxiv_id"]
            self.position[arxiv_id] = len(self.position)
            self.primary[arxiv_id] = row.get("primary_category") or ""
            self.categories[arxiv_id] = frozenset(row.get("categories") or ())
            self.month[arxiv_id] = month_index(arxiv_id)
        self.unit: dict[str, np.ndarray] = {}

    def add_vectors(self, rows: Iterable[tuple[str, list[float]]]) -> None:
        for arxiv_id, vector in rows:
            array = np.asarray(vector, dtype=np.float32)
            norm = float(np.linalg.norm(array))
            self.unit[arxiv_id] = array / norm if norm else array

    def cosine(self, left: str, right: str) -> float:
        first, second = self.unit.get(left), self.unit.get(right)
        if first is None or second is None:
            return 0.0
        return float(np.dot(first, second))


def score(
    references: dict[str, set[str]],
    table: LiveTable,
    month: int,
    load_vectors,
    model: Any,
    *,
    seed: int = dataset_module.SEED,
) -> dict[str, Any]:
    """그 달 라벨로 순위 과제를 만들고, 배포 모델과 휴리스틱을 같은 그룹에서 채점합니다."""
    dated = {citer: m for citer in references if (m := month_index(citer)) is not None}
    before = [citer for citer, m in dated.items() if m < month]
    during = [citer for citer, m in dated.items() if m == month]
    feature = feature_adjacency(references, before)
    labels = new_labels(references, during, feature)
    indegree = dataset_module.citation_indegree(references, set(before))

    allowed = set(dataset_module.eligible_nodes(feature, table))
    positives = dataset_module.usable_pairs(labels, allowed)
    counts = {
        "citers_before": len(before),
        "citers_in_month": len(during),
        "labels_new": len(labels),
        "labels_usable": len(positives),
    }
    if len(positives) > MAX_POSITIVES:
        positives = random.Random(seed).sample(positives, MAX_POSITIVES)
    forbidden = set(labels)
    forbidden.update(
        (left, right)
        for left, neighbours in feature.items()
        for right in neighbours
        if left < right
    )
    groups = dataset_module.ranking_groups(
        positives, feature, forbidden, table,
        candidates=dataset_module.RANKING_CANDIDATES, seed=seed + 11,
        limit=dataset_module.RANKING_GROUPS,
    )
    counts["groups"] = len(groups)
    if len(groups) < MIN_GROUPS:
        return {"counts": counts, "results": []}

    needed = sorted({group["anchor"] for group in groups}
                    | {group["partner"] for group in groups}
                    | {decoy for group in groups for decoy in group["decoys"]})
    table.add_vectors(load_vectors(needed))
    features = [dataset_module.group_features(group, feature, indegree, table) for group in groups]

    results: list[dict[str, Any]] = []
    ranks: dict[str, np.ndarray] = {}
    # 휴리스틱(학습하지 않는 칸)과 배포 모델만 채점합니다.
    # 사다리의 다른 학습 칸은 가중치가 없습니다.
    heuristic_rungs = [rung for rung in ladder() if rung.describe().get("kind") == "heuristic"]
    for rung in [*heuristic_rungs, model]:
        scored = [rung.score(matrix) for matrix in features]
        results.append({
            "model": rung.name,
            "detail": rung.describe(),
            "ranking_hard_negatives": metrics_module.ranking_metrics(scored),
        })
        ranks[rung.name] = metrics_module.ranking_ranks(scored)
    heuristics = [row["model"] for row in results if row["detail"].get("kind") == "heuristic"]
    best = max(heuristics, key=lambda name: (1.0 / ranks[name]).mean())
    for row in results:
        if row["detail"].get("kind") != "heuristic":
            row["mrr_vs_best_heuristic"] = {
                "baseline": best, **paired_bootstrap(ranks[best], ranks[row["model"]]),
            }
    return {"counts": counts, "results": results}


def read_graph(driver) -> tuple[dict[str, set[str]], list[dict[str, Any]]]:
    """인용 간선과 임베딩이 있는 논문의 분류. 둘 다 작습니다(간선 15만, 논문 4만여 행)."""
    from neo4j import READ_ACCESS

    references: dict[str, set[str]] = collections.defaultdict(set)
    database = os.getenv("NEO4J_DATABASE", "neo4j")
    with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        for record in session.run(
            "MATCH (c:ArxivPaper)-[:REFERENCES]->(t:ArxivPaper) "
            "RETURN c.arxiv_id AS citing, t.arxiv_id AS cited"
        ):
            references[record["citing"]].add(record["cited"])
        papers = [dict(record) for record in session.run(
            "MATCH (p:ArxivPaper) WHERE p.embedding IS NOT NULL "
            "RETURN p.arxiv_id AS arxiv_id, p.primary_category AS primary_category, "
            "p.categories AS categories ORDER BY p.arxiv_id"
        )]
    return references, papers


def vector_loader(driver):
    from neo4j import READ_ACCESS

    database = os.getenv("NEO4J_DATABASE", "neo4j")

    def load(ids: list[str]) -> Iterable[tuple[str, list[float]]]:
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            for start in range(0, len(ids), EMBEDDING_BATCH):
                batch = ids[start:start + EMBEDDING_BATCH]
                for record in session.run(
                    "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids "
                    "RETURN p.arxiv_id AS id, p.embedding AS v", ids=batch,
                ):
                    yield record["id"], record["v"]

    return load


def verdict(scored: dict[str, Any], card: dict[str, Any]) -> dict[str, Any]:
    """승격 판정과 같은 함수로 봅니다. 그룹이 모자라면 판정하지 않습니다."""
    from pipeline import model_registry

    if not scored["results"]:
        return {"status": "insufficient", "reasons": [f"순위 그룹 {scored['counts']['groups']}개 "
                                                       f"< {MIN_GROUPS}"]}
    judged = model_registry.judge({"results": scored["results"]})
    return {"status": "ok" if judged["passed"] else "below_bar", "reasons": judged["reasons"],
            "observed": judged.get("observed")}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="배포된 공동인용 예측 모델을 지난달 라벨로 다시 잽니다.")
    parser.add_argument("--month", type=parse_month, help="YYYY-MM. 비우면 지난달입니다.")
    parser.add_argument("--output", type=Path,
                        help="보고서 경로. 비우면 pipeline/state/reports/ 아래")
    args = parser.parse_args()

    from neo4j import GraphDatabase

    from src.forecast import load_model

    now = dt.datetime.now(dt.UTC)
    month = args.month if args.month is not None else previous_month(now)
    output = args.output or REPORTS / f"forecast-monitor-{month_label(month)}.json"
    model, card = load_model()
    driver = GraphDatabase.driver(
        os.environ["NEO4J_URI"],
        auth=(os.getenv("NEO4J_USERNAME") or os.environ["NEO4J_USER"],
              os.environ["NEO4J_PASSWORD"]),
    )
    try:
        references, papers = read_graph(driver)
        scored = score(references, LiveTable(papers), month, vector_loader(driver), model)
    finally:
        driver.close()
    judged = verdict(scored, card)
    report = {
        "generated_at": now.isoformat(),
        "month": month_label(month),
        "model": {key: card.get(key) for key in ("rung", "digest", "snapshot_version")},
        **judged,
        **scored,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"month": report["month"], "status": judged["status"],
                      "reasons": judged["reasons"], "counts": scored["counts"]},
                     ensure_ascii=False))
    print(f"보고서: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
