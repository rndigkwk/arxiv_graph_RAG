"""밖에서 채점한 쌍 점수를 들여올 때 **후보 집합이 같은지** 검증합니다.

교차 인코더는 GPU가 필요해서 채점이 밖에서 일어납니다. 그 왕복에서 조용히 깨질 수 있는
것이 둘입니다.

1. **후보가 달라집니다.** 다른 스냅샷·다른 `feature_cut`·다른 풀 크기로 내보낸 점수 파일을
   쓰면 `graph-text-gbm`과 다른 후보 집합의 점수로 순위를 매기게 되고, 그러면 "재정렬이
   못 했다"와 "후보가 달랐다"를 구별할 수 없습니다(ADR-029).
2. **순서가 달라집니다.** 후보 순서가 내보내기에서 굳었는데 점수와 짝이 틀어지면 **다른
   논문의 점수**로 순위가 매겨집니다. 그 행렬은 그냥 돌아가고 점수만 낮아집니다.

그래서 들여오는 쪽이 전부 검사합니다. 노트북은 `notebooks/rerank_pairs_colab.ipynb`입니다.
"""

from __future__ import annotations

import gzip
import json
import re
from pathlib import Path

import numpy as np
import pytest

from evals.related_study import GBM_POOL, RerankScores, pool_candidates

NOTEBOOK = Path("notebooks/rerank_pairs_colab.ipynb")


class StubGraph:
    """`pool_candidates`가 쓰는 것만 흉내 냅니다 — ids와 cocited."""

    def __init__(self, ids: list[str], cocited: dict[str, set[str]] | None = None):
        self.ids = ids
        self._cocited = cocited or {}

    def cocited(self, seed: str):
        return self._cocited.get(seed, {})


def write_scores(path: Path, rows: list[dict]) -> Path:
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return path


def test_the_candidate_pool_has_one_definition():
    """`Reranker`와 내보내기가 같은 함수를 불러야 같은 후보를 재게 됩니다."""
    import inspect

    from evals.related_study import Reranker

    body = inspect.getsource(Reranker.candidates)

    assert "pool_candidates(" in body
    assert "argpartition" not in body, "후보 계산이 두 벌이 됐습니다"


def test_the_pool_is_vector_top_n_union_visible_cocitation():
    graph = StubGraph([f"p{i}" for i in range(10)], cocited={"p0": {"p9"}})
    # p1..p3이 상위 점수, p9는 점수가 낮지만 공동인용으로 들어옵니다.
    scores = np.array([0.0, 0.9, 0.8, 0.7, 0.1, 0.1, 0.1, 0.1, 0.1, 0.0])

    pooled = pool_candidates(graph, "p0", scores, pool=3)

    assert "p9" in pooled, "보이는 공동인용이 빠졌습니다"
    assert {"p1", "p2", "p3"} <= set(pooled)
    assert "p0" not in pooled, "시드가 자기 후보에 들어갔습니다"
    assert pooled == sorted(pooled), "후보 순서가 정렬돼 있어야 재현됩니다"


def test_scores_are_read_and_ranked_descending(tmp_path):
    graph = StubGraph(["s", "a", "b", "c"])
    scores = np.array([0.0, 0.9, 0.8, 0.7])
    path = write_scores(tmp_path / "s.jsonl.gz", [
        {"seed": "s", "candidates": ["a", "b", "c"], "scores": [0.1, 0.9, 0.5]},
    ])

    ranked = RerankScores(path, pool=3).rank(graph, "s", scores, 3)

    assert ranked == ["b", "c", "a"], "점수 내림차순이어야 합니다"


def test_only_the_top_k_comes_back(tmp_path):
    # 풀이 결정적이어야 가드가 오탐하지 않습니다. 상위 `pool`에 시드가 포함되므로
    # 후보 셋을 담으려면 논문 다섯·풀 넷이 필요합니다.
    graph = StubGraph(["s", "a", "b", "c", "d"])
    vector_scores = np.array([1.0, 0.9, 0.8, 0.7, 0.1])
    path = write_scores(tmp_path / "s.jsonl.gz", [
        {"seed": "s", "candidates": ["a", "b", "c"], "scores": [0.1, 0.9, 0.5]},
    ])

    assert RerankScores(path, pool=4).rank(graph, "s", vector_scores, 2) == ["b", "c"]


