"""RunPod 파인튜닝 노트북(ADR-097)이 ADR에 적은 그대로인지 봅니다.

공통 불변식(문법, 출력 없이 커밋, 한 줄 명령)은 `test_colab_notebooks.py`가 봅니다. 여기는 이
노트북만의 조건입니다 - 바를 움직이지 않는가, 판정 하네스가 읽는 모양을 쓰는가, 학습 전에 토큰을
다 받는가.
"""

import json
from pathlib import Path

import pytest

NOTEBOOK = Path("notebooks/finetune_scale_runpod.ipynb")
ADR = Path("docs/adr/ADR-097-fine-tune-at-scale-on-a-rented-gpu.md")


@pytest.fixture(scope="module")
def cells():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return [("".join(cell["source"]), cell["cell_type"]) for cell in notebook["cells"]]


@pytest.fixture(scope="module")
def body(cells):
    return "\n".join(text for text, _ in cells)


def test_the_bar_matches_the_adr(body):
    """**결과를 보고 바를 움직일 수 없게** 노트북과 ADR 양쪽에 같은 수를 적습니다."""
    adr = ADR.read_text(encoding="utf-8")
    for number in ("0.1227", "0.4739", "0.3457", "0.1124", "0.2154", "0.783"):
        assert number in body, number
        assert number in adr, number
    assert "동점은 통과가 아닙니다" in body


def test_only_the_primary_variant_is_judged(body):
    """귀속용 변형까지 판정하면 비교를 세 번 하는 것이라 우연한 통과가 늘어납니다."""
    assert body.count('"primary": True') == 1
    assert '{"name": "b256-e3", "batch_size": 256, "epochs": 3, "primary": True}' in body
    assert "`b256-e3`만 판정합니다" in body


def test_the_training_does_not_see_the_future(body):
    """학습 인용자의 자르는 선이 미래 과제의 feature_cut과 같아야 합니다."""
    future = json.loads(Path("evals/cases_related_future.json").read_text(encoding="utf-8-sig"))

    assert f'"train_citers_before": "{future["feature_cut"]}"' in body
    assert f'"snapshot": "{future["snapshot_version"]}"' in body
    assert 'manifest["split"]["test_citers_from"]' in body


def test_every_token_is_asked_before_the_training(cells):
    """몇 시간 학습이 끝난 뒤 입력 대기로 멈추지 않게 합니다. 토큰은 찍지 않습니다."""
    code = [text for text, kind in cells if kind == "code"]
    asked = [i for i, text in enumerate(code) if "getpass.getpass(" in text]
    trained_at = next(i for i, text in enumerate(code) if "trainer.train(" in text)

    assert asked and max(asked) < trained_at, (asked, trained_at)
    joined = "\n".join(code)
    assert "print(token" not in joined
    assert 'print(os.environ["MLFLOW_TRACKING_PASSWORD"]' not in joined


def test_the_curve_leaves_the_pod(body):
    """팟 안 CSV는 기록이 아닙니다 - 원격 MLflow나 HF에 올린 TensorBoard로 남깁니다."""
    assert "dagshub.com" in body
    assert '"report_to": ["mlflow"]' in body
    assert '"report_to": ["tensorboard"]' in body
    assert "LossCsvCallback" not in body
    assert "추적을 끕니다" in body


def test_the_epoch_is_not_chosen_on_the_eval_queries(body):
    """epoch마다 찍는 점수로 중간 모델을 고르면 평가 질의로 모델을 고른 것입니다."""
    assert "load_best_model_at_end" not in body
    assert "epoch를 고르지 않습니다" in body


def test_a_saved_model_is_reused_only_when_the_config_matches(body):
    """SMOKE가 저장한 모델을 본 실행이 다시 쓰면 그럴듯하고 틀린 숫자가 나옵니다."""
    assert '"smoke": bool(SMOKE)' in body
    assert "trained_on.json" in body
    assert "_have != _want" in body
    assert 'f"out-{variant[\'name\']}-{fp}"' in body


def test_the_matrix_is_what_the_judge_reads(body):
    """판정 하네스가 `--embeddings`로 읽는 이름과 경로를 그대로 찍습니다."""
    assert 'stem = f"local-bge-arxiv-{name}{SUFFIX}"' in body
    assert "--embeddings derived/2026-10-14/local-bge-arxiv-{name}.npy" in body
    assert "--baseline reports/embedding_openai.json" in body
    assert "matrix.shape[0] != len(corpus_ids)" in body
    assert "normalize_embeddings=True" in body


def test_a_smoke_run_never_looks_real(body):
    assert 'SUFFIX = "-smoke" if SMOKE else ""' in body
    assert "이 파일로 판정하지 마세요" in body


def test_it_checks_the_gpu_architecture_before_downloading_weights(cells):
    """Blackwell(sm_120)을 모르는 torch는 모델을 받은 뒤가 아니라 여기서 죽어야 합니다."""
    code = [text for text, kind in cells if kind == "code"]
    arch_at = next(i for i, text in enumerate(code) if "get_arch_list()" in text)
    load_at = next(i for i, text in enumerate(code) if "SentenceTransformer(BASE_MODEL)" in text)

    assert arch_at < load_at
    assert "torch.zeros(8, device='cuda')" in code[arch_at]
    assert "GPU 없이 학습을 시작하지 않습니다" in code[arch_at]


def test_it_survives_an_out_of_memory(body):
    assert "except torch.cuda.OutOfMemoryError" in body
    assert "size //= 2" in body


def test_it_installs_the_tracking_client_not_the_server(body):
    """전체 mlflow는 RunPod 이미지의 blinker에서 설치가 깨집니다(2026-10-22)."""
    assert "mlflow-skinny" in body
    assert "install -q -U mlflow\n" not in body


def test_the_old_model_is_an_instrument_check(body):
    """2026-10-16 모델이 0.2051을 다시 내면 채점이 그때와 같다는 확인입니다."""
    assert '"expected_recall_at_10": 0.2051' in body
    assert "0.1909" in body
