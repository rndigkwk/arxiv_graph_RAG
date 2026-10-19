"""예산 소진을 품질 회귀와 구별하는지 검증합니다.

2026-10-19에 CI의 `evaluate`가 빨갛게 떴고, 로그 40줄을 열어야 품질과 무관함을 알 수 있었습니다
— OpenAI 프로젝트가 지출 한도에 닿은 것이었습니다. 고정할 것이 넷입니다.

1. **두 겹으로 싸인 예외에서도 찾습니다.** 실제 예외는 `RetryError` → `EmbeddingsGenerationError`
   → `RateLimitError`로 옵니다.
2. **일시적 429는 가로채지 않습니다.** 그것은 기다리면 풀리므로 보통의 실패입니다.
3. **통과로 바꾸지 않습니다.** 종료 코드가 0이 아니고, 요약이 "돌리지 못했다"고 적습니다.
4. **RAGAS가 조용히 floor 위반으로 바꾸지 않습니다.** 지표마다 None을 적고 계속 가면 게이트가
   "지표가 없습니다"라는 하한 위반으로 올립니다.
"""

from __future__ import annotations

import json

import pytest

from evals import budget

SPEND_LIMIT = (
    "Error code: 429 - {'error': {'message': 'Your project has reached its configured "
    "enforced spend limit.', 'type': 'insufficient_quota', 'param': None, 'code': "
    "'project_spend_limit_exceeded'}}"
)
TOO_MANY_REQUESTS = (
    "Error code: 429 - {'error': {'message': 'Rate limit reached for gpt-6-luna', "
    "'type': 'requests', 'code': 'rate_limit_exceeded'}}"
)


class FakeRateLimit(Exception):
    """Shaped like `openai.RateLimitError`: carries `code` and `body`."""

    def __init__(self, message: str, *, code: str = "", body: dict | None = None):
        super().__init__(message)
        self.code = code
        self.body = body or {}


def wrapped(inner: Exception) -> Exception:
    """What actually reaches our code: two layers over the provider's error."""
    try:
        try:
            raise inner
        except Exception as exc:
            raise RuntimeError("Failed to generate embedding with OpenAI") from exc
    except Exception as outer:
        return outer


def test_a_spend_limit_is_recognised_through_the_wrappers():
    found = budget.exhausted(wrapped(FakeRateLimit(SPEND_LIMIT)))

    assert found == "insufficient_quota"


def test_the_code_attribute_alone_is_enough():
    """메시지 문구는 공급자가 바꿀 수 있습니다. 코드 필드도 함께 봅니다."""
    assert budget.exhausted(FakeRateLimit("429", code="insufficient_quota"))
    assert budget.exhausted(
        FakeRateLimit("429", body={"error": {"code": "billing_hard_limit_reached"}})
    )


def test_a_transient_rate_limit_is_not_treated_as_exhausted():
    """기다리면 풀리는 429입니다. 가로채면 재시도가 맞는 대응인데 멈춰 버립니다."""
    assert budget.exhausted(wrapped(FakeRateLimit(TOO_MANY_REQUESTS))) is None
    assert budget.exhausted(ValueError("Vector-only baseline returned nothing")) is None
    assert budget.exhausted(TimeoutError("read timed out")) is None


def test_the_marker_records_how_far_the_run_got(tmp_path, monkeypatch):
    """0건과 12건은 다시 돌릴 때 할 일이 다릅니다."""
    marker = tmp_path / "eval_not_run.json"
    monkeypatch.setattr(budget, "MARKER", marker)

    budget.record("insufficient_quota", FakeRateLimit(SPEND_LIMIT), stage="evals.run", done=3)
    entry = json.loads(marker.read_text(encoding="utf-8"))

    assert entry["not_run"] is True
    assert entry["reason"] == "budget_exhausted"
    assert entry["provider_code"] == "insufficient_quota"
    assert entry["stage"] == "evals.run"
    assert entry["cases_done"] == 3


