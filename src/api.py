"""FastAPI 층 - 이전 계획 3단계. Streamlit과 **같은 모듈**(`src.chatbot`)을 HTTP로 엽니다.

    uvicorn src.api:app --host 0.0.0.0 --port 8000

Next.js 화면(4단계, 시안 다섯 화면)이 쓸 것만 엽니다. 질문은 Server-Sent Events로 흘려 보냅니다 -
Streamlit이 `on_answer`로 부분 답변을 그리던 자리를 SSE의 `partial` 이벤트가 대신합니다.

**돈이 드는 길에는 문턱을 둡니다.** 질문(`/api/ask`)과 번역(`/api/translate`)은 OpenAI를 부릅니다.
Streamlit 화면은 사람이 버튼을 눌러야 하지만, API는 스크립트 한 줄로 수천 번 부를 수 있습니다.
그래서 IP마다 분당·하루 상한과, 모든 IP를 합친 하루 상한을 둡니다(`Limiter`). 값은 환경변수로
바꿉니다. 나머지 길은 그래프만 읽습니다.

**기다리지 않는 것.** `chatbot`은 처음 불릴 때 불러옵니다(드라이버·모델 객체를 만들고 `.env`를
읽습니다). 테스트는 `create_app(backend=...)`로 가짜를 넣어 접속 없이 돕니다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src import abstain, citations, family_board, quality_board, release, routing, warmup

logger = logging.getLogger("arxiv_graph_rag.api")

MAX_QUESTION_CHARS = 2000
MAX_HISTORY_TURNS = 6
MAX_TRANSLATE_CHARS = 8000

# 정렬은 사용자 입력을 Cypher에 넣지 않고 이 표에서 고릅니다.
CITING_ORDER = {"recent": "c.published DESC", "cited": "cited_by DESC, c.published DESC"}
CITING = """
MATCH (:ArxivPaper {{arxiv_id: $arxiv_id}})<-[:REFERENCES]-(c:ArxivPaper)
WITH c, COUNT {{ (c)<-[:REFERENCES]-() }} AS cited_by
RETURN c.arxiv_id AS arxiv_id, c.title AS title, c.primary_category AS primary_category,
       toString(c.published) AS published, cited_by
ORDER BY {order} SKIP $skip LIMIT $limit
"""
RANK_BY = ("cited", "pagerank", "personalized")
# 그래프 소개 화면의 커뮤니티 그림. GDS로 Leiden을 돌린 노트북이 저장한 보고서를 읽습니다 -
# VM의 Neo4j Community에는 GDS가 없어 다시 계산할 수 없습니다.
COMMUNITY_REPORT = Path("reports/aura_graph_rag_quality_report.json")
MAX_RANK_DEPTH = 500


def ranked_ids(graph, score, mask, exclude, skip: int, limit: int) -> tuple[list[str], int]:
    """점수 순으로 skip부터 limit편. 점수가 0인 논문과 mask 밖, exclude는 세지 않습니다."""
    usable = np.asarray(score, dtype=float).copy()
    if mask is not None:
        usable = np.where(mask, usable, 0.0)
    for arxiv_id in exclude:
        if arxiv_id in graph["index"]:
            usable[graph["index"][arxiv_id]] = 0.0
    total = int((usable > 0).sum())
    skip = max(0, min(skip, MAX_RANK_DEPTH))
    limit = max(0, min(max(1, limit), 50, MAX_RANK_DEPTH - skip))
    order = np.argsort(-usable, kind="stable")[skip:skip + limit]
    return [graph["ids"][i] for i in order if usable[i] > 0], total


ARXIV_ID = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")

PAPER_COUNTS = """
MATCH (p:ArxivPaper {arxiv_id: $arxiv_id})
RETURN COUNT { (p)<-[:REFERENCES]-() } AS cited_by,
       COUNT { (p)-[:REFERENCES]->() } AS references
