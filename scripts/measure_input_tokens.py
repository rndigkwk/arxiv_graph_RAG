"""한 답변의 입력 토큰이 **어디에 쓰이는지** 나눠 셉니다.

    uv run --group dev python scripts/measure_input_tokens.py --report reports/routing_after2.json

[ADR-025](../docs/adr/ADR-025-reject-context-compression.md)가 컨텍스트 압축을 기각한 근거입니다.
압축은 **검색 문맥**을 줄이는 기법이므로, 그것이 입력의 몇 퍼센트인지 모르면 상한을 모릅니다.

API를 부르지 않습니다. 이미 받아 둔 보고서의 `chat_input_tokens`를 분모로 쓰고, 분자는
`tiktoken`으로 직접 셉니다 — 검색 문맥(`contexts`), 라우팅 힌트, 질문, 그리고 호출마다
다시 실리는 고정분(시스템 프롬프트 + 도구 설명).

**고정분은 캐시 할인을 받습니다.** 그 비율은 여기서 셀 수 없습니다(보고서에 캐시 항목이
있어야 합니다). `evals/run.py`의 `chat_input_tokens_fresh`가 있으면 같이 적습니다.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics as st
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `prompts`를 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

OUTPUT = Path("reports/input_tokens.json")
ENCODING = "o200k_base"
TOOL_NAMES = ("translate_to_english", "search_graph", "search_papers", "rank_related_papers")


def encoder():
    import tiktoken

    return tiktoken.get_encoding(ENCODING)


def schema_json() -> str:
    """Read the schema literal out of chatbot.py without importing it.

    `import chatbot`은 Aura와 OpenAI에 접속합니다. 토큰을 세는 데 접속이 필요하면 안 되므로
    모듈 본문에서 그 리터럴만 떼어 실행합니다.
    """
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    start = source.index("graph_schema = {")
    end = source.index("\n}\n", start) + 3
    namespace: dict[str, Any] = {}
    exec(source[start:end], namespace)  # noqa: S102 - 저장소 자신의 리터럴입니다.
    return json.dumps(namespace["graph_schema"], ensure_ascii=False)


def tool_docs() -> dict[str, str]:
    """도구 설명도 호출마다 실립니다. 본문에서 docstring만 긁어 옵니다."""
    source = Path("src/chatbot.py").read_text(encoding="utf-8")
    found = {}
    for name in TOOL_NAMES:
        match = re.search(rf"def {name}\(.*?\):\s+\"\"\"(.*?)\"\"\"", source, re.S)
        found[name] = match.group(1) if match else ""
    return found


def fixed_payload(enc) -> dict[str, Any]:
    from src import prompts

    system = (
        prompts.AGENT_SYSTEM
        .replace("{schema}", schema_json())
        .replace("{cypher_rules}", prompts.CYPHER_RULES)
    )
    docs = {name: len(enc.encode(text)) for name, text in tool_docs().items()}
    return {
        "system_prompt": len(enc.encode(system)),
        "schema_json": len(enc.encode(schema_json())),
        "cypher_rules": len(enc.encode(prompts.CYPHER_RULES)),
        "tool_docs": docs,
        "tool_docs_total": sum(docs.values()),
        "per_call": len(enc.encode(system)) + sum(docs.values()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="입력 토큰의 구성을 나눠 셉니다.")
    parser.add_argument("--report", type=Path, default=Path("reports/routing_after2.json"))
    parser.add_argument("--cases", type=Path, default=Path("evals/cases_graph.json"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    from pipeline.console import use_utf8_stdout
    from src.routing import classify

    use_utf8_stdout()
    enc = encoder()
    cases = {
        case["id"]: case
        for case in json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    }
    report = json.loads(args.report.read_text(encoding="utf-8"))

    totals: dict[str, list[int]] = {"total": [], "contexts": [], "hint": [], "question": []}
    fresh: list[int] = []
    for row in report.get("results", []):
        row = row.get("graph_rag", row)
        if row.get("chat_input_tokens") is None:
            continue
        case = cases.get(row["id"])
        if case is None:
            continue
        totals["total"].append(row["chat_input_tokens"])
        totals["contexts"].append(sum(len(enc.encode(c)) for c in row.get("contexts", [])))
        totals["hint"].append(len(enc.encode(classify(case["question"]).hint)))
        totals["question"].append(len(enc.encode(case["question"])))
        if row.get("chat_input_tokens_fresh") is not None:
            fresh.append(row["chat_input_tokens_fresh"])

    if not totals["total"]:
        print(f"{args.report}에 토큰이 기록된 행이 없습니다.")
        return 1

    p50 = {key: round(st.median(values)) for key, values in totals.items()}
    # 나머지는 뺄셈입니다. 누적 메시지와 응답 형식 스키마가 여기 들어갑니다.
    p50["rest"] = p50["total"] - p50["contexts"] - p50["hint"] - p50["question"]
    out = {
        "generated_at": datetime.now(UTC).isoformat(),
        "report": str(args.report).replace("\\", "/"),
        "cases": len(totals["total"]),
        "encoding": ENCODING,
        "p50": p50,
        "contexts_share": round(p50["contexts"] / p50["total"], 4),
        "rest_share": round(p50["rest"] / p50["total"], 4),
        "fixed": fixed_payload(enc),
        "fresh_p50": round(st.median(fresh)) if fresh else None,
        "note": (
            "contexts가 컨텍스트 압축이 손대는 부분입니다. rest는 호출마다 다시 실리는 "
            "고정분과 누적 메시지이고, 접두사가 같아 캐시 할인을 받습니다(ADR-025)."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
