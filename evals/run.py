from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from statistics import fmean
from time import perf_counter
from time import sleep as _sleep
from typing import Any

from evals import budget, ledger
from evals.metrics import score_answer, score_related, score_retrieval, summarize
from evals.telemetry import (
    build_run_metadata,
    count_embedding_tokens,
    estimate_api_cost,
    pricing_from_env,
    summarize_latency,
)

MAX_CONTEXT_ROWS = 50

# openai SDK가 HTTP 요청을 다시 보낼 때 남기는 INFO 로그입니다(`_sleep_for_retry`). 재시도는
# SDK 안에서 끝나 콜백에도 응답에도 남지 않으므로, 몇 번 일어났는지 알 길이 이것뿐입니다.
# ADR-073이 재시도를 2번에서 1번으로 줄이면서 "살아난 호출이 몇 번이었는지 모른다"고 적었습니다.
SDK_LOGGER = "openai._base_client"
SDK_RETRY_MESSAGE = "Retrying request to %s in %f seconds"


class _SdkRetryCounter(logging.Handler):
    """재시도 로그를 끝점(`responses`·`embeddings`…)별로 셉니다. 메시지 본문은 남기지 않습니다."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.counts: Counter[str] = Counter()

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == SDK_RETRY_MESSAGE and record.args:
            self.counts[str(record.args[0]).rstrip("/").rsplit("/", 1)[-1]] += 1


@contextmanager
def sdk_retries() -> Iterator[Counter[str]]:
    """이 블록 안에서 일어난 SDK 재시도를 셉니다. 로거 수준은 끝나면 되돌립니다."""
    logger = logging.getLogger(SDK_LOGGER)
    counter, previous = _SdkRetryCounter(), logger.level
    logger.addHandler(counter)
    if not logger.isEnabledFor(logging.INFO):
        logger.setLevel(logging.INFO)
    try:
        yield counter.counts
    finally:
        logger.removeHandler(counter)
        logger.setLevel(previous)


def tool_seconds(response: dict[str, Any]) -> list[dict[str, Any]]:
    """도구 호출마다 이름과 초만 남깁니다(ADR-066). 인자는 남기지 않습니다.

    합계(`generate_trace.tools_seconds`)만으로는 번역·Cypher·벡터 중 무엇이 긴지 말할 수
    없습니다. ADR-073이 "번역 지연은 잰 적이 없다"고 적은 값이 여기서 나옵니다.
    """
    return [{"name": call.get("name", ""), "seconds": call["seconds"]}
            for call in response.get("tool_calls", []) or [] if "seconds" in call]


def warm_citation_graph(chatbot: Any, mode: str) -> float | None:
    """앱의 부팅 예열(ADR-067)을 하네스에서도 합니다. 걸린 초를 돌려줍니다.

    2026-10-26 실행에서 첫 `rank_related_papers` 호출이 26.1초였고 나머지는 p50 0.68초였습니다.
    앱은 그 값을 부팅 때 배경에서 치르므로 사용자는 겪지 않습니다. 하네스만 그 값을 한 문항에
    몰아 주면 평가가 재는 지연이 앱보다 큽니다. 검색만 재는 모드는 그 도구를 부르지 않습니다.
    """
    if mode == "retrieval":
        return None
    started = perf_counter()
    chatbot.citation_graph()
    return round(perf_counter() - started, 3)


# arxiv_id 모양. 골드 Cypher의 매개변수에서 **기준 논문**만 골라내기 위한 것입니다.
ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}$")

# 조회가 무엇으로 정렬했는지. `reciprocal_rank`·`hit_at_k`의 "1위"가 무슨 뜻인지는 이것이
# 정합니다 - 알파벳순이면 1위는 품질이 아니라 제목의 첫 글자입니다(ADR-033).
ORDER_BY = re.compile(r"\bORDER\s+BY\s+(.+?)(?=\s+\b(?:LIMIT|RETURN|SKIP|UNION)\b|$)", re.I)


def ordered_by(response: dict[str, Any]) -> list[str]:
    """What each successful graph query sorted by, in call order.

    순위 지표를 읽으려면 **순위가 무엇으로 정해졌는지** 알아야 합니다. 2026-10-19 측정에서
    30건 중 **24건이 식별자 문자열순**(`title`·`arxiv_id`)이고 점수순(`citation_count DESC`)은
    6건뿐이었습니다. 그 사실이 보고서에 남아 있지 않으면 `reciprocal_rank` 1.0000을 "순위가
    좋다"로 잘못 읽습니다. 세는 쪽은 `evals/retrieval_metric_study.py`입니다.

    실패는 조용합니다 - 못 찾으면 빈 목록입니다. 이것은 **설명**이고 채점 입력이 아니라서,
    정규식이 Cypher 변형을 놓쳐도 지표가 흔들리면 안 됩니다.
    """
    clauses: list[str] = []
    for call in response.get("tool_calls", []):
        if call.get("name") != "search_graph" or call.get("status") != "success":
            continue
        output = call.get("output")
        if not isinstance(output, dict) or output.get("error"):
            continue
        cypher = " ".join((output.get("cypher") or "").split())
        clauses.extend(" ".join(match.group(1).split()) for match in ORDER_BY.finditer(cypher))
    return clauses


def anchor_ids(case: dict[str, Any]) -> set[str]:
    """질문이 지칭하는 논문. 골드 Cypher의 매개변수가 그것을 이미 적고 있습니다.

    `citing`·`coauthor`·`reference`는 "X를 인용한 논문", "X와 공저자가 겹치는 논문"처럼 **한
    논문을 기준으로** 묻고, 골드를 만든 Cypher가 `cypher_params={"arxiv_id": "..."}`로 그
    논문을 적습니다(6/6). `author`·`topcited`는 저자 이름·분류 코드라 기준 논문이 없습니다(0/6).

    **30건 중 기준 논문이 정답에도 들어 있는 사례는 없습니다.** 그래서 이것을 조회 결과에서
    빼도 정답을 지우지 않습니다. 그 성질이 깨지면 테스트가 막습니다.
    """
    params = case.get("cypher_params") or {}
    return {
        value for value in params.values()
        if isinstance(value, str) and ARXIV_ID.match(value)
    }


def is_lookup(rows: list[dict[str, Any]], anchors: set[str]) -> bool:
    """Whether this query only resolved the question's own paper.

    조회문 문구로 추측하지 않습니다. **모든 행의 `evidence_ids`가 기준 논문뿐인** 조회가
    제목 해석 조회입니다 - 그것은 답변의 근거가 아니라 다음 조회의 입력입니다.
    """
    if not anchors or not rows:
        return False
    seen: set[str] = set()
    for row in rows:
        ids = row.get("evidence_ids")
        if not isinstance(ids, list):
            return False
        seen.update(str(value) for value in ids)
    return bool(seen) and seen <= anchors


def generator_contexts(
    response: dict[str, Any], anchors: set[str] | None = None
) -> list[str]:
    """What the answer generator actually saw, as one string per retrieval call.

    RAGAS의 Faithfulness·Context Precision은 **생성기가 본 문맥**을 입력으로 받습니다. 그래서
    논문 ID만으로 초록을 되찾아 문맥이라고 넘기면 안 됩니다 — 그래프 조건 질문에서 모델이 본
    것은 초록이 아니라 Cypher가 돌려준 행입니다.

    **조회문을 행과 같은 문맥에 담습니다.** 행만 넘겼을 때 `citing-01`의 faithfulness가 0.0으로
    나왔습니다. 답변은 정확했고(근거 recall 1.000) 제목·분류도 행과 일치했는데, 답변의 핵심
    주장인 "이 논문들이 X를 **인용한다**"가 행에는 없었습니다. 관계는 행이 아니라 **조회문**에
    적혀 있기 때문입니다. 그것을 빼고 재면 모델이 아니라 우리 입력 실수를 재게 됩니다.

    한 조회를 한 문맥으로 묶는 이유도 같습니다. "이 질의가 이 행들을 돌려줬다"가 근거의 한
    단위이고, 그렇게 묶어야 Context Precision이 "쓸모 있는 근거가 위에 있는가"를 잽니다.

    **제목 해석 조회도 빼지 않습니다.** 2026-10-18에 그것을 빼 봤습니다 — RAGAS가 그 문맥을
    쓸모없다고 채점해서 `citing`의 Context Precision이 1.0000에서 0.5046으로 내려갔기
    때문입니다. 빼니 Context Precision은 1.0000으로 회복됐지만 **faithfulness가 1.0000에서
    0.2296으로 무너졌습니다.** 답변이 주제 논문을 제목으로 언급하고("X와 저자를 공유하는
    네 편"), 그 주장의 근거가 바로 그 조회입니다. 즉 그 조회는 순수한 중간 단계가 아니라
    답변 일부의 근거입니다.

    그래서 `anchors`는 받지만 문맥을 걸러내는 데 쓰지 않습니다. 기준 논문을 빼는 것은
    **순위 지표**에서만 맞습니다(`evaluate_case`). 두 지표가 충돌할 때 답변 정확도에 묶인
    faithfulness를 따릅니다 - Context Precision은 답변에 의존하는 지표이고 검색 지표가
    아니라는 것이 이미 측정돼 있습니다(`EXPERIMENTS.md` 11-10절).
    """
    contexts = []
    calls = [
        call for call in response.get("tool_calls", [])
        if call.get("status") == "success" and isinstance(call.get("output"), dict)
    ]
    for call in calls:
        output = call["output"]
        if call["name"] == "search_graph" and not output.get("error"):
            rows = output.get("rows", [])
            rendered = [
                " | ".join(
                    f"{key}={value}" for key, value in row.items() if value not in (None, [], "")
                )
                for row in rows[:MAX_CONTEXT_ROWS]
            ]
            parts = [f"Cypher: {' '.join((output.get('cypher') or '').split())}"]
            parts.extend(line for line in rendered if line)
            if len(rows) > MAX_CONTEXT_ROWS:
                parts.append(f"... ({len(rows) - MAX_CONTEXT_ROWS} more rows)")
            contexts.append("\n".join(parts))
        elif call["name"] == "resolve_paper":
            # 답변이 주제 논문을 제목으로 언급하는 주장의 **근거**입니다. 빼면 faithfulness가
            # 무너집니다 - 오늘 그렇게 재 봤고 `citing`이 1.0000에서 0.2296으로 갔습니다.
            found = output.get("arxiv_id")
            if found:
                titles = {c.get("arxiv_id"): c.get("title") for c in output.get("candidates", [])}
                contexts.append(
                    f"Title lookup: {output.get('query', '')} -> {found} "
                    f"({titles.get(found) or ''})"
                )
            for paper in output.get("papers", []):
                title = (paper.get("title") or "").strip()
                abstract = (paper.get("abstract") or "").strip()
                if title or abstract:
                    contexts.append(f"{title}\n{abstract}".strip())
    if contexts:
        # 같은 근거가 여러 호출에서 나오면 한 번만 남깁니다. 중복은 정밀도를 부풀립니다.
        return list(dict.fromkeys(contexts))

    # 도구 호출 기록이 없는 경로(옛 보고서·단위 테스트)에서는 모아 둔 행과 논문을 씁니다.
    for row in response.get("rows", []):
        rendered = " | ".join(
            f"{key}={value}" for key, value in row.items() if value not in (None, [], "")
        )
        if rendered:
            contexts.append(rendered)
    for paper in response.get("papers", []):
        title = (paper.get("title") or "").strip()
        abstract = (paper.get("abstract") or "").strip()
        if title or abstract:
            contexts.append(f"{title}\n{abstract}".strip())
    return list(dict.fromkeys(contexts))


def evaluate_case(
    case: dict[str, Any], response: dict[str, Any], *, mode: str, top_k: int
) -> dict[str, Any]:
    # 질문이 지칭한 논문은 **검색 결과가 아니라 질문의 일부**입니다. 제목을 arxiv_id로 바꾸는
    # 조회가 그것을 돌려주고, 행 순서대로 이어 붙이면 1위를 차지해 정답을 2위로 밉니다 -
    # reciprocal_rank가 0.7000에 묶이던 원인입니다(ADR-024).
    anchors = anchor_ids(case)
    retrieved = [paper["arxiv_id"] for paper in response.get("papers", []) if paper.get("arxiv_id")]
    for row in response.get("rows", []):
        retrieved.extend(row.get("evidence_ids", []))
    retrieved = [arxiv_id for arxiv_id in retrieved if arxiv_id not in anchors]
    related_ids = list(
        dict.fromkeys(
            paper["arxiv_id"] for paper in response.get("related", []) if paper.get("arxiv_id")
        )
    )
    retrieved = list(dict.fromkeys(retrieved))
    row = {"id": case["id"], "category": case["category"], "retrieved_ids": retrieved}
    # 순위 지표를 되짚을 수 있게 **정렬 기준을 같이 적습니다**. 숫자가 아니므로 게이트의
    # 지표 집계에는 들어가지 않습니다(`evals/gate.py`의 `_numeric`).
    if sorts := ordered_by(response):
        row["ordered_by"] = sorts
    if anchors:
        # 무엇을 뺐는지 보고서가 스스로 말해야 합니다. 조용히 빼면 지표를 되짚을 수 없습니다.
        row["anchor_ids"] = sorted(anchors)
    row.update(score_retrieval(case["expected_ids"], retrieved, k=top_k))
    if mode == "agent":
        evidence = response.get("evidence_ids", [])
        row.update(
            score_answer(
                response.get("answer", ""),
                expected_answer_contains=case.get("expected_answer_contains", []),
                expected_ids=case["expected_ids"],
                evidence_ids=evidence,
            )
        )
        row.update(score_related(related_ids, case.get("expected_related_ids")))
        row["related_ids"] = related_ids
        row["evidence_ids"] = evidence
        row["answer"] = response.get("answer", "")
        row["contexts"] = generator_contexts(response, anchors)  # anchors는 쓰이지 않습니다
        # 어느 경로로 보냈는지와 그 사유입니다. 지시를 지켰는지는 이 값과 임베딩 토큰을
        # 맞춰 봐야 알 수 있습니다(ADR-024).
        route = response.get("route")
        if route:
            row["route"] = route
        # 노드별 지연·방문 횟수입니다. "답변이 느리다"를 어느 단계인지로 바꿔 말할 수 있습니다.
        trace = response.get("graph_trace")
        if trace:
            # `rejections`를 빼면 어느 게이트가 막았는지가 보고서에서 사라집니다. 없으면
            # 조용히 넘기지 않고 터뜨립니다 - 빠진 키는 0이 아니라 결함입니다.
            row["graph_trace"] = {
                key: trace[key]
                for key in ("visits", "seconds", "rejections", "attempts", "total_seconds")
            }
        # `generate` 노드 안쪽입니다 - 도구·모델·나머지(ADR-066). 보고서에 남기지 않으면
        # 실행이 끝나는 순간 사라지고, 다시 보려면 **돈을 또 써야** 합니다.
        inside = response.get("generate_trace")
        if inside:
            row["generate_trace"] = inside
    return row


def load_cases(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value["cases"] if isinstance(value, dict) else value


def _token_totals(messages: Any) -> tuple[int | None, int | None]:
    """Sum provider-reported chat tokens without guessing when metadata is absent."""
    input_tokens = output_tokens = 0
    saw_input = saw_output = False
    if not isinstance(messages, (list, tuple)):
        messages = [messages]
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not usage and isinstance(message, dict):
            usage = message.get("usage_metadata")
        if not usage:
            metadata = getattr(message, "response_metadata", {}) or {}
            usage = metadata.get("token_usage", {}) if isinstance(metadata, dict) else {}
        if isinstance(usage, dict):
            input_value = usage.get("input_tokens", usage.get("prompt_tokens"))
            output_value = usage.get("output_tokens", usage.get("completion_tokens"))
            if input_value is not None:
                input_tokens += int(input_value)
                saw_input = True
            if output_value is not None:
                output_tokens += int(output_value)
                saw_output = True
    return input_tokens if saw_input else None, output_tokens if saw_output else None


def cached_input_tokens(messages: Any) -> int | None:
    """How much of the input was a cache read, or None when the provider did not say.

    **토큰 수는 비용이 아닙니다.** 2026-10-18에 한 질문을 재 보니 입력 11,239 중 9,702
    (86.3%)가 캐시 읽기였습니다 - 시스템 프롬프트와 도구 스키마가 round-trip마다 다시
    실리지만 접두사가 같아서 할인됩니다. 이것을 따로 세지 않으면 "입력 토큰 29% 감소"가
    "비용 29% 감소"로 읽힙니다(ADR-024에서 실제로 그렇게 적었다가 고쳤습니다).
    """
    cached = 0
    seen = False
    if not isinstance(messages, (list, tuple)):
        messages = [messages]
    for message in messages:
        usage = getattr(message, "usage_metadata", None)
        if not usage and isinstance(message, dict):
            usage = message.get("usage_metadata")
        if not isinstance(usage, dict):
            continue
        details = usage.get("input_token_details") or {}
        value = details.get("cache_read") if isinstance(details, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            cached += value
            seen = True
    return cached if seen else None


def _add_usage(total: int | None, extra: Any) -> int | None:
    """Add a separately recorded token count, keeping None when nothing was reported."""
    if not isinstance(extra, int) or isinstance(extra, bool):
        return total
    return extra if total is None else total + extra


def _with_validation_usage(
    input_tokens: int | None, output_tokens: int | None, response: dict[str, Any]
) -> tuple[int | None, int | None]:
    """Fold in the condition-validation call, which runs outside the agent message stream.

    `chatbot.ask` records it under `validation_usage`; without this the reported token
    totals understate what the answer actually cost.
    """
    usage = response.get("validation_usage")
    if not isinstance(usage, dict):
        return input_tokens, output_tokens
    return (
        _add_usage(input_tokens, usage.get("input_tokens")),
        _add_usage(output_tokens, usage.get("output_tokens")),
    )


def _answer_from_vector_context(chatbot: Any, case: dict[str, Any], papers: list[dict[str, Any]]):
    context = "\n\n".join(
        f"arXiv ID: {paper.get('arxiv_id', '')}\nTitle: {paper.get('title', '')}\n"
        f"Abstract: {paper.get('abstract', '')}"
        for paper in papers
    )
    prompt = (
        "Answer the question using only the retrieved papers. Return a concise English answer. "
        "Include only arXiv IDs from the context in evidence_ids; if none support the answer, "
        "say it cannot be confirmed and use an empty evidence_ids list.\n\n"
        f"Question: {case['question']}\n\nRetrieved papers:\n{context}"
    )
    return chatbot.llm.with_structured_output(chatbot.GroundedAnswer, include_raw=True).invoke(
        [("user", prompt)]
    )


def compare_case(
    chatbot: Any,
    case: dict[str, Any],
    *,
    top_k: int,
    rates: dict[str, float | str] | None = None,
) -> dict[str, Any]:
    """Run a vector-context answer baseline and the full graph agent for one case."""
    start = perf_counter()
    retrieval = chatbot.search_papers.invoke({"query": case["search_query"], "top_k": top_k})
    vector_response = _answer_from_vector_context(chatbot, case, retrieval.get("papers", []))
    vector_seconds = perf_counter() - start
    vector_answer = vector_response.get("parsed")
    if vector_answer is None:
        raise ValueError("Vector-only baseline did not return a structured answer")
    vector_row = evaluate_case(
        case,
        {
            "papers": retrieval.get("papers", []),
            "answer": vector_answer.answer,
            "evidence_ids": vector_answer.evidence_ids,
        },
        mode="agent",
        top_k=top_k,
    )
    vector_raw = vector_response.get("raw")
    vector_input, vector_output = _token_totals(vector_raw)
    embedding_model = getattr(chatbot, "EMBEDDING_MODEL", "text-embedding-3-large")
    vector_embedding_tokens = count_embedding_tokens([case["search_query"]], embedding_model)
    vector_cost = estimate_api_cost(vector_input, vector_output, vector_embedding_tokens, rates)
    vector_row.update(
        {
            "elapsed_seconds": vector_seconds,
            "chat_input_tokens": vector_input,
            "chat_output_tokens": vector_output,
            "embedding_tokens_estimate": vector_embedding_tokens,
            "estimated_api_cost_usd": vector_cost,
        }
    )

    start = perf_counter()
    try:
        with sdk_retries() as retries:
            graph_response, graph_messages = chatbot.ask(case["question"], [])
    except ValueError as exc:
        # 검증을 통과하지 못한 답변은 이 앱에서 "답하지 못함"입니다. 한 건의 실패가 나머지
        # 케이스의 측정을 막으면 안 되므로, 0점으로 기록하고 사유를 남기고 계속 갑니다.
        graph_response, graph_messages = {}, []
        graph_row = evaluate_case(case, graph_response, mode="agent", top_k=top_k)
        graph_row.update({
            "failed": True,
            "error": f"{type(exc).__name__}: {exc}",
            "elapsed_seconds": perf_counter() - start,
            # 사유 코드와 모델이 쓴 Cypher를 보고서에 남깁니다. 2026-10-16에 멈춘 5건의 원인을
            # 알려고 에이전트를 다시 돌려야 했습니다 - 오류 문자열만으로는 되짚을 수 없고,
            # 사유 코드가 CI 로그에만 있어서 사례와 이어 붙일 수도 없었습니다.
            **(exc.diagnostics() if hasattr(exc, "diagnostics") else {}),
        })
        return {
            "id": case["id"],
            "category": case["category"],
            "vector_only": vector_row,
            "graph_rag": graph_row,
        }
    graph_seconds = perf_counter() - start
    graph_row = evaluate_case(case, graph_response, mode="agent", top_k=top_k)
    graph_row["failed"] = False
    graph_input, graph_output = _with_validation_usage(
        *_token_totals(graph_messages), graph_response
    )
    graph_queries = [
        call.get("args", {}).get("query", "")
        for call in graph_response.get("tool_calls", [])
        if call.get("name") == "search_papers"
        and isinstance(call.get("args"), dict)
        and isinstance(call.get("args", {}).get("query"), str)
    ]
    graph_embedding_tokens = count_embedding_tokens(graph_queries, embedding_model)
    graph_cost = estimate_api_cost(graph_input, graph_output, graph_embedding_tokens, rates)
    graph_row.update(
        {
            "elapsed_seconds": graph_seconds,
            "chat_input_tokens": graph_input,
            "chat_output_tokens": graph_output,
            "embedding_tokens_estimate": graph_embedding_tokens,
            "estimated_api_cost_usd": graph_cost,
            "sdk_retries": dict(retries),
            "tool_seconds": tool_seconds(graph_response),
        }
    )
    return {
        "id": case["id"],
        "category": case["category"],
        "vector_only": vector_row,
        "graph_rag": graph_row,
    }


def summarize_comparison(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def summarize_rows(comparison_rows: list[dict[str, Any]]) -> dict[str, Any]:
        result_summary: dict[str, Any] = {}
        for name in ("vector_only", "graph_rag"):
            results = [row[name] for row in comparison_rows]
            if not results:
                continue
            metrics = [
                key
                for key in results[0]
                if key not in {"id", "category", "retrieved_ids", "evidence_ids", "answer"}
            ]
            result_summary[name] = {
                key: fmean(values)
                for key in metrics
                if (
                    values := [
                        result[key]
                        for result in results
                        if isinstance(result.get(key), (int, float))
                    ]
                )
            }
            input_values = [result.get("chat_input_tokens") for result in results]
            output_values = [result.get("chat_output_tokens") for result in results]
            result_summary[name]["chat_tokens_total"] = (
                sum(input_values) + sum(output_values)
                if all(value is not None for value in (*input_values, *output_values))
                else None
            )
            embedding_values = [result.get("embedding_tokens_estimate") for result in results]
            result_summary[name]["embedding_tokens_total_estimate"] = (
                sum(embedding_values)
                if all(value is not None for value in embedding_values)
                else None
            )
            result_summary[name]["latency_seconds"] = summarize_latency(
                [
                    result["elapsed_seconds"]
                    for result in results
                    if result.get("elapsed_seconds") is not None
                ]
            )
            cost_values = [result.get("estimated_api_cost_usd") for result in results]
            result_summary[name]["estimated_api_cost_usd_total"] = (
                sum(cost_values) if all(value is not None for value in cost_values) else None
            )
        return result_summary

    summary: dict[str, Any] = {"case_count": len(rows), **summarize_rows(rows)}
    # 답변 검증을 통과하지 못한 케이스 수를 평균 뒤에 숨기지 않고 따로 셉니다.
    summary["graph_rag_failures"] = sum(
        1 for row in rows if row.get("graph_rag", {}).get("failed")
    )
    summary["graph_rag_failed_ids"] = [
        row["id"] for row in rows if row.get("graph_rag", {}).get("failed")
    ]
    summary["by_category"] = {
        category: summarize_rows([row for row in rows if row["category"] == category])
        for category in sorted({row["category"] for row in rows})
    }
    costs = [
        summary.get(name, {}).get("estimated_api_cost_usd_total")
        for name in ("vector_only", "graph_rag")
    ]
    summary["cost_usd"] = (
        sum(costs) if costs and all(value is not None for value in costs) else None
    )
    summary["cost_note"] = (
        "Includes configured chat and estimated embedding API charges only; excludes Neo4j costs. "
        "Null means usage or sourced pricing is unavailable."
    )
    return summary


# 상류가 과부하라고 말할 때 사례 하나를 다시 돌려 봅니다(ADR-054). 전체 작업을 다시
# 돌리는 것보다 **훨씬 쌉니다** - 30건 중 한 건만 다시 치릅니다. 횟수를 작게 두는 이유는
# 재시도가 돈을 쓰기 때문입니다: 상류 장애가 길면 그건 재시도로 덮을 일이 아닙니다.
# 장부에 남기는 지표. 게이트의 `primary_metric`과 같아야 그 질문에 답할 수 있습니다.
LEDGER_PRIMARY = "answer_evidence_recall"
CASE_RETRIES = 2
CASE_BACKOFF = (5.0, 15.0)


def case_with_retry(
    chatbot: Any,
    case: dict[str, Any],
    *,
    mode: str,
    top_k: int,
    rates: dict[str, float | None],
    sleep: Any = _sleep,
) -> tuple[dict[str, Any], int]:
    """한 사례를 돌리고 `(행, 재시도 횟수)`를 돌려줍니다.

    **상류 장애만 다시 시도합니다.** 예산 소진은 기다려도 풀리지 않고(`budget.transient`가
    None을 돌려줍니다), 다른 예외는 결함일 수 있으므로 재시도가 그것을 덮으면 안 됩니다.

    `chatbot.llm`이 `streaming=True`라서 **이 층이 아니면 재시도할 수 없습니다.** SDK의
    `max_retries`는 최초 HTTP 요청만 덮고, 200으로 열린 스트림이 나중에 오류 이벤트를
    흘리면 거기까지 가지 않습니다 - 2026-10-21 PR #16이 그렇게 떨어졌습니다.
    """
    for attempt in range(CASE_RETRIES + 1):
        try:
            return one_case(chatbot, case, mode=mode, top_k=top_k, rates=rates), attempt
        except Exception as exc:  # noqa: BLE001 - 상류 장애만 가로채고 나머지는 올립니다.
            if attempt == CASE_RETRIES or budget.transient(exc) is None:
                raise
            wait = CASE_BACKOFF[min(attempt, len(CASE_BACKOFF) - 1)]
            print(
                f"  상류 장애로 다시 시도합니다({attempt + 1}/{CASE_RETRIES}, {wait:.0f}초 뒤): "
                f"{case['id']} [{type(exc).__name__}]"
            )
            sleep(wait)
    raise RuntimeError("재시도를 모두 소진했습니다")


def one_case(
    chatbot: Any,
    case: dict[str, Any],
    *,
    mode: str,
    top_k: int,
    rates: dict[str, float | str] | None,
) -> dict[str, Any]:
    """Run one case in whichever mode was asked for.

    세 갈래를 `main`의 루프 안에 두고 있었습니다. 떼어 낸 이유는 **루프가 한 줄이어야 그
    한 줄을 감쌀 수 있기** 때문입니다(ADR-044의 예산 판별).
    """
    if mode == "compare":
        return compare_case(chatbot, case, top_k=top_k, rates=rates)
    if mode == "retrieval":
        response = chatbot.search_papers.invoke(
            {"query": case["search_query"], "top_k": top_k}
        )
        return evaluate_case(case, response, mode=mode, top_k=top_k)
    # agent 모드도 토큰과 지연을 적습니다. compare 모드만 적고 있었는데, 그러면 벡터
    # 기준선까지 돌리지 않고는 비용을 비교할 수 없습니다 - 라우팅 측정이 정확히 그
    # 경우였습니다(ADR-024).
    start = perf_counter()
    with sdk_retries() as retries:
        response, messages = chatbot.ask(case["question"], [])
    row = evaluate_case(case, response, mode=mode, top_k=top_k)
    agent_input, agent_output = _token_totals(messages)
    agent_cached = cached_input_tokens(messages)
    row.update({
        "elapsed_seconds": perf_counter() - start,
        "chat_input_tokens": agent_input,
        # 캐시 읽기는 같은 접두사를 다시 보낼 때 할인되는 부분입니다. 빼고 보면 이번 답변이
        # 실제로 새로 읽힌 양이 나옵니다.
        "chat_input_tokens_cached": agent_cached,
        "chat_input_tokens_fresh": (
            None if agent_input is None or agent_cached is None
            else agent_input - agent_cached
        ),
        "chat_output_tokens": agent_output,
        "estimated_api_cost_usd": estimate_api_cost(agent_input, agent_output, 0, rates),
        # 어떤 도구를 불렀는지입니다. 임베딩 토큰으로 추정하지 않고 직접 셉니다.
        "tool_names": [
            call.get("name", "") for call in response.get("tool_calls", []) or []
        ],
        # 빈 사전은 "재시도 0번"입니다. 키가 없는 옛 보고서와 구별됩니다.
        "sdk_retries": dict(retries),
        "tool_seconds": tool_seconds(response),
    })
    return row


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a small arXiv GraphRAG retrieval/answer evaluation."
    )
    parser.add_argument("--mode", choices=("retrieval", "agent", "compare"), default="retrieval")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--case-id", help="Run one named case, for example title-01.")
    parser.add_argument(
        "--case-ids",
        help=(
            "쉼표로 구분한 사례 목록. 회귀 게이트의 smoke 분할은 가족마다 한 건씩 고르므로 "
            "--limit로는 지정할 수 없습니다(앞에서 n건을 자르면 한 가족만 봅니다)."
        ),
    )
    parser.add_argument("--output", type=Path, default=Path("reports/rag_eval.json"))
    args = parser.parse_args()
    if args.limit < 1 or args.top_k < 1:
        parser.error("--limit and --top-k must be positive")
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [case for case in cases if case["id"] == args.case_id]
    if args.case_ids:
        wanted = [value.strip() for value in args.case_ids.split(",") if value.strip()]
        available = {case["id"] for case in cases}
        if missing := [value for value in wanted if value not in available]:
            parser.error(f"없는 사례를 지정했습니다: {missing}")
        cases = [case for case in cases if case["id"] in set(wanted)]
    else:
        cases = cases[: args.limit]
    if not cases:
        parser.error("no evaluation cases found")

    # 형제 평가 모듈 14개가 이미 부르는 보호입니다. 이 PC의 콘솔은 cp949이고, 출력 글자
    # 때문에 평가가 실패하는 것은 평가의 실패가 아닙니다(`pipeline/console.py`).
    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    from src import chatbot

    rates = pricing_from_env(os.environ)
    warmup_seconds = warm_citation_graph(chatbot, args.mode)

    # 앞 실행이 남긴 표식이 이번 실패를 설명하면 안 됩니다.
    budget.clear()
    rows = []
    retried = 0
    for case in cases:
        try:
            row, attempts = case_with_retry(
                chatbot, case, mode=args.mode, top_k=args.top_k, rates=rates
            )
            rows.append(row)
            retried += attempts
        except Exception as exc:  # noqa: BLE001 - 두 범주만 가로채고 나머지는 그대로 올립니다.
            # 예산이 바닥난 것은 **품질 회귀가 아니라 평가를 돌리지 못한 것**입니다. 지금까지는
            # 둘이 똑같은 빨간 X였고, 어느 쪽인지 알려면 로그 40줄을 열어야 했습니다(ADR-044).
            # 재시도는 소용없습니다 - 기다려도 풀리지 않는 종류의 429입니다.
            if (code := budget.exhausted(exc)) is not None:
                budget.record(code, exc, stage="evals.run", done=len(rows), retries=retried)
                raise SystemExit(budget.EXIT_NOT_RUN) from None
            # 상류 장애는 기다리면 풀리지만 재시도를 다 썼습니다. 처방이 다르므로 사유도
            # 다르게 적습니다 - "한도를 올리세요"가 아니라 "다시 돌리세요"입니다(ADR-054).
            if (marker := budget.transient(exc)) is not None:
                budget.record(
                    marker, exc, stage="evals.run", done=len(rows),
                    reason=budget.REASON_UPSTREAM, retries=retried,
                )
                raise SystemExit(budget.EXIT_NOT_RUN) from None
            raise
    metadata = build_run_metadata(
        cases_path=args.cases,
        case_ids=[case["id"] for case in cases],
        mode=args.mode,
        top_k=args.top_k,
        model=getattr(chatbot, "llm_model", os.environ.get("OPENAI_MODEL", "")),
        embedding_model=getattr(chatbot, "EMBEDDING_MODEL", "text-embedding-3-large"),
    )
    # 이 값이 없는 보고서는 이 변경 전의 것이고, 첫 관련 논문 호출이 차갑습니다.
    metadata["citation_graph_warmup_seconds"] = warmup_seconds
    report = {
        "schema_version": 2,
        "generated_at": metadata["timestamp_utc"],
        "mode": args.mode,
        "top_k": args.top_k,
        "dataset": args.cases.name,
        "metadata": metadata,
        "pricing": rates,
        "results": rows,
        "summary": summarize_comparison(rows) if args.mode == "compare" else summarize(rows),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    # **실행마다 사례별 지표 한 줄을 장부에 남깁니다**(ADR-055). 보고서 자체는
    # `reports/regression_*.json`이 gitignore라 CI에서 버려지는데, 그 안의 사례별
    # 값이 `stable_case`가 변동으로 터지는지를 재는 데 필요한 유일한 데이터입니다.
    # 한 줄이면 diff가 읽히고, 담는 것은 지표 값과 묶음 열쇠뿐입니다.
    if ledger.record(report, primary=LEDGER_PRIMARY):
        print(f"장부에 한 줄 남겼습니다: {ledger.LEDGER}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
