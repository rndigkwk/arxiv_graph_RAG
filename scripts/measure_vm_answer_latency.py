"""배포된 VM API에서 답변 지연을 잽니다 — 질문을 실제로 보내므로 **돈이 듭니다**(질문당 1센트 안팎).

    uv run --locked python scripts/measure_vm_answer_latency.py
    uv run --locked python scripts/measure_vm_answer_latency.py --per-family 3
    uv run --locked python scripts/measure_vm_answer_latency.py --recent

목표는 p50 10초입니다(2026-10-27). 로컬 측정(`reports/inside_generate_2026-10-26.json`, Aura,
30문항)은 VM으로 옮기기 전 것이고, VM 첫날(`reports/vm_day1.json`)은 질문 하나가 30~42초였습니다 -
그때는 Streamlit이 같은 VM에서 돌았고 스왑을 썼습니다. **지금 VM의 수를 모르는 채로 흐름을 고치면
무엇이 효과였는지 가를 수 없습니다.** 그래서 먼저 잽니다.

**로컬과 같은 질문을 씁니다**(`evals/cases_graph.json`의 가족마다 앞 N개). 문항이 같아야 두 수를
나란히 놓을 수 있습니다. 서버가 잰 시간(`graph_trace`)과 브라우저 쪽에서 잰 시간을 따로 적습니다 -
둘의 차이가 네트워크·SSE·Caddy입니다.

**상한(ADR-081)을 지킵니다.** IP마다 분당 3회라 요청 사이를 21초 띄우고, 429가 오면
`Retry-After`만큼 기다립니다. 하루 30회 상한이 있으므로 한 번에 30문항을 넘기지 않습니다.

`--recent`는 그래프 가족 대신 "최근" 질문 6개(`probe_recent_questions.QS`)를 묻습니다.
ADR-090·091과 같은 문항입니다. 15문항 세트에는 이 질문이 없습니다.

**한국어 질문은 번역까지 잽니다(ADR-108).** 웹 화면은 한글이 든 질문의 답을 받자마자
`/api/translate`를 부르고, 사람은 그 번역이 와야 한국어 답을 읽습니다. `/api/ask`만 재면 그 몫이
빠집니다. 화면과 같은 규칙(질문에 한글이 있으면)으로 번역을 부르고, 받는 쪽 답 시간에 번역을 더한
`korean_ready_seconds`를 따로 적습니다. 게이트(서버 p50)는 그대로입니다. 15문항 세트는 모두 영어라
번역하지 않습니다 - `--examples`가 질문 화면의 한국어 예시를 묻습니다.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API = "https://8-230-25-138.sslip.io"
CASES = Path("evals/cases_graph.json")
LOCAL = Path("reports/inside_generate_2026-10-26.json")
OUTPUT = Path("reports/vm_answer_latency.json")
RECENT_OUTPUT = Path("reports/vm_answer_latency_recent.json")
EXAMPLES_OUTPUT = Path("reports/vm_answer_latency_examples.json")
WORKBENCH = Path("web/components/Workbench.tsx")
# 웹 화면의 `hasHangul`(web/lib/answer.ts)과 같은 범위입니다. 화면이 번역하는 질문만
# 번역하려면 같아야 합니다.
HANGUL = re.compile("[ㄱ-ㆎ가-힣]")
MAX_TRANSLATE_CHARS = 8000  # src/api.py와 같습니다. 넘으면 화면도 부르지 않습니다.
GAP_SECONDS = 21  # 분당 3회 상한 아래로.
TARGET_P50 = 10.0


def percentile(values: list[float], fraction: float) -> float | None:
    """정렬 후 위치로 읽습니다(`measure_answer_latency.py`와 같은 방식). 보간하지 않습니다."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3)


def pick_cases(cases: list[dict[str, Any]], per_family: int) -> list[dict[str, Any]]:
    seen: dict[str, int] = defaultdict(int)
    picked = []
    for case in cases:
        if seen[case["category"]] < per_family:
            seen[case["category"]] += 1
            picked.append(case)
    return picked


def recent_cases() -> list[dict[str, Any]]:
    """ADR-090·091이 잰 "최근" 질문 6개입니다. 문항이 같아야 10-28의 수와 나란히 놓습니다.

    `probe_recent_questions`는 가져오는 순간 앱(`chatbot`)을 띄우므로, 목록만 글로 읽습니다.
    """
    import ast

    tree = ast.parse(Path("scripts/probe_recent_questions.py").read_text(encoding="utf-8"))
    qs = next(node.value for node in tree.body if isinstance(node, ast.Assign)
              and getattr(node.targets[0], "id", None) == "QS")
    return [{"id": case, "category": "recent", "question": question}
            for case, question in ast.literal_eval(qs)]


