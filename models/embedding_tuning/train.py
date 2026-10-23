"""인용 쌍으로 임베딩 어댑터를 학습하고 Recall@k를 오프라인 측정합니다.

`Corpus`와 채점 함수는 `compare_models`가 로컬 모델을 같은 질의·같은 정답으로 재는 데도
씁니다. 비교가 성립하려면 과제가 하나여야 하므로 채점 경로를 복제하지 않습니다.

    uv run python -m models.embedding_tuning.train
    uv run python -m models.embedding_tuning.train --negative-weight 0.5 --ridge-multiplier 0.5

과제는 **"이 논문이 인용할 논문을 찾아라"**입니다. 질의는 논문의 제목+초록 임베딩이고 정답은 그
논문이 실제로 인용한 논문들입니다. 라벨 생성 비용이 0입니다 — 인용 관계가 그대로 정답입니다.

시간 분할을 지킵니다. 인용하는 논문의 발행월로 학습·평가를 나누고, 어댑터는 학습 구간에서만
적합합니다. 평가 구간의 인용 쌍은 W를 만드는 데 쓰이지 않습니다.

정답 집합이 작아야 Recall@k가 의미를 갖습니다. 참고문헌이 2~10편인 논문만 평가에 씁니다 —
21편을 인용하는 논문에 Recall@10을 적용하면 상한이 0.48로 묶여 모델 차이가 묻힙니다.

**비대칭 변환이므로 범용 검색기를 대체하지 않습니다.** 마지막에 `exact-title` 검색을 같은 변환으로
돌려 그것을 확인합니다. 대체가 아니라 의도가 다른 두 번째 경로라는 것이 측정 결과입니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from models.embedding_tuning import adapter
from pipeline import features
from pipeline import snapshot as snapshot_module
from pipeline.snapshot import month_index, month_label
from pipeline.tracking import track

OUTPUT = Path("reports/embedding_tuning.json")
DEFAULT_CUT = "2026-08"
MIN_REFERENCES = 2
MAX_REFERENCES = 10
MAX_TRAIN_PAIRS = 40000
MAX_TEST_QUERIES = 1200
MAX_VALIDATION_QUERIES = 400
TOP_K = 10
SEED = 20261015
VALIDATION_SHARE = 0.25
# ridge는 데이터 항과 같은 눈금에서 봐야 합니다. Va^T Va의 대각합이 학습 쌍 수와 비슷하므로,
# 쌍 수의 배수로 격자를 잡습니다. 작게 두면 W가 항등행렬에서 크게 벗어납니다.
RIDGE_MULTIPLIERS = (0.01, 0.1, 0.5, 2.0, 10.0, 50.0)
NEGATIVE_WEIGHTS = (0.0, 0.5, 0.9)


def parse_month(value: str) -> int:
    year, _, month = value.partition("-")
    if not (year.isdigit() and month.isdigit()):
        raise argparse.ArgumentTypeError(f"YYYY-MM 형식이어야 합니다: {value}")
    index = month_index(f"{int(year) % 100:02d}{int(month):02d}.00000")
    if index is None:
        raise argparse.ArgumentTypeError(f"월로 해석할 수 없습니다: {value}")
    return index


class Corpus:
    """Snapshot vectors plus the citation lists, keyed by arXiv ID."""

    def __init__(self, snap: Any) -> None:
        cache = features.Cache(snap)
        self.ids = cache.ids
        self.position = cache.position
        self.primary = {
            row["arxiv_id"]: row.get("primary_category") or "" for row in cache.meta()
        }
        self.vectors = adapter.unit(np.asarray(cache.embeddings(mmap=False), dtype=np.float32))
        self.references: dict[str, set[str]] = collections.defaultdict(set)
        for row in snap.rows("references"):
            if row["citing"] in self.position and row["cited"] in self.position:
                self.references[row["citing"]].add(row["cited"])
        self.by_category: dict[str, list[str]] = collections.defaultdict(list)
        for arxiv_id in self.ids:
            self.by_category[self.primary.get(arxiv_id, "")].append(arxiv_id)

    def row(self, arxiv_id: str) -> np.ndarray:
        return self.vectors[self.position[arxiv_id]]

    def with_vectors(self, matrix: np.ndarray) -> Corpus:
        """Same papers and citation lists, a different embedding matrix.

        Lets one local model be scored on exactly the queries and gold sets the OpenAI
        embeddings were scored on. Anything else would compare two tasks.
        """
        if matrix.shape[0] != len(self.ids):
            raise ValueError("행 수가 논문 수와 같아야 합니다")
        swapped = object.__new__(Corpus)
        swapped.__dict__.update(self.__dict__)
        swapped.vectors = adapter.unit(np.asarray(matrix, dtype=np.float32))
        return swapped


def split_citers(corpus: Corpus, cut: int) -> dict[str, list[str]]:
    """Split citing papers by their own publication month, never by the cited paper's."""
    train, test = [], []
    for citing in sorted(corpus.references):
        month = month_index(citing)
        if month is None:
            continue
        (train if month < cut else test).append(citing)
    return {"train": train, "test": test}


