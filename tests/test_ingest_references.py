"""주간 수집의 참고문헌·밖 논문·재조회(ADR-099, ADR-104). 네트워크와 그래프를 타지 않습니다."""

import xml.etree.ElementTree as ElementTree

import pytest

from pipeline import ingest, oai, s2
from pipeline import references as outside
from tests.test_ingest_pipeline import record_xml


def batch(references=None, empty=(), not_found=(), failures=()):
    return s2.ReferenceBatch(references=dict(references or {}), empty=list(empty),
                             not_found=list(not_found), requests=1,
                             failures=[{"arxiv_id": i, "error": "x"} for i in failures])


# --- references.py -----------------------------------------------------------------


def test_a_citation_into_the_graph_is_an_edge_and_one_outside_is_queued():
    edges, waiting = outside.count_citations({"new": ["old", "far", "far", "new"]}, {},
                                             in_graph={"old", "new"})

    assert edges == [{"citing": "new", "cited": "old"}]
    assert waiting == {"far": ["new"]}


def test_citations_accumulate_across_runs_without_counting_a_paper_twice():
    _, waiting = outside.count_citations({"a": ["far"], "b": ["far"]}, {}, in_graph={"a", "b"})
    _, waiting = outside.count_citations({"a": ["far"], "c": ["far"]}, waiting,
                                         in_graph={"a", "c"})

    assert sorted(waiting["far"]) == ["a", "b", "c"]
    assert outside.ready(waiting) == ["far"]


def test_two_citers_are_not_enough():
    assert outside.ready({"far": ["a", "b"]}) == []
    assert outside.OUTSIDE_MIN_CITERS == 3  # ADR-099


def test_a_waiting_paper_that_entered_the_graph_turns_into_edges():
    edges, waiting = outside.count_citations({}, {"far": ["a", "b"]}, in_graph={"far"})

    assert edges == [{"citing": "a", "cited": "far"}, {"citing": "b", "cited": "far"}]
    assert waiting == {}


def test_admitting_moves_the_stored_citations_onto_the_new_node():
    edges, waiting = outside.admit({"far": ["a", "b", "c"], "other": ["a"]}, ["far"])

    assert [edge["citing"] for edge in edges] == ["a", "b", "c"]
    assert waiting == {"other": ["a"]}


def test_the_input_queue_is_never_mutated():
    waiting = {"far": ["a"]}
    outside.count_citations({"b": ["far"]}, waiting, in_graph={"b"})
    outside.admit(waiting, ["far"])

    assert waiting == {"far": ["a"]}


def test_an_empty_paper_is_asked_again_and_then_let_go():
    retry: dict[str, int] = {}
    for _ in range(outside.RETRY_RUNS):
        retry, dropped = outside.next_retry(retry, asked=["late"], resolved=[])
        assert dropped == []
    retry, dropped = outside.next_retry(retry, asked=["late"], resolved=[])

    assert dropped == ["late"]
    assert retry == {}


def test_a_paper_whose_references_arrive_leaves_the_retry_queue():
    retry, dropped = outside.next_retry({"late": 2}, asked=[], resolved=["late"])

    assert retry == {} and dropped == []


def test_state_round_trips(tmp_path):
    path = tmp_path / "refs.json"
    assert outside.read_state(path) == {"waiting": {}, "retry": {}}
    outside.write_state({"waiting": {"far": ["a"]}, "retry": {"late": 1}}, path)

    assert outside.read_state(path) == {"waiting": {"far": ["a"]}, "retry": {"late": 1}}


# --- ingest.plan_references -------------------------------------------------------


def plan(harvested, state, refs, graph=(), records=None):
    asked_log = []

    def fetch(ids):
        asked_log.append(list(ids))
        return refs

    def get_records(ids):
        lookup = oai.RecordLookup()
        for arxiv_id in ids:
            if records is None or arxiv_id in records:
                lookup.records.append({"arxiv_id": arxiv_id, "source": "oai", "authors": []})
            else:
                lookup.missing.append(arxiv_id)
        return lookup

    result = ingest.plan_references(harvested, state, fetch_references=fetch,
                                    in_graph=lambda ids: set(ids) & set(graph),
                                    get_records=get_records)
    return result, asked_log


def test_new_papers_link_to_the_graph_and_to_each_other():
    result, _ = plan(["n1", "n2"], {"waiting": {}, "retry": {}},
                     batch({"n1": ["old", "n2"], "n2": ["old"]}), graph={"old"})

    assert {(e["citing"], e["cited"]) for e in result.references} == {
        ("n1", "old"), ("n1", "n2"), ("n2", "old")}
    assert result.outside_records == []