"""


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def wait_until(window, now: float, span: int) -> int:
    return int(span - (now - window[0])) + 1 if window else span


class Limiter:
    """IP마다 분당·하루 상한, 그리고 전체 하루 상한. 프로세스 메모리에만 둡니다.

    재시작하면 비워집니다 - 배포(재시작)가 하루에 몇 번이라 그만큼 상한이 느슨해지지만, 막으려는
    것은 스크립트로 수천 번 부르는 경우이고 그것은 재시작 사이에도 걸립니다.
    """

    def __init__(
        self,
        per_minute: int,
        per_day: int,
        total_per_day: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.per_minute, self.per_day, self.total_per_day = per_minute, per_day, total_per_day
        self.clock = clock
        self._lock = threading.Lock()
        self._calls: dict[str, deque[float]] = defaultdict(deque)
        self._total: deque[float] = deque()

    @classmethod
    def from_env(cls) -> Limiter:
        return cls(
            env_int("API_ASK_PER_MINUTE", 3),
            env_int("API_ASK_PER_DAY", 30),
            env_int("API_ASK_TOTAL_PER_DAY", 300),
        )

    def check(self, key: str) -> int | None:
        """통과하면 `None`, 막히면 다시 시도할 수 있을 때까지의 초."""
        now = self.clock()
        with self._lock:
            calls = self._calls[key]
            for window in (calls, self._total):
                while window and now - window[0] >= 86400:
                    window.popleft()
            # 상한이 0이면 창이 비어 있어도 막습니다(그 길을 닫는 설정).
            if len(self._total) >= self.total_per_day:
                return wait_until(self._total, now, 86400)
            if len(calls) >= self.per_day:
                return wait_until(calls, now, 86400)
            recent = [t for t in calls if now - t < 60]
            if len(recent) >= self.per_minute:
                return wait_until(recent, now, 60)
            calls.append(now)
            self._total.append(now)
            return None


def client_ip(request: Request) -> str:
    """Caddy 뒤에서 돕니다. 이 컨테이너는 호스트 포트가 없으므로 X-Forwarded-For는 Caddy가
    씁니다."""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class Turn(BaseModel):
    question: str
    answer: str


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)
    history: list[Turn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)


class TranslateBody(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TRANSLATE_CHARS)


class SubgraphBody(BaseModel):
    arxiv_ids: list[str] = Field(min_length=1, max_length=50)


def history_messages(history: list[Turn]) -> list[tuple[str, str]]:
    """API는 대화 상태를 들지 않습니다. 화면이 앞 문답을 보내면 그것으로 맥락을 만듭니다."""
    messages: list[tuple[str, str]] = []
    for turn in history:
        messages += [("user", turn.question), ("assistant", turn.answer)]
    return messages


def sse(kind: str, data: Any) -> str:
    return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def load_chatbot() -> Any:
    from src import chatbot

    return chatbot


def create_app(backend: Any = None, limiter: Limiter | None = None, warm: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if warm:
            # 앱처럼 부팅 때 인용망을 올립니다(ADR-067). 첫 순위·관련 논문 요청이 20초를
            # 기다리지 않게. 배경 스레드이므로 서버는 바로 요청을 받습니다.
            warmup_task().start()
        yield

    app = FastAPI(title="arXiv GraphRAG API", version="1", lifespan=lifespan)
    origins = [o.strip() for o in os.environ.get("API_ALLOWED_ORIGINS", "").split(",") if o.strip()]
    # Vercel의 미리보기 주소는 브랜치마다 달라서 목록으로 적을 수 없습니다. 정규식으로 받습니다.
    origin_regex = os.environ.get("API_ALLOWED_ORIGIN_REGEX") or None
    if origins or origin_regex:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_origin_regex=origin_regex,
            expose_headers=["retry-after"],
            allow_methods=["GET", "POST"],
            allow_headers=["content-type"],
        )
    limiter = limiter or Limiter.from_env()
    state: dict[str, Any] = {"backend": backend}
    lock = threading.Lock()

    def chatbot() -> Any:
        with lock:
            if state["backend"] is None:
                state["backend"] = load_chatbot()
            return state["backend"]

    def warmup_task() -> warmup.Warmup:
        with lock:
            if "warmup" not in state:
                state["warmup"] = warmup.Warmup(lambda: chatbot().citation_graph())
            return state["warmup"]

    def guard(request: Request) -> None:
        wait = limiter.check(client_ip(request))
        if wait is not None:
            raise HTTPException(
                429,
                "요청이 너무 많습니다. 잠시 뒤 다시 시도하세요.",
                headers={"Retry-After": str(wait)},
            )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "commit": release.commit(),
            "module_commit": getattr(state["backend"], "LOADED_COMMIT", None),
            "citation_graph": warmup_task().state(),
        }

    @app.get("/api/stats")
    def stats() -> dict[str, Any]:
        bot = chatbot()
        return {"counts": bot.graph_counts(), "top_categories": bot.top_categories(10)}

    @app.post("/api/ask")
    async def ask(body: AskBody, request: Request) -> StreamingResponse:
        guard(request)
        bot = chatbot()
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def put(kind: str, data: Any) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (kind, data))

        def work() -> None:
            try:
                response, _ = bot.ask(
                    body.question,
                    history_messages(body.history),
                    lambda text: put("partial", {"answer": text}),
                )
                put("final", bot.to_jsonable(response))
            except bot.AnswerValidationError as exc:
                diagnostics = exc.diagnostics()
                # 사유 코드를 사람 말로 바꾸는 일은 Streamlit과 같은 `abstain.explain`이 합니다.
                put("abstain", {"message": str(exc), **diagnostics,
                                "explained": abstain.explain(diagnostics)})
            except Exception as exc:  # noqa: BLE001 - 화면에는 사유만, 로그에는 전부
                logger.exception("ask_failed")
                put(
                    "error",
                    {
                        "message": "답변을 만들지 못했습니다. 다시 시도해 주세요.",
                        "type": type(exc).__name__,
                    },
                )

        loop.run_in_executor(None, work)

        async def events():
            while True:
                kind, data = await queue.get()
                yield sse(kind, data)
                if kind != "partial":
                    return

        # Caddy가 응답을 모아 두지 않도록 합니다(Caddyfile의 flush_interval과 짝).
        return StreamingResponse(
            events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.post("/api/translate")
    def translate(body: TranslateBody, request: Request) -> dict[str, str]:
        guard(request)
        return {"text": chatbot().translate_to_korean(body.text)}

    @app.get("/api/papers/{arxiv_id}")
    def paper(arxiv_id: str) -> dict[str, Any]:
        bot = chatbot()
        row = bot.fetch_paper_for_saving(arxiv_id)
        if row is None:
            raise HTTPException(404, "그래프에 없는 논문입니다.")
        counts = bot.run_read(PAPER_COUNTS, {"arxiv_id": arxiv_id})[0]
        related = bot.rank_related_papers.invoke({"arxiv_ids": [arxiv_id], "top_k": 5})
        return bot.to_jsonable(
            {**row, **counts, "bibtex": citations.entry(row), "related": related.get("related", [])}
        )

    @app.get("/api/papers/{arxiv_id}/forecast")
    def forecast(arxiv_id: str) -> dict[str, Any]:
        """앞으로 함께 인용될 논문(ADR-038). 화면의 버튼을 눌러야 부릅니다.

        모델은 저장된 가중치(`models/artifacts`)와 그래프만 씁니다 - OpenAI를 부르지 않으므로
        유료 길의 문턱(`guard`) 밖입니다. 가중치가 없거나 지문이 맞지 않으면 `reason`이 옵니다.
        """
        bot = chatbot()
        if bot.fetch_paper_for_saving(arxiv_id) is None:
            raise HTTPException(404, "그래프에 없는 논문입니다.")
        return bot.to_jsonable(bot.forecast_cocitation(arxiv_id))

    @app.get("/api/papers/{arxiv_id}/citing")
    def citing(
        arxiv_id: str, order: str = "recent", skip: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        if order not in CITING_ORDER:
            raise HTTPException(422, f"order는 {', '.join(CITING_ORDER)} 중 하나입니다.")
        rows = chatbot().run_read(
            CITING.format(order=CITING_ORDER[order]),
            {"arxiv_id": arxiv_id, "skip": max(0, skip), "limit": max(1, min(limit, 50))},
        )
        return chatbot().to_jsonable({"order": order, "papers": rows})

    @app.get("/api/search")
    def search(q: str) -> dict[str, Any]:
        """제목이나 arXiv ID로 논문을 찾습니다. 전문 색인이라 모델을 부르지 않습니다(0원)."""
        q = q.strip()
        bot = chatbot()
        if ARXIV_ID.match(q):
            row = bot.fetch_paper_for_saving(q.split("v")[0])
            return bot.to_jsonable({"query": q, "candidates": [row] if row else []})
        if len(q) < 2:
            raise HTTPException(422, "두 글자 이상 적어 주세요.")
        return bot.to_jsonable(bot.resolve_paper.invoke({"title": q, "top_k": 10}))

    @app.get("/api/route")
    def route(q: str) -> dict[str, Any]:
        """보내기 전 경로 판정. `routing.classify`의 규칙만 씁니다(모델도 그래프도 0원)."""
        return routing.classify(q).as_dict()

    @app.post("/api/subgraph")
    def subgraph(body: SubgraphBody) -> dict[str, Any]:
        bot = chatbot()
        return bot.to_jsonable(bot.fetch_subgraph(body.arxiv_ids))

    @app.get("/api/rank")
    def rank(
        by: str = "pagerank",
        category: str | None = None,
        include_secondary: bool = True,
        scope: str = "global",
        seed: str | None = None,
        skip: int = 0,
        limit: int = 20,
    ) -> dict[str, Any]:
        """탐색 화면의 순위. 그래프 안 피인용 수, PageRank, 한 논문에서 출발한 개인화 PageRank.

        셋 다 메모리의 인용망에서 셉니다(모델 0원). 순위는 점수가 0보다 큰 논문만 셉니다 -
        인용으로 이어지지 않은 논문에 순서를 매기면 그 순서는 임의입니다.
        """
        if by not in RANK_BY or scope not in ("global", "subgraph"):
            raise HTTPException(422, f"by는 {', '.join(RANK_BY)}, scope는 global·subgraph입니다.")
        bot = chatbot()
        graph = bot.citation_graph()
        exclude: list[str] = []
        if by == "personalized":
            if not seed:
                raise HTTPException(422, "개인화 PageRank에는 출발 논문(seed)이 필요합니다.")
            score, _ = bot.personalized_pagerank([seed])
            if score is None:
                raise HTTPException(404, "인용망에 없는 논문입니다.")
            exclude = [seed]
        elif by == "cited":
            score = np.bincount(graph["cols"], minlength=graph["size"]).astype(float)
        elif category and scope == "subgraph":
            score = bot.subgraph_pagerank(category, include_secondary)
        else:
            score = bot.global_pagerank()
        mask = bot.category_members(category, include_secondary) if category else None
        ids, total = ranked_ids(graph, score, mask, exclude, skip, limit)
        papers = bot.with_details(ids, lambda arxiv_id: score[graph["index"][arxiv_id]])
        for paper in papers:
            paper["rank"] += max(0, skip)
        return bot.to_jsonable({
            "by": by, "category": category, "include_secondary": include_secondary,
            "scope": scope if by == "pagerank" and category else "global", "seed": seed,
            "total": total, "skip": max(0, skip), "papers": papers,
        })

    @app.get("/api/quality")
    def quality() -> dict[str, Any]:
        baseline = quality_board.load(quality_board.BASELINE)
        if baseline is None:
            raise HTTPException(404, "기준선 파일이 없습니다.")
        stable = baseline.get("ragas_stable_families") or []
        return {
            "summary": quality_board.summary(baseline),
            "rules": [{"name": n, "blocks": w} for n, w in quality_board.RULES],
            "gates": quality_board.gate_rows(baseline),
            "families": family_board.load_rows(stable),
            "rationale": baseline.get("floor_rationale") or {},
            "slots": quality_board.slot_rows(baseline),
            "rejected": [
                {"model": m, "slot": slot, "result": result, "record": record}
                for m, slot, result, record in quality_board.REJECTED
            ],
            "basis": quality_board.basis_rows(quality_board.load(quality_board.FLOOR_BASIS)),
            "prompts": quality_board.prompt_rows(baseline),
            "future": future_study(),
        }

    def future_study() -> dict[str, Any] | None:
        report = quality_board.load(quality_board.FUTURE_STUDY)
        if not report:
            return None
        return {
            "cases": report.get("cases"),
            "feature_cut": report.get("feature_cut"),
            "variants": quality_board.variant_rows(report),
            "degree_variants": report.get("variants") or [],
            "degrees": quality_board.degree_rows(report),
        }

    @app.get("/api/overview")
    def overview() -> dict[str, Any]:
        """그래프 소개 화면. 노드·관계 수는 지금 센 값이고, 커뮤니티는 커밋된 보고서입니다."""
        return {"counts": chatbot().graph_counts(), "communities": communities()}

    def communities() -> dict[str, Any] | None:
        report = quality_board.load(COMMUNITY_REPORT)
        if not report or not report.get("communities_top5"):
            return None
        rows = report["communities_top5"]
        flow = report.get("community_citation_flow") or {}
        labels = [row["label"] for row in rows]
        # flow는 {인용한 쪽: {인용된 쪽: 건수}}입니다. 그림의 선은 방향 없이 양쪽을 합칩니다.
        links = [
            {"a": a, "b": b,
             "citations": flow.get(a, {}).get(b, 0) + flow.get(b, {}).get(a, 0)}
            for i, a in enumerate(labels) for b in labels[i + 1:]
        ]
        return {
            "generated_at": report.get("generated_at"),
            "mode": report.get("community_mode"),
            "rows": [
                {"label": row["label"], "papers": row["papers"],
                 "primary_category": row["primary_category"],
                 "secondary_category": row.get("secondary_category"),
                 "inside": flow.get(row["label"], {}).get(row["label"], 0)}
                for row in rows
            ],
            "links": [link for link in links if link["citations"]],
        }

    return app


app = create_app()