def hard_negative(
    corpus: Corpus, citing: str, cited: str, rng: random.Random
) -> str | None:
    """Same primary category as the cited paper, never cited by this citing paper.

    This is the roadmap's design point. A random negative is a different topic, so the model
    separates it on topic alone and learns nothing about citation. A same-field paper that is
    not cited is the case the model has to actually get right.
    """
    pool = corpus.by_category.get(corpus.primary.get(cited, ""), ())
    if len(pool) < 8:
        return None
    known = corpus.references[citing]
    for _ in range(16):
        candidate = pool[rng.randrange(len(pool))]
        if candidate != citing and candidate != cited and candidate not in known:
            return candidate
    return None


def training_arrays(
    corpus: Corpus, citers: list[str], *, limit: int, seed: int
) -> dict[str, Any]:
    """Build the anchor / positive / hard-negative matrices for the closed-form fit."""
    rng = random.Random(seed)
    pairs = [
        (citing, cited)
        for citing in citers
        for cited in sorted(corpus.references[citing])
    ]
    if len(pairs) > limit:
        pairs = rng.sample(pairs, limit)
    anchors, positives, negatives, used = [], [], [], 0
    for citing, cited in pairs:
        negative = hard_negative(corpus, citing, cited, rng)
        if negative is None:
            continue
        anchors.append(corpus.row(citing))
        positives.append(corpus.row(cited))
        negatives.append(corpus.row(negative))
        used += 1
    if not used:
        raise ValueError("학습 쌍을 만들지 못했습니다")
    return {
        "anchors": np.stack(anchors),
        "positives": np.stack(positives),
        "negatives": np.stack(negatives),
        "pairs_available": len(pairs),
        "pairs_used": used,
    }


def evaluation_queries(
    corpus: Corpus, citers: list[str], *, limit: int, seed: int
) -> list[tuple[str, list[str]]]:
    """Keep citing papers whose reference list is small enough for Recall@k to move."""
    eligible = [
        (citing, sorted(corpus.references[citing]))
        for citing in citers
        if MIN_REFERENCES <= len(corpus.references[citing]) <= MAX_REFERENCES
    ]
    if len(eligible) > limit:
        eligible = random.Random(seed).sample(eligible, limit)
    return sorted(eligible)


def per_query_recall(
    corpus: Corpus,
    queries: list[tuple[str, list[str]]],
    weights: np.ndarray,
    *,
    top_k: int,
) -> np.ndarray:
    """Recall@k for each query, kept unaveraged so two variants can be compared pairwise."""
    return np.asarray(_rank(corpus, queries, weights, top_k=top_k)["recalls"])


