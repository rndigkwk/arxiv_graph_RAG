"""적재 기준 시뮬레이션(`scripts/measure_ingest_criteria.py`)이 규칙을 적은 그대로 쌓는지 봅니다."""

import importlib.util
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "measure_ingest_criteria", Path("scripts/measure_ingest_criteria.py"))
mic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mic)

W1, W2, W3 = date(2026, 8, 3), date(2026, 8, 10), date(2026, 8, 17)


def paper(arxiv_id, authors=("A",)):
    return {"arxiv_id": arxiv_id, "primary_category": "cs.AI", "categories": ["cs.AI"],
            "authors": list(authors)}


# g1은 그래프 안, x는 그래프 밖. n1·n2가 1주에, n3가 2주에, n4가 3주에 x를 인용합니다.
REFS = {"n1": ["g1", "x"], "n2": ["x"], "n3": ["x", "y"], "n4": ["x"], "lone": ["z"]}
WEEKS = [(W1, [paper("n1"), paper("n2")]), (W2, [paper("n3"), paper("lone")]),
         (W3, [paper("n4", ("A", "B"))])]


def run(rule, mode="전부", popularity=None):
    return mic.simulate(WEEKS, REFS, {"g1"}, {"A"}, popularity or {}, new_mode=mode,
                        rule_name=rule)


def test_cumulative_threshold_admits_in_the_week_it_is_reached():
    result = run("누적 ≥3")
    assert [row["outside_admitted"] for row in result["weekly"]] == [0, 1, 0]
    # 2주에 x가 들어오며 n1·n2·n3의 간선 셋이 함께 붙고, 3주의 n4는 바로 잇습니다.
    assert [row["references_edges"] for row in result["weekly"]] == [1, 3, 1]
    assert result["outside_edges"] == 4
    assert result["later_citations"]["x"] == 1


def test_nothing_outside_leaves_only_edges_into_the_graph():
    result = run("안 들임")
    assert sum(row["references_edges"] for row in result["weekly"]) == 1
    assert result["admitted"] == 0


def test_linked_only_skips_papers_that_reach_nothing():
    result = run("안 들임", mode="닿는 것만")
    assert result["loaded"] == ["n1"]
    assert [row["new_papers"] for row in result["weekly"]] == [1, 0, 0]


def test_popularity_rule_admits_on_first_citation():
    result = run("S2 인용 ≥200", popularity={"y": 500})
    assert [row["outside_admitted"] for row in result["weekly"]] == [0, 1, 0]


def test_new_authors_are_counted_once():
    result = run("안 들임")
    assert [row["author_nodes_measured"] for row in result["weekly"]] == [0, 0, 1]


def test_summary_counts_isolated_new_papers():
    summary = mic.summarize(run("안 들임"), 3, 7.0, 0.5)
    # n1만 그래프에 닿습니다. 실린 새 논문 다섯 편 중 넷이 고립입니다.
    assert summary["isolated_new_share"] == 0.8


def test_reference_floor_drops_thin_and_unindexed_papers():
    counts = {"n1": 30, "n2": 3, "n3": 12, "n4": 5}  # lone은 S2에 없습니다
    result = mic.simulate(WEEKS, REFS, {"g1"}, {"A"}, {}, new_mode="참고문헌 ≥5",
                          rule_name="안 들임", reference_counts=counts)
    assert sorted(result["loaded"]) == ["n1", "n3", "n4"]


class FakeSnapshot:
    def __init__(self, papers, authored):
        self.tables = {"papers": [{"arxiv_id": i} for i in papers],
                       "authored": [{"author": a, "paper": p} for a, p in authored]}

    def rows(self, table):
        return iter(self.tables[table])


def test_graph_before_rewinds_the_window_and_later_ids():
    snap = FakeSnapshot(["2607.00001", "2608.00002", "2609.00003", "cs/0101001"],
                        [("A", "2607.00001"), ("B", "2608.00002"), ("A", "2609.00003"),
                         ("C", "cs/0101001")])
    papers, authors = mic.graph_before(snap, {"2607.00009"}, W1)
    # 2608 이후 ID는 창이 시작한 뒤에 들어온 것이라 뺍니다. 옛 형식 ID는 남깁니다.
    assert papers == {"2607.00001", "cs/0101001"}
    assert authors == {"A", "C"}
