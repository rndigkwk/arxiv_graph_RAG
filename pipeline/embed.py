"""수집한 새 논문의 임베딩을 만듭니다(ADR-064).

    # 1단계: 돈을 쓰기 전에 벡터 공간이 같은지 확인합니다
    uv run python -m pipeline.embed --verify --output reports/embedding_space.json

파이프라인에 **수동 단계**가 하나 끼어 있었습니다. `pipeline.ingest`의 가드가 임베딩 없는
논문의 적재를 막으면서 *"임베딩을 먼저 만드세요(notebooks/arxiv_ai_kg_to_aura.ipynb)"* 라고
말했는데, 그 노트북 밖에는 임베딩을 **채우는 코드가 없었습니다.** 이 모듈이 그 칸입니다.

## 입력 문자열을 바이트 단위로 그대로 씁니다

노트북이 적어 둔 경고입니다 — *"입력 문자열은 `{title}\\n\\n{abstract}`이며, 이 형식을 바꾸면
기존 벡터와 어긋나 전부 다시 만들어야 합니다."* 그래서 형식·모델·차원을 이 한 곳에 두고,
`--verify`가 **실제로 같은 공간인지 재서** 확인합니다(음성 대조군 포함).

## 그래프에 쓰지 않습니다

임베딩을 수집 레코드에 붙이기만 합니다. 쓰는 일은 이미 ADR-047의 가드와 용량 검사가 걸린
`pipeline.ingest --load`가 합니다. 단계를 섞지 않습니다.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline.quality import EMBEDDING_DIMENSIONS

# **노트북 20번 칸과 같아야 합니다.** 하나라도 바뀌면 기존 37,854편과 다른 공간이 됩니다.
EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_INPUT_LABEL = "title+abstract"
EMBEDDING_MAX_CHARS = 20000  # 입력 한도(8,191토큰)에 닿지 않는 안전한 최대 길이
EMBED_BATCH_SIZE = 128

# ADR-064의 바.
MIN_COSINE = 0.9999
VERIFY_SAMPLE = 20
MAX_COST_USD = 1.00
PRICE_PER_MILLION_USD = 0.13

OUTPUT = Path("reports/embedding_space.json")


def embedding_input(paper: dict[str, Any]) -> str:
    """임베딩에 넣는 문자열입니다. **표찰만 바꾸지 말고 이 함수를 바꾸세요.**

    `p.embedding_input`에는 `'title+abstract'`라는 **표찰**이 들어 있고 텍스트가 아닙니다.
    그래서 형식이 어긋나도 그래프를 봐서는 알 수 없습니다 - `--verify`가 재야 합니다.
    """
    title = paper.get("title") or ""
    abstract = paper.get("abstract") or ""
    return f"{title}\n\n{abstract}"[:EMBEDDING_MAX_CHARS]


def spaced_input(paper: dict[str, Any]) -> str:
    """**음성 대조군입니다.** 일부러 틀린 형식 - 줄바꿈 둘을 공백 하나로.

    이것이 없으면 코사인 검사가 형식을 가려내는지 알 수 없습니다. 2026-10-23에 Langfuse
    되읽기에서 고장 난 검출기가 그럴듯한 답을 내놨고, 음성 대조군이 있어서 잡혔습니다.
    """
    title = paper.get("title") or ""
    abstract = paper.get("abstract") or ""
    return f"{title} {abstract}"[:EMBEDDING_MAX_CHARS]


def count_tokens(texts: list[str], model: str = EMBEDDING_MODEL) -> int:
    import tiktoken

    encoding = tiktoken.encoding_for_model(model)
    return sum(len(encoding.encode(text)) for text in texts)


def estimate_cost(tokens: int) -> float:
    return tokens / 1_000_000 * PRICE_PER_MILLION_USD


def client() -> Any:
    """키를 읽어 OpenAI 클라이언트를 만듭니다. **값을 찍지 않습니다.**

    `chatbot`을 가져오지 않습니다 - 그 모듈은 임포트만으로 LLM과 임베딩 클라이언트를 만들고
    벡터 인덱스 상태를 확인하므로, 수집 파이프라인에는 하나도 필요하지 않습니다.
    """
    from openai import OpenAI

    from pipeline.graph import load_env

    load_env()
    key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not key:
        # CI에는 .env가 없고 시크릿이 환경변수로 옵니다. 메시지가 한쪽만 가리키면 헤맵니다.
        raise ValueError("OPENAI_API_KEY가 없습니다 (.env 또는 환경변수)")
    return OpenAI(api_key=key, timeout=60, max_retries=2)


def embed_texts(
    api: Any, texts: list[str], *, batch_size: int = EMBED_BATCH_SIZE
) -> list[list[float]]:
    """배치로 임베딩합니다. 차원과 유한성을 **벡터마다** 확인합니다."""
    vectors: list[list[float]] = []
    for offset in range(0, len(texts), batch_size):
        batch = texts[offset : offset + batch_size]
        response = api.embeddings.create(
            input=batch, model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        for item in ordered:
            vector = list(item.embedding)
            if len(vector) != EMBEDDING_DIMENSIONS:
                raise ValueError(
                    f"차원이 {len(vector)}입니다 - {EMBEDDING_DIMENSIONS}이어야 합니다"
                )
            if not all(math.isfinite(value) for value in vector):
                raise ValueError("임베딩에 유한하지 않은 값이 있습니다")
            vectors.append(vector)
    return vectors


def cosine(left: list[float], right: list[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / norm if norm else 0.0


def has_usable_embedding(paper: dict[str, Any]) -> bool:
    embedding = paper.get("embedding")
    return (
        isinstance(embedding, (list, tuple))
        and len(embedding) == EMBEDDING_DIMENSIONS
    )


def attach_embeddings(
    records: list[dict[str, Any]],
    api: Any,
    *,
    max_cost_usd: float = MAX_COST_USD,
    batch_size: int = EMBED_BATCH_SIZE,
    already_embedded: set[str] | None = None,
) -> dict[str, Any]:
    """임베딩이 없는 레코드에만 임베딩을 붙입니다. **그래프에 쓰지 않습니다.**

    두 가지를 건너뜁니다.

    - 레코드가 이미 쓸 수 있는 임베딩을 가진 경우 - 끊긴 실행을 다시 돌려도 두 번 쓰지 않게
    - `already_embedded`에 든 `arxiv_id` - **그래프가 이미 벡터를 가진 논문**입니다.
      2026-10-24의 첫 실행은 이것을 몰라서 934편 중 123편(13%)에 다시 돈을 썼습니다.
    """
    seen = already_embedded or set()
    todo = [
        paper
        for paper in records
        if not has_usable_embedding(paper) and str(paper["arxiv_id"]) not in seen
    ]
    texts = [embedding_input(paper) for paper in todo]
    tokens = count_tokens(texts) if texts else 0
    cost = estimate_cost(tokens)
    summary = {
        "records": len(records),
        "skipped_already_embedded": len(records) - len(todo),
        "skipped_in_graph": sum(
            1 for paper in records if str(paper["arxiv_id"]) in seen
        ),
        "to_embed": len(todo),
        "tokens": tokens,
        "estimated_cost_usd": round(cost, 4),
        "max_cost_usd": max_cost_usd,
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "input": EMBEDDING_INPUT_LABEL,
    }
    if not todo:
        return {**summary, "embedded": 0, "stopped_at": "nothing_to_embed"}
    # **천장을 넘으면 호출하지 않습니다.** 추정을 찍고 멈춥니다.
    if cost > max_cost_usd:
        return {**summary, "embedded": 0, "stopped_at": "cost_ceiling"}
    vectors = embed_texts(api, texts, batch_size=batch_size)
    for paper, vector in zip(todo, vectors):
        paper["embedding"] = vector
        paper["embedding_model"] = EMBEDDING_MODEL
        paper["embedding_dimensions"] = EMBEDDING_DIMENSIONS
        paper["embedding_input"] = EMBEDDING_INPUT_LABEL
    return {**summary, "embedded": len(vectors), "stopped_at": None}


SAMPLE_CYPHER = """
MATCH (p:ArxivPaper)
WHERE p.embedding IS NOT NULL AND size(p.embedding) = $dimensions
  AND p.embedding_model = $model AND p.embedding_input = $input
