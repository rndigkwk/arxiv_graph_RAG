"""위치 편향과 심판 자체의 변동을 구별하는 규칙을 검증합니다.

순서를 바꿔 판정이 달라졌다고 바로 "위치 편향"이라고 적으면 안 됩니다. 같은 순서로 두 번 불러도
달라질 수 있기 때문입니다. 이 테스트들이 지키는 것은 그 구별입니다.
"""

from evals.judge_bias import disagreements, summarize, verdict_shape


def shape(content=True, graph=False, conditions=0, matches=None):
    return {
        "requires_content": content,
        "requires_graph": graph,
        "graph_condition_count": conditions,
        "content_matches": dict(matches or {"2601.00001": True, "2601.00002": True}),
    }


def row(noise, order, family="topic"):
    return {
        "id": "x",
        "family": family,
        "question": "q",
        "paper_ids": ["2601.00001", "2601.00002"],
        "noise_disagreements": noise,
        "order_disagreements": order,
        "order_only": not noise and bool(order),
        "tokens": {},
    }


def test_verdict_shape_keeps_only_comparable_parts():
    verdict = {
        "requires_content": True,
        "requires_graph": False,
        "graph_conditions": ["a", "b"],
        "content_matches": {"2601.00001": True},
        "graph_conditions_covered": True,
    }
    kept = verdict_shape(verdict)

    # 조건 문장은 표현이 달라지므로 개수만 봅니다. 문장을 비교하면 변동을 과대평가합니다.
    assert kept["graph_condition_count"] == 2
    assert "graph_conditions" not in kept
    assert kept["content_matches"] == {"2601.00001": True}


def test_identical_verdicts_disagree_on_nothing():
    assert disagreements(shape(), shape()) == []


def test_per_paper_judgments_are_compared_by_id_not_position():
    """위치로 비교하면 순서를 바꾼 것 자체가 차이로 잡혀 아무것도 재지 못합니다."""
    forward = shape(matches={"2601.00001": True, "2601.00002": False})
    # 같은 판정인데 사전 순서만 다른 경우입니다.
    backward = shape(matches={"2601.00002": False, "2601.00001": True})

    assert disagreements(forward, backward) == []


def test_a_flipped_paper_judgment_is_named_with_its_id():
    left = shape(matches={"2601.00001": True, "2601.00002": True})
    right = shape(matches={"2601.00001": True, "2601.00002": False})

    assert disagreements(left, right) == ["content_matches[2601.00002]"]


def test_a_missing_paper_counts_as_a_disagreement():
    # 심판이 논문 하나를 빠뜨리면 판정이 불완전한 것이고, 같다고 볼 수 없습니다.
    left = shape(matches={"2601.00001": True, "2601.00002": True})
    right = shape(matches={"2601.00001": True})

    assert disagreements(left, right) == ["content_matches[2601.00002]"]


def test_the_top_level_judgments_are_compared_too():
    assert disagreements(shape(graph=False), shape(graph=True)) == ["requires_graph"]


def test_a_flip_under_repeat_is_not_attributed_to_the_order():
    """같은 순서로도 흔들린 질문은 위치 편향의 증거가 아닙니다."""
    rows = [row(noise=["requires_graph"], order=["requires_graph"])]
    summary = summarize(rows)

    assert summary["repeat_flip_rate"] == 1.0
    assert summary["order_only_flip_rate"] == 0.0
    assert summary["both_flipped"] == 1.0


def test_only_a_stable_judge_flipping_under_reversal_counts_as_position_bias():
    rows = [row(noise=[], order=["content_matches[2601.00002]"])]
    summary = summarize(rows)

    assert summary["order_only_flip_rate"] == 1.0
    assert summary["repeat_flip_rate"] == 0.0
    assert summary["order_flipped_fields"]["content_matches"] == 1
    assert summary["repeat_flipped_fields"]["content_matches"] == 0


def test_a_judge_that_never_moves_reports_zero_for_both():
    summary = summarize([row(noise=[], order=[]) for _ in range(4)])

    assert summary["repeat_flip_rate"] == 0.0
    assert summary["order_only_flip_rate"] == 0.0
    assert summary["stable_everywhere"] == 1.0


def test_families_are_counted_separately():
    rows = [
        row(noise=[], order=["requires_graph"], family="citing"),
        row(noise=["requires_graph"], order=[], family="topic"),
    ]
    by_family = summarize(rows)["by_family"]

    assert by_family["citing"]["order_only"] == 1
    assert by_family["citing"]["repeat_flips"] == 0
    assert by_family["topic"]["order_only"] == 0
    assert by_family["topic"]["repeat_flips"] == 1


def test_an_empty_run_says_so_instead_of_dividing_by_zero():
    assert summarize([])["questions"] == 0


def test_both_directions_are_counted_per_field():
    """순서 쪽만 세면 "내용 판정은 한 번도 안 뒤집혔다"처럼 잘못 읽힙니다."""
    rows = [
        row(noise=["content_matches[2601.00002]"], order=[]),
        row(noise=[], order=["graph_condition_count"]),
    ]
    summary = summarize(rows)

    assert summary["repeat_flipped_fields"]["content_matches"] == 1
    assert summary["order_flipped_fields"]["content_matches"] == 0
    assert summary["order_flipped_fields"]["graph_condition_count"] == 1
    assert summary["repeat_flipped_fields"]["graph_condition_count"] == 0
