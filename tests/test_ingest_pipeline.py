"""증분 수집 파이프라인의 운영 장치를 검증합니다.

수집 파이프라인에서 실제로 사고가 나는 지점은 평상시 경로가 아니라 예외 경로입니다. 재개 토큰이
반복될 때, 논문 한 편의 보강이 실패할 때, 배치가 용량 한도를 넘길 때, 같은 배치가 두 번 들어올
때입니다. 테스트도 거기에 둡니다.

HTTP는 전부 주입된 가짜로 대체하므로 네트워크를 타지 않습니다.
"""

import inspect
import json
import xml.etree.ElementTree as ElementTree
from datetime import date
from pathlib import Path

import pytest

from pipeline import ingest, oai

OAI_NS = 'xmlns="http://www.openarchives.org/OAI/2.0/"'
ARXIV_NS = 'xmlns:arxiv="http://arxiv.org/OAI/arXiv/"'


def record_xml(arxiv_id, *, categories="cs.AI cs.LG", deleted=False, datestamp="2026-09-20"):
    if deleted:
        return (
            '<record><header status="deleted">'
            f"<datestamp>{datestamp}</datestamp></header></record>"
        )
    return f"""<record>
      <header><datestamp>{datestamp}</datestamp></header>
      <metadata><arxiv:arXiv {ARXIV_NS}>
        <arxiv:id>{arxiv_id}</arxiv:id>
        <arxiv:created>2026-09-18</arxiv:created>
        <arxiv:title>A  Title</arxiv:title>
        <arxiv:abstract>  An abstract.  </arxiv:abstract>
        <arxiv:categories>{categories}</arxiv:categories>
        <arxiv:authors>
          <arxiv:author><arxiv:keyname>Kim</arxiv:keyname>
            <arxiv:forenames>Jee</arxiv:forenames></arxiv:author>
          <arxiv:author><arxiv:keyname>Lee</arxiv:keyname></arxiv:author>
        </arxiv:authors>
      </arxiv:arXiv></metadata>
    </record>"""


def page_xml(records, token=""):
    token_xml = f"<resumptionToken>{token}</resumptionToken>" if token else ""
    return (
        f"<OAI-PMH {OAI_NS}><ListRecords>"
        + "".join(records)
        + token_xml
        + "</ListRecords></OAI-PMH>"
    )


def test_parse_record_keeps_the_oai_datestamp_out_of_a_publication_field():
    document = ElementTree.fromstring(page_xml([record_xml("2609.00001")]))
    record = document.find(".//{http://www.openarchives.org/OAI/2.0/}record")

    parsed = oai.parse_record(record)

    assert parsed["arxiv_id"] == "2609.00001"
    assert parsed["title"] == "A Title"
    assert parsed["abstract"] == "An abstract."
    assert parsed["authors"] == ["Jee Kim", "Lee"]
    assert parsed["categories"] == ["cs.AI", "cs.LG"]
    assert parsed["primary_category"] == "cs.AI"
    # 수집 시점을 published로 쓰는 바람에 1,748편의 발행일이 틀어졌습니다. 이름을 분리합니다.
    assert parsed["oai_datestamp"] == "2026-09-20"
    assert parsed["created"] == "2026-09-18"
    assert "published" not in parsed


def test_parse_record_skips_a_deleted_header():
    document = ElementTree.fromstring(page_xml([record_xml("2609.00001", deleted=True)]))
    record = document.find(".//{http://www.openarchives.org/OAI/2.0/}record")

    assert oai.parse_record(record) is None


def test_harvest_follows_resumption_tokens_and_filters_categories():
    responses = [
        page_xml([record_xml("2609.00001"), record_xml("2609.00002", categories="math.ST")],
                 token="t1"),
        page_xml([record_xml("2609.00003", datestamp="2026-09-25")]),
    ]
    calls = []

    def fetch(params):
        calls.append(params)
        return responses[len(calls) - 1]

    result = oai.harvest(
        "2026-09-01", "2026-09-30", fetch=fetch, sleep=lambda _: None,
        categories=frozenset({"cs.AI"}),
    )

    assert [row["arxiv_id"] for row in result.records] == ["2609.00001", "2609.00003"]
    assert result.skipped == 1
    assert result.pages == 2
    assert result.last_datestamp == "2026-09-25"
    # 재개 요청에 원래 인자를 함께 보내면 arXiv가 badArgument를 돌려줍니다.
    assert calls[1] == {"verb": "ListRecords", "resumptionToken": "t1"}


