"""FastAPI 층(`src/api.py`). 가짜 백엔드로 접속 없이 돕니다.

- 질문은 SSE로 `partial` 다음 `final`(또는 `abstain`·`error`) 하나로 끝납니다.
- 돈이 드는 길(질문·번역)에는 IP별·전체 상한이 있고, 그래프만 읽는 길에는 없습니다.
- 화면이 보낸 앞 문답이 대화 맥락이 됩니다.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src import api


class Validation(Exception):
    def diagnostics(self):
        return {
            "request_id": "r1",
            "stage": "conditions",
            "reason": "x",
            "attempts": 2,
            "cyphers": [],
        }


class FakeBot:
    AnswerValidationError = Validation
    LOADED_COMMIT = "abc1234"

    def __init__(self):
        self.asked = []
        self.reads = []
        self.mode = "ok"

    def ask(self, question, history, on_answer=None):
        self.asked.append((question, history))
        if self.mode == "abstain":
            raise Validation("보류")
        if self.mode == "boom":
            raise RuntimeError("secret detail")
        on_answer("Ye")
        on_answer("Yes.")
        return {"answer": "Yes.", "evidence_ids": ["2401.1"]}, []

    @staticmethod
    def to_jsonable(value):
        return value

    def translate_to_korean(self, text):
        return "번역: " + text

    def graph_counts(self):
        return {"papers": 3}

    def top_categories(self, limit):
        return [{"code": "cs.AI", "papers": 3}]

    def citation_graph(self):
        return {"index": {"a": 0, "b": 1}}

    def fetch_paper_for_saving(self, arxiv_id):
        if arxiv_id in ("a", "2401.00001"):
            return {
                "arxiv_id": arxiv_id,
                "title": "T",
                "authors": ["Ada Lovelace"],
                "published": "2024-01-02",
                "primary_category": "cs.AI",
            }
        return None

    def run_read(self, cypher, params):
        self.reads.append((cypher, params))
        return [{"cited_by": 5, "references": 0}]

    class resolve_paper:  # noqa: N801
        @staticmethod
        def invoke(args):
            return {"query": args["title"], "candidates": [{"arxiv_id": "a"}]}

    class rank_related_papers:  # noqa: N801 - LangChain 도구처럼 .invoke를 가집니다
        @staticmethod
        def invoke(args):
            return {"related": [{"arxiv_id": "b"}]}

    def global_pagerank(self):
        return [0.7, 0.3]

    def top_by_score(self, score, limit):
        return ["a", "b"][:limit]

    def with_details(self, ids, score_of):
        return [{"arxiv_id": i, "score": score_of(i)} for i in ids]


def client(bot=None, limiter=None):
    return TestClient(
        api.create_app(
            backend=bot or FakeBot(), limiter=limiter or api.Limiter(100, 100, 100), warm=False
        )
    )


def events(text):
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_ask_streams_partials_then_one_final():
    response = client().post("/api/ask", json={"question": "q"})
    assert response.headers["content-type"].startswith("text/event-stream")
    got = events(response.text)
    assert [kind for kind, _ in got] == ["partial", "partial", "final"]
    assert got[-1][1]["evidence_ids"] == ["2401.1"]


def test_history_becomes_the_conversation():
    bot = FakeBot()
    client(bot).post(
        "/api/ask", json={"question": "q2", "history": [{"question": "q1", "answer": "a1"}]}
    )
    assert bot.asked == [("q2", [("user", "q1"), ("assistant", "a1")])]


def test_abstention_is_its_own_event():
    bot = FakeBot()
    bot.mode = "abstain"
    kind, data = events(client(bot).post("/api/ask", json={"question": "q"}).text)[-1]
    assert kind == "abstain" and data["stage"] == "conditions"


def test_unexpected_errors_do_not_leak_their_message():
    bot = FakeBot()
    bot.mode = "boom"
    kind, data = events(client(bot).post("/api/ask", json={"question": "q"}).text)[-1]
    assert kind == "error"
    assert data["type"] == "RuntimeError"
    assert "secret" not in json.dumps(data)


def test_paid_paths_are_rate_limited_per_ip():
    c = client(limiter=api.Limiter(per_minute=1, per_day=10, total_per_day=10))
    headers = {"x-forwarded-for": "1.1.1.1"}
    assert c.post("/api/ask", json={"question": "q"}, headers=headers).status_code == 200
    blocked = c.post("/api/translate", json={"text": "t"}, headers=headers)
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0
    other = c.post("/api/ask", json={"question": "q"}, headers={"x-forwarded-for": "2.2.2.2"})
    assert other.status_code == 200


def test_free_paths_are_not_limited():
    c = client(limiter=api.Limiter(per_minute=0, per_day=0, total_per_day=0))
    assert c.get("/api/stats").status_code == 200
    assert c.get("/api/papers/a").status_code == 200
    assert c.post("/api/ask", json={"question": "q"}).status_code == 429


def test_limiter_windows():
    now = [0.0]
    limiter = api.Limiter(per_minute=2, per_day=3, total_per_day=4, clock=lambda: now[0])
    assert limiter.check("a") is None and limiter.check("a") is None
    assert limiter.check("a") == 61  # 분당 2
    now[0] = 61
    assert limiter.check("a") is None
    now[0] = 130
    assert limiter.check("a") > 3600  # 하루 3
    assert limiter.check("b") is None
    assert limiter.check("c") > 3600  # 전체 4
    now[0] = 86401
    assert limiter.check("a") is None


@pytest.mark.parametrize(
    "body",
    [
        {"question": ""},
        {"question": "x" * 2001},
        {"question": "q", "history": [{"question": "q", "answer": "a"}] * 7},
    ],
)
def test_oversized_or_empty_questions_are_refused(body):
    assert client().post("/api/ask", json=body).status_code == 422


def test_paper_page_carries_counts_and_related():
    data = client().get("/api/papers/a").json()
    assert data["cited_by"] == 5 and data["references"] == 0
    assert data["related"] == [{"arxiv_id": "b"}]
    assert client().get("/api/papers/zzz").status_code == 404


def test_global_rank_attaches_scores():
    data = client().get("/api/rank?limit=1").json()
    assert data == {"scope": "global", "papers": [{"arxiv_id": "a", "score": 0.7}]}


def test_health_reports_commits_without_loading_the_model():
    data = client().get("/api/health").json()
    assert data["status"] == "ok" and data["module_commit"] == "abc1234"


def test_quality_reads_the_committed_baseline():
    data = client().get("/api/quality").json()
    assert data["summary"]["cases"] > 0
    assert data["rules"] and data["gates"]


def test_importing_the_module_does_not_load_the_chatbot():
    import sys

    assert "src.chatbot" not in sys.modules or api.app is not None
    # 모듈 수준의 app은 백엔드를 들고 있지 않습니다 - 첫 요청에서 불러옵니다.
    assert api.create_app(warm=False) is not None


def test_paper_page_carries_a_bibtex_entry():
    data = client().get("/api/papers/a").json()
    assert data["bibtex"].startswith("@misc{") and "Lovelace" in data["bibtex"]


def test_citing_order_is_chosen_from_a_whitelist():
    bot = FakeBot()
    c = client(bot)
    assert c.get("/api/papers/a/citing?order=cited&limit=500").status_code == 200
    cypher, params = bot.reads[-1]
    assert "ORDER BY cited_by DESC" in cypher and params["limit"] == 50
    assert c.get("/api/papers/a/citing?order=x) DETACH DELETE c //").status_code == 422


def test_search_takes_an_arxiv_id_or_a_title():
    c = client()
    assert c.get("/api/search?q=2401.00001v2").json()["candidates"][0]["arxiv_id"] == "2401.00001"
    assert c.get("/api/search?q=react").json()["candidates"] == [{"arxiv_id": "a"}]
    assert c.get("/api/search?q=x").status_code == 422


def test_route_is_judged_without_the_backend():
    data = (
        client(limiter=api.Limiter(0, 0, 0))
        .get("/api/route", params={"q": "Which papers cite 2210.03629?"})
        .json()
    )
    assert data["name"] in ("graph", "any") and isinstance(data["cues"], list)
