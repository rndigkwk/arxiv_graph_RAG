"""Aura -> VM 이전 스크립트와 VM compose 파일.

- 스키마를 **Aura와 같은 이름·옵션으로** 다시 만드는지. 전문 검색 인덱스가 빠지면 앱이 뜨기는
  하지만 제목 해석이 `CONTAINS`로 떨어집니다(ADR-024).
- 5.26 Community가 받지 않는 벡터 설정은 버리고, 버린 것을 말하는지.
- 검증이 빠진 인덱스·다른 분석기·다른 속성 타입을 잡는지.
- `load`가 Aura에는 쓰지 않는지.
- VM의 Neo4j가 ADR-071을 잰 설정과 같고, 인터넷에 포트를 열지 않는지.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from scripts import move_graph

# 2026-10-26 Aura의 `SHOW INDEXES`·`SHOW CONSTRAINTS`에서 옮긴 모양입니다(LOOKUP 제외).
AURA = {
    "constraints": [
        {"name": "arxiv_author_name", "type": "NODE_PROPERTY_UNIQUENESS",
         "labelsOrTypes": ["ArxivAuthorName"], "properties": ["name"]},
        {"name": "arxiv_paper_id", "type": "NODE_PROPERTY_UNIQUENESS",
         "labelsOrTypes": ["ArxivPaper"], "properties": ["arxiv_id"]},
    ],
    "indexes": [
        {"name": "arxiv_author_name", "type": "RANGE", "labelsOrTypes": ["ArxivAuthorName"],
         "properties": ["name"], "options": {"indexConfig": {}},
         "owningConstraint": "arxiv_author_name"},
        {"name": "arxiv_author_name_fulltext", "type": "FULLTEXT",
         "labelsOrTypes": ["ArxivAuthorName"], "properties": ["name"],
         "options": {"indexConfig": {"fulltext.analyzer": "standard",
                                     "fulltext.eventually_consistent": False}},
         "owningConstraint": None},
        {"name": "arxiv_paper_embedding_index", "type": "VECTOR",
         "labelsOrTypes": ["ArxivPaper"], "properties": ["embedding"],
         "options": {"indexConfig": {
             "vector.dimensions": 768, "vector.default_search_expansion_factor": 3.0,
             "vector.hnsw.m": 16, "vector.quantization.type": "BINARY",
             "vector.similarity_function": "COSINE", "vector.hnsw.ef_construction": 100}},
         "owningConstraint": None},
        {"name": "arxiv_paper_primary_category", "type": "RANGE",
         "labelsOrTypes": ["ArxivPaper"], "properties": ["primary_category"],
         "options": {"indexConfig": {}}, "owningConstraint": None},
        {"name": "arxiv_paper_title_fulltext", "type": "FULLTEXT",
         "labelsOrTypes": ["ArxivPaper"], "properties": ["title"],
         "options": {"indexConfig": {"fulltext.analyzer": "standard"}},
         "owningConstraint": None},
    ],
    "labels": ["ArxivAuthorName", "ArxivPaper"],
    "relationships": ["AUTHORED", "REFERENCES"],
    "paper_property_types": {"published": ["ZONED DATETIME NOT NULL"],
                             "title": ["STRING NOT NULL"]},
}


def test_schema_keeps_aura_names_and_fulltext_indexes():
    statements, _ = move_graph.schema_statements(AURA)
    text = "\n".join(statements)
    for name in ("arxiv_author_name", "arxiv_paper_id", "arxiv_paper_primary_category",
                 "arxiv_paper_title_fulltext", "arxiv_author_name_fulltext",
                 "arxiv_paper_embedding_index"):
        assert f"`{name}`" in text
    assert "local_" not in text
    assert text.count("`fulltext.analyzer`: 'standard'") == 2


def test_constraint_owned_index_is_left_to_the_constraint():
    statements, _ = move_graph.schema_statements(AURA)
    assert sum("`arxiv_author_name`" in s for s in statements) == 1
    assert len(statements) == 6


def test_vector_index_drops_only_what_community_rejects():
    statements, dropped = move_graph.schema_statements(AURA)
    vector = next(s for s in statements if s.startswith("CREATE VECTOR INDEX"))
    assert "`vector.dimensions`: 768" in vector
    assert "`vector.similarity_function`: 'cosine'" in vector
    assert "`vector.hnsw.m`: 16" in vector
    assert "`vector.hnsw.ef_construction`: 100" in vector
    assert "quantization" not in vector
    assert dropped == [
        "arxiv_paper_embedding_index.vector.default_search_expansion_factor=3.0",
        "arxiv_paper_embedding_index.vector.quantization.type=BINARY",
    ]


def test_unknown_index_type_stops_the_move():
    spec = {**AURA, "indexes": [{"name": "x", "type": "POINT", "labelsOrTypes": ["ArxivPaper"],
                                 "properties": ["loc"], "options": {"indexConfig": {}}}]}
    with pytest.raises(SystemExit, match="POINT"):
        move_graph.schema_statements(spec)


def test_compare_passes_the_same_schema_without_aura_only_vector_options():
    actual = {**AURA, "indexes": [
        {**i, "options": {"indexConfig": {"vector.dimensions": 768,
                                          "vector.quantization.enabled": True}}}
        if i["type"] == "VECTOR" else i for i in AURA["indexes"]]}
    assert move_graph.compare(AURA, actual) == []


def test_compare_names_a_missing_fulltext_index():
    actual = {**AURA, "indexes": [i for i in AURA["indexes"]
                                  if i["name"] != "arxiv_author_name_fulltext"]}
    assert move_graph.compare(AURA, actual) == ["인덱스 없음: arxiv_author_name_fulltext"]


def test_compare_names_a_different_analyzer():
    actual = {**AURA, "indexes": [
        {**i, "options": {"indexConfig": {"fulltext.analyzer": "standard-no-stop-words"}}}
        if i["name"] == "arxiv_paper_title_fulltext" else i for i in AURA["indexes"]]}
    assert move_graph.compare(AURA, actual) == ["분석기가 다름: arxiv_paper_title_fulltext"]


def test_compare_names_a_property_that_lost_its_type():
    # `published`가 문자열로 실리면 날짜 정렬·필터가 조용히 틀립니다.
    actual = {**AURA, "paper_property_types": {**AURA["paper_property_types"],
                                               "published": ["STRING NOT NULL"]}}
    problems = move_graph.compare(AURA, actual)
    assert problems == ["속성 타입이 다름: published ['ZONED DATETIME NOT NULL'] != "
                        "['STRING NOT NULL']"]


def test_load_refuses_to_write_to_aura(monkeypatch):
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://abcd1234.databases.neo4j.io")
    monkeypatch.setenv("NEO4J_PASSWORD", "x")
    with pytest.raises(SystemExit, match="Aura"):
        move_graph.target_driver()


def compose(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def test_vm_neo4j_matches_the_measured_setup():
    # ADR-071의 1배·2배 측정이 compose.local.yml의 이미지·heap·page cache입니다.
    local = compose("compose.local.yml")["services"]["neo4j"]
    vm = compose("deploy/vm/compose.yml")["services"]["neo4j"]
    assert vm["image"] == local["image"]
    for key in ("NEO4J_server_memory_heap_initial__size", "NEO4J_server_memory_heap_max__size",
                "NEO4J_server_memory_pagecache_size"):
        assert vm["environment"][key] == local["environment"][key]


def test_vm_opens_only_caddy_to_the_internet():
    services = compose("deploy/vm/compose.yml")["services"]
    assert "ports" not in services["neo4j"]
    assert "ports" not in services["api"]
    assert services["caddy"]["ports"] == ["80:80", "443:443"]


def test_app_image_does_not_carry_secrets():
    # 빌드 문맥은 의존성 목록 셋뿐입니다. .env와 .local/이 이미지에 들어가면 안 됩니다.
    lines = [line.strip() for line in Path(".dockerignore").read_text(encoding="utf-8")
             .splitlines() if line.strip() and not line.startswith("#")]
    assert lines == ["*", "!pyproject.toml", "!uv.lock", "!.python-version"]


def retrieval(ids_by_case: dict[str, list[str]], recall: float) -> dict:
    return {"top_k": 3,
            "results": [{"id": case, "retrieved_ids": ids} for case, ids in ids_by_case.items()],
            "summary": {"hit_at_k": 1.0, "recall_at_k": recall, "reciprocal_rank": 1.0}}


def test_retrieval_diff_counts_lists_top1_and_overlap():
    before = retrieval({"a": ["1", "2", "3"], "b": ["4", "5", "6"]}, 0.5)
    after = retrieval({"a": ["1", "2", "3"], "b": ["5", "4", "9"]}, 0.49996)
    result = move_graph.retrieval_diff(before, after)
    assert result["identical_lists"] == 1
    assert result["same_top1"] == 1
    assert result["top1_changed"] == ["b"]
    assert result["mean_overlap_at_k"] == round((1 + 2 / 3) / 2, 3)
    assert result["summary"]["recall_at_k"] == {"before": 0.5, "after": 0.5}


def test_retrieval_diff_refuses_reports_in_a_different_order():
    before = retrieval({"a": ["1"], "b": ["2"]}, 0.5)
    after = retrieval({"b": ["2"], "a": ["1"]}, 0.5)
    with pytest.raises(SystemExit, match="순서"):
        move_graph.retrieval_diff(before, after)


def test_vm_env_carries_no_aura_or_gds_secrets():
    from scripts.make_vm_env import render

    names = [line.split("=", 1)[0] for line in render("sk-x", "a.sslip.io", "p").splitlines()
             if line]
    assert names == ["OPENAI_API_KEY", "NEO4J_PASSWORD", "APP_DOMAIN", "LANGFUSE_TRACING",
                     "API_ALLOWED_ORIGINS", "API_ALLOWED_ORIGIN_REGEX"]


def test_vm_env_lets_only_this_projects_previews_call_the_api():
    import re

    from scripts.make_vm_env import WEB_PREVIEW_ORIGINS

    allowed = re.compile(WEB_PREVIEW_ORIGINS)
    assert allowed.fullmatch("https://arxiv-graphrag-git-web-ask-screen-rndigkwks-projects.vercel.app")
    assert not allowed.fullmatch("https://arxiv-graphrag-x-rndigkwks-projects.vercel.app.evil.example")
    assert not allowed.fullmatch("https://someone-else-rndigkwks-projects.vercel.app")


def test_vm_env_is_written_with_lf_even_on_windows(tmp_path, monkeypatch):
    # 2026-10-26 VM에 처음 올린 .env가 CRLF였습니다. compose가 비밀번호 끝의 CR까지 읽습니다.
    from scripts import make_vm_env

    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-x\n", encoding="utf-8")
    monkeypatch.setattr(make_vm_env, "ROOT", tmp_path)
    monkeypatch.setattr(make_vm_env, "OUTPUT", tmp_path / ".local" / "vm.env")
    monkeypatch.setattr("sys.argv", ["make_vm_env.py", "--domain", "a.sslip.io"])
    assert make_vm_env.main() == 0
    assert b"\r" not in (tmp_path / ".local" / "vm.env").read_bytes()
