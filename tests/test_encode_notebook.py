"""Colab 인코딩 노트북이 **그대로 실행만 하면 되는** 상태인지 검증합니다.

노트북은 손으로 고치기 쉬워서 조용히 깨집니다. 고정할 것은 셋입니다.

1. **문법이 맞습니다.** 셀 하나가 깨지면 Colab에서야 알게 됩니다.
2. **텍스트를 다시 만들지 않습니다.** 말뭉치의 `text`를 읽기만 해야 합니다 - 스냅샷에서
   재구성하면 재는 것이 모델이 아니라 전처리 차이입니다(ADR-039).
3. **인코딩 조건이 기존 비교와 같습니다.** `normalize_embeddings=True`와 `float32`가
   어긋나면 숫자를 나란히 놓을 수 없습니다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

NOTEBOOK = Path("notebooks/encode_corpus_colab.ipynb")


@pytest.fixture(scope="module")
def notebook():
    if not NOTEBOOK.exists():
        pytest.skip(f"노트북이 없습니다: {NOTEBOOK}")
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def sources(notebook, kind="code"):
    return ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == kind]


def test_every_code_cell_compiles(notebook):
    for index, source in enumerate(sources(notebook)):
        # `%pip`은 IPython 매직이라 파이썬 문법이 아닙니다.
        compile(source.replace("%pip", "# %pip"), f"<cell {index}>", "exec")


def test_the_notebook_reads_the_frozen_text_instead_of_rebuilding_it(notebook):
    body = "\n".join(sources(notebook))

    assert "row['text']" in body or 'row["text"]' in body
    # 제목·초록을 직접 이어 붙이면 전처리가 갈립니다.
    assert "abstract" not in body


def test_the_encoding_conditions_match_the_existing_comparison(notebook):
    body = "\n".join(sources(notebook))

    assert "normalize_embeddings=True" in body
    assert "float32" in body
    # 행 수 검사가 없으면 어긋난 행렬을 그대로 가져옵니다.
    assert "행 수가 다릅니다" in body


def test_the_notebook_refuses_to_run_the_full_encode_without_a_gpu(notebook):
    """CPU로는 10시간을 넘깁니다. 시작하는 것 자체가 낭비입니다."""
    body = "\n".join(sources(notebook))

    assert "cuda.is_available()" in body
    assert "GPU 없이 전체 인코딩을 시작하지 않습니다" in body


def test_the_notebook_says_which_model_is_out_of_scope(notebook):
    """8B는 이겨도 배포 경로가 없습니다. 노트북이 그것을 적어야 돌리지 않습니다."""
    body = "\n".join(sources(notebook, "markdown"))

    assert "Qwen3-Embedding-8B" in body
    assert "배포 경로가 없습니다" in body
