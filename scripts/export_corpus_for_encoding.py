"""다른 임베딩 모델로 인코딩할 말뭉치를 **순서까지 고정해서** 내보냅니다.

    uv run python scripts/export_corpus_for_encoding.py

**왜 텍스트를 내보내는가.** Colab이 스냅샷에서 텍스트를 다시 만들면 공백 하나, 순서 하나가
달라질 수 있고, 그러면 재는 것이 모델이 아니라 **전처리 차이**가 됩니다. 기존 비교
(`models/embedding_tuning/compare_models.py`)가 쓰는 것과 **같은 문자열, 같은 순서**를 파일로
굳혀서 보냅니다.

| 고정하는 것 | 값 |
|---|---|
| 텍스트 | `f"{title}\\n{abstract}".strip()` |
| 순서 | 파생 캐시의 `embedding_ids.json` |
| 정규화 | 인코딩 쪽에서 `normalize_embeddings=True` |
| dtype | `float32` |

이 PC에는 GPU가 없고 bge-base(109M) 인코딩이 CPU로 3.5시간이었습니다. BGE-M3(568M)·
Qwen3-Embedding-0.6B(595M)는 그 다섯 배라 **CPU로는 답을 얻을 수 없습니다.** 그래서 인코딩만
Colab에서 하고, 돌아오는 것은 행렬 하나입니다(37,854 x 1024 float32 = 155MB).

돌아온 행렬은 `derived/<version>/local-<slug>.npy`에 두면 기존 하네스 둘이 그대로 읽습니다 —
`compare_models`(인용 과제)와 `related_study --embeddings`(관련 논문 과제).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `pipeline`을 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import features  # noqa: E402
from pipeline import snapshot as snapshot_module  # noqa: E402

NAME = "corpus_for_encoding.jsonl"
MANIFEST = "corpus_for_encoding.json"


def build_text(row: dict) -> str:
    """The exact string `compare_models.encode_corpus` builds. 바꾸면 비교가 깨집니다."""
    return f"{row.get('title') or ''}\n{row.get('abstract') or ''}".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="인코딩용 말뭉치를 순서까지 고정해 내보냅니다.")
    parser.add_argument("--version", help="생략하면 가장 최근 스냅샷입니다.")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    cache = features.Cache(snap)
    texts = {row["arxiv_id"]: build_text(row) for row in snap.rows("papers")}

    destination = cache.directory / NAME
    empty = 0
    with destination.open("w", encoding="utf-8") as handle:
        for arxiv_id in cache.ids:
            text = texts.get(arxiv_id, "")
            empty += not text
            handle.write(
                json.dumps({"arxiv_id": arxiv_id, "text": text}, ensure_ascii=False) + "\n"
            )

    card = {
        "snapshot_version": snap.version,
        "papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "rows": len(cache.ids),
        "empty_texts": empty,
        "text_recipe": 'f"{title}\\n{abstract}".strip()',
        "order": "derived/<version>/embedding_ids.json",
        "encode_with": {
            "normalize_embeddings": True,
            "dtype": "float32",
            "save_as": "derived/<version>/local-<slug>.npy",
        },
        "note": (
            "순서와 문자열을 여기서 굳힙니다. 인코딩하는 쪽은 이 파일을 그대로 읽기만 "
            "하세요 - 스냅샷에서 다시 만들면 전처리 차이를 재게 됩니다."
        ),
    }
    (cache.directory / MANIFEST).write_text(
        json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "corpus": str(destination).replace("\\", "/"),
        "manifest": str(cache.directory / MANIFEST).replace("\\", "/"),
        **{key: card[key] for key in ("rows", "empty_texts", "snapshot_version")},
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