def example_cases() -> list[dict[str, Any]]:
    """질문 화면의 예시(`EXAMPLES`)입니다. 사람이 누르는 질문이라 한국어 쪽 지연을 이것으로 봅니다.

    화면 코드를 글로 읽습니다 - 목록을 여기 베끼면 화면이 바뀔 때 조용히 갈라집니다.
    """
    text = WORKBENCH.read_text(encoding="utf-8")
    block = re.search(r"const EXAMPLES = \[(.*?)\];", text, re.DOTALL)
    if not block:
        raise SystemExit(f"{WORKBENCH}에서 EXAMPLES를 찾지 못했습니다")
    questions = re.findall(r'"([^"]+)"', block.group(1))
    return [{"id": f"example-{n}", "category": "example", "question": q}
            for n, q in enumerate(questions, 1)]


def needs_translation(question: str) -> bool:
    return bool(HANGUL.search(question))


def read_events(lines) -> list[tuple[str, dict[str, Any], float]]:
    """SSE를 (이벤트, 데이터, 받은 시각)으로. 받은 시각은 첫 부분 답을 재는 데 씁니다."""
    events, kind, data = [], None, []
    for raw in lines:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
        elif not line and kind:
            events.append((kind, json.loads("\n".join(data)), time.perf_counter()))
            kind, data = None, []
    return events


