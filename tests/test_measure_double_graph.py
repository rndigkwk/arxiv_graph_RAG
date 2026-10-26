"""그래프 두 배 측정의 시험입니다.

이 측정의 결론은 "같은 컨테이너에서 1배와 2배를 쟀다"에 달려 있습니다. 그 전제가 조용히 깨지는
길이 셋 있고, 시험이 하나씩 막습니다.

- 거울 사본이 원본과 겹치면 `MERGE`가 새 노드를 만들지 않아 2배가 1배로 잽니다.
- 측정용 compose 파일의 메모리·CPU가 1배 기준선과 갈라지면 두 실험을 비교할 수 없습니다.
- 앱 질의를 옮겨 적으면 앱이 바뀌어도 측정은 옛 질의를 잽니다.
"""

import ast
import json
from pathlib import Path

import pytest
import yaml

from scripts import local_graph_test as base
from scripts.measure_double_graph import (
    APP_QUERIES,
    MIRROR,
    URI,
    app_queries,
    compare,
    memory_gib,
    mirror_row,
    parse_du,
    project,
    require_empty,
)

SAMPLE = {
    "papers": [{"arxiv_id": "2501.00001", "title": "t", "embedding": [0.1, 0.2]}],
    "authors": [{"name": "Ada Lovelace"}],
    "authored": [{"author": "Ada Lovelace", "paper": "2501.00001"}],
    "references": [{"citing": "2501.00001", "cited": "2501.00002"}],
}


def test_the_mirror_never_collides_with_the_original():
    """겹치면 MERGE가 기존 노드를 다시 쓰고 2배 측정이 1배를 잽니다."""
    for label, rows in SAMPLE.items():
        for row in rows:
            ids = {k for k, v in row.items() if isinstance(v, str) and k != "title"}
            copy = mirror_row(label, row)
            assert {copy[k] for k in ids}.isdisjoint(row[k] for k in ids)


def test_the_mirror_keeps_its_relationships_inside_the_copy():
    """관계의 양 끝이 사본 노드를 가리켜야 MATCH가 걸리고 관계가 두 배가 됩니다."""
    papers = {mirror_row("papers", p)["arxiv_id"] for p in SAMPLE["papers"]}
    authors = {mirror_row("authors", a)["name"] for a in SAMPLE["authors"]}
    edge = mirror_row("authored", SAMPLE["authored"][0])

    assert edge["paper"] in papers and edge["author"] in authors
    assert mirror_row("references", SAMPLE["references"][0])["citing"] in papers


def test_the_mirror_keeps_everything_but_the_identifier():
    """임베딩과 초록이 그대로여야 저장소·벡터 인덱스 크기가 두 배가 됩니다."""
    paper = SAMPLE["papers"][0]
    copy = mirror_row("papers", paper)

    assert copy["arxiv_id"] == MIRROR + paper["arxiv_id"]
    assert {k: v for k, v in copy.items() if k != "arxiv_id"} == {
        k: v for k, v in paper.items() if k != "arxiv_id"}


def test_an_unknown_kind_is_not_silently_copied():
    with pytest.raises(ValueError):
        mirror_row("cites", {})


def test_a_used_database_is_refused():
    """이미 무언가 있으면 1배 크기가 아닙니다. 지우지 않고 멈춥니다."""
    with pytest.raises(SystemExit):
        require_empty({"papers": 1, "authors": 0, "authored": 0, "references": 0})
    require_empty({"papers": 0, "authors": 0, "authored": 0, "references": 0})


def test_the_measurement_never_writes_into_the_baseline_database():
    """1배 로컬 DB(17687)에 사본을 얹으면 그쪽의 benchmark 검사가 깨집니다."""
    assert URI.startswith("bolt://127.0.0.1:")
    assert URI != base.URI


def test_the_double_container_matches_the_baseline_container():
    """메모리·CPU·이미지가 다르면 10-14 실측과도, 서로도 비교할 수 없습니다."""
    local = yaml.safe_load(Path("compose.local.yml").read_text(encoding="utf-8"))
    double = yaml.safe_load(Path("compose.double.yml").read_text(encoding="utf-8"))
    one, two = local["services"]["neo4j"], double["services"]["neo4j"]

    for key in ("image", "mem_limit", "cpus", "environment"):
        assert one[key] == two[key], key
    assert one["container_name"] != two["container_name"]
    assert set(one["ports"]).isdisjoint(two["ports"])
    assert local["volumes"]["data"]["name"] != double["volumes"]["data"]["name"]


