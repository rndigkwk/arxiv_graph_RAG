"""스냅샷에서 파생 캐시를 만듭니다.

`papers.jsonl.gz`는 행마다 768개 부동소수를 품고 있어 전체 파싱에 2~3분 걸립니다. 평가
케이스 생성과 링크 예측 학습은 같은 데이터를 여러 번 읽으므로, 한 번만 파싱해서 메타데이터와
임베딩을 따로 떨어뜨려 둡니다.

    uv run python -m pipeline.features

파생물은 스냅샷 버전 아래에 두고, 매니페스트의 papers sha256을 함께 적습니다. 스냅샷이
바뀌면 캐시가 낡았음을 바로 알 수 있어야 합니다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from pipeline import snapshot as snapshot_module
from pipeline.snapshot import Snapshot, month_index

DERIVED_ROOT = Path("derived")
META_NAME = "papers_meta.jsonl"
EMBEDDING_NAME = "embeddings.npy"
ORDER_NAME = "embedding_ids.json"
CACHE_MANIFEST = "cache.json"

META_FIELDS = ("arxiv_id", "title", "primary_category", "categories", "published", "source")


def build(snap: Snapshot, root: Path = DERIVED_ROOT) -> dict[str, object]:
    """Write metadata, the embedding matrix and the ID order for one snapshot."""
    destination = root / snap.version
    destination.mkdir(parents=True, exist_ok=True)
    total = snap.counts["papers"]
    matrix = np.zeros((total, snapshot_dimensions(snap)), dtype=np.float32)
    order: list[str] = []
    missing_embedding = 0
    with (destination / META_NAME).open("w", encoding="utf-8") as handle:
        for row in snap.rows("papers"):
            arxiv_id = row.get("arxiv_id") or ""
            meta = {field: row.get(field) for field in META_FIELDS}
            meta["month_index"] = month_index(arxiv_id)
            handle.write(json.dumps(meta, ensure_ascii=False) + "\n")
            embedding = row.get("embedding")
            if isinstance(embedding, (list, tuple)) and len(embedding) == matrix.shape[1]:
                matrix[len(order)] = np.asarray(embedding, dtype=np.float32)
            else:
                missing_embedding += 1
            order.append(arxiv_id)
    matrix = matrix[: len(order)]
    np.save(destination / EMBEDDING_NAME, matrix)
    (destination / ORDER_NAME).write_text(
        json.dumps(order, ensure_ascii=False), encoding="utf-8"
    )
    cache = {
        "snapshot_version": snap.version,
        "papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "rows": len(order),
        "dimensions": int(matrix.shape[1]),
        "missing_embedding": missing_embedding,
    }
    (destination / CACHE_MANIFEST).write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return cache


def snapshot_dimensions(snap: Snapshot) -> int:
    """Read the embedding width from the first paper rather than assuming 768."""
    for row in snap.rows("papers"):
        embedding = row.get("embedding")
        if isinstance(embedding, (list, tuple)) and embedding:
            return len(embedding)
    raise ValueError("임베딩이 있는 논문을 찾지 못했습니다")


class Cache:
    """Loads the derived files and checks they still belong to the snapshot."""

    def __init__(self, snap: Snapshot, root: Path = DERIVED_ROOT) -> None:
        self.directory = root / snap.version
        manifest = json.loads((self.directory / CACHE_MANIFEST).read_text(encoding="utf-8"))
        expected = snap.manifest["files"]["papers"]["sha256"]
        if expected and manifest["papers_sha256"] != expected:
            raise ValueError(
                "파생 캐시가 스냅샷과 맞지 않습니다. pipeline.features를 다시 실행하세요."
            )
        self.manifest = manifest
        self.ids: list[str] = json.loads(
            (self.directory / ORDER_NAME).read_text(encoding="utf-8")
        )
        self.position = {arxiv_id: index for index, arxiv_id in enumerate(self.ids)}

    def meta(self) -> list[dict[str, object]]:
        """Read the metadata rows in the same order as the embedding matrix."""
        with (self.directory / META_NAME).open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle]

    def embeddings(self, *, mmap: bool = True) -> np.ndarray:
        """Memory-map the embedding matrix; 37,854x768 float32 is 111MB on disk."""
        return np.load(self.directory / EMBEDDING_NAME, mmap_mode="r" if mmap else None)


def main() -> int:
    parser = argparse.ArgumentParser(description="스냅샷 파생 캐시를 만듭니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷을 씁니다.")
    parser.add_argument("--root", type=Path, default=DERIVED_ROOT)
    args = parser.parse_args()
    snap = (
        snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    )
    cache = build(snap, args.root)
    print(json.dumps(cache, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
