"""순위 지표가 무엇을 재고 있었는지, 집합 지표가 무엇을 더 잡는지 **저장된 보고서로** 재봅니다.

    uv run python -m evals.retrieval_metric_study

API를 쓰지 않습니다. 이미 받아 둔 보고서의 `retrieved_ids`에 집합 연산을 다시 적용하는
것이 전부여서, 모델을 다시 돌릴 때 생기는 비결정성이 결론에 섞이지 않습니다
([ADR-033](../docs/adr/ADR-033-rank-metrics-measure-the-alphabet.md)).

세 가지를 냅니다.

1. **경로별 지표** — 보고서 세대(라우팅 전 / 라우팅 후 / `resolve_paper` 후)와 `vector_only`
   대조에서 다섯 지표가 어떻게 갈리는지.
2. **정렬 기준** — 답변 조회의 `ORDER BY`가 무엇이었는지 세어, `reciprocal_rank`의 "1위"가
   품질인지 알파벳인지 말합니다.
3. **하한·허용폭 시연** — 초과 조회를 사례 수만큼 심어 게이트가 **몇 건에서 걸리는지** 재서,
   `allowed_drop`의 0.05가 "사례 세 건"이라는 설명이 실제로 맞는지 확인합니다.
"""

from __future__ import annotations

import argparse
import collections
import copy
import json
import re
from pathlib import Path
from statistics import fmean
from typing import Any

from evals.gate import check, normalize
from evals.metrics import score_retrieval
from evals.run import ORDER_BY, load_cases

# 보고서 세대입니다. 같은 30건에 같은 골드이고, 바뀐 것은 파이프라인뿐입니다.
GENERATIONS = (
    ("2026-10-16 라우팅 전", Path("reports/regression_full_20261016.json")),
    ("2026-10-18 라우팅 후", Path("reports/baseline_routed_run1.json")),
    ("2026-10-19 resolve_paper 후", Path("reports/baseline_resolve_run1.json")),
)
METRICS = ("retrieval_precision", "retrieval_recall", "recall_at_k", "hit_at_k", "reciprocal_rank")
JUNK_PER_CASE = 5

# `ORDER BY answer_value`의 `answer_value`가 무엇인지 되짚기 위한 것입니다. 실제로 `p.title`도
# 있고 `citing.arxiv_id`도 있어서, 별칭만 보면 "제목순"이라고 잘못 적게 됩니다.
ALIAS = re.compile(r"([\w.]+)\s+AS\s+(\w+)", re.I)


def _kind(column: str) -> str:
    """Whether this sort key is a relevance score or just an identifier string."""
    name = column.rsplit(".", 1)[-1]
    if name in {"title", "arxiv_id", "name"}:
        return "identifier"
    if name in {"citation_count", "score", "shared_citers", "degree"}:
        return "score"
    return f"other:{name}"


def _rows(report: dict[str, Any], side: str) -> list[tuple[str, dict[str, Any]]]:
    out = []
    for result in report.get("results", []):
        row = result.get(side, result if side == "graph_rag" else {})
        if isinstance(row, dict) and "retrieved_ids" in row:
            out.append((result["id"], row))
    return out


def score_side(
    report: dict[str, Any], cases: dict[str, Any], side: str, top_k: int
) -> dict[str, Any]:
    """Re-score one side of a stored report, by family and overall."""
    by_family: dict[str, list[dict[str, float]]] = collections.defaultdict(list)
    sizes: list[int] = []
    for case_id, row in _rows(report, side):
        case = cases.get(case_id)
        if case is None:
            continue
        scored = score_retrieval(case["expected_ids"], row["retrieved_ids"], k=top_k)
        by_family[case_id.split("-")[0]].append(scored)
        sizes.append(len(set(row["retrieved_ids"])))
    everything = [s for family in by_family.values() for s in family]
    if not everything:
        return {}
    return {
        "cases": len(everything),
        "mean_retrieved": round(fmean(sizes), 4),
        "overall": {name: round(fmean(s[name] for s in everything), 4) for name in METRICS},
        "by_family": {
            family: {name: round(fmean(s[name] for s in scored), 4) for name in METRICS}
            for family, scored in sorted(by_family.items())
        },
    }


def sort_clauses(report: dict[str, Any]) -> dict[str, Any]:
    """What the stored queries sorted by. The frozen contexts carry the Cypher.

    둘을 따로 셉니다. `every_query`는 조회마다 한 번이고, `last_query_per_case`는 사례마다
    **마지막 조회**만 봅니다 - 답을 만든 조회가 그것이고, 순위 지표가 매겨진 목록도 거기서
    나옵니다.

    `contexts`가 없는 옛 보고서에서는 셀 것이 없습니다(2026-10-16 보고서가 그렇습니다 -
    RAGAS용 문맥을 그 뒤에 넣었습니다). 빈 결과를 "ORDER BY가 없었다"로 읽지 않도록
    `available`을 같이 적습니다.
    """
    every: collections.Counter[str] = collections.Counter()
    last: collections.Counter[str] = collections.Counter()
    kind: collections.Counter[str] = collections.Counter()
    available = False
    for _case_id, row in _rows(report, "graph_rag"):
        clauses: list[str] = []
        resolved: list[str] = []
        for context in row.get("contexts", []) or []:
            if isinstance(context, str) and context.startswith("Cypher:"):
                available = True
                head = context.split("\n", 1)[0]
                aliases = {
                    match.group(2).lower(): match.group(1).lower()
                    for match in ALIAS.finditer(head)
                }
                for match in ORDER_BY.finditer(head):
                    clause = " ".join(match.group(1).split())
                    clauses.append(clause)
                    column = clause.split()[0].lower()
                    resolved.append(aliases.get(column, column))
        every.update(clauses)
        if clauses:
            last[clauses[-1]] += 1
            kind[_kind(resolved[-1])] += 1
    return {
        "available": available,
        "every_query": dict(every.most_common()),
        "last_query_per_case": dict(last.most_common()),
        # 핵심 숫자입니다. "순위"가 점수 순인지 **식별자 문자열 순**인지입니다.
        "last_query_sorts_by": dict(kind.most_common()),
    }


