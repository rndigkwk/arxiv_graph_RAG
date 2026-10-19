"""손으로 고른 임계값과 학습된 결정을 같은 질의에서 비교합니다.

    uv run --extra ml python -m models.title_disambiguation.train

**비교 대상이 규칙입니다.**
[ADR-028](../../docs/adr/ADR-028-resolve-titles-with-a-fulltext-index.md)의
`title_search.choose`는 세 단계입니다 — 부분문자열이 한 편이면 그것, 아니면 전문 검색 1위,
단 적중률 0.8 미만이면 거부. 그 규칙을 데이터에서 **그대로 재현**해서 같은 질의에 대고
비교합니다. 규칙을 약하게 재현하면 이기는 것이 당연해지므로 `exact_id`까지 기록해 뒀습니다.

**모델은 로지스틱 회귀입니다.** 후보 하나하나에 "이게 정답인가"를 매기고, 질의별로 최대값을
고른 뒤 그 값이 임계를 넘을 때만 받아들입니다. 임계는 **검증 창에서** 고르고 평가 창은
건드리지 않습니다.

이유가 셋입니다.

- **배포 비용이 작습니다.** 계수 10여 개라 앱에 넣는 것이 행렬곱보다 가볍습니다. 다만
  `scikit-learn` 임포트 비용은 따로 재야 합니다(ADR-016의 교훈 — 모델 크기와 임포트 비용은
  다릅니다). 그 측정 없이 "메모리 1MB"라고 적지 않습니다.
- **계수를 읽을 수 있습니다.** 어느 피처가 결정을 만드는지 보고할 수 있고, `starts_with`가
  실제로 쓰이는지 확인할 수 있습니다.
- **결정적입니다.** 닫힌 해는 아니지만 `lbfgs`가 같은 입력에서 같은 결과를 냅니다.

## 세 창

    학습   month_index <  valid_cut
    검증   valid_cut   <= month_index < test_cut      임계값을 여기서 고릅니다
    평가   month_index >= test_cut                    한 번만 봅니다

`month_index`는 arxiv ID의 `YYMM`입니다([ADR-002](../../docs/adr/ADR-002-arxiv-id-as-time-key.md)).
무작위 분할을 쓰면 같은 시기의 제목 분포가 양쪽에 들어가 점수가 부풀려집니다(11-3절).
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from title_search import AMBIGUITY_GAP, MIN_COVERAGE

DATASET = Path("derived/title_disambiguation.jsonl")
OUTPUT = Path("reports/title_disambiguation.json")
WEIGHTS = Path("derived/title_disambiguation_model.json")
FEATURES = (
    "score_norm",
    "score_gap",
    "query_coverage",
    "title_coverage",
    "starts_with",
    "order_kept",
    "title_words",
    "query_words",
    "rank",
    "exact_unique",
)
# 받아들일 임계값 후보. 검증 창에서 고릅니다.
THRESHOLDS = tuple(round(0.05 * step, 2) for step in range(1, 20))
SEED = 20261019


def load(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def by_query(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for row in rows:
        grouped[row["query_id"]].append(row)
    for candidates in grouped.values():
        candidates.sort(key=lambda row: row["rank"])
    return grouped


def target_of(candidates: list[dict[str, Any]]) -> str | None:
    """정답 논문. "없는 논문" 질의는 None입니다."""
    for row in candidates:
        if row["label"]:
            return row["arxiv_id"]
    return None


def rule_decision(candidates: list[dict[str, Any]]) -> str | None:
    """`title_search.choose`를 데이터에서 그대로 재현합니다.

    1. 부분문자열이 정확히 한 편이면 그것.
    2. 아니면 전문 검색 1위. 단 적중률이 `MIN_COVERAGE` 미만이면 **거부**(None).

    `AMBIGUITY_GAP`은 받아들일지 말지를 바꾸지 않습니다 - 애매하다고 **말할** 뿐이고
    `arxiv_id`는 그대로 돌려줍니다. 그래서 정확도 비교에는 들어가지 않습니다.
    """
    top = candidates[0]
    if top["exact_unique"] and top.get("exact_id"):
        return top["exact_id"]
    matched = max(top["query_coverage"], top["title_coverage"])
    if matched < MIN_COVERAGE:
        return None
    return top["arxiv_id"]


def model_decision(
    candidates: list[dict[str, Any]], weights: np.ndarray, bias: float, threshold: float
) -> str | None:
    matrix = np.asarray([[row[name] for name in FEATURES] for row in candidates], dtype=float)
    scores = 1.0 / (1.0 + np.exp(-(matrix @ weights + bias)))
    best = int(np.argmax(scores))
    if scores[best] < threshold:
        return None
    return candidates[best]["arxiv_id"]


def judge(grouped: dict[str, list[dict[str, Any]]], decide) -> dict[str, Any]:
    """Accuracy on answerable queries, and what happens on the unanswerable ones."""
    answerable = wrong = right = rejected = 0
    fake = fake_blocked = 0
    per_query: dict[str, float] = {}
    for query_id, candidates in grouped.items():
        target = target_of(candidates)
        picked = decide(candidates)
        if target is None:
            fake += 1
            fake_blocked += int(picked is None)
            per_query[query_id] = float(picked is None)
            continue
        answerable += 1
        if picked is None:
            rejected += 1
        elif picked == target:
            right += 1
        else:
            wrong += 1
        per_query[query_id] = float(picked == target)
    return {
        "answerable": answerable,
        "top1": round(right / answerable, 4) if answerable else None,
        "wrong": round(wrong / answerable, 4) if answerable else None,
        "falsely_rejected": round(rejected / answerable, 4) if answerable else None,
        "nonexistent": fake,
        "nonexistent_blocked": round(fake_blocked / fake, 4) if fake else None,
        "per_query": per_query,
    }


def paired_bootstrap(
    rule: dict[str, float], model: dict[str, float], *, samples: int = 2000
) -> dict[str, float]:
    shared = sorted(set(rule) & set(model))
    diff = np.asarray([model[q] - rule[q] for q in shared], dtype=float)
    rng = np.random.default_rng(SEED)
    draws = rng.integers(0, len(diff), size=(samples, len(diff)))
    means = diff[draws].mean(axis=1)
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "queries": len(shared),
        "mean": round(float(diff.mean()), 4),
        "low": round(low, 4),
        "high": round(high, 4),
        "excludes_zero": bool(low > 0 or high < 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="손으로 고른 규칙과 학습된 결정을 비교합니다.")
    parser.add_argument("--dataset", type=Path, default=DATASET)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--weights", type=Path, default=WEIGHTS)
    parser.add_argument("--valid-cut", type=int, help="생략하면 분위로 정합니다.")
    parser.add_argument("--test-cut", type=int)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    from sklearn.linear_model import LogisticRegression

    rows = load(args.dataset)
    months = sorted({row["month_index"] for row in rows})
    valid_cut = args.valid_cut or months[int(len(months) * 0.6)]
    test_cut = args.test_cut or months[int(len(months) * 0.8)]

    windows = {"train": [], "valid": [], "test": []}
    for row in rows:
        month = row["month_index"]
        key = "train" if month < valid_cut else ("valid" if month < test_cut else "test")
        windows[key].append(row)
    grouped = {name: by_query(subset) for name, subset in windows.items()}

    train = windows["train"]
    matrix = np.asarray([[row[name] for name in FEATURES] for row in train], dtype=float)
    labels = np.asarray([row["label"] for row in train], dtype=int)
    if labels.sum() == 0 or labels.sum() == len(labels):
        print("학습 창에 한쪽 라벨만 있습니다. 창을 다시 잡으세요.")
        return 1
    model = LogisticRegression(max_iter=2000, C=1.0).fit(matrix, labels)
    weights = model.coef_[0]
    bias = float(model.intercept_[0])

    # 임계값은 **검증 창에서만** 고릅니다.
    picked_threshold, best_score = THRESHOLDS[0], -1.0
    for threshold in THRESHOLDS:
        scored = judge(
            grouped["valid"],
            lambda c, t=threshold: model_decision(c, weights, bias, t),
        )
        # 답할 수 있는 질의의 정확도와 없는 논문 차단율을 같은 무게로 봅니다.
        value = (scored["top1"] or 0.0) + (scored["nonexistent_blocked"] or 0.0)
        if value > best_score:
            picked_threshold, best_score = threshold, value

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset).replace("\\", "/"),
        "rows": len(rows),
        "features": list(FEATURES),
        "split": {
            "key": "month_index",
            "valid_cut": valid_cut,
            "test_cut": test_cut,
            "queries": {name: len(group) for name, group in grouped.items()},
        },
        "rule_thresholds": {"ambiguity_gap": AMBIGUITY_GAP, "min_coverage": MIN_COVERAGE},
        "model": {
            "kind": "logistic_regression",
            "threshold": picked_threshold,
            "bias": round(bias, 4),
            "coefficients": {
                name: round(float(value), 4)
                for name, value in zip(FEATURES, weights, strict=True)
            },
        },
    }

    for window in ("valid", "test"):
        rule = judge(grouped[window], rule_decision)
        learned = judge(
            grouped[window],
            lambda c: model_decision(c, weights, bias, picked_threshold),
        )
        report[window] = {
            "rule": {k: v for k, v in rule.items() if k != "per_query"},
            "model": {k: v for k, v in learned.items() if k != "per_query"},
            "model_minus_rule": paired_bootstrap(rule["per_query"], learned["per_query"]),
        }
        # 질의 형태별로도 봅니다. 상한이 `prefix`에 몰려 있기 때문입니다.
        shapes: dict[str, dict[str, Any]] = {}
        for shape in sorted({row["shape"] for row in windows[window]}):
            subset = {
                q: c for q, c in grouped[window].items()
                if c and c[0]["shape"] == shape
            }
            if not subset:
                continue
            shapes[shape] = {
                "queries": len(subset),
                "rule": judge(subset, rule_decision)["top1"],
                "model": judge(
                    subset, lambda c: model_decision(c, weights, bias, picked_threshold)
                )["top1"],
            }
        report[window]["by_shape"] = shapes

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.weights.parent.mkdir(parents=True, exist_ok=True)
    args.weights.write_text(json.dumps({
        "features": list(FEATURES),
        "coefficients": [round(float(v), 6) for v in weights],
        "bias": round(bias, 6),
        "threshold": picked_threshold,
        "trained_on": str(args.dataset).replace("\\", "/"),
        "split": report["split"],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(
        {k: report[k] for k in ("rows", "split", "model", "valid", "test")},
        ensure_ascii=False, indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
