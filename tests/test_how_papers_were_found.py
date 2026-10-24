"""화면이 **어떻게 찾았는지**를 사실대로 말하는지 지킵니다(ADR-065).

배포 앱에서 저자 질문을 하면 캡션이 이렇게 나왔습니다.

    한국어→영어 번역: 선택됨 | 벡터 의미 검색: 선택 안 됨 | Text2Cypher: 선택됨 |
    개인화 PageRank: 선택됨

그런데 그래프에는 답변이 인용하지 않은 논문이 함께 그려져 있었습니다. **캡션 한 줄에 거짓이
둘이었습니다.**

1. `rank_related_papers`가 빈자리를 **임베딩으로** 채우는데, 그 길은 `search_papers` 호출이
   아니므로 "벡터: 선택 안 됨"으로 적혔습니다.
2. 그 도구의 주 경로는 **공동인용**입니다(ADR-034에서 개인화 PageRank의 2.2배). 그런데
   이름이 "개인화 PageRank"로만 적혔고, 표의 점수 열도 "PageRank"였습니다 — 그 열에는
   공유 인용자 수(정수)·PPR 점수(확률)·0.0이 섞입니다.

**필요한 값은 이미 있었습니다.** `rank_related_papers`가 논문마다 `ranked_by`를 붙입니다
(`chatbot.py`). 화면이 그것을 읽지 않았을 뿐입니다. 그래서 이 파일이 지키는 것은
**표시가 그 값을 읽는지**입니다 — 후보를 지우는 것이 아닙니다.
"""

import json
from pathlib import Path

import app

ADR = Path("docs/adr/ADR-065-say-how-each-paper-was-found.md")


def response(*, tools=(), related=(), evidence=("1111.1111",), cypher=""):
    """캡션과 그래프가 읽는 필드만 담은 최소 응답입니다."""
    return {
        "tool_calls": [{"name": name, "status": "success"} for name in tools],
        "related": list(related),
        "evidence_ids": list(evidence),
        "rows": [],
        "papers": [],
        "cypher": cypher,
        "translations": [],
    }


def hit(arxiv_id, ranked_by, score=0.0, shared=None):
    return {"arxiv_id": arxiv_id, "ranked_by": ranked_by, "score": score,
            "rank": 1, "shared_citers": shared, "title": f"T {arxiv_id}",
            "primary_category": "cs.AI", "cited_by": 0, "published": "2026-01-01T00:00:00Z"}


def marks(**kwargs):
    """캡션 줄을 `{표찰: 값}`으로 쪼갭니다."""
    return {line.split(":")[0]: line.split(": ", 1)[1]
            for line in app.route_marks(response(**kwargs))}


# ── 거짓 1: 임베딩이 채운 것을 "벡터: 선택 안 됨"으로 적었습니다 ─────────────


def test_an_embedding_fill_is_not_reported_as_no_vector_search():
    """**이 테스트가 이 파일의 이유입니다.** 고치기 전에는 '선택 안 됨'이었습니다."""
    line = marks(tools=["search_graph", "rank_related_papers"],
                 related=[hit("2301.00001", "embedding")])

    assert "선택 안 됨" not in line["벡터 의미 검색"]
    assert "1편" in line["벡터 의미 검색"]


def test_the_two_vector_paths_are_told_apart():
    """직접 검색과 관련 논문 보충은 다른 일입니다. 합치면 어느 쪽인지 알 수 없습니다."""
    searched = marks(tools=["search_papers"])
    filled = marks(tools=["rank_related_papers"], related=[hit("a", "embedding")])
    both = marks(tools=["search_papers", "rank_related_papers"],
                 related=[hit("a", "embedding")])

    assert searched["벡터 의미 검색"] == "선택됨"
    assert "보충에만" in filled["벡터 의미 검색"]
    assert "선택됨 +" in both["벡터 의미 검색"]


def test_no_vector_at_all_still_says_so():
    """쓰지 않은 것을 썼다고 적으면 반대 방향의 거짓입니다."""
    line = marks(tools=["search_graph"], related=[hit("a", "cocitation", shared=3)])

    assert line["벡터 의미 검색"] == "선택 안 됨"


# ── 거짓 2: 공동인용을 "개인화 PageRank"로 불렀습니다 ────────────────────────


def test_cocitation_is_not_called_pagerank():
    """주 경로를 보조 경로의 이름으로 부르면 ADR-034의 결과가 가려집니다."""
    line = marks(tools=["rank_related_papers"],
                 related=[hit("a", "cocitation", shared=4), hit("b", "cocitation", shared=2)])

    assert line["관련 논문"] == "공동인용 2편"
    assert "PageRank" not in line["관련 논문"]


def test_each_mechanism_is_counted_separately():
    line = marks(tools=["rank_related_papers"],
                 related=[hit("a", "cocitation", shared=4), hit("b", "pagerank", 0.01),
                          hit("c", "embedding")])

    assert "공동인용 1편" in line["관련 논문"]
    assert "개인화 PageRank 1편" in line["관련 논문"]
    assert "임베딩 보충 1편" in line["관련 논문"]


