"""Colab에서 말뭉치를 다른 임베딩 모델로 인코딩합니다. **이 PC에서는 돌리지 않습니다.**

    # Colab 셀 하나
    !pip -q install sentence-transformers
    !python colab_encode.py --corpus corpus_for_encoding.jsonl --model BAAI/bge-m3

이 PC에는 GPU가 없고 bge-base(109M) 인코딩이 CPU로 3.5시간이었습니다
([`export_for_colab.py`](../models/embedding_tuning/export_for_colab.py)). BGE-M3(568M)·
Qwen3-Embedding-0.6B(595M)는 그 다섯 배라 CPU로는 답을 얻을 수 없습니다.

## 쓰는 법

1. 여기서 `uv run python scripts/export_corpus_for_encoding.py`
2. `derived/<version>/corpus_for_encoding.jsonl`과 이 파일을 Colab에 올립니다
3. 위 셀을 돌립니다(T4 기준 37,854편에 20~40분)
4. 나온 `local-<slug>.npy`를 `derived/<version>/`에 둡니다
5. 여기서 두 하네스를 돌립니다

```bash
uv run python -m models.embedding_tuning.compare_models --model BAAI/bge-m3   # 인용 과제
uv run python -m evals.related_study --cases evals/cases_related_future.json \
    --variants vector --embeddings derived/2026-10-14/local-baai-bge-m3.npy
```

## 왜 텍스트를 다시 만들지 않는가

**순서와 문자열이 내보낸 파일에 굳어 있습니다.** Colab이 스냅샷에서 다시 만들면 공백 하나,
정렬 하나가 달라질 수 있고 그러면 재는 것이 모델이 아니라 **전처리 차이**입니다. 이 스크립트는
`text` 필드를 그대로 읽기만 합니다.

`normalize_embeddings=True`와 `float32`도 기존 비교와 같습니다
(`models/embedding_tuning/compare_models.py`). BGE가 권하는 질의 접두사는 **쓰지 않습니다** —
그 접두사는 짧은 질의로 긴 문서를 찾는 비대칭 검색용이고 이 과제는 논문으로 논문을 찾는
대칭 검색입니다.

## 8B를 넣지 않는 이유

`Qwen3-Embedding-8B`는 fp16으로도 가중치가 16GB라 배포 경로가 없습니다. 이겨도 쓸 수
없으므로 재지 않습니다 — **쓸 수 없는 것이 더 좋다는 사실**을 아는 데 시간을 쓰지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from time import perf_counter


def slug(name: str) -> str:
    """`compare_models.slug`과 같아야 합니다 - 파일 이름이 어긋나면 캐시를 못 찾습니다."""
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def main() -> int:
    parser = argparse.ArgumentParser(description="Colab에서 말뭉치를 인코딩합니다.")
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    import numpy as np
    from sentence_transformers import SentenceTransformer

    rows = [json.loads(line) for line in args.corpus.read_text(encoding="utf-8").splitlines()]
    texts = [row["text"] for row in rows]
    print(f"{len(texts)}편을 {args.model}로 인코딩합니다.")

    model = SentenceTransformer(args.model, trust_remote_code=True)
    start = perf_counter()
    vectors = model.encode(
        texts, batch_size=args.batch, normalize_embeddings=True,
        show_progress_bar=True, convert_to_numpy=True,
    )
    seconds = perf_counter() - start

    matrix = np.asarray(vectors, dtype=np.float32)
    output = args.output or Path(f"local-{slug(args.model)}.npy")
    np.save(output, matrix)
    # 사이드카를 함께 남깁니다. 행렬만 있으면 어느 모델로 구운 것인지 알 수 없습니다.
    output.with_suffix(".json").write_text(
        json.dumps({
            "model": args.model,
            "rows": int(matrix.shape[0]),
            "dimensions": int(matrix.shape[1]),
            "max_seq_length": int(getattr(model, "max_seq_length", 0) or 0),
            "normalize_embeddings": True,
            "encode_seconds": round(seconds, 1),
            "corpus": args.corpus.name,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "output": str(output),
        "rows": int(matrix.shape[0]),
        "dimensions": int(matrix.shape[1]),
        "minutes": round(seconds / 60, 1),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