def test_harvest_counts_deleted_records_separately():
    def fetch(params):
        return page_xml([record_xml("2609.00001", deleted=True), record_xml("2609.00002")])

    result = oai.harvest("2026-09-01", "2026-09-30", fetch=fetch, sleep=lambda _: None)

    assert result.deleted == 1
    assert len(result.records) == 1


def test_a_repeated_resumption_token_stops_the_harvest():
    def fetch(params):
        return page_xml([record_xml("2609.00001")], token="same")

    with pytest.raises(RuntimeError, match="resumptionToken"):
        oai.harvest("2026-09-01", "2026-09-30", fetch=fetch, sleep=lambda _: None)


def test_no_records_match_is_an_empty_result_not_an_error():
    def fetch(params):
        return f'<OAI-PMH {OAI_NS}><error code="noRecordsMatch">none</error></OAI-PMH>'

    result = oai.harvest("2026-09-01", "2026-09-30", fetch=fetch, sleep=lambda _: None)

    assert result.records == []
    assert result.pages == 0


def test_another_oai_error_is_raised():
    def fetch(params):
        return f'<OAI-PMH {OAI_NS}><error code="badArgument">bad</error></OAI-PMH>'

    with pytest.raises(RuntimeError, match="badArgument"):
        oai.harvest("2026-09-01", "2026-09-30", fetch=fetch, sleep=lambda _: None)


def test_backoff_prefers_the_servers_retry_after():
    assert oai.backoff_delay(0, "45") == 45.0
    # Retry-After가 요청 간격보다 짧아도 간격 아래로는 내려가지 않습니다.
    assert oai.backoff_delay(0, "0.5") == oai.REQUEST_INTERVAL_SECONDS
    assert oai.backoff_delay(0, "soon") == oai.BACKOFF_BASE_SECONDS
    assert oai.backoff_delay(0, None) == oai.BACKOFF_BASE_SECONDS
    assert oai.backoff_delay(2, None) == oai.BACKOFF_BASE_SECONDS * 4
    assert oai.backoff_delay(99, None) == oai.BACKOFF_MAX_SECONDS


def test_harvest_window_starts_the_day_after_the_watermark():
    state = {"last_datestamp": "2026-09-14"}

    start, end = ingest.harvest_window(state, days=7, today=date(2026, 9, 20))

    assert (start, end) == ("2026-09-15", "2026-09-20")


def test_harvest_window_without_a_watermark_looks_back_a_bounded_number_of_days():
    # 워터마크가 없다고 전체 아카이브를 요청하면 안 됩니다.
    start, end = ingest.harvest_window({}, days=7, today=date(2026, 9, 20))

    assert (start, end) == ("2026-09-13", "2026-09-20")


def test_harvest_window_clamps_a_watermark_that_is_already_current():
    state = {"last_datestamp": "2026-09-20"}

    start, end = ingest.harvest_window(state, days=7, today=date(2026, 9, 20))

    assert start == end == "2026-09-20"


def test_harvest_window_recovers_from_a_corrupt_watermark():
    state = {"last_datestamp": "not-a-date"}

    start, end = ingest.harvest_window(state, days=3, today=date(2026, 9, 20))

    assert (start, end) == ("2026-09-17", "2026-09-20")


