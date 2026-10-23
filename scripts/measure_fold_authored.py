"""AUTHORED를 접으면 질의가 얼마나 느려지는지 잽니다(ADR-063).

    uv run python scripts/measure_fold_authored.py --output reports/fold_authored.json

[ADR-045](../docs/adr/ADR-045-what-to-delete-before-adding-a-month.md)가 관계를 지울 가장 큰
후보(AUTHORED를 논문 속성 배열로 접기, 관계 −268,928)만 판정을 미뤘습니다 — *"그 지연은
DB에서만 잴 수 있습니다."* 그 측정입니다.

## 쓰기를 하지 않습니다

`p.authors`가 아직 없으므로 접은 모양을 그대로 돌릴 수 없습니다. 대신 **이미 있는 리스트
속성** `p.categories`로 **같은 일의 모양**을 돌립니다 — 전수 주사 + 리스트 교집합.

## 행을 맞춥니다 — 그러지 않으면 다른 것을 재게 됩니다

첫 판에서 흔한 분류(`cs.AI`)를 썼더니 대리가 **21,183행**을 돌려줬습니다. 진짜 질의는 2~3행
입니다. 그 측정에는 주사 비용에 **21,183행을 돌려주고 정렬하는 비용**이 섞여 있었습니다 —
접은 뒤에는 일어나지 않는 일입니다.

그래서 **시드마다 행 수가 같아지는 드문 분류를 골라** 돌립니다(`rare_categories`). 흔한
분류 쪽도 함께 적지만 **판정은 행을 맞춘 쪽으로만** 합니다.

## `EXPLAIN`도 함께 적습니다

시간은 환경에 흔들립니다. 그런데 **`NodeUniqueIndexSeek`가 `NodeByLabelScan`으로 바뀌는
것**은 흔들리지 않습니다. 그 연산자 이름이 시간보다 단단한 증거이고,
`pipeline.graph.plan`이 돌려줍니다 - 계획은 레코드가 아니라 결과 요약에 실립니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CASES = Path("evals/cases_graph.json")
OUTPUT = Path("reports/fold_authored.json")
RUNS = 5
# ADR-063의 바. 여기 적어 두면 보고서가 스스로 판정합니다.
MAX_RATIO = 5.0
MAX_SECONDS = 1.0

# 지금 쓰는 질의입니다. `evals/cases_graph.json`의 정답 Cypher와 **같아야** 비교가 성립합니다.
NOW = {
    "author": (
        "MATCH (a:ArxivAuthorName {name: $name})-[:AUTHORED]->(p:ArxivPaper) "
        "RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id"
    ),
    "coauthor": (
        "MATCH (p:ArxivPaper {arxiv_id: $arxiv_id})<-[:AUTHORED]-(:ArxivAuthorName)"
        "-[:AUTHORED]->(q:ArxivPaper) WHERE q.arxiv_id <> $arxiv_id "
        "RETURN DISTINCT q.arxiv_id AS arxiv_id ORDER BY arxiv_id"
    ),
}

# 접은 뒤의 모양을 `categories`로 흉내 낸 것입니다. **리스트만 다르고 일은 같습니다.**
# `$category`에 **행 수가 맞는 드문 분류**가 들어갑니다(흔한 분류는 `wide`에서 따로 잽니다).
FOLDED_PROXY = {
    "author": (
        "MATCH (p:ArxivPaper) WHERE $category IN p.categories "
        "RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id"
    ),
    "coauthor": (
        "MATCH (p:ArxivPaper {arxiv_id: $arxiv_id}) "
        "MATCH (q:ArxivPaper) WHERE q.arxiv_id <> $arxiv_id "
        "  AND any(c IN q.categories WHERE c = $category) "
        "RETURN DISTINCT q.arxiv_id AS arxiv_id ORDER BY arxiv_id"
    ),
}

# **외삽을 가정이 아니라 측정으로 묶습니다.** `$many`에 드문 분류 7개를 주면 노드마다
# 비교가 1.98x7 = 약 13.9회 일어납니다 - 접은 뒤의 7.1회(`$name IN p.authors`)보다
# **많습니다.** 그래서 이 변종이 바를 넘으면 리스트 길이에 의한 외삽도 바 안에 있습니다.
FOLDED_LONG = {
    "author": (
        "MATCH (p:ArxivPaper) WHERE any(c IN p.categories WHERE c IN $many) "
        "RETURN p.arxiv_id AS arxiv_id ORDER BY arxiv_id"
    ),
    "coauthor": (
        "MATCH (p:ArxivPaper {arxiv_id: $arxiv_id}) "
        "MATCH (q:ArxivPaper) WHERE q.arxiv_id <> $arxiv_id "
        "  AND any(c IN q.categories WHERE c IN $many) "
        "RETURN DISTINCT q.arxiv_id AS arxiv_id ORDER BY arxiv_id"
    ),
}
LONG_LIST = 7  # p.authors의 평균 길이 7.1을 비교 횟수로 덮습니다.


def timed(read, query: str, params: dict[str, Any]) -> tuple[float, int]:
    started = perf_counter()
    rows = read(query, params)
    return perf_counter() - started, len(rows)


def sample(read, query: str, params: dict[str, Any], runs: int) -> dict[str, Any]:
    """`runs`회 돌려 중앙값과 최악을 적습니다.

    **첫 회를 버리지 않습니다.** 캐시가 비어 느린 그 회가 사용자가 만나는 회입니다.
    """
    seconds, rows = [], 0
    for _ in range(runs):
        elapsed, rows = timed(read, query, params)
        seconds.append(elapsed)
    return {
        "median_seconds": round(statistics.median(seconds), 4),
        "worst_seconds": round(max(seconds), 4),
        "rows": rows,
        "runs": runs,
    }


def access(plan: list[str]) -> list[str]:
    """계획에서 **자료에 닿는 연산자**만 꺼냅니다.

    결정적인 것은 트리 **아래쪽**입니다 - 위쪽은 `ProduceResults`·`Sort`처럼 양쪽이 같습니다.
    `Seek`가 `Scan`으로 바뀌는지가 이 측정의 단단한 증거입니다.
    """
    return [name for name in plan if "Seek" in name or "Scan" in name]


def rare_categories(read) -> dict[int, str]:
    """편수별로 **가장 드문 분류 하나**를 고릅니다. 손으로 고르지 않습니다.

    대리 질의가 진짜 질의와 **같은 행 수**를 돌려주게 하려고 씁니다. 행 수가 다르면
    주사 비용이 아니라 행을 돌려주는 비용을 재게 됩니다.
    """
    rows = read(
        "MATCH (p:ArxivPaper) UNWIND p.categories AS c "
        "WITH c, count(*) AS n WHERE n <= 20 "
        "RETURN c, n ORDER BY n, c"
    )
    by_count: dict[int, str] = {}
    for row in rows:
        by_count.setdefault(int(row["n"]), str(row["c"]))
    return by_count


def pick_category(by_count: dict[int, str], rows: int) -> tuple[str, int]:
    """행 수가 같은 분류, 없으면 **가장 가까운** 것을 고릅니다."""
    if not by_count:
        raise ValueError("드문 분류를 찾지 못했습니다")
    count = min(by_count, key=lambda n: (abs(n - rows), n))
    return by_count[count], count


def seeds(path: Path) -> dict[str, list[dict[str, Any]]]:
    """정답 파일에서 같은 시드·같은 이름을 가져옵니다. 손으로 고르지 않습니다."""
    cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
    picked: dict[str, list[dict[str, Any]]] = {"author": [], "coauthor": []}
    for case in cases:
        family = case["category"]
        if family in picked:
            picked[family].append({"id": case["id"], **case["cypher_params"]})
    return picked


def verdict(now: dict[str, Any], folded: dict[str, Any]) -> dict[str, Any]:
    """ADR-063의 바를 그대로 적용합니다 — 둘 다 넘겨야 합니다."""
    ratio = folded["worst_seconds"] / now["worst_seconds"] if now["worst_seconds"] else 0.0
    within_ratio = ratio <= MAX_RATIO
    within_absolute = folded["worst_seconds"] <= MAX_SECONDS
    return {
        "worst_ratio": round(ratio, 2),
        "within_ratio_limit": bool(within_ratio),
        "within_absolute_limit": bool(within_absolute),
        "passes": bool(within_ratio and within_absolute),
        # **넘겨도 허가가 아닙니다.** 대리 측정의 비대칭을 보고서가 스스로 말합니다.
        "note": (
            "넘지 못하면 접은 모양은 더 느립니다(기각 확정). 넘어도 '접어도 된다'가 "
            "아닙니다 - authors는 평균 7.1개로 categories(1.98)보다 리스트가 길어, "
            "충실한 측정이 필요합니다(ADR-063)."
        ),
    }


def main() -> int:
    from pipeline.console import use_utf8_stdout
    from pipeline.graph import connect

    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="AUTHORED를 접은 뒤의 질의 지연을 잽니다(ADR-063). 읽기만 합니다."
    )
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--runs", type=int, default=RUNS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    # 흔한 분류를 쓰면 대리가 21,183행을 돌려줍니다. 그 쪽은 **행을 돌려주는 비용**까지
    # 섞인 수이므로 판정에 쓰지 않고 참고로만 적습니다.
    parser.add_argument("--wide-category", default="cs.AI")
    args = parser.parse_args()

    picked = seeds(args.cases)
    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "runs_per_query": args.runs,
        "bar": {"max_worst_ratio": MAX_RATIO, "max_worst_seconds": MAX_SECONDS},
        "proxy": (
            "p.categories로 전수 주사 + 리스트 교집합을 흉내 냅니다. 시드마다 **행 수가 "
            "맞는 드문 분류**를 골라, 주사 비용에 행을 돌려주는 비용이 섞이지 않게 "
            "합니다. p.authors는 평균 7.1개(최대 3,434)이므로 이 대리는 **기각만** 할 수 "
            "있습니다."
        ),
        "families": {},
    }
    with connect() as graph:
        report["papers"] = graph.read(
            "MATCH (p:ArxivPaper) RETURN count(p) AS papers"
        )[0]["papers"]
        by_count = rare_categories(graph.read)
        many = [by_count[n] for n in sorted(by_count)][:LONG_LIST]
        report["long_list"] = {"categories": many, "length": len(many)}
        for family, params_list in picked.items():
            entries, plan_now, plan_folded = [], [], []
            for params in params_list:
                case_id = params.pop("id")
                # **같은 세션에서 번갈아 돌립니다.** 환경의 흔들림이 양쪽에 같이 들어가게.
                now = sample(graph.read, NOW[family], params, args.runs)
                # 행 수를 맞춥니다 - 진짜 질의가 돌려준 수에 가장 가까운 분류를 씁니다.
                category, category_papers = pick_category(by_count, now["rows"])
                matched_params = dict(params) | {"category": category}
                wide_params = dict(params) | {"category": args.wide_category}
                folded = sample(
                    graph.read, FOLDED_PROXY[family], matched_params, args.runs
                )
                wide = sample(graph.read, FOLDED_PROXY[family], wide_params, args.runs)
                long_params = dict(params) | {"many": many}
                long = sample(graph.read, FOLDED_LONG[family], long_params, args.runs)
                entries.append({
                    "case": case_id,
                    "category": category,
                    "category_papers": category_papers,
                    "now": now,
                    "folded_proxy": folded,
                    # 참고용입니다. **판정에 쓰지 않습니다** - 행을 돌려주는 비용이 섞입니다.
                    "folded_wide": wide,
                    # 리스트 길이 외삽의 **상한**입니다. 판정에 쓰지 않고, 바 안에 있는지만
                    # 봅니다 - 안에 있으면 "authors가 3.5배 길다"는 외삽도 바 안입니다.
                    "folded_long": long,
                    "folded_long_verdict": verdict(now, long),
                    "verdict": verdict(now, folded),
                })
                plan_now = graph.plan(NOW[family], params)
                plan_folded = graph.plan(FOLDED_PROXY[family], matched_params)
            worst_now = max(e["now"]["worst_seconds"] for e in entries)
            worst_folded = max(e["folded_proxy"]["worst_seconds"] for e in entries)
            report["families"][family] = {
                "cases": entries,
                "worst_now_seconds": worst_now,
                "worst_folded_seconds": worst_folded,
                "worst_wide_seconds": max(
                    e["folded_wide"]["worst_seconds"] for e in entries
                ),
                "worst_long_seconds": max(
                    e["folded_long"]["worst_seconds"] for e in entries
                ),
                "long_list_within_bar": all(
                    e["folded_long_verdict"]["passes"] for e in entries
                ),
                "verdict": verdict(
                    {"worst_seconds": worst_now}, {"worst_seconds": worst_folded}
                ),
                # **계획은 `pipeline.graph.plan`이 돌려줍니다.** 계획은 레코드가 아니라
                # 결과 요약에 실려서, `read`로는 빈 목록이 옵니다(2026-10-24에 그렇게
                # 돌렸습니다). 마지막 시드의 계획입니다 - 모양이 시드마다 같습니다.
                "plan_now": plan_now,
                "plan_folded": plan_folded,
                "access_now": access(plan_now),
                "access_folded": access(plan_folded),
                # **인덱스를 잃었는가.** 눈으로 보지 않고 셉니다.
                "index_lost": bool(
                    any("Seek" in name for name in plan_now)
                    and not any("Seek" in name for name in plan_folded)
                ),
            }
    report["passes"] = all(
        entry["verdict"]["passes"] for entry in report["families"].values()
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for family, entry in report["families"].items():
        print(
            f"{family}: 지금 최악 {entry['worst_now_seconds']:.3f}s -> "
            f"접은 뒤(행 맞춤) {entry['worst_folded_seconds']:.3f}s "
            f"({entry['verdict']['worst_ratio']}배) "
            f"-> {'통과' if entry['verdict']['passes'] else '바를 넘지 못함'}"
        )
        print(
            f"  리스트 7개(상한) {entry['worst_long_seconds']:.3f}s -> "
            f"{'바 안' if entry['long_list_within_bar'] else '바 밖'}"
            " (authors 평균 7.1개를 비교 횟수로 덮습니다)"
        )
        print(
            f"  참고: 흔한 분류로는 {entry['worst_wide_seconds']:.3f}s"
            " (행을 돌려주는 비용이 섞여 판정에 쓰지 않습니다)"
        )
        print(f"  자료 접근 지금:   {entry['access_now']}")
        print(f"  자료 접근 접은 뒤: {entry['access_folded']}"
              f"{'  <- 인덱스를 잃습니다' if entry['index_lost'] else ''}")
    print(f"전체 판정: {'통과' if report['passes'] else '바를 넘지 못했습니다'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
