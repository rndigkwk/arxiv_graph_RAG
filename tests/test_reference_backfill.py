"""빠진 인용 간선을 얹어 보는 측정(`scripts/measure_reference_backfill.py`, ADR-100)이
간선을 바르게 고르고 얹는지 봅니다."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "measure_reference_backfill", Path("scripts/measure_reference_backfill.py"))
mrb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mrb)


def test_new_edges_keep_only_papers_inside_the_graph_and_skip_known_ones():
    raw = {"references": {"a": ["b", "c", "outside", "a"], "d": ["b"]}}
    edges = mrb.new_edges(raw, {"a", "b", "c", "d"}, existing={("a", "b")})
    # 밖 논문은 노드를 늘리므로 빼고, 이미 있는 간선과 자기 인용도 뺍니다.
    assert edges == [{"citing": "a", "cited": "c"}, {"citing": "d", "cited": "b"}]


def test_extra_references_respect_the_cut_and_are_not_counted_twice():
    try:
        from evals.related_study import Graph
        from models.link_prediction.train import parse_month
        from pipeline import snapshot as snapshot_module

        snap = snapshot_module.load(mrb.SNAPSHOT)
        cut = parse_month("2026-08")
        plain = Graph(snap, feature_cut=cut)
    except Exception as exc:  # noqa: BLE001 - 스냅샷이 없는 곳에서는 건너뜁니다
        pytest.skip(f"스냅샷 캐시를 읽을 수 없습니다: {type(exc).__name__}")

    known = next(iter(snap.rows("references")))
    old = next(i for i in plain.ids if i.startswith("2301.") and i not in plain.cites)
    late = next(i for i in plain.ids if i.startswith("2609."))
    target = next(i for i in plain.ids if i != old and i not in plain.cites.get(old, ()))
    extra = [known, {"citing": old, "cited": target}, {"citing": late, "cited": target}]
    grown = Graph(snap, feature_cut=cut, extra_references=extra)

    def count(graph):
        return sum(len(v) for v in graph.cites.values())

    # 이미 있는 간선은 그대로, 자르는 선 뒤의 인용자는 버리고, 앞의 것 하나만 늘어납니다.
    assert count(grown) == count(plain) + 1
    assert target in grown.cites[old]
    assert late not in grown.cites
