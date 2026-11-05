"""주간 수집의 참고문헌·밖 논문·재조회(ADR-099, ADR-104). 네트워크와 그래프를 타지 않습니다."""

import pytest

from pipeline import arxiv_api, ingest, oai, s2
from pipeline import references as outside


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
        lookup = arxiv_api.RecordLookup()
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


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def test_a_read_timeout_is_retried_and_then_given_up(monkeypatch):
    calls = []

    def urlopen(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise TimeoutError("The read operation timed out")
        return _Response(b"<ok/>")

    monkeypatch.setattr(oai.urllib.request, "urlopen", urlopen)
    pauses = []
    assert oai.http_fetch({"verb": "Identify"}, sleep=pauses.append) == "<ok/>"
    assert len(calls) == 2 and len(pauses) == 1

    def always(request, timeout):
        calls.append(timeout)
        raise TimeoutError("slow")

    calls.clear()
    monkeypatch.setattr(oai.urllib.request, "urlopen", always)
    with pytest.raises(TimeoutError):
        oai.http_fetch({"verb": "Identify"}, sleep=lambda _: None)
    assert len(calls) == oai.NETWORK_RETRIES + 1



ATOM = """<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry><id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T17:57:34Z</published><title>Attention Is
      All You Need</title><summary> The dominant ... </summary>
    <author><name>Ashish Vaswani</name></author><author><name>Noam Shazeer</name></author>
    <arxiv:primary_category term="cs.CL"/><category term="cs.CL"/><category term="cs.LG"/>
  </entry>
  <entry><id>http://arxiv.org/abs/cs/0501001v1</id><published>2005-01-01T03:14:14Z</published>
    <title>Old</title><summary>s</summary><category term="cs.CR"/></entry>
</feed>"""


def test_the_arxiv_api_rows_match_the_loader_and_keep_old_ids():
    lookup = arxiv_api.get_records(["1706.03762", "cs/0501001", "9999.99999"],
                                   fetch=lambda ids: ATOM, sleep=lambda _: None)

    first, old = lookup.records
    assert first["arxiv_id"] == "1706.03762"
    assert first["title"] == "Attention Is All You Need"
    assert first["created"] == "2017-06-12T17:57:34Z"
    assert first["authors"] == ["Ashish Vaswani", "Noam Shazeer"]
    assert first["primary_category"] == "cs.CL" and first["categories"] == ["cs.CL", "cs.LG"]
    assert old["arxiv_id"] == "cs/0501001" and old["primary_category"] == "cs.CR"
    assert lookup.missing == ["9999.99999"]


def test_one_failed_arxiv_batch_does_not_lose_the_others():
    def fetch(ids):
        if "b" in ids:
            raise TimeoutError("slow")
        return ATOM

    pauses = []
    lookup = arxiv_api.get_records(["1706.03762", "b"], fetch=fetch, sleep=pauses.append,
                                   batch_size=1)

    assert [r["arxiv_id"] for r in lookup.records] == ["1706.03762"]
    assert lookup.failures[0]["arxiv_id"] == "b"
    assert pauses == [arxiv_api.REQUEST_INTERVAL_SECONDS]
