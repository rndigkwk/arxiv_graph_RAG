"""공동인용 파인튜닝 노트북(ADR-098)과 학습 쌍 내보내기가 ADR에 적은 그대로인지 봅니다.

공통 불변식(문법, 출력 없이 커밋, 한 줄 명령)은 `test_colab_notebooks.py`가 봅니다. 여기는 이
노트북만의 조건입니다 - 바를 움직이지 않는가, 바꾸는 것이 쌍의 정의 하나인가, 디스크가 차서 죽지
않는가(2026-10-29).
"""

import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from models.embedding_tuning.export_cocitation import cocitation_rows, isolated_seeds

NOTEBOOK = Path("notebooks/finetune_cocite_runpod.ipynb")
ADR = Path("docs/adr/ADR-098-fine-tune-on-co-citation-pairs.md")
SCALE_NOTEBOOK = Path("notebooks/finetune_scale_runpod.ipynb")


@pytest.fixture(scope="module")
def cells():
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    return [("".join(cell["source"]), cell["cell_type"]) for cell in notebook["cells"]]


@pytest.fixture(scope="module")
def body(cells):
    return "\n".join(text for text, _ in cells)


def code_cells(cells):
    return [text for text, kind in cells if kind == "code"]


def test_the_bar_matches_the_adr(body):
    adr = ADR.read_text(encoding="utf-8")
    for number in ("0.1227", "0.4739", "0.3457", "0.1124", "0.2154", "0.783"):
        assert number in body, number
        assert number in adr, number
    assert "동점은 통과가 아닙니다" in body
    assert "`cocite-b256-e3`만 판정합니다" in body


def test_only_the_pair_definition_changes(body):
    """`b256-e3`과 다른 것이 쌍 하나여야 차이를 쌍에 돌릴 수 있습니다."""
    scale = "\n".join("".join(c["source"]) for c in
                      json.loads(SCALE_NOTEBOOK.read_text(encoding="utf-8"))["cells"])
    for line in ('BASE_MODEL = "BAAI/bge-base-en-v1.5"', "MAX_SEQ_LENGTH = 512",
                 "LEARNING_RATE = 2e-5", "WARMUP_RATIO = 0.1", "SEED = 20261015",
                 "batch_sampler=BatchSamplers.NO_DUPLICATES",
                 "CachedMultipleNegativesRankingLoss(model, mini_batch_size=MINI_BATCH)"):
        assert line in body and line in scale, line
    assert body.count('"primary": True') == 1
    assert '{"name": "cocite-b256-e3", "batch_size": 256, "epochs": 3, "primary": True}' in body
    assert "train_pairs.jsonl.gz" not in body


def test_the_pairs_file_is_pinned(body):
    adr = ADR.read_text(encoding="utf-8")
    sha = next(line.split('"')[1] for line in body.splitlines() if line.startswith("PAIRS_SHA256"))
    assert len(sha) == 64 and sha in adr
    assert "pairs_digest != PAIRS_SHA256" in body
    manifest = Path("exports/colab/cocite_manifest.json")
    if manifest.exists():  # 내보낸 PC에서만 있습니다(exports/는 커밋하지 않습니다)
        assert json.loads(manifest.read_text(encoding="utf-8"))["file"]["content_sha256"] == sha


def test_the_training_does_not_see_the_future(body):
    future = json.loads(Path("evals/cases_related_future.json").read_text(encoding="utf-8-sig"))
    assert f'"train_citers_before": "{future["feature_cut"]}"' in body
    assert f'"snapshot": "{future["snapshot_version"]}"' in body
    assert 'pair_manifest["leakage"]["isolated_seeds_in_pairs"] != 0' in body


def test_every_token_is_asked_before_the_training(cells):
    code = code_cells(cells)
    asked = [i for i, text in enumerate(code) if "getpass.getpass(" in text]
    trained_at = next(i for i, text in enumerate(code) if "trainer.train(" in text)
    assert asked and max(asked) < trained_at
    assert "print(token" not in "\n".join(code)


def test_the_curve_leaves_the_pod(body):
    assert "dagshub.com" in body
    assert '"report_to": ["mlflow"]' in body
    assert "mlflow-skinny" in body
    assert "load_best_model_at_end" not in body


def test_the_disk_does_not_fill_up(cells):
    """2026-10-29: 체크포인트를 쓰다 디스크가 차서 죽었고, 쓰다 만 것이 재개를 막았습니다."""
    train_cell = next(text for text in code_cells(cells) if "trainer.train(" in text)
    assert "save_total_limit=1," in train_cell
    resume_at = train_cell.index("get_last_checkpoint(str(ckpt_dir))")
    assert train_cell.index("drop_broken_checkpoints(ckpt_dir)") < resume_at
    assert train_cell.index('require_free_disk("학습")') < train_cell.index("trainer.train(")
    assert train_cell.index("stamp.write_text") < train_cell.index("shutil.rmtree(ckpt_dir")
    body = "\n".join(code_cells(cells))
    assert 'require_free_disk("인코딩")' in body
    assert '(checkpoint / "trainer_state.json").exists()' in body
    assert "VOLUME_GB - used_gb(WORK_DIR)" in body


def test_only_this_notebooks_outputs_are_uploaded(body):
    """같은 볼륨에 ADR-097 행렬이 남아 있어도 다시 올리지 않습니다."""
    assert '"cocite" not in path.name' in body
    assert "--embeddings derived/2026-10-14/local-bge-arxiv-{name}.npy" in body
    assert 'REPORT_PATH = OUT / f"finetune_cocite_report{SUFFIX}.json"' in body


def test_a_smoke_run_keeps_its_pairs(body):
    """공동인용 쌍은 두 논문이 모두 줄인 말뭉치에 있어야 남습니다 - 쌍을 먼저 고릅니다."""
    smoke = body[body.index("if SMOKE:\n    keep_ids"):]
    assert smoke.index("train_rows = train_rows[:SMOKE_PAIRS]") < smoke.index("extra = [")
    assert 'SUFFIX = "-smoke" if SMOKE else ""' in body


def fake_corpus(references):
    return SimpleNamespace(references={k: set(v) for k, v in references.items()})


def test_cocitation_rows_cap_each_citer():
    corpus = fake_corpus({"a": [f"p{i}" for i in range(30)], "b": ["x", "y", "z"]})
    rows = cocitation_rows(corpus, ["a", "b"], limit=1000, per_citer=5, seed=1)
    by_citer = {}
    for row in rows:
        by_citer[row["citing"]] = by_citer.get(row["citing"], 0) + 1
        assert row["anchor"] != row["positive"]
    assert by_citer == {"a": 5, "b": 3}


def test_cocitation_rows_trim_to_the_limit_and_repeat():
    corpus = fake_corpus({c: [f"{c}{i}" for i in range(8)] for c in "abcd"})
    first = cocitation_rows(corpus, list("abcd"), limit=10, per_citer=20, seed=7)
    second = cocitation_rows(corpus, list("abcd"), limit=10, per_citer=20, seed=7)
    assert len(first) == 10 and first == second
    random.seed(0)  # 전역 난수에 기대지 않습니다
    assert cocitation_rows(corpus, list("abcd"), limit=10, per_citer=20, seed=7) == first


def test_isolated_seeds_are_those_no_train_citer_touches():
    corpus = fake_corpus({"c1": ["s1", "p"], "s2": ["q"]})
    assert isolated_seeds(corpus, ["c1", "s2"], {"s1", "s2", "s3"}) == {"s3"}
