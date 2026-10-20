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


def without_magics(source: str) -> str:
    """Comment out IPython magics so what is left is the cell's Python.

    `%pip`만 걸러내고 있었는데, `!pip install ...` 꼴의 **셸 매직**에서 깨졌습니다 —
    Colab 노트북에서 정상 문법이고 이 저장소의 지시문도 그 줄을 쓰라고 적습니다. 이 테스트가
    보려는 것은 "셀의 파이썬이 문법에 맞는가"이므로, IPython이 하는 것과 같이 매직 줄을
    먼저 덜어냅니다. 줄 전체를 지우지 않고 **주석으로 바꿉니다** - 지우면 줄 번호가 밀려
    오류 위치를 못 찾습니다.
    """
    lines = []
    for line in source.splitlines():
        stripped = line.lstrip()
        lines.append(f"# {line}" if stripped[:1] in {"!", "%"} else line)
    return "\n".join(lines)


def test_every_code_cell_compiles(notebook):
    for index, source in enumerate(sources(notebook)):
        compile(without_magics(source), f"<cell {index}>", "exec")


def test_the_magic_stripper_keeps_the_python_and_the_line_numbers():
    """매직을 지우면 줄 번호가 밀려 오류 위치를 못 찾습니다."""
    source = "!pip install x\n  %pip install y\nvalue = 1\n"

    cleaned = without_magics(source)

    assert cleaned.splitlines()[2] == "value = 1"
    assert len(cleaned.splitlines()) == len(source.splitlines())
    compile(cleaned, "<test>", "exec")
    # 파이썬 줄은 건드리지 않습니다 - `a != b`의 `!`가 주석이 되면 안 됩니다.
    assert without_magics("a != b") == "a != b"


def test_the_notebook_does_not_rely_on_colab_only_download(notebook):
    """2026-10-20에 배운 것입니다 — `files.download()`는 **조용히 아무것도 하지 않습니다.**

    VS Code로 붙은 세션에는 그 JS를 실행할 Colab 브라우저 화면이 없어서 `Javascript object`만
    찍히고 예외도 없습니다. 44분 실행이 끝난 뒤 파일이 오지 않았다는 것을 로컬을 뒤져서야
    알았습니다. 돌아오는 길은 **어디서 실행해도 되는 것**이어야 합니다(ADR-046).
    """
    body = "\n".join(sources(notebook))

    assert "files.download" not in body
    assert "upload_file" in body, "가져가는 경로가 하나는 있어야 합니다"


def test_the_upload_cell_stands_alone(notebook):
    """인코딩이 끝난 뒤 커널이 끊기는 일이 있습니다. 그때 1절과 이 셀만으로 올라가야 합니다."""
    upload = next(source for source in sources(notebook) if "upload_file" in source)

    for name in ("import re", "from pathlib import Path", "import getpass"):
        assert name in upload, name


def test_the_batch_size_is_justified_by_a_measurement(notebook):
    """배치를 올린 근거가 셀에 남아 있어야 합니다. 숫자만 바꿔 두면 다음 사람이 되짚지 못합니다."""
    config = sources(notebook)[0]

    assert "BATCH = " in config
    # 실측 피크와 표본당 사용량이 적혀 있어야 합니다.
    assert "2.7GB" in config and "27MB" in config


def test_the_notebook_is_committed_without_outputs(notebook):
    """모델을 바꾼 뒤 옛 출력이 남아 있으면 화면이 거짓말을 합니다."""
    for index, cell in enumerate(notebook["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert not cell.get("outputs"), f"셀 {index}에 출력이 남아 있습니다"
        assert cell.get("execution_count") is None, f"셀 {index}에 실행 번호가 남아 있습니다"


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


def test_the_notebook_caps_the_sequence_length_with_a_measured_reason(notebook):
    """8192/32k를 열어 두면 어텐션 활성값이 seq_len^2으로 커집니다 - OOM 위험의 거의 전부입니다.

    이 말뭉치는 **512에서 초과 논문이 0.0%**이므로(advanced.md의 토큰 분포) 자르는 대가가
    없습니다. 기존 비교(512)와 같은 조건이 되는 덤도 있습니다.
    """
    body = "\n".join(sources(notebook))
    prose = "\n".join(sources(notebook, "markdown"))

    assert "MAX_SEQ = 512" in body
    assert "model.max_seq_length = MAX_SEQ" in body
    assert "0.0%" in body or "0.0%" in prose


def test_the_notebook_survives_an_out_of_memory_instead_of_dying(notebook):
    """터지는 대신 느려져야 합니다. 조각 저장과 합쳐지면 다시 돌릴 때 이어서 합니다."""
    body = "\n".join(sources(notebook))

    assert "OutOfMemoryError" in body
    assert "size // 2" in body
    # 피크를 찍지 않으면 여유가 얼마인지 끝날 때까지 모릅니다.
    assert "max_memory_allocated" in body