def test_a_call_that_found_nothing_is_not_silence():
    """호출했는데 빈 결과인 것과 호출하지 않은 것은 다릅니다."""
    called = marks(tools=["rank_related_papers"])
    never = marks(tools=["search_graph"])

    assert called["관련 논문"] == "관련 논문: 찾았지만 결과 없음".split(": ", 1)[1]
    assert never["관련 논문"] == "선택 안 됨"


def test_an_unknown_mechanism_is_not_silently_dropped():
    """`ranked_by`가 새 값으로 늘면 세다가 사라지는 것이 아니라 보여야 합니다."""
    assert app.found_by({"related": [{"arxiv_id": "a"}]}) == {"unknown": 1}


# ── 그래프: 후보를 **지우지 않고** 구분합니다 ───────────────────────────────


def subgraph(*ids):
    return {"papers": [{"arxiv_id": i, "title": f"T {i}", "primary_category": "cs.AI",
                        "authors": [], "author_count": 0} for i in ids],
            "references": []}


def test_the_graph_marks_candidates_instead_of_removing_them():
    """제안받은 '그래프를 evidence_ids로 제한'은 ADR-034가 이긴 결과를 지웁니다."""
    mechanism = {"2301.00002": "cocitation", "2301.00003": "embedding"}

    dot = app.make_graph(subgraph("1111.1111", "2301.00002", "2301.00003"), mechanism)

    # 세 논문 모두 그려집니다.
    for arxiv_id in ("1111.1111", "2301.00002", "2301.00003"):
        assert arxiv_id in dot
    # 테두리가 다릅니다.
    assert 'style=solid' in dot
    assert 'style=dashed' in dot
    assert 'style=dotted' in dot


def test_evidence_papers_keep_the_plain_border():
    """근거로 인용한 논문이 후보처럼 보이면 가장 나쁜 쪽으로 틀립니다."""
    dot = app.make_graph(subgraph("1111.1111"), {})

    assert '"1111.1111" [label=' in dot
    assert "style=dashed" not in dot
    assert "style=dotted" not in dot


def test_the_graph_still_draws_without_a_mechanism_map():
    """`mechanism`을 넘기지 않는 호출이 남아 있어도 그림이 깨지지 않아야 합니다."""
    assert "1111.1111" in app.make_graph(subgraph("1111.1111"))


def test_the_candidate_ids_still_reach_the_graph():
    """`graph_ids`가 관련 논문을 빼기 시작하면 테두리를 구분할 대상이 사라집니다."""
    ids = app.graph_ids(response(related=[hit("2301.00002", "cocitation", shared=3)]))

    assert "1111.1111" in ids
    assert "2301.00002" in ids


# ── 점수 열이 섞인 단위를 한 이름으로 부르지 않습니다 ───────────────────────


def test_the_score_column_is_not_named_after_one_mechanism():
    """공유 인용자 수와 PPR 점수가 한 열에 섞입니다. 'PageRank'라고 적으면 거짓입니다."""
    assert "점수" in app.RELATED_COLUMN
    assert "PageRank" not in app.RELATED_COLUMN


def test_every_mechanism_has_a_label_and_a_unit():
    """새 메커니즘이 늘었는데 표찰이 없으면 화면에 빈칸이 나옵니다."""
    for key in ("cocitation", "pagerank", "embedding", "unknown"):
        assert app.MECHANISM_LABEL.get(key)
        assert key in app.MECHANISM_UNIT
    for key in app.MECHANISM_NODE_STYLE:
        assert key in app.MECHANISM_LABEL


def test_the_adr_records_what_it_refused_to_do():
    """후보를 지우는 쪽이 더 쉬웠습니다. 왜 안 했는지가 남아 있어야 합니다."""
    text = ADR.read_text(encoding="utf-8")

    assert "ADR-034" in text
    assert "ranked_by" in text
    # 제안받은 처방과 그것을 택하지 않은 이유가 적혀 있어야 합니다.
    assert "기각" in text
    assert "evidence_ids" in text
    # 지우지 않은 근거는 측정값입니다. 그 수가 사라지면 기각 이유가 의견이 됩니다.
    assert "2.2배" in text


# ── 지연 분해: 잰 것과 재지 못한 것 ─────────────────────────────────────────


LATENCY = Path("reports/answer_latency.json")


def test_the_latency_report_names_what_it_could_not_measure():
    """`generate` 안쪽을 못 쟀다는 사실이 보고서에 남아야 다음에 그것을 잽니다."""
    if not LATENCY.exists():
        return
    report = json.loads(LATENCY.read_text(encoding="utf-8"))

    assert "generate" in report["not_measured"]
    assert report["adr_024"]["reproducible"] is False
    for entry in report["modes"].values():
        # 두 노드가 거의 전부라는 것이 이 분해의 결론입니다.
        assert entry["shape_held"], entry["other_share"]
        assert entry["heavy_share"] > 0.95