RETURN p.arxiv_id AS arxiv_id, p.title AS title, p.abstract AS abstract,
       p.embedding AS embedding
ORDER BY p.arxiv_id
LIMIT $sample
"""


def verify_space(read, api: Any, *, sample: int = VERIFY_SAMPLE) -> dict[str, Any]:
    """저장된 벡터를 **다시 만들어 비교합니다.** 967편에 돈을 쓰기 전의 관문입니다.

    올바른 형식과 **일부러 틀린 형식**을 같이 재서, 검사가 형식을 가려내는지도 봅니다.
    """
    stored = read(
        SAMPLE_CYPHER,
        {
            "dimensions": EMBEDDING_DIMENSIONS,
            "model": EMBEDDING_MODEL,
            "input": EMBEDDING_INPUT_LABEL,
            "sample": sample,
        },
    )
    if not stored:
        raise ValueError("비교할 벡터를 가진 논문이 없습니다")
    correct = embed_texts(api, [embedding_input(row) for row in stored])
    control = embed_texts(api, [spaced_input(row) for row in stored])
    rows = [
        {
            "arxiv_id": row["arxiv_id"],
            "cosine": round(cosine(list(row["embedding"]), fresh), 6),
            "cosine_control": round(cosine(list(row["embedding"]), wrong), 6),
        }
        for row, fresh, wrong in zip(stored, correct, control)
    ]
    worst = min(entry["cosine"] for entry in rows)
    best_control = max(entry["cosine_control"] for entry in rows)
    return {
        "sample": len(rows),
        "min_cosine": worst,
        "max_cosine_control": best_control,
        # **대조군이 더 낮아야 검사가 형식을 가려냅니다.** 겹치면 이 검사는 무의미합니다.
        "control_is_lower": bool(worst > best_control),
        "margin": round(worst - best_control, 6),
        "passes": bool(worst >= MIN_COSINE and worst > best_control),
        "min_cosine_bar": MIN_COSINE,
        "cases": rows,
        "tokens": count_tokens(
            [embedding_input(row) for row in stored]
            + [spaced_input(row) for row in stored]
        ),
    }


MISSING_CYPHER = """
MATCH (p:ArxivPaper)
WHERE p.embedding IS NULL OR size(p.embedding) <> $dimensions
RETURN p.arxiv_id AS arxiv_id, p.title AS title, p.abstract AS abstract
ORDER BY p.arxiv_id
"""

# 노트북 20번 칸과 같은 문장입니다. **표찰까지 같이 씁니다.**
UPSERT_CYPHER = """
UNWIND $rows AS row
MATCH (p:ArxivPaper {arxiv_id: row.arxiv_id})
SET p.embedding            = row.embedding,
    p.embedding_model      = $model,
    p.embedding_dimensions = $dimensions,
    p.embedding_input      = $input
