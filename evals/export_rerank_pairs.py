"""교차 인코더가 채점할 (시드, 후보) 쌍을 **여기서 굳혀** 내보냅니다.

    uv run python -m evals.export_rerank_pairs
    uv run python -m evals.export_rerank_pairs --limit 40 --output exports/colab/rerank_smoke

교차 인코더는 두 논문을 **함께 읽어** 점수를 냅니다. 그래서 GPU가 필요하고, 이 PC에는
없습니다. 인코딩처럼 Colab에서 채점하고 행렬만 돌려받는 구조로 갑니다 — ADR-039가 임베딩에
세운 것과 같은 선입니다.

## Colab이 다시 만들어서는 안 되는 것이 둘입니다

**후보 집합.** `vector 상위 50 ∪ 보이는 공동인용`이고, 이 파일은 그 계산을
[`related_study.pool_candidates`](related_study.py)에서 **그대로 불러옵니다.** Colab이
자기 식으로 후보를 뽑으면 `graph-text-gbm`과 **다른 후보 집합**을 재게 되고, 그러면
"재정렬이 못 했다"와 "후보가 달랐다"를 구별할 수 없습니다(ADR-029).

**텍스트.** 굳어 있는 `corpus.jsonl.gz`의 `text`를 그대로 보냅니다. 스냅샷에서 다시 만들면
공백 하나가 달라질 수 있고, 그러면 재는 것이 모델이 아니라 **전처리 차이**입니다(ADR-039).

## 내보내는 것

| 파일 | 내용 |
|---|---|
| `rerank_pairs.jsonl.gz` | 줄마다 `{seed, candidates: [id, ...]}` — 순서까지 고정 |
| `rerank_texts.jsonl.gz` | 쌍에 등장하는 논문만 `{arxiv_id, text}` |
| `rerank_manifest.json` | 시드·쌍·논문 수와 정답 파일·피처 창·풀 크기 |

**점수는 여기서 내지 않습니다.** Colab이 쌍마다 점수를 돌려주고, 판정은
`evals.related_study --variants rerank --rerank-scores ...`가 합니다.
"""

from __future__ import annotations

import argparse
import gzip
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.related_study import GBM_POOL, Graph, pool_candidates
from models.link_prediction.train import parse_month
from pipeline import snapshot as snapshot_module

CORPUS = Path("exports/colab/corpus.jsonl.gz")
OUTPUT = Path("exports/colab")


def corpus_text(path: Path = CORPUS) -> dict[str, str]:
    """`arxiv_id -> text`. 굳어 있는 문자열을 그대로 씁니다."""
    if not path.exists():
        raise SystemExit(
            f"{path}가 없습니다. uv run python -m models.embedding_tuning.export_for_colab "
            "로 먼저 만드세요."
        )
    texts: dict[str, str] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            texts[row["arxiv_id"]] = row["text"]
    return texts


def build(
    cases: list[dict[str, Any]],
    graph: Graph,
    texts: dict[str, str],
    *,
    pool: int,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """One row per seed, with the candidate order frozen.

    텍스트가 없는 논문은 **후보에서 빼고 셉니다.** 조용히 두면 Colab이 빈 문자열을 채점하고,
    그 점수가 모델의 사실처럼 보고서에 들어갑니다.
    """
    rows: list[dict[str, Any]] = []
    missing_text = 0
    skipped_seeds: list[str] = []
    for case in cases:
        seed = case["seed_id"]
        if seed not in graph.position or seed not in texts:
            skipped_seeds.append(seed)
            continue
        scores = graph.vectors @ graph.vectors[graph.position[seed]]
        pooled = pool_candidates(graph, seed, scores, pool=pool)
        usable = [paper for paper in pooled if paper in texts]
        missing_text += len(pooled) - len(usable)
        rows.append({"seed": seed, "candidates": usable})
    counts = {
        "seeds": len(rows),
        "pairs": sum(len(row["candidates"]) for row in rows),
        "candidates_without_text": missing_text,
        "seeds_skipped": len(skipped_seeds),
    }
    return rows, counts


def write(rows: list[dict[str, Any]], texts: dict[str, str], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    pairs_path = out / "rerank_pairs.jsonl.gz"
    texts_path = out / "rerank_texts.jsonl.gz"
    wanted = {row["seed"] for row in rows}
    wanted.update(paper for row in rows for paper in row["candidates"])

    with gzip.open(pairs_path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    # 쌍에 등장하는 논문만 보냅니다. 말뭉치 전체(18MB)를 보낼 이유가 없습니다.
    with gzip.open(texts_path, "wt", encoding="utf-8", newline="\n") as handle:
        for paper in sorted(wanted):
            handle.write(
                json.dumps({"arxiv_id": paper, "text": texts[paper]}, ensure_ascii=False) + "\n"
            )
    return {
        "pairs_file": pairs_path.name,
        "pairs_bytes": pairs_path.stat().st_size,
        "texts_file": texts_path.name,
        "texts_bytes": texts_path.stat().st_size,
        "papers": len(wanted),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="교차 인코더용 쌍을 내보냅니다.")
    parser.add_argument("--cases", type=Path, default=Path("evals/cases_related_future.json"))
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부입니다.")
    parser.add_argument("--pool", type=int, default=GBM_POOL)
    parser.add_argument("--embeddings", type=Path, help="파생 캐시 대신 쓸 임베딩 행렬")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()

    dataset = json.loads(args.cases.read_text(encoding="utf-8"))
    cases = dataset["cases"] if isinstance(dataset, dict) else dataset
    if args.limit:
        cases = cases[: args.limit]
    # **정답이 자르는 선을 정합니다** - `related_study`와 같은 규칙입니다. 자르지 않으면
    # 후보에 라벨 창의 인용자가 섞여 들어옵니다(ADR-035).
    cut_label = dataset.get("feature_cut") if isinstance(dataset, dict) else None
    if cut_label is None:
        raise SystemExit(
            f"{args.cases}에 feature_cut이 없습니다. 미래를 라벨로 쓰는 정답이어야 합니다."
        )

    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    graph = Graph(snap, feature_cut=parse_month(cut_label), embeddings=args.embeddings)
    texts = corpus_text()
    rows, counts = build(cases, graph, texts, pool=args.pool)
    written = write(rows, texts, args.output)

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot": snap.manifest["version"],
        "cases_file": str(args.cases),
        "feature_cut": cut_label,
        "pool": args.pool,
        **counts,
        **written,
        "note": (
            "후보 순서와 텍스트가 여기서 굳었습니다. Colab은 채점만 하고 후보를 다시 "
            "만들지 않습니다 - 다시 만들면 graph-text-gbm과 다른 후보 집합을 재게 됩니다."
        ),
    }
    (args.output / "rerank_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