def test_the_measured_queries_are_the_app_queries():
    """옮겨 적지 않고 읽습니다. 앱이 다른 상수를 읽기 시작하면 측정은 옛 질의를 잽니다.

    `chatbot`을 임포트하지 않습니다 - CI에는 `.env`가 없습니다. 대신 `citation_graph()`가
    실제로 읽는 이름이 측정이 읽는 이름과 같은지를 AST로 봅니다.
    """
    tree = ast.parse(Path("src/chatbot.py").read_text(encoding="utf-8"))
    body = next(node for node in tree.body
                if isinstance(node, ast.FunctionDef) and node.name == "citation_graph")
    read = {arg.id for node in ast.walk(body)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "read_with_retry"
            for arg in node.args if isinstance(arg, ast.Name)}

    assert read == set(APP_QUERIES)
    found = app_queries()
    assert "ArxivPaper" in found["PAPER_ROWS"]
    assert ":REFERENCES" in found["CITATION_EDGES"]


def test_a_query_that_stops_being_a_literal_is_reported(tmp_path):
    """f-string 따위로 바뀌면 AST가 못 읽습니다. 조용히 빈 질의를 재지 않고 터집니다."""
    source = tmp_path / "chatbot.py"
    source.write_text('PAPER_ROWS = f"MATCH {x}"\nCITATION_EDGES = "MATCH ()"\n',
                      encoding="utf-8")
    with pytest.raises(ValueError, match="PAPER_ROWS"):
        app_queries(source)


@pytest.mark.parametrize("usage, expected", [
    ("1.745GiB / 4GiB", 1.745),
    ("512MiB / 4GiB", 0.5),
    ("1.5GB / 4GB", 1.5e9 / 1024**3),
])
def test_docker_memory_is_read_in_gib(usage, expected):
    assert memory_gib(usage) == pytest.approx(expected)


def test_du_output_is_read_per_path():
    out = "507904\t/data/databases/neo4j\n120000\t/data/databases/neo4j/schema\n"
    assert parse_du(out) == {"/data/databases/neo4j": 507904,
                             "/data/databases/neo4j/schema": 120000}


def test_the_projection_uses_aura_times_local_growth():
    """추정은 Aura에서 잰 값에 같은 배치의 증가율을 곱한 것입니다.

    증가율이 없으면 추정도 없습니다.
    """
    projected = project({"p95": 10.0}, 2.0)

    assert projected["aura_p95_projected"] == 20.0
    assert projected["headroom_ratio_projected"] == 1.5
    assert project({"p95": 10.0}, None) is None
    assert project({}, 2.0) is None


def test_growth_compares_stages_of_the_same_run():
    def stage(scale):
        return {
            "data_mib": {"store": 500 * scale, "graph_store": 400 * scale,
                         "schema_index": 100 * scale, "transaction_logs": 700 * scale},
            "queries": {"paper_rows": {"p95": 1.0 * scale},
                        "citation_edges": {"p95": 2.0 * scale},
                        "vector_top5": {"p95": 0.02}},
            "load_total_seconds": 190 * scale,
            "app_path": {"citation_graph_seconds": 3.0 * scale, "peak_mb": 270 * scale},
        }

    out = compare(stage(1), stage(2), {"citation_edges": {"p95": 10.0}})

    assert out["data_mib"]["store"] == 2.0
    assert out["queries_p95"]["vector_top5"] == 1.0
    assert out["aura_projection"]["citation_edges"]["aura_p95_projected"] == 20.0
    assert out["aura_projection"]["paper_rows"] is None


def test_the_committed_report_is_a_doubling():
    """보고서가 있다면 2배 단계의 개수가 정확히 두 배여야 합니다 - 아니면 사본이 겹친 것입니다."""
    path = Path("reports/double_graph.json")
    if not path.exists():
        pytest.skip("측정 전입니다")
    report = json.loads(path.read_text(encoding="utf-8"))
    one, two = report["stages"]["1x"]["counts"], report["stages"]["2x"]["counts"]

    assert {k: v * 2 for k, v in one.items()} == two