"""


def backfill(
    read,
    write,
    api: Any,
    *,
    max_cost_usd: float = MAX_COST_USD,
    batch_size: int = EMBED_BATCH_SIZE,
) -> dict[str, Any]:
    """**이미 그래프에 있는데 벡터가 없는 논문**을 채웁니다.

    제목과 초록이 이미 노드에 있으므로 다시 수집하지 않습니다. 2026-10-24에 `--embed`가
    만든 벡터를 적재기가 버려서 811편이 이 상태로 남았고, 그것을 고치려고 만들었습니다.

    채운 뒤 **되읽어서** 커버리지를 확인합니다 - 쓴 것을 셌다고 들어간 것이 아닙니다.
    """
    missing = read(MISSING_CYPHER, {"dimensions": EMBEDDING_DIMENSIONS})
    texts = [embedding_input(row) for row in missing]
    tokens = count_tokens(texts) if texts else 0
    cost = estimate_cost(tokens)
    summary = {
        "missing": len(missing),
        "tokens": tokens,
        "estimated_cost_usd": round(cost, 4),
        "max_cost_usd": max_cost_usd,
    }
    if not missing:
        return {**summary, "upserted": 0, "stopped_at": "nothing_missing"}
    if cost > max_cost_usd:
        return {**summary, "upserted": 0, "stopped_at": "cost_ceiling"}
    upserted = 0
    for offset in range(0, len(missing), batch_size):
        batch = missing[offset : offset + batch_size]
        vectors = embed_texts(
            api, [embedding_input(row) for row in batch], batch_size=batch_size
        )
        write(
            UPSERT_CYPHER,
            {
                "rows": [
                    {"arxiv_id": row["arxiv_id"], "embedding": vector}
                    for row, vector in zip(batch, vectors)
                ],
                "model": EMBEDDING_MODEL,
                "dimensions": EMBEDDING_DIMENSIONS,
                "input": EMBEDDING_INPUT_LABEL,
            },
        )
        upserted += len(vectors)
    # **되읽습니다.** 쓴 횟수가 아니라 그래프의 상태가 결론입니다.
    left = read(MISSING_CYPHER, {"dimensions": EMBEDDING_DIMENSIONS})
    return {
        **summary,
        "upserted": upserted,
        "still_missing": len(left),
        "complete": not left,
        "stopped_at": None if not left else "still_missing",
    }


def main() -> int:
    from pipeline.console import use_utf8_stdout
    from pipeline.graph import connect

    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="수집한 새 논문의 임베딩을 만듭니다(ADR-064)."
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="저장된 벡터를 다시 만들어 같은 공간인지 확인합니다. 유료 호출을 소량 합니다.",
    )
    parser.add_argument(
        "--backfill", action="store_true",
        help="이미 그래프에 있는데 벡터가 없는 논문을 채웁니다(유료, 그래프에 씁니다).",
    )
    parser.add_argument("--sample", type=int, default=VERIFY_SAMPLE)
    parser.add_argument("--max-cost", type=float, default=MAX_COST_USD)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    if not args.verify and not args.backfill:
        parser.error(
            "--verify로 벡터 공간을 확인하거나 --backfill로 빈 벡터를 채우세요. 수집한 "
            "논문의 임베딩은 `pipeline.ingest --embed --load`가 만듭니다."
        )
    api = client()
    with connect() as graph:
        if args.backfill:
            filled = backfill(
                graph.read, graph.write, api, max_cost_usd=args.max_cost
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(
                    {"generated_at": datetime.now(UTC).isoformat(), "backfill": filled},
                    ensure_ascii=False, indent=2,
                ) + "\n",
                encoding="utf-8",
            )
            print(
                f"벡터가 없던 {filled['missing']}편 중 {filled['upserted']}편을 "
                f"채웠습니다(${filled['estimated_cost_usd']}). 되읽은 뒤 남은 수 "
                f"{filled.get('still_missing')}"
            )
            return 0 if filled.get("complete") else 1
        result = verify_space(graph.read, api, sample=args.sample)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": EMBEDDING_MODEL,
        "dimensions": EMBEDDING_DIMENSIONS,
        "input": EMBEDDING_INPUT_LABEL,
        "estimated_cost_usd": round(estimate_cost(result["tokens"]), 6),
        **result,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{result['sample']}편 비교 - 최소 코사인 {result['min_cosine']:.6f} "
        f"(바 {MIN_COSINE})"
    )
    print(
        f"음성 대조군 최대 {result['max_cosine_control']:.6f} -> "
        f"{'대조군이 더 낮습니다' if result['control_is_lower'] else '겹칩니다(검사 무의미)'}"
        f", 차이 {result['margin']:.6f}"
    )
    print(f"판정: {'통과' if result['passes'] else '통과하지 못했습니다'}")
    if not result["passes"]:
        print(
            "967편을 임베딩하지 마세요. 입력 형식이나 모델 판이 어긋났습니다 - "
            "밀어붙이면 벡터 검색이 말없이 두 공간으로 쪼개집니다."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