def paired_bootstrap(
    baseline: np.ndarray, candidate: np.ndarray, *, samples: int = 2000, seed: int = SEED
) -> dict[str, float]:
    """Confidence interval for the per-query difference, resampling queries together.

    An average that moves by a fraction of one query is not a result. The queries are the unit
    of variation here, so they are resampled in pairs and the interval is read off the
    difference. An interval that straddles zero means the change was not measured.
    """
    if baseline.shape != candidate.shape:
        raise ValueError("두 배열의 질의 수가 같아야 합니다")
    difference = candidate - baseline
    generator = np.random.default_rng(seed)
    indices = generator.integers(0, len(difference), size=(samples, len(difference)))
    means = difference[indices].mean(axis=1)
    low, high = np.percentile(means, [2.5, 97.5])
    return {
        "mean_difference": round(float(difference.mean()), 4),
        "ci95_low": round(float(low), 4),
        "ci95_high": round(float(high), 4),
        "excludes_zero": bool(low > 0.0 or high < 0.0),
        "queries": int(len(difference)),
    }


def _rank(
    corpus: Corpus,
    queries: list[tuple[str, list[str]]],
    weights: np.ndarray,
    *,
    top_k: int,
) -> dict[str, Any]:
    """Rank the whole corpus for every query and return the per-query numbers."""
    query_matrix = adapter.apply(
        weights, np.stack([corpus.row(citing) for citing, _ in queries])
    )
    similarity = query_matrix @ corpus.vectors.T
    # 질의 논문 자신은 후보에서 뺍니다. 자기 자신이 늘 1위라 k를 소모합니다.
    for index, (citing, _) in enumerate(queries):
        similarity[index, corpus.position[citing]] = -np.inf
    ranked = np.argpartition(-similarity, top_k, axis=1)[:, :top_k]
    recalls, hits, reciprocal = [], [], []
    for index, (_, gold_ids) in enumerate(queries):
        order = ranked[index][np.argsort(-similarity[index, ranked[index]])]
        retrieved = [corpus.ids[position] for position in order]
        gold = set(gold_ids)
        found = [rank for rank, paper in enumerate(retrieved, start=1) if paper in gold]
        recalls.append(len(gold & set(retrieved)) / len(gold))
        hits.append(float(bool(found)))
        reciprocal.append(1.0 / found[0] if found else 0.0)
    return {"recalls": recalls, "hits": hits, "reciprocal": reciprocal}


def score(
    corpus: Corpus,
    queries: list[tuple[str, list[str]]],
    weights: np.ndarray,
    *,
    top_k: int,
) -> dict[str, float]:
    """Average the per-query numbers into the reported metrics."""
    parts = _rank(corpus, queries, weights, top_k=top_k)
    return {
        "queries": len(queries),
        f"recall_at_{top_k}": round(float(np.mean(parts["recalls"])), 4),
        f"hit_at_{top_k}": round(float(np.mean(parts["hits"])), 4),
        "mrr": round(float(np.mean(parts["reciprocal"])), 4),
    }


def title_regression(
    corpus: Corpus, weights: np.ndarray, cases: Path, *, top_k: int
) -> dict[str, Any] | None:
    """Check the adapter against exact-title retrieval, which it is not meant to serve.

    The transform is asymmetric: it maps a query toward "papers this one would cite". Applying
    it to a title lookup should hurt, and measuring that is how the claim "a second route, not
    a replacement" stops being an assertion.
    """
    if not cases.exists():
        return None
    value = json.loads(cases.read_text(encoding="utf-8-sig"))
    rows = value["cases"] if isinstance(value, dict) else value
    titled = [
        (case["expected_ids"][0], case["expected_ids"])
        for case in rows
        if case["category"] == "exact-title"
        and len(case["expected_ids"]) == 1
        and case["expected_ids"][0] in corpus.position
    ]
    if not titled:
        return None
    # 제목 질문의 정답은 그 논문 자신이므로, 자기 제외를 끄고 그 논문을 찾는지 봅니다.
    query_matrix = adapter.apply(
        weights, np.stack([corpus.row(paper_id) for paper_id, _ in titled])
    )
    similarity = query_matrix @ corpus.vectors.T
    ranked = np.argmax(similarity, axis=1)
    found = sum(
        1 for index, (paper_id, _) in enumerate(titled)
        if corpus.ids[ranked[index]] == paper_id
    )
    return {"cases": len(titled), "self_retrieved_at_1": round(found / len(titled), 4)}