def test_the_third_citer_brings_the_outside_paper_in_as_a_reference():
    state = {"waiting": {"far": ["a", "b"]}, "retry": {}}
    result, _ = plan(["c"], state, batch({"c": ["far"]}), graph={"a", "b"})

    assert [r["arxiv_id"] for r in result.outside_records] == ["far"]
    assert result.outside_records[0]["source"] == "reference"
    assert {e["citing"] for e in result.references if e["cited"] == "far"} == {"a", "b", "c"}
    assert "far" not in result.state["waiting"]
    assert state == {"waiting": {"far": ["a", "b"]}, "retry": {}}  # 저장은 적재 뒤


def test_an_outside_paper_without_metadata_stays_queued():
    result, _ = plan(["c"], {"waiting": {"far": ["a", "b"]}, "retry": {}},
                     batch({"c": ["far"]}), graph={"a", "b"}, records=set())

    assert result.outside_records == []
    assert sorted(result.state["waiting"]["far"]) == ["a", "b", "c"]
    assert result.report["outside_missing"] == 1


def test_papers_waiting_for_references_are_asked_again():
    result, asked = plan(["n1"], {"waiting": {}, "retry": {"late": 1}},
                         batch({"late": ["old"]}, empty=["n1"]), graph={"old", "late"})

    assert asked == [["n1", "late"]]
    assert {"citing": "late", "cited": "old"} in result.references
    assert result.state["retry"] == {"n1": 1}
    assert result.report["asked_again"] == 1


def test_s2_failures_count_against_the_run_and_are_retried():
    result, _ = plan(["a", "b"], {"waiting": {}, "retry": {}}, batch(failures=["a", "b"]))

    assert result.enrichment.failure_rate == pytest.approx(1.0)
    assert result.state["retry"] == {"a": 1, "b": 1}


def test_the_per_run_admission_cap_leaves_the_rest_waiting(monkeypatch):
    monkeypatch.setattr(ingest, "MAX_OUTSIDE_PER_RUN", 1)
    waiting = {"x": ["a", "b", "c", "d"], "y": ["a", "b", "c"]}
    result, _ = plan([], {"waiting": waiting, "retry": {}}, batch(), graph={"a", "b", "c", "d"})

    assert [r["arxiv_id"] for r in result.outside_records] == ["x"]
    assert "y" in result.state["waiting"]


# --- 대상과 발표일 ----------------------------------------------------------------


def test_only_aura_has_the_free_tier_limits():
    assert ingest.targets_aura("neo4j+s://abcd1234.databases.neo4j.io")
    assert not ingest.targets_aura("bolt://neo4j:7687")
    assert not ingest.targets_aura(None)


def test_the_merge_fills_a_missing_publication_date_from_the_first_version():
    assert "p.published = coalesce(p.published" in ingest.MERGE_PAPER
    assert "datetime(row.created)" in ingest.MERGE_PAPER


# --- oai.get_records --------------------------------------------------------------

OAI = 'xmlns="http://www.openarchives.org/OAI/2.0/"'


def test_get_record_parses_each_paper_and_keeps_failures_apart():
    def fetch(params):
        identifier = params["identifier"]
        assert params["verb"] == "GetRecord"
        if identifier.endswith("gone"):
            return f'<OAI-PMH {OAI}><error code="idDoesNotExist">no</error></OAI-PMH>'
        if identifier.endswith("boom"):
            raise TimeoutError("slow")
        return f"<OAI-PMH {OAI}><GetRecord>{record_xml('2101.00001')}</GetRecord></OAI-PMH>"

    pauses = []
    lookup = oai.get_records(["2101.00001", "gone", "boom"], fetch=fetch, sleep=pauses.append)

    assert [r["arxiv_id"] for r in lookup.records] == ["2101.00001"]
    assert lookup.records[0]["created"] == "2026-09-18"
    assert lookup.missing == ["gone"]
    assert lookup.failures[0]["arxiv_id"] == "boom"
    assert len(pauses) == 2


def test_old_style_ids_keep_their_slash():
    assert oai.oai_identifier("cs/0501001") == "oai:arXiv.org:cs/0501001"
    ElementTree.fromstring(f"<OAI-PMH {OAI}/>")  # 네임스페이스 문자열이 유효한지


def test_the_backfill_writes_only_the_publication_date_and_never_guesses():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "backfill_published", Path("scripts/backfill_published.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.rows([{"arxiv_id": "a", "created": "2026-10-01"}, {"arxiv_id": "b"}]) == [
        {"arxiv_id": "a", "created": "2026-10-01"}]
    assert "SET p.published = coalesce(p.published" in module.FILL
    assert "DELETE" not in module.FILL and "MERGE" not in module.FILL
