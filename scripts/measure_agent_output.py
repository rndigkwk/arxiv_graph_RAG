"""답 쓰는 에이전트의 모델 호출마다 시간이 어디에 쓰이는지 잽니다 — 로컬, 돈이 듭니다(질문당 1센트).

    uv run --locked python scripts/measure_agent_output.py
    uv run --locked python scripts/measure_agent_output.py --per-family 2

VM에서 판정 대기가 0초가 된 뒤(ADR-092) 남은 지연은 `generate` 하나입니다. 그 안이 **첫 토큰까지의
대기**인지, **입력 처리**인지, **출력 토큰을 쓰는 시간**인지에 따라 고칠 자리가 다릅니다. 그래서
호출마다 다음을 적습니다.

- 입력 토큰과 그중 캐시에서 읽은 토큰(`input_token_details.cache_read`)
- 첫 토큰까지 시간과 전체 시간
- 출력 토큰이 무엇에 쓰였는지 - 도구 인자(Cypher), 답 문장, `evidence_ids`

에이전트 모델(`chatbot.agent_llm`)에 콜백만 붙입니다. 앱 흐름은 그대로이므로 판정·재시도도 평소처럼
일어나고, 재시도로 늘어난 턴도 그대로 적힙니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CASES = Path("evals/cases_graph.json")
OUTPUT = Path("reports/agent_output_tokens.json")


def probe_class():
    from langchain_core.callbacks import BaseCallbackHandler

    class Probe(BaseCallbackHandler):
        def __init__(self) -> None:
            self.calls: dict[str, dict[str, Any]] = {}

        def on_chat_model_start(self, serialized, messages, *, run_id, **kw):
            self.calls[str(run_id)] = {"start": time.perf_counter(), "first": None}

        def on_llm_new_token(self, token, *, run_id, **kw):
            call = self.calls.get(str(run_id))
            if call is not None and call["first"] is None and token:
                call["first"] = time.perf_counter()

        def on_llm_end(self, response, *, run_id, **kw):
            call = self.calls.get(str(run_id))
            if call is None:
                return
            message = response.generations[0][0].message
            call["end"] = time.perf_counter()
            call["usage"] = getattr(message, "usage_metadata", None) or {}
            call["text"] = message.text if isinstance(message.text, str) else str(message.text)
            call["tool_args"] = [json.dumps(c.get("args"), ensure_ascii=False)
                                 for c in message.tool_calls]

    return Probe


def turn_rows(case_id: str, calls: list[dict[str, Any]], response: dict[str, Any],
              count) -> list[dict[str, Any]]:
    rows = []
    ids_json = json.dumps(response.get("evidence_ids") or [])
    for index, call in enumerate(calls, start=1):
        usage = call.get("usage") or {}
        details = usage.get("input_token_details") or {}
        text = call.get("text") or ""
        rows.append({
            "case": case_id,
            "turn": index,
            "input_tokens": usage.get("input_tokens"),
            "cached_tokens": details.get("cache_read"),
            "output_tokens": usage.get("output_tokens"),
            "first_token_seconds": (
                round(call["first"] - call["start"], 3) if call["first"] else None),
            "seconds": round(call.get("end", call["start"]) - call["start"], 3),
            "tool_arg_tokens": sum(count(arg) for arg in call.get("tool_args") or []),
            "text_tokens": count(text),
            "evidence_ids_tokens": count(ids_json) if '"evidence_ids"' in text else 0,
        })
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def median(key, pick=lambda r: True):
        values = [r[key] for r in rows if pick(r) and r.get(key) is not None]
        return statistics.median(values) if values else None

    cached = [r["cached_tokens"] / r["input_tokens"] for r in rows
              if r.get("input_tokens") and r.get("cached_tokens") is not None]
    out = sum(r["output_tokens"] or 0 for r in rows)
    seconds = sum(r["seconds"] for r in rows)
    return {
        "turns": len(rows),
        "cases": len({r["case"] for r in rows}),
        "cache_share_median": statistics.median(cached) if cached else None,
        "input_tokens_median": median("input_tokens"),
        "first_token_seconds_median": median("first_token_seconds"),
        "tool_turn_output_median": median("output_tokens", lambda r: r["tool_arg_tokens"] > 0),
        "answer_turn_output_median": median("output_tokens", lambda r: r["text_tokens"] > 0),
        "output_tokens_per_second": round(out / seconds, 1) if seconds else None,
        "output_share": {
            "tool_args": round(sum(r["tool_arg_tokens"] for r in rows) / out, 3) if out else None,
            "evidence_ids": round(sum(r["evidence_ids_tokens"] for r in rows) / out, 3)
            if out else None,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--per-family", type=int, default=1)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    import tiktoken

    from src import chatbot  # 접속이 필요한 모듈은 실행할 때만 가져옵니다.

    encoding = tiktoken.get_encoding("o200k_base")
    probe = probe_class()()
    chatbot.agent_llm.callbacks = [probe]
    cases = json.loads(CASES.read_text(encoding="utf-8-sig"))["cases"]
    seen: dict[str, int] = {}
    picked = []
    for case in cases:
        if seen.get(case["category"], 0) < args.per_family:
            seen[case["category"]] = seen.get(case["category"], 0) + 1
            picked.append(case)

    rows = []
    for case in picked:
        probe.calls.clear()
        response, _ = chatbot.ask(case["question"], [])
        calls = sorted(probe.calls.values(), key=lambda c: c["start"])
        rows += turn_rows(case["id"], calls, response, lambda s: len(encoding.encode(s or "")))
        mine = [(r["output_tokens"], r["seconds"]) for r in rows if r["case"] == case["id"]]
        print(case["id"], mine, flush=True)
    report = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
              "model": chatbot.llm_model, "summary": summarize(rows), "rows": rows}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
