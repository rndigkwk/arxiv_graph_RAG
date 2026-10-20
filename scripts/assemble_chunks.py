"""Colab이 조각으로 저장한 임베딩을 하나의 행렬로 합칩니다.

    uv run python scripts/assemble_chunks.py <조각 폴더> --model BAAI/bge-m3
    uv run python scripts/assemble_chunks.py <조각 폴더> --model BAAI/bge-m3 --check-only

[`notebooks/encode_corpus_colab.ipynb`](../notebooks/encode_corpus_colab.ipynb)는 2,000편씩
`chunks-<slug>/{begin:06d}.npy`로 저장한 뒤 VM 안에서 합쳐 하나의 `.npy`를 만듭니다. 그런데
**합친 파일이 VM을 떠나지 못하는 경우가 있습니다** — `google.colab.files.download()`는 Colab 웹
프론트엔드에 JS를 밀어 넣는 방식이라, VS Code로 붙은 세션에서는 객체만 만들어지고 예외도 없이
아무것도 받아지지 않습니다. 2026-10-20에 BGE-M3 44분 실행이 그 상태였습니다.

조각은 개당 8MB이므로 받기 쉽습니다. 이 스크립트가 그것을 **노트북과 같은 순서로** 합칩니다.

## 순서가 보존되는 이유

이름이 `{begin:06d}`로 0으로 채워져 있어 **사전순 정렬이 숫자순과 같습니다**. 노트북도
`sorted(chunks.glob('*.npy'))`로 합치므로 같은 결과가 나옵니다. 채움이 없으면 `10000.npy`가
`2000.npy`보다 앞에 와서 행이 섞이는데, 그러면 모든 논문의 임베딩이 다른 논문의 것이 되고
**점수는 조용히 나빠지기만 합니다.** 그래서 이름 규칙을 검사합니다.

## 무엇을 검사하는가

| 검사 | 왜 |
|---|---|
| 조각 이름이 전부 6자리 숫자 | 정렬이 숫자순과 같아야 합니다 |
| 시작 위치가 `STEP` 간격으로 빠짐없이 이어짐 | 한 조각이 없으면 그 뒤가 전부 밀립니다 |
| 조각마다 차원이 같음 | 모델을 바꿔 다시 돌린 조각이 섞이면 안 됩니다 |
| 총 행 수 == `embedding_ids.json` | 행 i가 논문 i여야 합니다 |
| 노름 중앙값 | 정규화된 행렬이면 1.0입니다 |

**합치기 전에 전부 봅니다.** 섞인 행렬은 돌아가고 점수만 낮으므로, 나중에 발견하면 그 점수가
모델의 사실인지 조립 실수인지 구별할 수 없습니다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np

# `scripts/`에서 돌리면 최상위가 경로에 없어 `pipeline`을 가져올 수 없습니다
# (`check_doc_numbers.py`와 같은 처리입니다).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DERIVED = Path("derived/2026-10-14")
IDS = DERIVED / "embedding_ids.json"
CHUNK_NAME = re.compile(r"^(\d{6})\.npy$")
STEP = 2000


def slug_of(model: str) -> str:
    """노트북과 같은 규칙입니다. 파일 이름이 갈라지면 비교 보고서가 다른 것을 가리킵니다."""
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-")


def chunk_paths(folder: Path) -> list[Path]:
    """Sorted chunk files, refusing any name whose order cannot be trusted."""
    found = sorted(folder.glob("*.npy"))
    if not found:
        raise SystemExit(f"조각이 없습니다: {folder}")
    bad = [path.name for path in found if not CHUNK_NAME.match(path.name)]
    if bad:
        raise SystemExit(
            f"이름이 `{{begin:06d}}.npy`가 아닌 조각이 있습니다: {bad}. "
            "0으로 채워져 있지 않으면 사전순 정렬이 숫자순과 달라 행이 섞입니다."
        )
    return found


def inspect(folder: Path, *, expected_rows: int | None) -> dict[str, Any]:
    """Read every chunk's shape without concatenating, and report what is wrong."""
    paths = chunk_paths(folder)
    problems: list[str] = []
    rows, dimensions, starts = 0, set(), []
    for path in paths:
        part = np.load(path, mmap_mode="r")
        if part.ndim != 2:
            problems.append(f"{path.name}: 2차원이 아닙니다 {part.shape}")
            continue
        starts.append(int(CHUNK_NAME.match(path.name).group(1)))
        dimensions.add(int(part.shape[1]))
        rows += int(part.shape[0])
    if len(dimensions) > 1:
        problems.append(f"조각마다 차원이 다릅니다: {sorted(dimensions)}")
    # 시작 위치가 0, STEP, 2*STEP ... 으로 빠짐없이 와야 합니다.
    expected_starts = list(range(0, len(starts) * STEP, STEP))
    if starts != expected_starts:
        missing = sorted(set(expected_starts) - set(starts))
        problems.append(
            f"시작 위치가 {STEP} 간격으로 이어지지 않습니다. 빠진 것: {missing[:10]}"
        )
    if expected_rows is not None and rows != expected_rows:
        problems.append(f"총 행 수가 다릅니다: {rows} != {expected_rows}")
    return {
        "folder": str(folder),
        "chunks": len(paths),
        "rows": rows,
        "dimensions": sorted(dimensions)[0] if len(dimensions) == 1 else None,
        "expected_rows": expected_rows,
        "problems": problems,
    }