def test_a_score_count_that_does_not_match_the_candidates_is_refused(tmp_path):
    """짝이 틀어지면 **다른 논문의 점수**로 순위가 매겨집니다."""
    path = write_scores(tmp_path / "s.jsonl.gz", [
        {"seed": "s", "candidates": ["a", "b", "c"], "scores": [0.1, 0.9]},
    ])

    with pytest.raises(ValueError, match="점수 수가 후보 수와 다릅니다"):
        RerankScores(path)


def test_a_candidate_this_harness_never_pooled_is_refused(tmp_path):
    """다른 스냅샷이나 feature_cut으로 내보낸 파일의 증거입니다."""
    graph = StubGraph(["s", "a", "b"])
    path = write_scores(tmp_path / "s.jsonl.gz", [
        {"seed": "s", "candidates": ["a", "b", "9999.99999"], "scores": [0.1, 0.2, 0.9]},
    ])

    with pytest.raises(ValueError, match="후보가 아닌 논문"):
        RerankScores(path, pool=2).rank(graph, "s", np.array([0.0, 0.9, 0.8]), 3)


def test_a_subset_is_allowed_because_missing_text_is_counted_upstream(tmp_path):
    """텍스트가 없어 빠진 후보는 내보내기가 세어 둡니다. 그 경우는 막지 않습니다."""
    graph = StubGraph(["s", "a", "b", "c", "d"])
    vector_scores = np.array([1.0, 0.9, 0.8, 0.7, 0.1])
    path = write_scores(tmp_path / "s.jsonl.gz", [
        {"seed": "s", "candidates": ["a", "c"], "scores": [0.2, 0.9]},
    ])

    assert RerankScores(path, pool=4).rank(graph, "s", vector_scores, 2) == ["c", "a"]


def test_a_seed_missing_from_the_scores_is_named(tmp_path):
    graph = StubGraph(["s", "t", "a"])
    path = write_scores(tmp_path / "s.jsonl.gz", [
        {"seed": "s", "candidates": ["a"], "scores": [0.5]},
    ])

    with pytest.raises(ValueError, match="점수 파일에 없습니다"):
        RerankScores(path, pool=1).rank(graph, "t", np.array([0.5, 1.0, 0.9]), 1)


def test_an_empty_scores_file_is_refused(tmp_path):
    with pytest.raises(ValueError, match="점수가 비어 있습니다"):
        RerankScores(write_scores(tmp_path / "empty.jsonl.gz", []))


def test_the_default_pool_matches_the_gbm_reranker():
    """두 재정렬기가 다른 후보를 쓰면 서로 비교할 수 없습니다."""
    assert RerankScores.__init__.__kwdefaults__["pool"] == GBM_POOL


# --- 내보내기 -----------------------------------------------------------------------


def test_the_exporter_refuses_a_gold_file_without_a_feature_cut():
    """자르지 않으면 후보에 라벨 창의 인용자가 섞입니다(ADR-035)."""
    import inspect

    from evals import export_rerank_pairs

    source = inspect.getsource(export_rerank_pairs.main)

    assert "feature_cut이 없습니다" in source
    assert "parse_month(cut_label)" in source


def test_the_exporter_counts_candidates_without_text():
    """조용히 빠지면 Colab이 빈 문자열을 채점하고 그 점수가 사실처럼 보입니다."""
    from evals.export_rerank_pairs import build

    graph = StubGraph(["s", "a", "b", "c"])
    graph.position = {"s": 0}
    # 시드와의 코사인이 s > a > b > c가 되게 둬서 풀(상위 3)이 {a, b}가 되게 합니다.
    graph.vectors = np.array([
        [1.0, 0.0], [0.95, 0.31], [0.8, 0.6], [0.0, 1.0],
    ], dtype=np.float32)
    cases = [{"seed_id": "s"}]
    texts = {"s": "seed", "a": "a text"}   # b의 텍스트가 없습니다

    rows, counts = build(cases, graph, texts, pool=3)

    assert rows == [{"seed": "s", "candidates": ["a"]}]
    assert counts["candidates_without_text"] == 1
    assert counts["pairs"] == 1


# --- 노트북 -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def notebook():
    if not NOTEBOOK.exists():
        pytest.skip(f"노트북이 없습니다: {NOTEBOOK}")
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def sources(notebook, kind="code"):
    return ["".join(cell["source"]) for cell in notebook["cells"] if cell["cell_type"] == kind]


