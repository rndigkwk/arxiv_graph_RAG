"""'최근' 질문을 로컬 앱(Aura)으로 물어 거절·턴·시간을 셉니다(ADR-090). 질문당 1~3센트.

    uv run python scripts/probe_recent_questions.py reports/recent_questions_after.json

로컬의 벽시계 시간은 OpenAI 속도에 따라 실행마다 두 배까지 흔들립니다. 두 버전을 견줄 때는
워크트리 둘에서 **동시에** 돌리고, 시간보다 재시도·턴 수를 먼저 보세요.
"""

import json
import sys
import time

sys.path.insert(0, ".")
from src import chatbot  # noqa: E402

QS = [
    ("gnn-en", "Recommend recent papers on predicting molecular properties with graph neural "
               "networks"),
    ("gnn-ko", "그래프 신경망으로 분자 특성을 예측하는 최근 논문 추천해줘"),
    ("diffusion-en", "What are the latest papers on diffusion models for image generation?"),
    ("rlhf-ko", "최근에 나온 RLHF 관련 논문 알려줘"),
    ("agents-en", "Show me recent work on LLM agents that use tools"),
    ("retrieval-ko", "검색 증강 생성(RAG)에 관한 최신 연구는?"),
]


def probe(case, question):
    started = time.perf_counter()
    try:
        response, _ = chatbot.ask(question, [])
    except Exception as exc:
        # 기권도 결과입니다. 그래프 기록은 남지 않으므로 시간만 적습니다.
        return {"id": case, "error": type(exc).__name__,
                "total": round(time.perf_counter() - started, 2)}
    nodes = response["graph_trace"]["nodes"]
    verdict = response.get("condition_validation") or {}
    return {
        "id": case,
        "total": response["graph_trace"]["total_seconds"],
        "rejected": [node["rejected"] for node in nodes if node.get("rejected")],
        "turns": (response.get("generate_trace") or {}).get("model_turns"),
        "attempts": max(node["attempt"] for node in nodes),
        "calls": [call["name"] for call in response.get("tool_calls", [])
                  if call.get("by") != "app"],
        "abstained": response.get("abstained"),
        "conditions": verdict.get("graph_conditions"),
        "cyphers": response.get("cyphers"),
        "evidence": response.get("evidence_ids"),
    }


def main():
    rows = []
    for case, question in QS:
        row = probe(case, question)
        rows.append(row)
        print(case, row.get("total"), row.get("error") or "", "att", row.get("attempts"),
              "turns", row.get("turns"), row.get("rejected"), row.get("calls"), flush=True)
    with open(sys.argv[1], "w", encoding="utf-8") as handle:
        json.dump(rows, handle, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