def test_capacity_counts_only_genuinely_new_nodes():
    papers = [{"arxiv_id": "new"}, {"arxiv_id": "known"}]
    authored = [
        {"paper": "new", "author": "New Author"},
        {"paper": "known", "author": "Known Author"},
    ]

    check = ingest.plan_capacity(
        papers, [{"citing": "new", "cited": "known"}], authored,
        counts={"nodes": 100, "relationships": 200},
        known_papers={"known"},
        known_authors={"Known Author"},
    )

    # MERGE가 기존 노드를 다시 만들지 않으므로 새 노드는 논문 1 + 저자 1뿐입니다.
    assert check.new_papers == 1
    assert check.new_authors == 1
    assert check.projected_nodes == 102
    assert check.projected_relationships == 203
    assert check.fits is True


def test_capacity_refuses_a_batch_that_would_cross_the_free_tier_ceiling():
    check = ingest.CapacityCheck(
        current_nodes=149_025,
        current_relationships=338_366,
        new_papers=0,
        new_authors=0,
        new_authored=0,
        new_references=60_000,
    )

    assert check.fits is False
    assert check.as_dict()["binding_constraint"] == "relationships"
    assert check.relationship_usage > ingest.CAPACITY_SAFETY


def test_capacity_accepts_a_batch_that_stays_under_the_ceiling():
    check = ingest.CapacityCheck(
        current_nodes=149_025,
        current_relationships=338_366,
        new_papers=500,
        new_authors=1_500,
        new_authored=2_000,
        new_references=4_000,
    )

    assert check.fits is True


def test_load_batch_writes_papers_before_the_relationships_that_match_them():
    order = []

    def run_write(cypher, params):
        order.append(cypher.strip().splitlines()[1].strip())
        return {"nodes_created": 1, "relationships_created": 2, "properties_set": 3}

    written = ingest.load_batch(
        run_write,
        [{"arxiv_id": "a"}],
        [{"paper": "a", "author": "A"}],
        [{"citing": "a", "cited": "b"}],
    )

    assert order[0].startswith("MERGE (p:ArxivPaper")
    assert all("MERGE" in statement or "MATCH" in statement for statement in order)
    assert written["nodes_created"] == 3
    assert written["relationships_created"] == 6
    assert written["rows_sent"] == {"papers": 1, "authored": 1, "references": 1}


def test_every_load_statement_uses_merge_and_never_delete():
    for cypher in (ingest.MERGE_PAPER, ingest.MERGE_AUTHORED, ingest.MERGE_REFERENCES):
        assert "MERGE" in cypher
        assert "CREATE " not in cypher
        assert "DELETE" not in cypher
        assert "DETACH" not in cypher


def test_authored_rows_deduplicate_a_repeated_author_on_one_paper():
    rows = ingest.authored_rows([{"arxiv_id": "a", "authors": ["X", "X", "Y", ""]}])

    assert rows == [{"paper": "a", "author": "X"}, {"paper": "a", "author": "Y"}]


# --- 임베딩 검사 (ADR-047) ---------------------------------------------------------


def test_harvested_records_never_carry_an_embedding():
    """OAI는 메타데이터만 줍니다. 이 사실이 임베딩 게이트가 필요한 이유입니다."""
    document = ElementTree.fromstring(page_xml([record_xml("2609.00001")]))
    record = document.find(".//{http://www.openarchives.org/OAI/2.0/}record")

    parsed = oai.parse_record(record)

    assert parsed is not None and parsed["arxiv_id"] == "2609.00001"
    assert "embedding" not in parsed


def test_a_batch_without_embeddings_is_not_ready_to_load():
    """임베딩 없이 적재하면 그 논문은 **벡터 검색에 영원히 안 나옵니다.**"""
    check = ingest.plan_embeddings(
        [{"arxiv_id": "a"}, {"arxiv_id": "b"}], known_papers=set()
    )

    assert check.new_papers == 2
    assert check.with_embedding == 0
    assert check.missing == 2
    assert check.coverage == 0.0
    assert not check.ready


def test_a_batch_with_full_coverage_is_ready():
    rows = [{"arxiv_id": name, "embedding": [0.0] * ingest.EMBEDDING_DIMENSIONS}
            for name in ("a", "b")]

    check = ingest.plan_embeddings(rows, known_papers=set())

    assert check.coverage == 1.0 and check.ready


