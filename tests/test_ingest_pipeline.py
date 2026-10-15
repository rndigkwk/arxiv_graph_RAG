"""증분 수집 파이프라인의 운영 장치를 검증합니다.

수집 파이프라인에서 실제로 사고가 나는 지점은 평상시 경로가 아니라 예외 경로입니다. 재개 토큰이
반복될 때, 논문 한 편의 보강이 실패할 때, 배치가 용량 한도를 넘길 때, 같은 배치가 두 번 들어올
때입니다. 테스트도 거기에 둡니다.

HTTP는 전부 주입된 가짜로 대체하므로 네트워크를 타지 않습니다.
"""

import xml.etree.ElementTree as ElementTree
from datetime import date

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


def test_one_papers_enrichment_failure_does_not_lose_the_batch():
    papers = [{"arxiv_id": "a"}, {"arxiv_id": "b"}, {"arxiv_id": "c"}]

    def lookup(arxiv_id):
        if arxiv_id == "b":
            raise TimeoutError("upstream timed out")
        return [f"{arxiv_id}-ref"]

    enrichment = ingest.enrich_references(papers, lookup)

    assert [row["citing"] for row in enrichment.references] == ["a", "c"]
    assert enrichment.attempted == 3
    assert enrichment.failures[0]["arxiv_id"] == "b"
    assert "TimeoutError" in enrichment.failures[0]["error"]
    assert enrichment.failure_rate == pytest.approx(1 / 3)


def test_enrichment_drops_duplicate_and_self_references():
    enrichment = ingest.enrich_references(
        [{"arxiv_id": "a"}], lambda _: ["x", "x", "a", ""]
    )

    assert enrichment.references == [{"citing": "a", "cited": "x"}]


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