def ask(api: str, question: str) -> dict[str, Any]:
    body = json.dumps({"question": question, "history": []}).encode("utf-8")
    while True:
        request = urllib.request.Request(api + "/api/ask", data=body,
                                         headers={"content-type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                events = read_events(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("retry-after") or 60)
                print(f"  상한에 걸려 {wait}초 기다립니다", flush=True)
                time.sleep(wait)
                continue
            raise
        break
    finished = time.perf_counter()
    first = next((at for kind, _, at in events if kind == "partial"), None)
    kind, data, _ = events[-1]
    return {
        "outcome": kind,
        "client_seconds": round(finished - started, 3),
        "first_partial_seconds": round(first - started, 3) if first else None,
        "graph_trace": data.get("graph_trace") if kind == "final" else None,
        "generate_trace": data.get("generate_trace") if kind == "final" else None,
        "condition_timing": data.get("condition_timing") if kind == "final" else None,
        "tools": [{"name": c.get("name"), "seconds": c.get("seconds"), "by": c.get("by")}
                  for c in data.get("tool_calls", [])] if kind == "final" else [],
        "detail": None if kind == "final" else data,
        # 번역에 넘길 원문입니다. 보고서에는 남기지 않습니다(`main`이 꺼냅니다).
        "answer_text": data.get("answer") if kind == "final" else None,
    }


def translate(api: str, text: str) -> dict[str, Any]:
    """화면처럼 답 원문을 번역합니다. 번역은 질문과 따로 센 상한을 씁니다(ADR-108)."""
    body = json.dumps({"text": text}).encode("utf-8")
    while True:
        request = urllib.request.Request(api + "/api/translate", data=body,
                                         headers={"content-type": "application/json"})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = int(exc.headers.get("retry-after") or 60)
                print(f"  번역 상한에 걸려 {wait}초 기다립니다", flush=True)
                time.sleep(wait)
                continue
            return {"translate_seconds": None, "translate_error": exc.code}
        return {"translate_seconds": round(time.perf_counter() - started, 3)}


def translation_summary(done: list[dict[str, Any]]) -> dict[str, Any]:
    """한국어 답이 화면에 뜨기까지. 목표 10초와 견주되 **보여 주기만 합니다**.

    게이트는 서버 p50입니다.
    """
    timed = [row for row in done if row.get("translate_seconds") is not None]
    ready = [row["client_seconds"] + row["translate_seconds"] for row in timed]
    return {
        "translated": len(timed),
        "of": len(done),
        "translate_seconds_p50": percentile([row["translate_seconds"] for row in timed], 0.5),
        "korean_ready_seconds": {"p50": percentile(ready, 0.5), "p90": percentile(ready, 0.9),
                                 "max": round(max(ready), 3) if ready else None},
        "korean_ready_within_target": sum(1 for value in ready if value <= TARGET_P50),
    }


def local_totals(report: dict[str, Any] | None) -> dict[str, float]:
    if not report:
        return {}
    return {row["id"]: row["graph_trace"]["total_seconds"]
            for row in report.get("results", []) if row.get("graph_trace")}


def within_target(done: list[dict[str, Any]]) -> dict[str, Any]:
    """목표(10초) 안에 끝난 답의 몫과, 넘긴 문항입니다. **보여 주기만 합니다** - 게이트는 p50입니다.

    p50이 목표 안이어도 절반 가까이가 넘을 수 있습니다. 넘긴 문항을 느린 순으로 적어 어디를
    볼지 바로 고르게 합니다.
    """
    over = sorted(({"id": row["id"], "seconds": row["graph_trace"]["total_seconds"]}
                   for row in done if row["graph_trace"]["total_seconds"] > TARGET_P50),
                  key=lambda item: -item["seconds"])
    count = len(done) - len(over)
    return {"count": count, "of": len(done),
            "share": round(count / len(done), 3) if done else None, "over": over}


def summarize(rows: list[dict[str, Any]], local: dict[str, float]) -> dict[str, Any]:
    """서버가 잰 합계가 목표의 기준입니다. 브라우저 쪽 시간은 그 위에 얹히는 몫을 보려고 둡니다."""
    done = [row for row in rows if row["graph_trace"]]
    server = [row["graph_trace"]["total_seconds"] for row in done]
    nodes: dict[str, list[float]] = defaultdict(list)
    for row in done:
        for node, value in row["graph_trace"]["seconds"].items():
            nodes[node].append(value)
    inside = [row["generate_trace"] for row in done
              if row.get("generate_trace") and row["generate_trace"].get("adds_up")]
    tools: dict[str, list[float]] = defaultdict(list)
    for row in done:
        for call in row["tools"]:
            if call["seconds"] is not None:
                tools[call["name"]].append(call["seconds"])
    paired = [(local[row["id"]], row["graph_trace"]["total_seconds"])
              for row in done if row["id"] in local]
    total = sum(server) or 1.0
    families: dict[str, list[float]] = defaultdict(list)
    for row in done:
        families[row["category"]].append(row["graph_trace"]["total_seconds"])
    return {
        "asked": len(rows),
        "answered": len(done),
        "not_answered": [{"id": row["id"], "outcome": row["outcome"]} for row in rows
                         if not row["graph_trace"]],
        "server_seconds": {"p50": percentile(server, 0.5), "p90": percentile(server, 0.9),
                           "max": max(server, default=None)},
        "client_seconds": {"p50": percentile([r["client_seconds"] for r in done], 0.5),
                           "p90": percentile([r["client_seconds"] for r in done], 0.9)},
        "first_partial_seconds_p50": percentile(
            [r["first_partial_seconds"] for r in done if r["first_partial_seconds"]], 0.5),
        "target_p50": TARGET_P50,
        "meets_target": (percentile(server, 0.5) or 1e9) <= TARGET_P50,
        "within_target": within_target(done),
        "translation": translation_summary(done),
        "nodes": sorted(({"node": node, "p50": percentile(values, 0.5),
                          "share": round(sum(values) / total, 3)}
                         for node, values in nodes.items()), key=lambda n: -n["share"]),
        "inside_generate": {
            "traced": len(inside),
            "model_turns_mean": round(statistics.mean(t["model_turns"] for t in inside), 2)
            if inside else None,
            "seconds_per_turn_p50": percentile(
                [t["model_seconds"] / t["model_turns"] for t in inside if t["model_turns"]], 0.5),
            "model_share": round(sum(t["model_seconds"] for t in inside)
                                 / (sum(t["generate_seconds"] for t in inside) or 1), 3),
            "tools_share": round(sum(t["tools_seconds"] for t in inside)
                                 / (sum(t["generate_seconds"] for t in inside) or 1), 3),
        },
        "tools": {name: {"calls": len(v), "p50": percentile(v, 0.5)} for name, v in tools.items()},
        "judge": judge_summary(done),
        "last_turn": last_turn_summary(inside),
        "families_p50": {name: percentile(v, 0.5) for name, v in sorted(families.items())},
        # 같은 문항의 로컬(Aura, 2026-10-26) 합계와 나란히. 짝이 없으면 비교하지 않습니다.
        "vs_local": {
            "pairs": len(paired),
            "local_p50": percentile([a for a, _ in paired], 0.5),
            "vm_p50": percentile([b for _, b in paired], 0.5),
        },
    }


def judge_summary(done: list[dict[str, Any]]) -> dict[str, Any]:
    """판정 자체 시간과, 답 글 뒤에 숨은 몫 / 기다린 몫(ADR-085의 겹치기가 얼마나 먹혔는가)."""
    timed = [row["condition_timing"] for row in done
             if (row.get("condition_timing") or {}).get("judge_seconds") is not None]
    return {
        "early": sum(1 for row in done if (row.get("condition_timing") or {}).get("early")),
        "timed": len(timed),
        **{f"{key}_p50": percentile([t[key] for t in timed], 0.5)
           for key in ("judge_seconds", "ahead_seconds", "waited_seconds")},
    }


def last_turn_summary(inside: list[dict[str, Any]]) -> dict[str, Any]:
    """마지막 모델 턴(답 글을 쓰는 턴)과 그 앞 턴들. 턴 기록이 없는 옛 서버면 비웁니다."""
    turns = [t["turns"] for t in inside if t.get("turns")]
    tokens = [each[-1]["output_tokens"] for each in turns
              if each[-1].get("output_tokens") is not None]
    return {
        "traced": len(turns),
        "last_seconds_p50": percentile([each[-1]["seconds"] for each in turns], 0.5),
        "earlier_seconds_p50": percentile(
            [turn["seconds"] for each in turns for turn in each[:-1]], 0.5),
        "last_output_tokens_p50": percentile(tokens, 0.5),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default=API)
    parser.add_argument("--per-family", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recent", action="store_true", help="'최근' 질문 6개를 묻습니다")
    parser.add_argument("--examples", action="store_true",
                        help="질문 화면의 한국어 예시를 묻습니다(번역까지 잽니다)")
    args = parser.parse_args()
    if args.recent and args.examples:
        raise SystemExit("--recent와 --examples는 함께 쓰지 않습니다")
    args.output = args.output or (
        RECENT_OUTPUT if args.recent else EXAMPLES_OUTPUT if args.examples else OUTPUT)

    if args.recent:
        cases = recent_cases()
    elif args.examples:
        cases = example_cases()
    else:
        cases = pick_cases(json.loads(CASES.read_text(encoding="utf-8"))["cases"],
                           args.per_family)
    if len(cases) > 30:
        raise SystemExit("하루 상한(30회)을 넘깁니다. --per-family를 줄이세요.")
    health = json.loads(urllib.request.urlopen(args.api + "/api/health", timeout=30).read())
    rows = []
    for index, case in enumerate(cases):
        if index:
            time.sleep(GAP_SECONDS)
        result = ask(args.api, case["question"])
        answer = result.pop("answer_text", None)
        # 화면과 같은 규칙입니다: 한국어 질문의 답이 오면 바로 부르고, 너무 길면 부르지 않습니다.
        if answer and needs_translation(case["question"]) and len(answer) <= MAX_TRANSLATE_CHARS:
            result.update(translate(args.api, answer))
        rows.append({"id": case["id"], "category": case["category"], **result})
        server = (result["graph_trace"] or {}).get("total_seconds")
        extra = (f" · 번역 {result['translate_seconds']}초"
                 if result.get("translate_seconds") is not None else "")
        print(f"{index + 1}/{len(cases)} {case['id']}: {result['outcome']} "
              f"서버 {server}초 · 받는 쪽 {result['client_seconds']}초{extra}", flush=True)

    local = local_totals(json.loads(LOCAL.read_text(encoding="utf-8")) if LOCAL.exists() else None)
    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "api": args.api,
        "commit": health.get("commit"),
        "module_commit": health.get("module_commit"),
        "dataset": ("scripts/probe_recent_questions.py" if args.recent
                    else f"{WORKBENCH} EXAMPLES" if args.examples else str(CASES)),
        "summary": summarize(rows, local),
        "results": rows,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    inside = report["summary"]["within_target"]
    if inside["of"]:
        print(f"{TARGET_P50:g}초 안: {inside['count']}/{inside['of']} ({inside['share']:.0%})"
              + ("" if not inside["over"] else " · 넘긴 문항: " + ", ".join(
                  f"{item['id']} {item['seconds']:.1f}초" for item in inside["over"])))
    korean = report["summary"]["translation"]
    if korean["translated"]:
        ready = korean["korean_ready_seconds"]
        print(f"한국어 답이 뜨기까지(답 + 번역): p50 {ready['p50']}초 · p90 {ready['p90']}초 · "
              f"{TARGET_P50:g}초 안 {korean['korean_ready_within_target']}/{korean['translated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
