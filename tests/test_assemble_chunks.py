"""조각을 합칠 때 **행 순서가 보존되는지** 검증합니다.

섞인 행렬은 그냥 돌아갑니다. 모든 논문의 임베딩이 다른 논문의 것이 되고 점수만 조용히
낮아지므로, 나중에 발견하면 그 점수가 모델의 사실인지 조립 실수인지 **구별할 수 없습니다.**
그래서 합치기 전에 전부 검사합니다.

고정할 것이 넷입니다.

1. **노트북과 같은 순서**로 합칩니다(`sorted()` + 6자리 0채움).
2. 이름이 규칙을 벗어나면 **거부합니다** — 채움이 없으면 정렬이 숫자순과 다릅니다.
3. 조각이 **빠지면** 거부합니다 — 한 조각이 없으면 그 뒤가 전부 밀립니다.
4. 행 수가 `embedding_ids.json`과 다르면 거부합니다 — 행 i가 논문 i여야 합니다.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

from scripts.assemble_chunks import STEP, assemble, card, chunk_paths, inspect, slug_of


def write_chunks(folder, sizes, *, dimensions=4, pad=6):
    """Chunks whose every value is its own global row index, so order is checkable."""
    folder.mkdir(parents=True, exist_ok=True)
    begin = 0
    for size in sizes:
        rows = np.full((size, dimensions), 0.0, dtype=np.float32)
        for offset in range(size):
            rows[offset, :] = begin + offset
        np.save(folder / f"{begin:0{pad}d}.npy", rows)
        begin += STEP
    return folder


def test_the_slug_matches_the_notebook():
    """이름이 갈라지면 비교 보고서가 다른 파일을 가리킵니다."""
    assert slug_of("BAAI/bge-m3") == "baai-bge-m3"
    assert slug_of("Qwen/Qwen3-Embedding-0.6B") == "qwen-qwen3-embedding-0-6b"
    assert slug_of("BAAI/bge-base-en-v1.5") == "baai-bge-base-en-v1-5"


def test_rows_keep_their_global_order(tmp_path):
    """조각 둘을 이어 붙인 뒤 행 i의 값이 i여야 합니다."""
    folder = write_chunks(tmp_path / "chunks", [STEP, STEP, 7])

    matrix = assemble(folder)

    assert matrix.shape == (STEP * 2 + 7, 4)
    assert matrix[0, 0] == 0
    assert matrix[STEP, 0] == STEP  # 둘째 조각의 첫 행
    assert matrix[-1, 0] == STEP * 2 + 6
    assert np.array_equal(matrix[:, 0], np.arange(matrix.shape[0], dtype=np.float32))


def test_an_unpadded_name_is_refused(tmp_path):
    """`10000.npy`가 `2000.npy`보다 앞에 오면 행이 섞입니다."""
    folder = write_chunks(tmp_path / "chunks", [STEP, STEP], pad=1)

    with pytest.raises(SystemExit, match="0으로 채워져"):
        chunk_paths(folder)


def test_a_missing_chunk_is_named_not_silently_skipped(tmp_path):
    folder = write_chunks(tmp_path / "chunks", [STEP, STEP, STEP])
    (folder / f"{STEP:06d}.npy").unlink()

    report = inspect(folder, expected_rows=STEP * 3)

    assert report["problems"]
    assert any("이어지지 않습니다" in problem for problem in report["problems"])


def test_mixed_dimensions_are_refused(tmp_path):
    """모델을 바꿔 다시 돌린 조각이 섞이면 안 됩니다."""
    folder = write_chunks(tmp_path / "chunks", [STEP])
    np.save(folder / f"{STEP:06d}.npy", np.zeros((STEP, 8), dtype=np.float32))

    report = inspect(folder, expected_rows=STEP * 2)

    assert any("차원이 다릅니다" in problem for problem in report["problems"])
    assert report["dimensions"] is None


def test_a_row_count_that_misses_the_corpus_is_refused(tmp_path):
    folder = write_chunks(tmp_path / "chunks", [STEP, 5])

    report = inspect(folder, expected_rows=STEP + 6)

    assert any("총 행 수가 다릅니다" in problem for problem in report["problems"])


def test_a_clean_folder_has_no_problems(tmp_path):
    folder = write_chunks(tmp_path / "chunks", [STEP, STEP, 1854])

    report = inspect(folder, expected_rows=STEP * 2 + 1854)

    assert report["problems"] == []
    assert report["chunks"] == 3
    assert report["dimensions"] == 4


def test_the_card_does_not_invent_what_only_the_run_knows():
    """`minutes`·`batch_size_used`는 실행만 압니다. 추측하면 조용한 거짓이 됩니다."""
    matrix = np.full((3, 4), 0.5, dtype=np.float32)

    entry = card("BAAI/bge-m3", matrix)

    assert entry["rows"] == 3 and entry["dimensions"] == 4
    assert "minutes" not in entry and "batch_size_used" not in entry
    assert entry["assembled_from_chunks"] is True


def test_the_card_records_whether_the_matrix_is_normalised():
    unit = np.zeros((2, 4), dtype=np.float32)
    unit[:, 0] = 1.0
    assert card("m", unit)["normalize_embeddings"] is True
    assert card("m", unit)["norm_median"] == pytest.approx(1.0)

    scaled = unit * 3.0
    assert card("m", scaled)["normalize_embeddings"] is False


def test_the_corpus_row_count_this_repo_expects():
    """조립 결과를 대조할 상대입니다. 37,854가 아니면 파생 캐시가 바뀐 것입니다."""
    from pathlib import Path

    ids = Path("derived/2026-10-14/embedding_ids.json")
    if not ids.exists():
        pytest.skip(f"파생 캐시가 없습니다: {ids}")

    assert len(json.loads(ids.read_text(encoding="utf-8"))) == 37_854
