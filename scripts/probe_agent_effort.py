"""답 쓰는 에이전트의 추론 강도를 로컬 앱(Aura)에서 번갈아 바꿔 잽니다. 질문당 1~3센트.

    uv run --locked python scripts/probe_agent_effort.py reports/agent_effort_probe.json
    uv run --locked python scripts/probe_agent_effort.py out.json --efforts default low none

판정은 이미 추론을 끄고 부릅니다(ADR-089). 여기서는 **에이전트만** 바꿉니다 - `chatbot.agent`를
같은 도구·지시문·구조화 응답으로 다시 만들되 모델 복사본의 `reasoning`만 다르게 둡니다.

로컬 벽시계는 공급자 속도로 실행마다 두 배까지 흔들립니다. 그래서 **질문마다 강도를 번갈아**
묻습니다(같은 질문의 세 강도가 몇십 초 안에 붙어 있습니다). 시간보다 턴 수, 턴마다의 시간과
출력 토큰, 재시도, 근거가 정답을 맞혔는지를 먼저 보세요.
"""

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")
from langchain.agents import create_agent  # noqa: E402
from langchain.agents.structured_output import ProviderStrategy  # noqa: E402

from src import chatbot  # noqa: E402

CASES = Path("evals/cases_graph.json")
RECENT = [
    ("recent-gnn-en", "Recommend recent papers on predicting molecular properties with graph "
                      "neural networks"),
    ("recent-rlhf-ko", "최근에 나온 RLHF 관련 논문 알려줘"),
]


def agent_with(effort):
    model = chatbot.llm if effort == "default" else chatbot.llm.model_copy(
        update={"reasoning": {"effort": effort}})
    return create_agent(
        model=model,
        tools=[chatbot.resolve_paper, chatbot.search_graph, chatbot.search_papers],
        system_prompt=chatbot.agent_system,
        response_format=ProviderStrategy(chatbot.GroundedAnswer, strict=True),
    )


def questions(per_family):
    seen = defaultdict(int)
    picked = []
    for case in json.loads(CASES.read_text(encoding="utf-8"))["cases"]:
        if seen[case["category"]] < per_family:
            seen[case["category"]] += 1
            picked.append((case["id"], case["question"], set(case["expected_ids"])))
    return picked + [(case, question, None) for case, question in RECENT]


def probe(case, question, expected):
    started = time.perf_counter()
    try:
        response, _ = chatbot.ask(question, [])
    except Exception as exc:
        # 기권도 결과입니다. 그래프 기록은 남지 않으므로 시간만 적습니다.
        return {"id": case, "error": type(exc).__name__,
                "total": round(time.perf_counter() - started, 2)}
    trace = response["graph_trace"]
    inside = response.get("generate_trace") or {}
    evidence = set(response.get("evidence_ids") or [])
    return {
        "id": case,
        "total": trace["total_seconds"],
        "generate": trace["seconds"].get("generate"),
        "check_conditions": trace["seconds"].get("check_conditions"),
        "attempts": max(node["attempt"] for node in trace["nodes"]),
        "rejected": [node["rejected"] for node in trace["nodes"] if node.get("rejected")],
        "model_turns": inside.get("model_turns"),
        "turns": inside.get("turns"),
        "calls": [call["name"] for call in response.get("tool_calls", [])
                  if call.get("by") != "app"],
        # 근거가 정답 집합 안에 있는가(정밀도)와 정답을 하나라도 짚었는가.
        # "최근" 질문은 정답이 없습니다.
        "evidence_precision": (round(len(evidence & expected) / len(evidence), 3)
                               if expected is not None and evidence else None),
        "hit": bool(evidence & expected) if expected is not None else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path)
    parser.add_argument("--efforts", nargs="+", default=["default", "low", "none"])
    parser.add_argument("--per-family", type=int, default=2)
    args = parser.parse_args()

    agents = {effort: agent_with(effort) for effort in args.efforts}
    rows = []
    for index, (case, question, expected) in enumerate(questions(args.per_family)):
        # 질문마다 순서를 돌려, 늘 먼저 묻는 강도가 생기지 않게 합니다.
        order = args.efforts[index % len(args.efforts):] + args.efforts[:index % len(args.efforts)]
        for effort in order:
            chatbot.agent = agents[effort]
            row = {"effort": effort, **probe(case, question, expected)}
            rows.append(row)
            print(effort, case, row.get("total"), row.get("error") or "", "att",
                  row.get("attempts"), "turns", row.get("model_turns"), row.get("rejected"),
                  "hit", row.get("hit"), flush=True)
    args.output.write_text(json.dumps({"efforts": args.efforts, "results": rows},
                                      ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
