"""Colab에서 파인튜닝할 수 있게 스냅샷을 작은 학습 산출물로 내보냅니다.

    uv run python -m models.embedding_tuning.export_for_colab

이 PC에는 GPU가 없습니다(Intel Iris Xe, CUDA 불가). 37,854편 인코딩이 CPU로 3.5시간이고
파인튜닝은 비현실적이라, 열려 있는 질문을 CPU에서는 답할 수 없습니다.

- BGE는 `text-embedding-3-large`와 **동등**합니다(구간이 0 포함). 인용 쌍으로 파인튜닝하면
  넘는가?
- hard negative는 **선형 정식화에서** 기각됐습니다. 용량 있는 모델에서는 작동하는가?

내보내는 것은 네 파일입니다. 임베딩(110MB)은 넣지 않습니다 — Colab은 자기가 다시 인코딩하고,
비교에 필요한 것은 OpenAI 임베딩 자체가 아니라 **질의별 점수**뿐입니다.

| 파일 | 내용 |
|---|---|
| `corpus.jsonl` | 37,854편의 `arxiv_id`와 제목+초록 |
| `train_pairs.jsonl` | 앵커·양성·하드 네거티브 (학습 구간에서만) |
| `eval_queries.jsonl` | 평가 365질의와 정답 인용 목록 |
| `baseline_recall.jsonl` | 질의별 `text-embedding-3-large` recall@10 |

마지막 파일이 핵심입니다. 그것이 있으면 Colab이 **쌍체 부트스트랩을 그대로** 돌릴 수 있고,
110MB 임베딩을 옮기지 않아도 됩니다.

`manifest.json`에 스냅샷 버전과 papers sha256을 적습니다. 어느 스냅샷으로 학습했는지 모르면
수치를 되짚을 수 없습니다.
"""

from __future__ import annotations

import argparse
import gzip
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from models.embedding_tuning.train import (
    MAX_REFERENCES,
    MIN_REFERENCES,
    SEED,
    Corpus,
    evaluation_queries,
    hard_negative,
    per_query_recall,
    split_citers,
)
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_label

OUTPUT = Path("exports/colab")
DEFAULT_CUT = "2026-08"
MAX_TRAIN_PAIRS = 40000
MAX_TEST_QUERIES = 1200
TOP_K = 10


def parse_month(value: str) -> int:
    from models.embedding_tuning.train import parse_month as parse

    return parse(value)


def write_gzip_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"path": path.name, "rows": len(rows), "bytes": path.stat().st_size}


def corpus_rows(snap: Any, corpus: Corpus) -> list[dict[str, Any]]:
    """Title and abstract per paper, in the snapshot's own order."""
    texts = {}
    for row in snap.rows("papers"):
        title = (row.get("title") or "").strip()
        abstract = (row.get("abstract") or "").strip()
        texts[row["arxiv_id"]] = f"{title}\n{abstract}".strip()
    return [
        {"arxiv_id": arxiv_id, "text": texts.get(arxiv_id, "")}
        for arxiv_id in corpus.ids
    ]


def training_rows(
    corpus: Corpus, citers: list[str], *, limit: int, seed: int
) -> list[dict[str, Any]]:
    """Anchor, positive and a same-category hard negative, as IDs only.

    Text stays in `corpus.jsonl` so a pair costs three short strings instead of three
    abstracts. The notebook joins them.
    """
    rng = random.Random(seed)
    pairs = [
        (citing, cited)
        for citing in citers
        for cited in sorted(corpus.references[citing])
    ]
    if len(pairs) > limit:
        pairs = rng.sample(pairs, limit)
    rows = []
    for citing, cited in pairs:
        negative = hard_negative(corpus, citing, cited, rng)
        if negative is None:
            continue
        rows.append({"anchor": citing, "positive": cited, "hard_negative": negative})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Colab 학습용 산출물을 내보냅니다.")
    parser.add_argument("--version")
    parser.add_argument("--cut", default=DEFAULT_CUT)
    parser.add_argument("--max-train-pairs", type=int, default=MAX_TRAIN_PAIRS)
    parser.add_argument("--max-test-queries", type=int, default=MAX_TEST_QUERIES)
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    cut = parse_month(args.cut)
    corpus = Corpus(snap)
    split = split_citers(corpus, cut)
    queries = evaluation_queries(
        corpus, split["test"], limit=args.max_test_queries, seed=SEED + 5
    )
    if not queries:
        raise SystemExit("평가 질의를 만들지 못했습니다.")

    identity = np.eye(corpus.vectors.shape[1], dtype=np.float32)
    baseline = per_query_recall(corpus, queries, identity, top_k=args.top_k)

    files = {
        "corpus": write_gzip_jsonl(
            args.output / "corpus.jsonl.gz", corpus_rows(snap, corpus)
        ),
        "train_pairs": write_gzip_jsonl(
            args.output / "train_pairs.jsonl.gz",
            training_rows(corpus, split["train"], limit=args.max_train_pairs, seed=SEED),
        ),
        "eval_queries": write_gzip_jsonl(
            args.output / "eval_queries.jsonl.gz",
            [{"anchor": citing, "gold": gold} for citing, gold in queries],
        ),
        "baseline_recall": write_gzip_jsonl(
            args.output / "baseline_recall.jsonl.gz",
            [
                {"anchor": citing, "recall_at_10": round(float(value), 6)}
                for (citing, _), value in zip(queries, baseline, strict=True)
            ],
        ),
    }

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "snapshot_papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "task": "retrieve the papers a paper cites",
        "split": {
            "key": "arxiv_id_yymm",
            "test_citers_from": month_label(cut),
            "train_citers": len(split["train"]),
            "test_citers": len(split["test"]),
            "eval_reference_count_range": [MIN_REFERENCES, MAX_REFERENCES],
        },
        "top_k": args.top_k,
        "baseline": {
            "model": "text-embedding-3-large",
            "dimensions": int(corpus.vectors.shape[1]),
            "recall_at_10": round(float(np.mean(baseline)), 4),
            "note": "질의별 점수를 함께 내보내므로 Colab에서 쌍체 부트스트랩이 가능합니다.",
        },
        "files": files,
        "hard_negative_rule": (
            "양성 논문과 같은 primary_category이면서, 앵커 논문이 인용하지 않은 논문. "
            "무작위 음성은 주제가 달라 임베딩 유사도만으로 갈리므로 쓰지 않습니다."
        ),
        "max_seq_length_note": (
            "제목+초록 토큰 중앙값이 258입니다. 128이나 256으로 자르면 논문 절반이 잘려 "
            "모델이 아니라 절단을 재게 됩니다. 512를 쓰세요."
        ),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    total = sum(entry["bytes"] for entry in files.values())
    print(json.dumps({
        "output": str(args.output),
        "files": files,
        "total_mb": round(total / 1024 / 1024, 1),
        "baseline_recall_at_10": manifest["baseline"]["recall_at_10"],
        "eval_queries": len(queries),
        "train_pairs": files["train_pairs"]["rows"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
