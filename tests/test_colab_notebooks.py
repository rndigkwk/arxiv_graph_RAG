"""Colab 노트북 **전부**에 같은 불변식을 겁니다.

**보편적인 것만 겁니다.** 처음에 OOM 반감·CUDA 컨텍스트 가드·"토큰은 한 번만"까지 전역으로
걸었다가 옛 노트북 셋에서 깨졌습니다. 재 보니 **제가 소급해서 규칙을 씌운 것**이었습니다 —
그 셋은 2026-10-20 교차 인코더 사고에서 나온 특정 교훈이고, `finetune_embedding_colab`은
HF와 DagsHub **서로 다른 토큰 둘**을 묻는 것이 정당합니다. 그래서 그 조건들은 아래
미세조정 노트북 절로 옮겼습니다.

그 과정에서 **진짜 결함 하나**를 찾았습니다 — `finetune_embedding_colab.ipynb`이 출력이
담긴 채 커밋돼 있었습니다(12셀). 저장소가 다른 노트북에 대해 이미 선언한 불변식인데
테스트가 없어서 몰랐습니다.

노트북 테스트가 하나뿐이었고 `encode_corpus_colab.ipynb` **한 파일에 박혀** 있었습니다
([`test_encode_notebook.py`](test_encode_notebook.py)). 그래서 `rerank_pairs_colab`과
`finetune_embedding_colab`은 **검사 밖에 있었습니다** — 출력이 섞인 채 커밋돼도, 코드 셀이
구문 오류여도 아무도 몰랐습니다.

이 저장소에서 손으로 적은 목록이 조용히 빠뜨린 자리를 **아홉 번째**로 봅니다. 그래서
`notebooks/*_colab.ipynb`를 **글로브로** 집습니다 — 새 노트북이 자동으로 들어옵니다.

여기 두는 것은 **노트북 종류와 무관한 성질**뿐입니다. 특정 노트북의 조건(어느 모델이어야
하는가, 어떤 측정이 배치를 정했는가)은 그 노트북의 테스트에 남습니다.
"""

import json
import subprocess
from pathlib import Path

import pytest

NOTEBOOKS = sorted(Path("notebooks").glob("*_colab.ipynb"))
# 이 수가 줄면 노트북이 사라졌거나 이름 규칙이 바뀐 것입니다. 늘면 그냥 새 노트북입니다.
EXPECTED_AT_LEAST = 4


def ids(paths):
    return [path.name for path in paths]


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def code_cells(notebook: dict):
    return [
        (index, "".join(cell["source"]))
        for index, cell in enumerate(notebook["cells"])
        if cell["cell_type"] == "code"
    ]


def without_magics(source: str) -> str:
    """`!pip`·`%pip` 줄을 주석으로 바꿉니다. **줄 수를 유지해** 오류 위치가 맞습니다."""
    return "\n".join(
        "# " + line if line.lstrip().startswith(("!", "%")) else line
        for line in source.splitlines()
    )


def test_the_glob_finds_the_notebooks():
    """글로브가 빈 목록을 내면 아래 테스트들이 전부 조용히 통과합니다."""
    assert len(NOTEBOOKS) >= EXPECTED_AT_LEAST, ids(NOTEBOOKS)


@pytest.mark.parametrize("path", NOTEBOOKS, ids=ids(NOTEBOOKS))
def test_every_code_cell_compiles(path):
    """구문 오류가 든 노트북을 올리면 사용자가 GPU를 띄운 뒤에 알게 됩니다."""
    import ast

    for index, source in code_cells(load(path)):
        try:
            ast.parse(without_magics(source))
        except SyntaxError as exc:
            pytest.fail(f"{path.name} 셀 {index}: {exc}")


@pytest.mark.parametrize("path", NOTEBOOKS, ids=ids(NOTEBOOKS))
def test_the_notebook_is_committed_without_outputs(path):
    """**작업 사본이 아니라 커밋을 봅니다.**

    Colab에 붙여 돌리는 동안 로컬 파일에는 출력이 계속 쓰이고 그것은 정상입니다. 고정하려는
    성질은 "커밋된 노트북이 실행 전 상태인가"입니다 - 작업 사본을 보면 돌리는 중에 깨집니다.
    """
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{path.as_posix()}"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"커밋된 노트북을 읽지 못했습니다: {exc}")

    for index, cell in enumerate(json.loads(blob)["cells"]):
        if cell["cell_type"] != "code":
            continue
        assert not cell.get("outputs"), f"{path.name} 셀 {index}에 출력이 남아 있습니다"
        assert cell.get("execution_count") is None, f"{path.name} 셀 {index}에 실행 번호"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=ids(NOTEBOOKS))
def test_the_notebook_does_not_rely_on_colab_only_download(path):
    """`files.download()`가 VS Code↔Colab 세션에서 **예외 없이 아무 일도 하지 않습니다.**

    2026-10-20에 그 셀이 끝난 것처럼 보였고 파일은 내려오지 않았습니다. 그래서 가져가기는
    HF를 거칩니다.
    """
    body = "\n".join(source for _, source in code_cells(load(path)))

    assert "files.download" not in body, path.name


# ── 미세조정 노트북의 고유 조건 ───────────────────────────────────────────

FINETUNE = Path("notebooks/rerank_finetune_colab.ipynb")


@pytest.fixture
def finetune():
    if not FINETUNE.exists():
        pytest.skip(f"노트북이 없습니다: {FINETUNE}")
    return load(FINETUNE)


def test_it_survives_an_out_of_memory(finetune):
    """**터지는 대신 느려집니다.** 2026-10-20에 배치 256이 OOM이고 128로 완주했습니다.

    학습은 채점보다 무겁습니다 - 역전파가 활성값을 들고 있어야 하므로 같은 배치에서
    VRAM이 몇 배입니다.
    """
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "OutOfMemoryError" in body
    assert "//= 2" in body


