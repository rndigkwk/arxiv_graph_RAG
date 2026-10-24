"""임베딩 단계가 **기존 벡터와 같은 공간**에 머무는지 지킵니다(ADR-064).

`p.embedding_input`에는 `'title+abstract'`라는 **표찰**이 들어 있고 텍스트가 아닙니다. 그래서
입력 형식이 어긋나도 그래프를 봐서는 알 수 없고, 새 논문만 조용히 다른 공간에 들어갑니다.
벡터 검색은 계속 돌고 일부 질문만 답을 못 찾습니다.

그래서 이 파일이 지키는 것은 세 가지입니다.

1. 입력 형식이 **노트북 원본과 같은 문자열**인지 (원본을 읽어서 비교합니다)
2. 음성 대조군이 **실제로 다른 문자열**인지 — 같으면 검사가 아무것도 가려내지 못합니다
3. 돈을 쓰는 경로에 **천장과 건너뛰기**가 있는지
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline.embed import (
    EMBED_BATCH_SIZE,
    EMBEDDING_DIMENSIONS,
    EMBEDDING_INPUT_LABEL,
    EMBEDDING_MAX_CHARS,
    EMBEDDING_MODEL,
    MIN_COSINE,
    attach_embeddings,
    backfill,
    cosine,
    embedding_input,
    estimate_cost,
    has_usable_embedding,
    spaced_input,
    verify_space,
)

ADR = Path("docs/adr/ADR-064-the-pipeline-cannot-finish-without-leaving-the-pipeline.md")
NOTEBOOK = Path("notebooks/arxiv_ai_kg_to_aura.ipynb")
REPORT = Path("reports/embedding_space.json")

PAPER = {"arxiv_id": "2501.00001", "title": "제목", "abstract": "초록"}


class FakeApi:
    """부르면 **정해진 벡터**를 돌려줍니다. 네트워크도 비용도 없습니다."""

    def __init__(self, vectors):
        self.vectors = list(vectors)
        self.calls = 0
        self.embeddings = self

    def create(self, *, input, model, dimensions):  # noqa: A002 - OpenAI의 인자 이름입니다
        self.calls += 1
        assert model == EMBEDDING_MODEL
        assert dimensions == EMBEDDING_DIMENSIONS

        class Item:
            def __init__(self, index, embedding):
                self.index = index
                self.embedding = embedding

        taken = [self.vectors.pop(0) for _ in input]
        return type("Response", (), {"data": [Item(i, v) for i, v in enumerate(taken)]})


def unit(seed: float) -> list[float]:
    """길이가 맞는 벡터 하나입니다. 값은 중요하지 않습니다."""
    return [seed] + [0.0] * (EMBEDDING_DIMENSIONS - 1)


def test_the_input_string_matches_the_notebook():
    """**이 테스트가 이 파일의 이유입니다.** 형식이 갈라지면 공간이 갈라집니다."""
    cells = json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]
    # `source`는 **문자열이거나 리스트**입니다. 그대로 돌면 글자를 순회합니다.
    sources = [
        cell["source"] if isinstance(cell["source"], str) else "".join(cell["source"])
        for cell in cells
    ]
    original = [text for text in sources if "def embedding_input" in text]

    assert original, "노트북에서 원본 embedding_input을 찾지 못했습니다"
    assert 'f"{paper[\'title\']}\\n\\n{paper[\'abstract\']}"' in original[0]
    assert f"EMBEDDING_MAX_CHARS = {EMBEDDING_MAX_CHARS}" in "".join(sources)
    assert f'EMBED_BATCH_SIZE = {EMBED_BATCH_SIZE}' in "".join(sources)


def test_the_input_string_is_two_newlines_and_truncated():
    assert embedding_input(PAPER) == "제목\n\n초록"
    long = {"arxiv_id": "x", "title": "가" * 30000, "abstract": "나"}
    assert len(embedding_input(long)) == EMBEDDING_MAX_CHARS


def test_the_control_is_actually_a_different_string():
    """대조군이 같은 문자열이면 검사가 형식을 가려내지 못합니다."""
    assert spaced_input(PAPER) != embedding_input(PAPER)
    assert "\n" not in spaced_input(PAPER)


def test_the_model_and_dimensions_match_the_stored_corpus():
    from pipeline.quality import EMBEDDING_DIMENSIONS as GATE_DIMENSIONS

    assert EMBEDDING_MODEL == "text-embedding-3-large"
    assert EMBEDDING_DIMENSIONS == GATE_DIMENSIONS == 768
    assert EMBEDDING_INPUT_LABEL == "title+abstract"


def test_verify_fails_when_the_control_is_not_lower():
    """**대조군이 더 낮지 않으면 통과시키지 않습니다.** 코사인이 높아도 그렇습니다."""
    stored = [{"arxiv_id": "1", "title": "t", "abstract": "a", "embedding": unit(1.0)}]
    # 올바른 형식과 틀린 형식이 **같은** 벡터를 받습니다 - 검사가 가려내지 못하는 상황.
    api = FakeApi([unit(1.0), unit(1.0)])

    result = verify_space(lambda *_: stored, api, sample=1)

    assert result["min_cosine"] == pytest.approx(1.0)
    assert result["control_is_lower"] is False
    assert result["passes"] is False


def test_verify_passes_only_above_the_bar():
    stored = [{"arxiv_id": "1", "title": "t", "abstract": "a", "embedding": unit(1.0)}]
    off = [1.0, 0.05] + [0.0] * (EMBEDDING_DIMENSIONS - 2)
    api = FakeApi([unit(1.0), off])

    result = verify_space(lambda *_: stored, api, sample=1)

    assert result["min_cosine"] >= MIN_COSINE
    assert result["control_is_lower"] is True
    assert result["passes"] is True


def test_cosine_is_one_for_the_same_vector():
    assert cosine(unit(2.0), unit(3.0)) == pytest.approx(1.0)
    assert cosine([0.0] * EMBEDDING_DIMENSIONS, unit(1.0)) == 0.0


def test_already_embedded_records_are_skipped():
    """끊긴 실행을 다시 돌려도 같은 논문에 두 번 돈을 쓰지 않습니다."""
    done = {"arxiv_id": "a", "title": "t", "abstract": "a", "embedding": unit(1.0)}
    todo = {"arxiv_id": "b", "title": "u", "abstract": "b"}
    api = FakeApi([unit(2.0)])

    summary = attach_embeddings([done, todo], api)

    assert summary["skipped_already_embedded"] == 1
    assert summary["to_embed"] == 1
    assert summary["embedded"] == 1
    assert api.calls == 1
    assert has_usable_embedding(todo)


def test_the_cost_ceiling_refuses_before_calling():
    """천장을 넘으면 **호출하지 않습니다.** 추정만 적습니다."""
    papers = [
        {"arxiv_id": str(i), "title": "t" * 400, "abstract": "a" * 4000}
        for i in range(50)
    ]
    api = FakeApi([unit(1.0)] * 50)

    summary = attach_embeddings(papers, api, max_cost_usd=0.0)

    assert summary["stopped_at"] == "cost_ceiling"
    assert summary["embedded"] == 0
    assert api.calls == 0
    assert summary["tokens"] > 0


def test_the_wrong_dimension_is_rejected():
    api = FakeApi([[0.1, 0.2]])

    with pytest.raises(ValueError, match="차원"):
        attach_embeddings([{"arxiv_id": "a", "title": "t", "abstract": "a"}], api)


def test_a_non_finite_value_is_rejected():
    api = FakeApi([[float("nan")] + [0.0] * (EMBEDDING_DIMENSIONS - 1)])

    with pytest.raises(ValueError, match="유한"):
        attach_embeddings([{"arxiv_id": "a", "title": "t", "abstract": "a"}], api)


def test_papers_the_graph_already_embedded_are_skipped():
    """첫 실행은 수집한 934편 중 이미 벡터가 있던 123편에 다시 돈을 썼습니다."""
    records = [
        {"arxiv_id": "a", "title": "t", "abstract": "x"},
        {"arxiv_id": "b", "title": "u", "abstract": "y"},
    ]
    api = FakeApi([unit(2.0)])

    summary = attach_embeddings(records, api, already_embedded={"a"})

    assert summary["skipped_in_graph"] == 1
    assert summary["to_embed"] == 1
    assert api.calls == 1
    assert "embedding" not in records[0]
    assert has_usable_embedding(records[1])


class FakeGraph:
    """`MISSING_CYPHER`를 두 번 받습니다 - 채우기 전과 **되읽을 때**."""

    def __init__(self, missing):
        self.remaining = list(missing)
        self.writes = []

    def read(self, cypher, params):
        return list(self.remaining)

    def write(self, cypher, params):
        written = {row["arxiv_id"] for row in params["rows"]}
        self.remaining = [r for r in self.remaining if r["arxiv_id"] not in written]
        self.writes.append(params)
        return {"properties_set": len(written) * 4}


def test_backfill_fills_and_reads_back():
    """**쓴 횟수가 아니라 그래프의 상태가 결론입니다.**"""
    missing = [
        {"arxiv_id": "a", "title": "t", "abstract": "x"},
        {"arxiv_id": "b", "title": "u", "abstract": "y"},
    ]
    graph = FakeGraph(missing)
    api = FakeApi([unit(1.0), unit(2.0)])

    result = backfill(graph.read, graph.write, api)

    assert result["missing"] == 2
    assert result["upserted"] == 2
    assert result["still_missing"] == 0
    assert result["complete"] is True
    # 표찰과 모델도 같이 씁니다 - 표찰만 빠지면 --verify가 그 논문을 못 고릅니다.
    assert graph.writes[0]["input"] == EMBEDDING_INPUT_LABEL
    assert graph.writes[0]["model"] == EMBEDDING_MODEL


def test_backfill_reports_what_it_could_not_fill():
    """되읽어 남아 있으면 **완료라고 하지 않습니다.**"""

    class Stubborn(FakeGraph):
        def write(self, cypher, params):
            return {"properties_set": 0}  # 쓰기가 아무것도 바꾸지 못한 경우

    graph = Stubborn([{"arxiv_id": "a", "title": "t", "abstract": "x"}])
    api = FakeApi([unit(1.0)])

    result = backfill(graph.read, graph.write, api)

    assert result["upserted"] == 1
    assert result["still_missing"] == 1
    assert result["complete"] is False
    assert result["stopped_at"] == "still_missing"


def test_backfill_refuses_over_the_ceiling():
    graph = FakeGraph([
        {"arxiv_id": str(i), "title": "t" * 400, "abstract": "a" * 4000}
        for i in range(50)
    ])
    api = FakeApi([unit(1.0)] * 50)

    result = backfill(graph.read, graph.write, api, max_cost_usd=0.0)

    assert result["stopped_at"] == "cost_ceiling"
    assert result["upserted"] == 0
    assert api.calls == 0


def test_the_loader_read_back_catches_what_the_gate_cannot():
    """**이 테스트가 2026-10-24의 결함 그 자체입니다.**

    상류 가드는 수집 레코드를 봅니다. 적재기가 그 값을 버려도 가드는 `ready: true`라고
    말합니다. 되읽기만이 그래프의 상태를 말할 수 있습니다.
    """
    from pipeline.ingest import verify_loaded_embeddings

    papers = [{"arxiv_id": "a"}, {"arxiv_id": "b"}]
    dropped = verify_loaded_embeddings(
        lambda *_: [{"papers": 2, "with_embedding": 0}], papers
    )
    kept = verify_loaded_embeddings(
        lambda *_: [{"papers": 2, "with_embedding": 2}], papers
    )

    assert dropped["coverage"] == 0.0 and dropped["complete"] is False
    assert kept["coverage"] == 1.0 and kept["complete"] is True


def test_the_cost_arithmetic():
    assert estimate_cost(1_000_000) == pytest.approx(0.13)
    assert estimate_cost(0) == 0.0


def test_embedding_without_loading_is_refused():
    """임베딩만 하면 **돈을 쓰고 버립니다.** 벡터는 메모리 레코드에만 붙습니다."""
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.ingest", "--embed"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )

    assert result.returncode != 0
    assert "--load와 함께" in result.stderr


def test_the_guard_no_longer_points_at_a_notebook():
    """가드가 저장소 밖을 가리키면 그 가드는 통과할 방법이 없습니다."""
    source = Path("pipeline/ingest.py").read_text(encoding="utf-8")
    # **주석을 걷어냅니다.** 설명하는 주석이 토큰 검사에 걸린 적이 세 번 있습니다.
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )

    assert "임베딩을 먼저 만드세요(--embed)" in code
    assert "arxiv_ai_kg_to_aura.ipynb" not in code


def test_the_recorded_verification_passed():
    if not REPORT.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {REPORT}")
    report = json.loads(REPORT.read_text(encoding="utf-8"))

    assert report["min_cosine"] >= MIN_COSINE, report["min_cosine"]
    assert report["control_is_lower"] is True
    assert report["passes"] is True
    # 대조군과의 차이가 측정 잡음보다 커야 검사가 의미 있습니다.
    assert report["margin"] > 0.001, report["margin"]


def test_the_adr_wrote_the_bar_before_spending():
    text = ADR.read_text(encoding="utf-8")

    assert str(MIN_COSINE) in text
    assert "음성 대조군" in text
    assert "비용 천장" in text
    # 혼자 쓰면 돈을 버린다는 결함도 적혀 있어야 합니다.
    assert "돈을 쓰고 버립니다" in text