def test_a_stale_marker_cannot_explain_a_new_failure(tmp_path, monkeypatch):
    marker = tmp_path / "eval_not_run.json"
    monkeypatch.setattr(budget, "MARKER", marker)
    budget.record("insufficient_quota", FakeRateLimit(SPEND_LIMIT), stage="evals.run")

    budget.clear()

    assert not marker.exists()
    assert budget.explain(marker) == ""
    budget.clear()  # 없는 파일을 지우는 것도 조용히 성공해야 합니다.


def test_the_summary_says_it_did_not_run_and_did_not_pass(tmp_path, monkeypatch):
    marker = tmp_path / "eval_not_run.json"
    monkeypatch.setattr(budget, "MARKER", marker)
    budget.record("project_spend_limit_exceeded", FakeRateLimit(SPEND_LIMIT),
                  stage="evals.ragas_study", done=7)

    text = budget.explain(marker)

    assert "돌리지 못했습니다" in text
    assert "품질 회귀가 아닙니다" in text
    assert "evals.ragas_study" in text
    assert "통과로 바뀌지 않습니다" in text


def test_a_broken_marker_is_not_an_explanation(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{ 이건 JSON이 아닙니다", encoding="utf-8")

    assert budget.explain(broken) == ""


def test_not_run_does_not_look_like_success():
    """종료 코드가 0이면 GitHub가 초록색을 칠합니다."""
    assert budget.EXIT_NOT_RUN != 0


def test_ragas_reraises_a_budget_error_instead_of_scoring_it_as_none():
    """지표마다 None을 적으면 게이트가 **floor 위반**으로 올립니다 - 품질처럼 읽힙니다."""
    import asyncio

    from evals.ragas_study import score_one

    class Boom:
        async def ascore(self, **_):
            raise FakeRateLimit(SPEND_LIMIT)

    class Ordinary:
        async def ascore(self, **_):
            raise ValueError("판정이 형식을 벗어났습니다")

    row = {"question": "q", "answer": "a", "contexts": ["c"]}

    with pytest.raises(FakeRateLimit):
        asyncio.run(score_one({"faithfulness": Boom()}, row))

    # 보통의 실패는 그대로 한 지표만 None입니다.
    scores = asyncio.run(score_one({"faithfulness": Ordinary()}, row))
    assert scores["faithfulness"] is None
    assert "faithfulness_error" in scores


def test_the_case_loop_is_one_call_so_it_can_be_wrapped():
    """세 갈래가 루프 안에 있으면 그 루프를 감쌀 수 없습니다."""
    from evals.run import one_case

    assert callable(one_case)


def test_the_harnesses_clear_the_marker_and_exit_with_the_not_run_code():
    """배선이 지워지기 쉬운 자리입니다. 지워지면 다시 트레이스백만 남습니다."""
    from pathlib import Path

    for name in ("evals/run.py", "evals/ragas_study.py"):
        source = Path(name).read_text(encoding="utf-8")
        assert "budget.clear()" in source, name
        assert "budget.exhausted(exc)" in source, name
        assert "budget.record(" in source, name
        assert "budget.EXIT_NOT_RUN" in source, name


def test_the_workflow_explains_the_marker_before_the_gate_summary():
    """요약 맨 위에 적히지 않으면 로그를 열어야 하고, 그게 고치려던 문제입니다."""
    from pathlib import Path

    workflow = Path(".github/workflows/regression-eval.yml").read_text(encoding="utf-8")

    assert "reports/eval_not_run.json" in workflow
    assert "evals.budget --explain" in workflow
    # 표식을 artifact로 올려야 PR에서 받아 볼 수 있습니다.
    assert workflow.count("reports/eval_not_run.json") >= 2
    # 게이트를 통과로 바꾸는 장치(continue-on-error)를 이 잡에 붙이지 않았는지 봅니다.
    assert "continue-on-error" not in workflow