def assemble(folder: Path) -> np.ndarray:
    """Concatenate in the notebook's order. Call `inspect` first."""
    return np.concatenate([np.load(path) for path in chunk_paths(folder)])


def card(
    model: str, matrix: np.ndarray, *, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The sidecar the notebook writes, rebuilt from the matrix plus recorded facts.

    `minutes`·`batch_size_used`처럼 **실행만 아는 값**은 추측하지 않습니다. 넘겨주면 적고,
    없으면 칸을 비웁니다 - 서지 항목에서와 같은 이유입니다(ADR-042).
    """
    norms = np.linalg.norm(matrix, axis=1)
    return {
        "model": model,
        "rows": int(matrix.shape[0]),
        "dimensions": int(matrix.shape[1]),
        "normalize_embeddings": bool(abs(float(np.median(norms)) - 1.0) < 1e-3),
        "norm_median": round(float(np.median(norms)), 6),
        "assembled_from_chunks": True,
        **(extra or {}),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Colab 조각을 하나의 행렬로 합칩니다.")
    parser.add_argument("folder", type=Path, help="chunks-<slug> 폴더")
    parser.add_argument("--model", required=True, help='예: "BAAI/bge-m3"')
    parser.add_argument("--derived", type=Path, default=DERIVED, help="결과를 둘 곳")
    # **출력 위치와 대조 상대를 분리합니다.** 한 플래그로 묶으면 `--derived`를 다른 곳으로
    # 돌린 순간 행 수 대조가 조용히 건너뛰어집니다 - 꺼지면 안 되는 검사입니다.
    parser.add_argument("--ids", type=Path, default=IDS,
                        help="행 수를 대조할 embedding_ids.json")
    parser.add_argument("--check-only", action="store_true", help="합치지 않고 검사만 합니다.")
    parser.add_argument("--minutes", type=float, help="노트북이 적은 인코딩 시간")
    parser.add_argument("--batch-size", type=int, help="노트북이 쓴 마지막 배치 크기")
    parser.add_argument("--max-seq", type=int, help="노트북이 쓴 max_seq_length")
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()

    expected = None
    if args.ids.exists():
        expected = len(json.loads(args.ids.read_text(encoding="utf-8")))
    else:
        print(
            f"주의: {args.ids}가 없어 행 수를 대조하지 못합니다. "
            "행 i가 논문 i라는 보장이 없습니다."
        )

    report = inspect(args.folder, expected_rows=expected)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["problems"]:
        print("\n합치지 않았습니다. 위 문제를 먼저 해결하세요.")
        return 1
    if args.check_only:
        print("\n검사만 했습니다(--check-only).")
        return 0

    matrix = assemble(args.folder)
    extra = {
        key: value
        for key, value in (
            ("minutes", args.minutes),
            ("batch_size_used", args.batch_size),
            ("max_seq_length", args.max_seq),
        )
        if value is not None
    }
    entry = card(args.model, matrix, extra=extra)
    slug = slug_of(args.model)
    args.derived.mkdir(parents=True, exist_ok=True)
    out = args.derived / f"local-{slug}.npy"
    np.save(out, np.asarray(matrix, dtype=np.float32))
    (args.derived / f"local-{slug}.json").write_text(
        json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    print(f"\n{out}  {out.stat().st_size / 1e6:.0f}MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
