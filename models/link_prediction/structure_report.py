"""링크 예측의 과제를 정하기 전에 인용 그래프의 구조를 먼저 재고 기록합니다.

    uv run python -m models.link_prediction.structure_report

처음 설계는 "A가 B를 인용할까"였습니다. 그 과제가 성립하려면 두 논문의 이웃이 겹쳐야 하는데,
이 그래프에서 실제 인용 간선의 공통 이웃이 몇인지는 재 보기 전에는 알 수 없습니다. 재 봤더니
과제를 바꿔야 한다는 결론이 나왔고, 그 숫자를 여기서 다시 뽑을 수 있게 남겨 둡니다.

수집 방식이 "최근 논문을 모으고 그 논문의 참고문헌을 보강"이었으므로, 인용 간선은 거의 전부
최근 논문에서 과거 논문으로 한 방향으로만 뻗습니다. 과거 논문의 참고문헌은 수집하지 않았으니
과거 논문에는 나가는 간선이 없습니다. 그래서 그래프가 별 모양에 가깝고 삼각형이 거의 없습니다.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import random
from pathlib import Path
from typing import Any

from pipeline import snapshot as snapshot_module
from pipeline.snapshot import Snapshot, month_index, month_label

OUTPUT = Path("reports/link_prediction_structure.json")
SAMPLE = 3000
SEED = 20261015
# 참고문헌이 비정상적으로 많은 논문 한 편이 공동인용 쌍 수십만 개를 혼자 만들어 냅니다.
MAX_REFERENCES_FOR_COCITATION = 150


def load_edges(snap: Snapshot) -> list[tuple[str, str]]:
    return [(row["citing"], row["cited"]) for row in snap.rows("references")]


def degree_profile(edges: list[tuple[str, str]]) -> dict[str, Any]:
    """Count how one-directional the harvested citation graph is."""
    out_degree: collections.Counter[str] = collections.Counter()
    in_degree: collections.Counter[str] = collections.Counter()
    for citing, cited in edges:
        out_degree[citing] += 1
        in_degree[cited] += 1
    citing_set, cited_set = set(out_degree), set(in_degree)
    inner = sum(1 for citing, cited in edges if cited in citing_set)
    return {
        "edges": len(edges),
        "papers_with_outgoing_edges": len(citing_set),
        "papers_with_incoming_edges": len(cited_set),
        "papers_on_both_sides": len(citing_set & cited_set),
        "edges_with_both_endpoints_citing": inner,
        "mean_out_degree": round(len(edges) / max(1, len(citing_set)), 3),
        "mean_in_degree": round(len(edges) / max(1, len(cited_set)), 3),
    }


def direct_citation_signal(edges: list[tuple[str, str]], *, sample: int = SAMPLE) -> dict[str, Any]:
    """Measure common neighbours on true citation edges, the feature the first design relied on.

    The edge under test is removed from both neighbourhoods, which is what a link-prediction
    feature would see at inference time.
    """
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    for citing, cited in edges:
        adjacency[citing].add(cited)
        adjacency[cited].add(citing)
    chosen = random.Random(SEED).sample(edges, min(sample, len(edges)))
    counts = [
        len((adjacency[citing] - {cited}) & (adjacency[cited] - {citing}))
        for citing, cited in chosen
    ]
    positive = sum(1 for value in counts if value > 0)
    return {
        "sampled_edges": len(counts),
        "share_with_any_common_neighbour": round(positive / max(1, len(counts)), 4),
        "mean_common_neighbours": round(sum(counts) / max(1, len(counts)), 3),
        "verdict": (
            "공통 이웃이 0인 간선이 대부분이면 그래프 피처에 신호가 없습니다. "
            "직접 인용 예측은 사실상 텍스트 유사도 모델이 됩니다."
        ),
    }


def cocitation_profile(edges: list[tuple[str, str]]) -> dict[str, Any]:
    """Measure the co-citation graph, the alternative the direct-citation numbers point to."""
    references: dict[str, set[str]] = collections.defaultdict(set)
    for citing, cited in edges:
        references[citing].add(cited)
    pairs: set[tuple[str, str]] = set()
    skipped = 0
    for targets in references.values():
        if len(targets) > MAX_REFERENCES_FOR_COCITATION:
            skipped += 1
            continue
        for left, right in itertools.combinations(sorted(targets), 2):
            pairs.add((left, right))
    adjacency: dict[str, set[str]] = collections.defaultdict(set)
    for left, right in pairs:
        adjacency[left].add(right)
        adjacency[right].add(left)
    return {
        "citers": len(references),
        "citers_skipped_as_too_broad": skipped,
        "cocitation_pairs": len(pairs),
        "cocitation_nodes": len(adjacency),
        "mean_cocitation_degree": round(
            2 * len(pairs) / max(1, len(adjacency)), 3
        ),
    }


def citer_months(edges: list[tuple[str, str]]) -> dict[str, Any]:
    """Show how the citing side is distributed in time, which bounds any temporal split."""
    months: collections.Counter[int] = collections.Counter()
    for citing, _ in edges:
        month = month_index(citing)
        if month is not None:
            months[month] += 1
    citers: dict[int, set[str]] = collections.defaultdict(set)
    for citing, _ in edges:
        month = month_index(citing)
        if month is not None:
            citers[month].add(citing)
    return {
        "distinct_months": len(months),
        "first_month": month_label(min(months)),
        "last_month": month_label(max(months)),
        "citers_by_month": {
            month_label(month): len(names)
            for month, names in sorted(citers.items())
            if month >= max(citers) - 11
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="인용 그래프 구조를 측정합니다.")
    parser.add_argument("--version")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    edges = load_edges(snap)
    report = {
        "snapshot_version": snap.version,
        "degree_profile": degree_profile(edges),
        "direct_citation_common_neighbours": direct_citation_signal(edges),
        "cocitation_profile": cocitation_profile(edges),
        "citer_months": citer_months(edges),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