def test_every_code_cell_compiles(notebook):
    for index, source in enumerate(sources(notebook)):
        cleaned = "\n".join(
            f"# {line}" if line.lstrip()[:1] in {"!", "%"} else line
            for line in source.splitlines()
        )
        compile(cleaned, f"<cell {index}>", "exec")


def test_the_notebook_does_not_rely_on_colab_only_download(notebook):
    """RunPod에도 Colab 브라우저 화면이 없습니다(ADR-046에서 배웠습니다)."""
    body = "\n".join(sources(notebook))

    assert "files.download" not in body
    assert "upload_file" in body


def test_the_notebook_scores_without_ranking(notebook):
    """순위는 하네스가 매깁니다 — 동점 처리까지 다른 변형과 같아야 합니다."""
    body = "\n".join(sources(notebook))

    assert "model.predict" in body
    assert "argsort" not in body and "sorted(zip" not in body


def test_the_notebook_records_the_bar_before_measuring(notebook):
    """결과를 보고 바를 움직이지 않기 위해 미리 적습니다(ADR-039)."""
    body = "\n".join(sources(notebook, "markdown"))

    assert "0.1227" in body and "0.4739" in body and "0.3457" in body
    assert "구간이 0을 제외하도록" in body


def test_the_notebook_refuses_a_collapsed_score_file(notebook):
    """헤드가 어긋나면 모든 쌍에 같은 값이 나오고, 그 파일은 그냥 돌아갑니다."""
    body = "\n".join(sources(notebook))

    assert "점수가 전부 같습니다" in body
    assert "시드 수가 다릅니다" in body and "쌍 수가 다릅니다" in body


def test_the_notebook_is_committed_without_outputs():
    import subprocess

    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{NOTEBOOK.as_posix()}"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip(f"커밋된 노트북을 읽지 못했습니다: {exc}")

    for index, cell in enumerate(json.loads(blob)["cells"]):
        if cell["cell_type"] == "code":
            assert not cell.get("outputs"), f"커밋된 셀 {index}에 출력이 남아 있습니다"


def test_the_slug_in_the_notebook_matches_the_repo_rule(notebook):
    """파일 이름이 갈라지면 받는 명령이 다른 파일을 가리킵니다."""
    body = "\n".join(sources(notebook))

    assert r"re.sub(r'[^a-z0-9]+', '-', MODEL.lower()).strip('-')" in body
    # 문서에 적어 둔 예시 이름이 그 규칙과 맞는지.
    expected = "rerank-" + re.sub(r"[^a-z0-9]+", "-", "BAAI/bge-reranker-base".lower()).strip("-")
    assert expected == "rerank-baai-bge-reranker-base"
    assert expected in "\n".join(sources(notebook, "markdown"))


def test_the_token_is_asked_once_and_before_the_long_run(notebook):
    """한 시간 채점이 끝난 뒤 입력을 기다리며 멈춰 있지 않아야 합니다.

    처음에는 업로드 셀에서 물었고, 그러면 채점이 끝난 다음에 토큰을 넣게 됩니다. 받기와
    올리기가 **같은 토큰**을 쓰므로 한 번만 묻고 앞에 둡니다.
    """
    cells = [(index, "".join(cell["source"]))
             for index, cell in enumerate(notebook["cells"])
             if cell["cell_type"] == "code"]

    assert sum(source.count("getpass.getpass") for _, source in cells) == 1

    def first(probe):
        return next(index for index, source in cells if probe in source)

    assert first("getpass.getpass") < first("model.predict"), "토큰이 채점보다 뒤입니다"
    assert first("getpass.getpass") < first("upload_file")
    # 설치보다 뒤여야 합니다 - 설치가 의존성을 바꾸고 나서 임포트해야 합니다.
    assert first("%pip") < first("getpass.getpass")
    # 업로드 셀은 토큰을 다시 묻지 않고 재사용해야 합니다.
    upload = next(source for _, source in cells if "upload_file" in source)
    assert "HF_TOKEN" in upload and "getpass" not in upload