def test_a_wrong_dimension_is_refused_even_at_full_coverage():
    """1024차원 행렬을 768 인덱스에 넣으면 조용히 쓸 수 없는 노드가 됩니다."""
    rows = [{"arxiv_id": "a", "embedding": [0.0] * 1024}]

    check = ingest.plan_embeddings(rows, known_papers=set())

    assert check.wrong_dimensions == 1
    assert check.with_embedding == 0
    assert not check.ready


def test_papers_already_in_the_graph_are_not_counted():
    """`MERGE_PAPER`가 `coalesce`로 쓰므로 기존 논문은 갖고 있던 벡터를 그대로 둡니다."""
    rows = [{"arxiv_id": "a"}, {"arxiv_id": "b"}]

    check = ingest.plan_embeddings(rows, known_papers={"a", "b"})

    assert check.new_papers == 0
    assert check.coverage == 1.0 and check.ready


def test_the_merge_statement_writes_the_embedding_without_clobbering():
    """2026-10-24까지 적재문은 `embedding`을 **아예 쓰지 않았습니다.**

    그래서 `--embed`가 만든 벡터 934개가 조용히 버려지고 811편이 벡터 검색에 안 보이는
    상태로 적재됐습니다. 지금은 씁니다 - 다만 `coalesce`로, 레코드가 가져왔을 때만.
    그러지 않으면 임베딩 없이 돌린 평소 적재가 기존 벡터를 지웁니다.
    """
    assert "coalesce(row.embedding, p.embedding)" in ingest.MERGE_PAPER
    for field in ("embedding_model", "embedding_dimensions", "embedding_input"):
        assert f"coalesce(row.{field}, p.{field})" in ingest.MERGE_PAPER
    # **레코드 쪽을 먼저 씁니다.** 순서가 뒤집히면 새 벡터가 들어가지 않습니다.
    assert "coalesce(p.embedding, row.embedding)" not in ingest.MERGE_PAPER


def test_the_upstream_gate_is_still_needed():
    """적재문이 쓰게 됐어도 게이트가 필요합니다 - `--embed`는 기본이 아닙니다."""
    rows = [{"arxiv_id": "a", "title": "t", "abstract": "b"}]

    check = ingest.plan_embeddings(rows, known_papers=set())

    assert not check.ready


def test_the_upstream_check_shares_the_snapshot_gate_constants():
    """두 검사가 갈라지면 상류에서 통과한 배치가 하류에서 막힙니다."""
    from pipeline import quality

    assert ingest.EMBEDDING_DIMENSIONS is quality.EMBEDDING_DIMENSIONS
    assert ingest.MIN_EMBEDDING_COVERAGE is quality.MIN_EMBEDDING_COVERAGE


def test_loading_without_embeddings_needs_an_explicit_flag():
    """조용히 통과시키지 않습니다. 알고 하는 경우만 넘어갑니다."""
    source = inspect.getsource(ingest.main)

    assert "allow_missing_embeddings" in source
    assert '"embeddings_missing"' in source
    # 검사가 용량 검사 뒤, `--load` 분기 앞에 있어야 합니다.
    assert source.index('"capacity"') < source.index('"embeddings_missing"')
    assert source.index('"embeddings_missing"') < source.index('"dry_run"')


# ── 여유 횟수가 보고서와 문서에서 같은 수인지 ───────────────────────────────
#
# 11-42절은 여유를 **산출값**으로 4.2회라고 셌습니다. `plan_capacity`가 관계를 행 수 그대로
# 세기 때문입니다(노드는 이미 있는 것을 빼는데 관계는 빼지 않습니다). 실제로 적재해 보니
# 같은 배치 안에서 1.38배 보수적이었고, 실측 여유는 7.7회입니다. 산출 쪽은 고치지 않습니다 —
# 용량 가드가 과대평가하는 것은 안전한 방향입니다. 고친 것은 **여유를 셀 때 쓰는 수**입니다.

LOADED = Path("reports/ingest_embedded.json")
EXPERIMENTS = Path("EXPERIMENTS.md")