def _inject_junk(
    report: dict[str, Any], cases: dict[str, Any], count: int, top_k: int
) -> dict[str, Any]:
    """Add irrelevant papers to the first `count` cases, keeping the alphabetical order.

    쓰레기는 **같은 보고서의 `vector_only`**에서 가져옵니다. 바깥에서 만든 가짜 ID를 넣으면
    "존재하지 않는 논문"을 심는 것이고, 실제 회귀는 그런 모양이 아닙니다 - 라우팅이 깨졌을 때
    섞여 들어온 것은 **진짜지만 무관한 논문**이었습니다(ADR-024).

    정렬은 알파벳순으로 다시 합니다. 측정된 `ORDER BY title`과 같은 모양이어야 `recall_at_k`가
    실제 회귀에서처럼 움직입니다.
    """
    mutated = copy.deepcopy(report)
    pool = [
        arxiv_id
        for _case_id, row in _rows(report, "vector_only")
        for arxiv_id in row.get("retrieved_ids", [])
    ]
    # **모든 행을 다시 채점합니다.** 심은 사례만 채점하면 옛 보고서에 없는 집합 지표가 그
    # 사례들에만 생기고, 게이트의 평균이 30건이 아니라 심은 건수의 평균이 됩니다. 처음에
    # 그렇게 재서 "초과 조회 1건에 precision 0.2857"이라는 틀린 값을 봤습니다.
    for index, result in enumerate(mutated.get("results", [])):
        row = result.get("graph_rag")
        if not isinstance(row, dict) or "retrieved_ids" not in row:
            continue
        found = row["retrieved_ids"]
        if index < count:
            junk = [arxiv_id for arxiv_id in pool if arxiv_id not in found][:JUNK_PER_CASE]
            found = sorted(set(found) | set(junk))
            row["retrieved_ids"] = found
        row.update(score_retrieval(cases[result["id"]]["expected_ids"], found, k=top_k))
    return mutated


def floor_demo(
    report: dict[str, Any], cases: dict[str, Any], baseline: dict[str, Any], top_k: int
) -> list[dict[str, Any]]:
    """How many over-retrieving cases it takes for the gate to fire."""
    out = []
    for count in (0, 1, 2, 3, 5, 30):
        mutated = _inject_junk(report, cases, count, top_k)
        run = normalize(mutated)
        run["ragas_judge_model"] = baseline.get("ragas_judge_model", "")
        # RAGAS는 이 실험에서 건드리지 않습니다. 기준선 값을 그대로 두어 **검색 규칙만** 봅니다.
        for name in ("ragas_faithfulness", "ragas_context_precision_stable_families"):
            if name in baseline["metrics"]:
                run["metrics"][name] = baseline["metrics"][name]
        violations = check(baseline, run)
        out.append({
            "over_retrieving_cases": count,
            "metrics": {
                name: round(run["metrics"][name], 4)
                for name in METRICS
                if name in run["metrics"]
            },
            "violations": [str(violation) for violation in violations],
        })
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="검색 지표가 무엇을 재는지 저장된 보고서로 재봅니다."
    )
    parser.add_argument("--cases", type=Path, default=Path("evals/cases_graph.json"))
    parser.add_argument("--baseline", type=Path, default=Path("baselines/eval_baseline.json"))
    parser.add_argument("--product", type=Path, default=Path("reports/baseline_resolve_run1.json"))
    parser.add_argument("--output", type=Path, default=Path("reports/retrieval_metric_study.json"))
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    cases = {case["id"]: case for case in load_cases(args.cases)}
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    product = json.loads(args.product.read_text(encoding="utf-8"))
    top_k = product.get("top_k", 5)

    generations = []
    for label, path in GENERATIONS:
        if not path.exists():
            continue
        report = json.loads(path.read_text(encoding="utf-8"))
        generations.append({
            "label": label,
            "report": str(path),
            "graph_rag": score_side(report, cases, "graph_rag", report.get("top_k", top_k)),
            "ordered_by": sort_clauses(report),
        })

    study = {
        "generated_at": __import__("datetime").datetime.now(
            __import__("datetime").UTC
        ).isoformat(),
        "note": (
            "API를 쓰지 않습니다. 저장된 retrieved_ids에 집합 연산을 다시 적용한 것이 전부입니다."
        ),
        "generations": generations,
        "vector_only_contrast": score_side(product, cases, "vector_only", top_k),
        "floor_demo": floor_demo(product, cases, baseline, top_k),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(study, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    columns = [name.replace("retrieval_", "set_") for name in METRICS]
    print(f"{'상태':<30}" + "".join(f"{name:>20}" for name in columns))
    for generation in generations:
        overall = generation["graph_rag"].get("overall", {})
        cells = "".join(f"{overall.get(name, 0):>20.4f}" for name in METRICS)
        print(f"{generation['label']:<30}{cells}")
    contrast = study["vector_only_contrast"].get("overall", {})
    cells = "".join(f"{contrast.get(name, 0):>20.4f}" for name in METRICS)
    print(f"{'vector_only 대조':<30}{cells}")
    print()
    for step in study["floor_demo"]:
        print(f"초과 조회 {step['over_retrieving_cases']:>2}건 -> 위반 {len(step['violations'])}건"
              f"  {step['metrics']}")
    print(f"\n{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