def test_nothing_needs_editing_to_run_as_shipped(notebook):
    """런타임만 고르고 전부 실행하면 돌아야 합니다. 쌍 파일은 HF에 올라가 있습니다."""
    config = "".join(notebook["cells"][2]["source"])

    assert "USE_HF = True" in config
    assert 'DATA_REPO = "arxiv-graphrag-colab"' in config
    # 같은 계열 셋 중 1024를 받는 것은 v2-m3뿐입니다(base·large는 둘 다 512).
    assert 'MODEL = "BAAI/bge-reranker-v2-m3"' in config
    assert "MAX_SEQ = 1024" in config


def test_the_notebook_refuses_to_score_on_the_cpu(notebook):
    """조용히 CPU로 가면 한 시간이 아니라 몇 시간이고, 끝나고서야 압니다."""
    body = "\n".join("".join(cell["source"]) for cell in notebook["cells"]
                     if cell["cell_type"] == "code")

    assert "parameters()).device" in body
    assert "CPU에 올라갔습니다" in body


def test_the_notebook_checks_the_position_limit_before_downloading_weights(notebook):
    """2026-10-20에 여기서 터졌습니다 — `cudaErrorAssert`에는 길이 이야기가 없습니다.

    `bge-reranker-base`는 위치 임베딩이 514(실사용 512)인데 MAX_SEQ를 1024로 줬습니다.
    범위를 벗어난 인덱스를 조회해 GPU 커널이 죽었고, 메시지만 보고는 원인을 알 수
    없었습니다. 설정은 몇 KB이므로 **가중치 2.3GB를 받기 전에** 확인합니다.
    """
    cells = [(index, "".join(cell["source"]))
             for index, cell in enumerate(notebook["cells"])
             if cell["cell_type"] == "code"]
    check = next(source for _, source in cells if "AutoConfig.from_pretrained" in source)

    assert "max_position_embeddings" in check
    assert "MAX_SEQ > _limit" in check
    assert "cudaErrorAssert" in check, "무엇이 터지는지 메시지에 적어야 합니다"

    def first(probe):
        return next(index for index, source in cells if probe in source)

    assert first("AutoConfig.from_pretrained") < first("CrossEncoder(")
    assert first("AutoConfig.from_pretrained") < first("model.predict")


def test_the_oom_handler_does_not_rely_on_one_exception_type(notebook):
    """형으로만 보면 다른 형으로 올라온 OOM을 놓치고 반감 장치가 비껴갑니다."""
    body = "\n".join(sources(notebook))

    assert "def is_oom" in body
    assert "out of memory" in body
    # 채점 셀이 torch를 스스로 가져와야 세션을 다시 시작해도 돕니다.
    score_cell = next(source for source in sources(notebook) if "model.predict" in source)
    assert "import torch" in score_cell


def test_the_pair_length_measurement_is_recorded(notebook):
    """1024를 고른 근거가 셀에 남아 있어야 합니다. 숫자만 바꿔 두면 되짚지 못합니다."""
    config = "".join(notebook["cells"][2]["source"])

    assert "636" in config and "84.8" in config


def test_the_notebook_detects_a_poisoned_cuda_context_early(notebook):
    """`cudaErrorAssert`가 한 번 나면 **그 프로세스의 CUDA가 끝까지 망가집니다.**

    2026-10-20에 길이 초과로 커널이 죽은 뒤, 설정을 고치고 다시 돌렸더니 이번에는
    **모델 로드**(`.to(device)`)에서 터졌습니다 — 모델이 원인처럼 보이지만 피해자입니다.
    CUDA 오류는 비동기로 보고되므로 터진 자리가 원인이 아닙니다. 작은 연산 하나로 먼저
    가려내 "세션을 다시 시작하세요"를 띄웁니다.
    """
    cells = [(index, "".join(cell["source"]))
             for index, cell in enumerate(notebook["cells"])
             if cell["cell_type"] == "code"]
    guard = next(source for _, source in cells if "torch.zeros(8" in source)

    assert "세션 다시 시작" in guard
    assert "망가진 상태입니다" in guard

    def first(probe):
        return next(index for index, source in cells if probe in source)

    # 가드가 모델 로드보다 **앞**이어야 합니다 - 뒤면 로드에서 터지고 원인을 가립니다.
    assert first("torch.zeros(8") < first("CrossEncoder(")
    assert first("torch.zeros(8") < first("%pip"), "설치 전에 몇 초로 가려내야 합니다"