@pytest.fixture(scope="module")
def loaded():
    if not LOADED.exists():
        pytest.skip(f"보고서를 읽을 수 없습니다: {LOADED}")
    return json.loads(LOADED.read_text(encoding="utf-8"))


def batches_left(loaded: dict, kind: str) -> float:
    """실측 증가분으로 안전 천장까지 남은 배치 수를 셉니다."""
    limit = ingest.RELATIONSHIP_LIMIT if kind == "relationships" else ingest.NODE_LIMIT
    ceiling = limit * ingest.CAPACITY_SAFETY
    now = loaded["counts"]["after"][kind]
    if kind == "relationships":
        grew = loaded["written"]["counters"]["authored"]["relationships_created"]
    else:
        grew = loaded["written"]["nodes_created"]

    assert grew > 0, (kind, grew)
    return (ceiling - now) / grew


def test_the_headroom_in_the_docs_is_the_measured_number(loaded):
    """**문서의 7.7회가 보고서에서 다시 나와야 합니다.** 적재가 또 돌면 이 테스트가 먼저 압니다."""
    text = EXPERIMENTS.read_text(encoding="utf-8")

    assert round(batches_left(loaded, "relationships"), 1) == 7.7
    assert round(batches_left(loaded, "nodes"), 1) == 14.7
    assert "약 7.7회" in text
    assert "14.7회" in text


def test_the_projection_was_conservative_in_the_safe_direction(loaded):
    """과대평가는 안전하고 과소평가는 위험합니다. 부호가 뒤집히면 가드를 다시 봐야 합니다."""
    projected = (
        loaded["capacity"]["projected_relationships"]
        - loaded["counts"]["before"]["relationships"]
    )
    actual = (
        loaded["counts"]["after"]["relationships"]
        - loaded["counts"]["before"]["relationships"]
    )

    assert projected > actual, (projected, actual)
    assert round(projected / actual, 2) == 1.38


def test_the_projection_gap_is_fully_accounted_for(loaded):
    """설명되지 않은 차이가 남으면 1.38배가 우연일 수 있습니다. 한 개도 남지 않아야 합니다."""
    sent, made = loaded["written"]["rows_sent"], loaded["written"]["counters"]
    references_that_vanished = sent["references"] - made["references"]["relationships_created"]
    authored_already_there = sent["authored"] - made["authored"]["relationships_created"]
    projected_minus_actual = (
        loaded["capacity"]["projected_relationships"]
        - loaded["counts"]["after"]["relationships"]
    )

    assert references_that_vanished + authored_already_there == projected_minus_actual
    # 참조는 가리키는 논문이 그래프에 없으면 **하나도** 만들어지지 않습니다.
    assert made["references"]["relationships_created"] == 0


def test_the_prediction_that_was_wrong_is_kept(loaded):
    """4.2회를 지우면 산출이 왜 보수적인지가 같이 사라집니다."""
    text = EXPERIMENTS.read_text(encoding="utf-8")

    assert "약 4.2회" in text
    assert "산출이고, 실측은 7.7회입니다" in text
    # 왜 바로 비교하면 안 되는지도 적혀 있어야 합니다.
    assert "배치 크기가 다르므로" in text


def test_the_capacity_guard_still_counts_rows_on_purpose():
    """이 테스트가 깨지면 가드가 과소평가하기 시작한 것입니다 — 11-42절을 다시 읽어야 합니다."""
    source = inspect.getsource(ingest.plan_capacity)

    assert "new_authored=len(authored)" in source.replace(" ", "")
    assert "new_references=len(references)" in source.replace(" ", "")


# --- 하루씩 받기 (ADR-104) ----------------------------------------------------------


def _day_harvest(fail_on=()):
    calls = []

    def harvest_one(start, end, *, sleep, max_pages, **kwargs):
        calls.append((start, end, max_pages, kwargs))
        if start in fail_on:
            raise TimeoutError("The read operation timed out")
        result = oai.HarvestResult(pages=1, last_datestamp=start)
        result.records.append({"arxiv_id": start, "oai_datestamp": start})
        return result

    return harvest_one, calls


