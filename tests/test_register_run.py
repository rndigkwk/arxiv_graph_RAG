"""외부에서 돌린 보고서를 추적에 등록하는 평탄화 규칙을 검증합니다.

보고서 모양을 미리 정해 두지 않는 것이 요점입니다. Colab 실행은 `report_to=[]`로 돌아
자기 기록을 남기지 않으므로 JSON을 받아 여기서 등록하는데, 그 JSON의 구조는 실험마다
다릅니다. 그래서 중첩을 훑어 숫자와 짧은 문자열만 꺼냅니다.
"""

from pipeline.register_run import MAX_PARAM_LENGTH, flatten, split_leaves


def test_nested_keys_are_joined_with_dots():
    flat = flatten({"results": {"model": {"recall_at_10": 0.19}}})

    assert flat == {"results.model.recall_at_10": 0.19}


def test_a_list_contributes_only_its_length():
    # 원소까지 펼치면 지표 이름이 수십 개로 번집니다.
    flat = flatten({"cases": [1, 2, 3], "nested": {"ids": ["a", "b"]}})

    assert flat == {"cases.count": 3, "nested.ids.count": 2}


def test_booleans_become_metrics_not_params():
    metrics, params = split_leaves(flatten({"gate": {"passed": True, "skipped": False}}))

    # bool은 int의 하위형이므로 순서를 잘못 쓰면 파라미터로 새어 나갑니다.
    assert metrics == {"gate.passed": 1.0, "gate.skipped": 0.0}
    assert params == {}


def test_numbers_become_metrics_and_short_strings_become_params():
    metrics, params = split_leaves(
        flatten({"recall": 0.19, "queries": 365, "model": "bge-base-en-v1.5"})
    )

    assert metrics == {"recall": 0.19, "queries": 365.0}
    assert params == {"model": "bge-base-en-v1.5"}


def test_a_long_string_is_dropped():
    _, params = split_leaves(flatten({"description": "가" * (MAX_PARAM_LENGTH + 1)}))

    # 긴 설명문은 지표도 파라미터도 아니므로 기록하지 않습니다.
    assert params == {}


def test_prose_keys_are_skipped():
    metrics, params = split_leaves(
        flatten({"note": "읽는 법", "reading": "구간이 0을 포함하면", "recall": 0.19})
    )

    assert metrics == {"recall": 0.19}
    assert params == {}


def test_empty_strings_and_nulls_are_dropped():
    metrics, params = split_leaves(flatten({"blank": "", "missing": None, "value": 1}))

    assert metrics == {"value": 1.0}
    assert params == {}


def test_a_realistic_colab_report_keeps_the_numbers_that_matter():
    report = {
        "smoke": False,
        "base_model": "BAAI/bge-base-en-v1.5",
        "hyperparameters": {"batch_size": 32, "mini_batch_size": 32},
        "results": {
            "bge (hard)": {
                "recall_at_10": 0.2105,
                "vs_untuned": {"mean_difference": 0.0196, "excludes_zero": True},
                "loss_curve": {"first": 2.1, "last": 0.72, "logged_steps": 1142},
            }
        },
    }

    metrics, params = split_leaves(flatten(report))

    assert metrics["results.bge (hard).recall_at_10"] == 0.2105
    assert metrics["results.bge (hard).vs_untuned.excludes_zero"] == 1.0
    assert metrics["results.bge (hard).loss_curve.logged_steps"] == 1142.0
    assert metrics["hyperparameters.mini_batch_size"] == 32.0
    assert metrics["smoke"] == 0.0
    assert params["base_model"] == "BAAI/bge-base-en-v1.5"