def sweep(
    corpus: Corpus,
    arrays: dict[str, Any],
    validation: list[tuple[str, list[str]]],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Choose the hyperparameters on a held-out validation split, never on the test split.

    The grid is written in multiples of the training pair count because the data term scales
    with it: a fixed ridge of 1.0 against 40,000 pairs is effectively no regularization, which
    is how the first run let W drift 0.55 away from the identity and lose to doing nothing.
    """
    pairs = arrays["pairs_used"]
    rows = []
    for multiplier in RIDGE_MULTIPLIERS:
        for negative_weight in NEGATIVE_WEIGHTS:
            weights = adapter.fit(
                arrays["anchors"], arrays["positives"], arrays["negatives"],
                ridge=multiplier * pairs, negative_weight=negative_weight,
            )
            rows.append({
                "ridge_multiplier": multiplier,
                "ridge": multiplier * pairs,
                "negative_weight": negative_weight,
                "drift_from_identity": round(adapter.drift_from_identity(weights), 4),
                **score(corpus, validation, weights, top_k=top_k),
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="인용 쌍으로 임베딩 어댑터를 학습합니다.")
    parser.add_argument("--version")
    parser.add_argument("--cut", default=DEFAULT_CUT,
                        help="이 달부터 인용하는 논문이 평가 질의가 됩니다 (YYYY-MM).")
    parser.add_argument("--ridge-multiplier", type=float,
                        help="생략하면 검증 분할에서 격자 탐색으로 고릅니다.")
    parser.add_argument("--negative-weight", type=float,
                        help="생략하면 검증 분할에서 격자 탐색으로 고릅니다.")
    parser.add_argument("--top-k", type=int, default=TOP_K)
    parser.add_argument("--max-train-pairs", type=int, default=MAX_TRAIN_PAIRS)
    parser.add_argument("--max-test-queries", type=int, default=MAX_TEST_QUERIES)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(args.version) if args.version else snapshot_module.latest()
    cut = parse_month(args.cut)
    corpus = Corpus(snap)
    split = split_citers(corpus, cut)
    # 학습 구간 안에서 다시 떼어 하이퍼파라미터를 고릅니다. 평가 구간은 건드리지 않습니다.
    holdout = random.Random(SEED + 11)
    train_citers = list(split["train"])
    holdout.shuffle(train_citers)
    boundary = max(1, int(len(train_citers) * (1.0 - VALIDATION_SHARE)))
    fit_citers, validation_citers = train_citers[:boundary], train_citers[boundary:]

    arrays = training_arrays(
        corpus, fit_citers, limit=args.max_train_pairs, seed=SEED
    )
    validation = evaluation_queries(
        corpus, validation_citers, limit=MAX_VALIDATION_QUERIES, seed=SEED + 3
    )
    queries = evaluation_queries(
        corpus, split["test"], limit=args.max_test_queries, seed=SEED + 5
    )
    if not queries or not validation:
        raise SystemExit("평가 또는 검증 질의를 만들지 못했습니다. --cut을 조정하세요.")

    recall_key = f"recall_at_{args.top_k}"
    grid = sweep(corpus, arrays, validation, top_k=args.top_k)
    raw_validation = score(
        corpus, validation, np.eye(corpus.vectors.shape[1], dtype=np.float32),
        top_k=args.top_k,
    )
    best = max(grid, key=lambda row: row[recall_key])
    chosen = {
        "ridge_multiplier": args.ridge_multiplier
        if args.ridge_multiplier is not None else best["ridge_multiplier"],
        "negative_weight": args.negative_weight
        if args.negative_weight is not None else best["negative_weight"],
    }
    chosen["ridge"] = chosen["ridge_multiplier"] * arrays["pairs_used"]

    variants: dict[str, np.ndarray] = {
        "raw": np.eye(corpus.vectors.shape[1], dtype=np.float32),
        "ridge_only": adapter.fit(
            arrays["anchors"], arrays["positives"],
            ridge=chosen["ridge"], negative_weight=0.0,
        ),
        "hard_negatives": adapter.fit(
            arrays["anchors"], arrays["positives"], arrays["negatives"],
            ridge=chosen["ridge"], negative_weight=chosen["negative_weight"],
        ),
    }
    results = {
        name: {
            **score(corpus, queries, weights, top_k=args.top_k),
            "drift_from_identity": round(adapter.drift_from_identity(weights), 4),
            "exact_title_check": title_regression(
                corpus, weights, args.cases, top_k=args.top_k
            ),
        }
        for name, weights in variants.items()
    }

    baseline_recall = per_query_recall(
        corpus, queries, variants["raw"], top_k=args.top_k
    )
    comparison = {
        name: paired_bootstrap(
            baseline_recall,
            per_query_recall(corpus, queries, weights, top_k=args.top_k),
        )
        for name, weights in variants.items()
        if name != "raw"
    }

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "snapshot_version": snap.version,
        "snapshot_papers_sha256": snap.manifest["files"]["papers"]["sha256"],
        "task": "retrieve the papers a paper cites",
        "method": (
            "기존 768차원 공간 위의 선형 어댑터. 닫힌 해이므로 학습 루프와 난수 시드가 없고 "
            "질의 시 행렬곱 한 번이라 sentence-transformers도 torch도 필요하지 않습니다."
        ),
        "split": {
            "key": "arxiv_id_yymm",
            "test_citers_from": month_label(cut),
            "train_citers": len(split["train"]),
            "test_citers": len(split["test"]),
            "train_pairs_available": arrays["pairs_available"],
            "train_pairs_used": arrays["pairs_used"],
            "fit_citers": len(fit_citers),
            "validation_citers": len(validation_citers),
            "validation_queries": len(validation),
            "eval_queries": len(queries),
            "eval_reference_count_range": [MIN_REFERENCES, MAX_REFERENCES],
        },
        "hyperparameters": chosen,
        "hyperparameter_search": {
            "selected_on": "validation split inside the training window",
            "raw_validation": raw_validation,
            "grid": grid,
        },
        "results": results,
        "paired_bootstrap_vs_raw": comparison,
        "reading": (
            "질의가 변동의 단위이므로 질의를 쌍으로 재표본해 차이의 95% 구간을 냈습니다. "
            "구간이 0을 포함하면 그 변화는 측정되지 않은 것입니다."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with track("embedding-tuning") as run:
        run.log_params({
            "snapshot_version": snap.version,
            "cut": month_label(cut),
            "ridge": chosen["ridge"],
            "ridge_multiplier": chosen["ridge_multiplier"],
            "negative_weight": chosen["negative_weight"],
            "train_pairs_used": arrays["pairs_used"],
        })
        run.log_tags({"snapshot_papers_sha256": report["snapshot_papers_sha256"][:16]})
        for name, row in results.items():
            for metric, value in row.items():
                if isinstance(value, (int, float)):
                    run.log_metrics({f"{name}.{metric}": value})
        for name, row in comparison.items():
            run.log_metrics({
                f"{name}.diff_vs_raw": row["mean_difference"],
                f"{name}.ci_excludes_zero": row["excludes_zero"],
            })
        run.log_artifact(args.output)

    print(json.dumps({
        "split": report["split"],
        "chosen": chosen,
        "raw_validation_recall": raw_validation[recall_key],
        "grid": [
            {key: row[key] for key in
             ("ridge_multiplier", "negative_weight", "drift_from_identity", recall_key)}
            for row in grid
        ],
        "results": results,
        "paired_bootstrap_vs_raw": comparison,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