def test_it_checks_the_cuda_context_before_loading_weights(finetune):
    """장치 어서션은 **프로세스 끝까지** CUDA를 망가뜨리고, 보통 모델 로드에서 터져

    원인처럼 보입니다. 작은 연산 하나로 몇 초 만에 가려냅니다(2026-10-20).
    """
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "torch.zeros(8, device='cuda')" in body


def test_it_asks_for_the_token_once_before_the_training(finetune):
    """두 번 묻으면 한 시간 학습이 끝난 뒤 **입력 대기로 멈춥니다.** 찍으면 유출입니다.

    이 노트북은 토큰 하나(HF)만 씁니다 - 둘을 쓰는 노트북에는 이 규칙이 맞지 않습니다.
    """
    cells = ["".join(cell["source"]) for cell in finetune["cells"]]
    body = "\n".join(cells)

    assert body.count("getpass.getpass(") == 1
    assert "print(HF_TOKEN" not in body
    asked_at = next(i for i, text in enumerate(cells) if "getpass.getpass(" in text)
    trained_at = next(i for i, text in enumerate(cells) if "model.fit(" in text)
    assert asked_at < trained_at


def test_the_bar_is_written_in_the_notebook(finetune):
    """**결과를 보고 바를 움직일 수 없게** 합니다(ADR-039·050이 그렇게 해서 판정이 성립).

    ADR-050이 "이 표를 ADR·노트북 양쪽에 지금 적어 둔다"고 적은 그 자리입니다.
    """
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    for number in ("0.1227", "0.4739", "0.3457"):
        assert number in body, number
    # 상한도 적어 두어 좋은 결과를 의심할 수 있게 합니다.
    assert "0.2690" in body
    assert "동점은 통과가 아닙니다" in body


def test_it_opens_in_colab_from_a_badge(finetune):
    """링크를 누르면 바로 Colab에서 열려야 합니다 - 그게 요청받은 것입니다."""
    first = "".join(finetune["cells"][0]["source"])

    assert "colab.research.google.com/github/rndigkwk/arxiv_graph_RAG" in first
    assert "blob/main/notebooks/rerank_finetune_colab.ipynb" in first


def test_the_output_shape_matches_the_pretrained_run(finetune):
    """저장소가 **같은 명령**으로 판정해야 같은 후보·같은 정답·같은 실행이 됩니다."""
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "rerank-" in body and ".jsonl.gz" in body
    assert "'scores'" in body or '"scores"' in body
    assert "evals.related_study" in body


def test_the_scores_it_writes_are_what_the_judge_reads(finetune, tmp_path):
    """**판정기를 실제로 먹여 봅니다.** 모양을 손으로 베끼면 틀립니다 - 틀렸습니다.

    처음에 `scores`를 딕셔너리로 쓰고 `candidates`를 넣지 않아, 판정에서 `KeyError`로
    죽는 파일을 만드는 노트북을 올렸습니다. 노트북 문자열만 보는 테스트로는 못 잡습니다 -
    그래서 노트북이 쓰는 모양으로 파일을 만들어 `RerankScores`에 **넣어 봅니다.**
    """
    import gzip
    import json as _json

    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])
    assert "'candidates': row['candidates']" in body, "candidates를 쓰지 않습니다"

    path = tmp_path / "scores.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(_json.dumps({
            "seed": "2404.04940",
            "candidates": ["2601.00001", "2601.00002"],
            "scores": [0.731, 0.112],
        }) + "\n")

    from evals.related_study import RerankScores

    candidates, values = RerankScores(path).by_seed["2404.04940"]

    assert candidates == ["2601.00001", "2601.00002"]
    assert values.tolist() == [0.731, 0.112]


def test_the_judge_refuses_a_mismatched_score_count(tmp_path):
    """수가 어긋나면 거부해야 합니다 - 잘린 파일이 조용히 판정되면 안 됩니다."""
    import gzip
    import json as _json

    path = tmp_path / "bad.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(_json.dumps({
            "seed": "x", "candidates": ["a", "b"], "scores": [0.1],
        }) + "\n")

    from evals.related_study import RerankScores

    with pytest.raises(ValueError, match="점수 수가 후보 수와"):
        RerankScores(path)


def test_the_slug_does_not_collide_with_the_pretrained_scores(finetune):
    """같은 이름이면 사전학습 점수를 덮어써 비교 상대를 잃습니다."""
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "cocite-ft" in body


def test_the_validation_split_is_by_seed(finetune):
    """쌍 단위로 나누면 같은 시드가 양쪽에 들어가 검증이 샙니다."""
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "val_seeds" in body
    assert "시드 단위로 나눕니다" in body


def test_it_measures_time_and_vram_instead_of_estimating(finetune):
    """2026-10-20에 어림을 두 번 틀렸습니다 - 조각마다 실측을 찍습니다."""
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "max_memory_allocated" in body
    assert "perf_counter" in body


def test_it_refuses_to_train_without_a_gpu(finetune):
    body = "\n".join("".join(cell["source"]) for cell in finetune["cells"])

    assert "GPU 없이 학습을 시작하지 않습니다" in body


def test_the_length_limit_is_checked_before_the_weights_download(finetune):
    """설정은 몇 KB이고, 틀렸다면 2.3GB를 받을 이유가 없습니다."""
    cells = ["".join(cell["source"]) for cell in finetune["cells"]]
    config_at = next(i for i, text in enumerate(cells) if "max_position_embeddings" in text)
    load_at = next(i for i, text in enumerate(cells) if "CrossEncoder(MODEL" in text)

    assert config_at < load_at
