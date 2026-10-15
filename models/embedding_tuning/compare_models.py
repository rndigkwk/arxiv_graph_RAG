"""로컬 임베딩 모델의 출발점을 `text-embedding-3-large`와 같은 과제에서 비교합니다.

    uv run --with sentence-transformers python -m models.embedding_tuning.compare_models
    uv run --with sentence-transformers python -m models.embedding_tuning.compare_models \
        --model sentence-transformers/allenai-specter

Day 74의 원래 경로(모델 파인튜닝)로 가려면 **출발점을 먼저 알아야 합니다.** 로컬 모델은 튜닝
전에 `text-embedding-3-large`보다 아래에서 시작하므로, 파인튜닝이 이겨야 하는 것은 자기 자신이
아니라 **이미 측정된 0.2019**입니다. 격차가 크면 통상적인 MNR 파인튜닝 이득으로는 넘지 못하고,
그때는 2GB 다운로드와 배포 블로커를 감당할 이유가 없습니다.

`all-MiniLM-L6-v2`는 쓰지 않습니다. 이 말뭉치의 제목+초록 중앙값이 258토큰인데 그 모델의 한도가
256이라 **논문의 50.9%가 잘립니다.** 그러면 재는 것이 모델이 아니라 절단입니다. 512토큰 모델만
후보로 둡니다(`advanced.md`의 토큰 분포 표).

과제·질의·정답·지표는 `train.py`와 같은 것을 그대로 씁니다. 임베딩 행렬만 바뀝니다.

BGE 계열이 권하는 질의 접두사(`Represent this sentence for searching...`)는 쓰지 않습니다.
그 접두사는 짧은 질의로 긴 문서를 찾는 비대칭 검색용이고, 이 과제는 논문으로 논문을 찾는
대칭 검색입니다. 질의와 문서를 같은 방식으로 인코딩하는 것이 맞습니다.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np

from models.embedding_tuning.train import (
    SEED,
    Corpus,
    evaluation_queries,
    paired_bootstrap,
    parse_month,
    per_query_recall,
    score,
    split_citers,
)
from pipeline import features
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_label
from pipeline.tracking import track

OUTPUT = Path("reports/embedding_models.json")
DEFAULT_CUT = "2026-08"
DEFAULT_MODELS = (
    "sentence-transformers/allenai-specter",
    "BAAI/bge-base-en-v1.5",
)
TOP_K = 10
BATCH = 32
MAX_TEST_QUERIES = 1200


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def cache_path(snap: Any, name: str, root: Path = features.DERIVED_ROOT) -> Path:
    return root / snap.version / f"local-{slug(name)}.npy"


def encode_corpus(snap: Any, corpus: Corpus, name: str, *, batch: int = BATCH) -> dict[str, Any]:
    """Encode every paper's title+abstract once and cache it next to the snapshot.

    Encoding 37,854 papers on CPU takes minutes, and the comparison needs the whole corpus as
    the candidate pool. Restricting the pool would change the task and make the numbers
    incomparable to the baseline, so the full encode is the price of a fair comparison.
    """
    from sentence_transformers import SentenceTransformer

    path = cache_path(snap, name)
    model = SentenceTransformer(name)
    texts = {}
    for row in snap.rows("papers"):
        texts[row["arxiv_id"]] = f"{row.get('title') or ''}\n{row.get('abstract') or ''}".strip()
    ordered = [texts.get(arxiv_id, "") for arxiv_id in corpus.ids]
    start = perf_counter()
    vectors = model.encode(
        ordered, batch_size=batch, normalize_embeddings=True,
        show_progress_bar=False, convert_to_numpy=True,
    )
    seconds = perf_counter() - start
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, np.asarray(vectors, dtype=np.float32))
    return {
        "path": str(path),
        "rows": int(vectors.shape[0]),
        "dimensions": int(vectors.shape[1]),
        "max_seq_length": int(model.max_seq_length),
        "encode_seconds": round(seconds, 1),
    }


def load_or_encode(snap: Any, corpus: Corpus, name: str, *, reencode: bool) -> dict[str, Any]:
    path = cache_path(snap, name)
    if path.exists() and not reencode:
        matrix = np.load(path)
        return {"path": str(path), "rows": int(matrix.shape[0]),
                "dimensions": int(matrix.shape[1]), "cached": True, "matrix": matrix}
    info = encode_corpus(snap, corpus, name)
    return {**info, "cached": False, "matrix": np.load(path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="로컬 임베딩 모델의 출발점을 비교합니다.")
    parser.add_argument("--version")
    parser.add_argument("--cut", default=DEFAULT_CUT)
    parser.add_argument("--model", action="append", dest="models",
                        help="여러 번 주면 모델을 여러 개 비교합니다.")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--max-test-queries", type=int, default=MAX_TEST_QUERIES)
    parser.add_argument("--reencode", action="store_true",
                        help="캐시를 무시하고 다시 인코딩합니다.")
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
    baseline_recall = per_query_recall(corpus, queries, identity, top_k=args.top_k)
    results: dict[str, Any] = {
        "text-embedding-3-large": {
            "dimensions": int(corpus.vectors.shape[1]),
            "source": "snapshot (OpenAI API, 배포 중인 공간)",
            **score(corpus, queries, identity, top_k=args.top_k),
        }
    }

    for name in args.models or list(DEFAULT_MODELS):
        loaded = load_or_encode(snap, corpus, name, reencode=args.reencode)
        swapped = corpus.with_vectors(loaded.pop("matrix"))
        local_identity = np.eye(swapped.vectors.shape[1], dtype=np.float32)
        results[name] = {
            **{key: value for key, value in loaded.items() if key != "path"},
            **score(swapped, queries, local_identity, top_k=args.top_k),
            "vs_openai": paired_bootstrap(
                baseline_recall,
                per_query_recall(swapped, queries, local_identity, top_k=args.top_k),
            ),
        }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "task": "retrieve the papers a paper cites",
        "note": (
            "과제·질의·정답·지표는 embedding_tuning.train과 같습니다. 임베딩 행렬만 바뀝니다. "
            "모든 모델이 전체 말뭉치를 후보로 봅니다."
        ),
        "split": {
            "test_citers_from": month_label(cut),
            "eval_queries": len(queries),
        },
        "top_k": args.top_k,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with track("embedding-model-comparison") as run:
        run.log_params({"snapshot_version": snap.version, "cut": month_label(cut),
                        "eval_queries": len(queries)})
        for name, row in results.items():
            for metric, value in row.items():
                if isinstance(value, (int, float)):
                    run.log_metrics({f"{slug(name)}.{metric}": value})
        run.log_artifact(args.output)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