def test_the_window_is_harvested_one_day_at_a_time():
    harvest_one, calls = _day_harvest()
    pauses = []
    result = oai.harvest_by_day("2026-10-30", "2026-11-01", harvest_one=harvest_one,
                                sleep=pauses.append, max_pages=10, categories={"cs.AI"})

    assert [(start, end) for start, end, *_ in calls] == [
        ("2026-10-30", "2026-10-30"), ("2026-10-31", "2026-10-31"), ("2026-11-01", "2026-11-01")]
    assert [max_pages for _, _, max_pages, _ in calls] == [10, 9, 8]  # 페이지 상한은 전체에 겁니다
    assert calls[0][3] == {"categories": {"cs.AI"}}
    assert result.days == 3 and result.pages == 3 and len(result.records) == 3
    assert result.last_datestamp == "2026-11-01"
    assert len(pauses) == 2


def test_a_failed_day_keeps_what_came_before_and_the_watermark_stops_there():
    harvest_one, _ = _day_harvest(fail_on={"2026-10-31"})
    result = oai.harvest_by_day("2026-10-30", "2026-11-01", harvest_one=harvest_one,
                                sleep=lambda _: None)

    assert [r["arxiv_id"] for r in result.records] == ["2026-10-30"]
    assert result.last_datestamp == "2026-10-30"  # 다음 실행은 10-31부터
    assert result.stopped_on == "2026-10-31"
    assert "TimeoutError" in result.stop_error


def test_failing_on_the_first_day_is_an_error_not_an_empty_success():
    harvest_one, _ = _day_harvest(fail_on={"2026-10-30"})
    with pytest.raises(TimeoutError):
        oai.harvest_by_day("2026-10-30", "2026-11-01", harvest_one=harvest_one,
                           sleep=lambda _: None)


def test_the_page_limit_stops_the_days_too():
    harvest_one, calls = _day_harvest()
    result = oai.harvest_by_day("2026-10-30", "2026-11-05", harvest_one=harvest_one,
                                sleep=lambda _: None, max_pages=2)

    assert len(calls) == 2 and result.truncated_by_page_limit
    assert result.last_datestamp == "2026-10-31"  # 두 날은 끝까지 받았습니다
    assert result.truncated_on == ""


def _cut_on(cut_day):
    """`cut_day`에서 페이지 한도에 걸려 하루 중간에 끊기는 수집입니다."""
    calls = []

    def harvest_one(start, end, *, sleep, max_pages, **kwargs):
        calls.append(start)
        result = oai.HarvestResult(pages=1, last_datestamp=start)
        result.records.append({"arxiv_id": start, "oai_datestamp": start})
        result.truncated_by_page_limit = start == cut_day
        return result

    return harvest_one, calls


def test_a_day_cut_by_the_page_limit_does_not_move_the_watermark_past_it():
    harvest_one, calls = _cut_on("2026-10-31")
    result = oai.harvest_by_day("2026-10-30", "2026-11-05", harvest_one=harvest_one,
                                sleep=lambda _: None)

    assert calls == ["2026-10-30", "2026-10-31"]  # 끊긴 날 뒤로는 받지 않습니다
    assert [r["arxiv_id"] for r in result.records] == ["2026-10-30", "2026-10-31"]
    assert result.last_datestamp == "2026-10-30"  # 다음 실행은 10-31을 처음부터 다시 받습니다
    assert result.truncated_on == "2026-10-31" and result.truncated_by_page_limit


def test_a_cut_first_day_leaves_the_watermark_where_it_was():
    harvest_one, _ = _cut_on("2026-10-30")
    result = oai.harvest_by_day("2026-10-30", "2026-11-05", harvest_one=harvest_one,
                                sleep=lambda _: None)

    assert len(result.records) == 1  # 받은 것은 적재합니다(MERGE라 다시 받아도 겹치지 않음)
    assert result.last_datestamp == ""  # 빈 워터마크는 _finish가 옮기지 않습니다
    assert result.truncated_on == "2026-10-30"
