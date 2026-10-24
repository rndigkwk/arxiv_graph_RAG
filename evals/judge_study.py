"""조건 판정 심판만 따로 재는 하네스입니다.

    uv run python -m evals.judge_study
    uv run python -m evals.judge_study --output reports/judge_study_after.json

`candidate_validation.judge_content`의 프롬프트를 고칠 때는 양쪽을 함께 봐야 합니다. 2026-10-13에
`requires_graph` 오탐을 잡으려고 한 문장을 넣었다가 `requires_content` 정확도가 21/21에서
11/21로 떨어진 적이 있습니다. 두 판정이 한 호출에 묶여 있어서 한쪽 설명이 다른 쪽으로 번집니다.

그래서 에이전트 전체를 돌리지 않고 심판 호출만 잽니다. 질문당 구조화 호출 한 번이라 싸고,
혼동 행렬이 바로 나옵니다. 라벨은 질문 문장에서 기계적으로 정합니다.

- 제목·주제만 묻는 질문: 내용 조건 있음, 그래프 조건 없음
- 저자·인용·분류·집계만 묻는 질문: 내용 조건 없음, 그래프 조건 있음

두 번째 묶음이 핵심입니다. "X를 인용한 논문은?"에서 제목 X는 **인용 대상**을 가리키며, 돌려줄
논문이 X에 관한 내용이어야 한다는 뜻이 아닙니다.

**공급자를 바꿔 다시 돌릴 수 있습니다**(`--provider anthropic`, ADR-058). 라벨이 기계적으로
있으므로 공급자를 바꾸면 일치율이 아니라 **정확도**가 나옵니다 - RAGAS 심판에는 정답이 없어
그쪽에서는 같은 일을 해도 "어느 쪽이 맞는지"를 말할 수 없습니다. 바는 ADR-058에 미리 적었고
`compare()`가 그 바를 그대로 적용합니다.
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUTPUT = Path("reports/judge_study.json")
SAMPLE_PAPERS = 2

# ── 공급자 ───────────────────────────────────────────────────────────────
# 프롬프트·스키마·붙이는 논문은 양쪽이 같습니다. 다른 것은 공급자 하나입니다.
PROVIDERS = ("openai", "anthropic")
# `.env`의 변수명입니다. GitHub Secrets에는 넣지 않습니다 - 로컬 평가 전용입니다.
ANTHROPIC_KEY = "CLAUDE"
# 조직 키는 워크스페이스에 묶이지 않아 400을 돌려줍니다(2026-10-22에 그렇게 걸렸습니다).
# 그때 이 헤더가 필요합니다. 워크스페이스 전용 키를 쓰면 비워 둬도 됩니다.
ANTHROPIC_WORKSPACE_KEY = "CLAUDE_WORKSPACE_ID"
# 워크스페이스 ID의 접두사입니다. 조직 ID는 UUID로 모양이 다릅니다.
ANTHROPIC_WORKSPACE_PREFIX = "wrkspc_"
ANTHROPIC_MODEL = "claude-haiku-5-5"
# 이 모델은 **사고가 기본으로 켜지고** 그 토큰이 출력 한도를 먹습니다. 한도가 작으면 구조화
# 도구 호출이 잘려 "판정이 불완전합니다"로 죽습니다. 넉넉히 둡니다.
ANTHROPIC_MAX_TOKENS = 8192
# 쌍체 비교의 유의수준입니다. 몇 건이 갈려야 "다르다"인지는 세어 구합니다(needed_one_way).
ALPHA = 0.05


def labelled_questions(graph_cases: Path, base_cases: Path) -> list[dict[str, Any]]:
    """Build the labelled set from the existing case files; nothing is hand-written here."""
    questions = []
    graph = json.loads(graph_cases.read_text(encoding="utf-8"))["cases"]
    for case in graph:
        questions.append({
            "id": case["id"],
            "family": case["category"],
            "question": case["question"],
            "paper_ids": case["expected_ids"][:SAMPLE_PAPERS],
            "expect_content": False,
            "expect_graph": True,
            "why": (
                "질문의 제목·분류는 관계의 상대편을 가리킵니다. 돌려줄 논문의 주제 조건이 "
                "아닙니다."
            ),
        })
    base = json.loads(base_cases.read_text(encoding="utf-8-sig"))
    for case in base["cases"] if isinstance(base, dict) else base:
        questions.append({
            "id": case["id"],
            "family": case["category"],
            "question": case["question"],
            "paper_ids": case["expected_ids"][:SAMPLE_PAPERS],
            "expect_content": True,
            "expect_graph": False,
            "why": "주제나 정확한 제목만 묻습니다. 그래프 조건이 없습니다.",
        })
    return questions


SPLITS = ("diagnostic", "final", "all")


def use_prompt(path: Path | None) -> dict[str, Any]:
    """후보 프롬프트를 끼워 넣고, 무엇으로 쟀는지 돌려줍니다(ADR-059).

    `prompts.py`를 고쳐 가며 재면 두 가지가 깨집니다 - 게이트의 `prompt_version` 규칙이
    기준선과 다른 본문을 막고(설계상 그렇습니다), 탐색 중의 본문이 앱에 들어갑니다. 그래서
    후보는 **파일로** 두고 이 연구에서만 끼웁니다.
    """
    from pipeline.prompt_registry import digest
    from src import prompts

    if path is None:
        return {"source": "prompts.JUDGE_CONTENT",
                "sha256": digest(prompts.JUDGE_CONTENT)}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"{path}가 비어 있습니다.")
    prompts.JUDGE_CONTENT = text
    print(f"후보 프롬프트를 씁니다: {path} ({digest(text)})")
    return {"source": str(path), "sha256": digest(text)}


def holdout_questions(path: Path, split: str) -> list[dict[str, Any]]:
    """보류 집합(ADR-059)에서 질문을 읽습니다. 라벨은 그 파일이 이미 갖고 있습니다.

    **최종 확인 조각은 프롬프트를 고치는 동안 보지 않습니다.** 한 번 보고 고치면 그 조각도
    튜닝 집합이 되고, 그러면 보류 집합이 소진됩니다. 그래서 고를 때 크게 적습니다.
    """
    report = json.loads(path.read_text(encoding="utf-8"))
    if "questions" not in report:
        raise SystemExit(
            f"{path}는 보류 집합이 아닙니다. evals.holdout_questions가 만든 파일이어야 "
            "합니다."
        )
    chosen = [
        item for item in report["questions"]
        if split == "all" or item.get("split") == split
    ]
    if not chosen:
        raise SystemExit(f"{path}의 {split} 조각이 비어 있습니다.")
    if split in ("final", "all"):
        print(
            "** 최종 확인 조각을 엽니다. 이것을 보고 프롬프트를 고치면 그 조각도 튜닝 "
            "집합이 됩니다(ADR-059). 마지막 한 번이어야 합니다. **"
        )
    return chosen


def judge_for(provider: str, model: str) -> tuple[Any, str]:
    """공급자의 심판 호출과 그 모델 이름을 돌려줍니다.

    토큰 누적은 양쪽이 `candidate_validation.structured_judge`를 씁니다. 공급자별로 베껴 두면 한쪽만
    고쳐지고, **다른 방식으로 센 비용**을 같은 표에 올리게 됩니다.
    """
    from src.candidate_validation import QuestionVerdict, structured_judge

    if provider == "openai":
        # 앱의 모델을 쓰므로 여기서만 `chatbot`이 필요합니다. anthropic 쪽은 앱 자격증명
        # 없이도 만들어져야 합니다 - `chatbot`은 `.env`의 Neo4j·OpenAI 값이 없으면
        # 임포트 자체가 실패하고, CI의 `test` job에는 그 값이 없습니다.
        from src import chatbot

        if model and model != chatbot.llm_model:
            raise SystemExit(
                f"openai 심판의 모델은 앱과 같은 {chatbot.llm_model}입니다. "
                f"{model}로 재려면 OPENAI_MODEL을 바꿔 돌리세요 - 그러면 기준선과 비교할 수 "
                "없는 값이 됩니다(ADR-021의 gpt-4o-mini 사고)."
            )
        return chatbot.judging_model, chatbot.llm_model

    key = (os.getenv(ANTHROPIC_KEY) or "").strip()
    if not key:
        raise SystemExit(
            f"{ANTHROPIC_KEY}가 비어 있습니다. .env에 넣고 다시 돌리세요. 값은 출력하지 "
            "않습니다."
        )
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError:
        raise SystemExit(
            "langchain-anthropic이 없습니다. 평가 전용 의존성이므로 dev 그룹에 있습니다: "
            "uv sync --group dev"
        ) from None

    model = model or ANTHROPIC_MODEL
    headers = {}
    workspace = (os.getenv(ANTHROPIC_WORKSPACE_KEY) or "").strip()
    if workspace:
        # **조직 ID와 워크스페이스 ID는 다른 값입니다.** 2026-10-22에 조직 UUID를 넣어
        # 400을 한 번 더 받았습니다. 모양이 아니면 호출하기 전에 막습니다 - 61문항을
        # 보내고 나서 알게 되는 것보다 낫습니다.
        if not workspace.startswith(ANTHROPIC_WORKSPACE_PREFIX):
            raise SystemExit(
                f"{ANTHROPIC_WORKSPACE_KEY}가 워크스페이스 ID 모양이 아닙니다. "
                f"`{ANTHROPIC_WORKSPACE_PREFIX}`로 시작해야 합니다 - 조직 ID(UUID)는 "
                "다른 값입니다. 콘솔의 Settings > Workspaces에서 워크스페이스를 열면 "
                "주소에 그 ID가 있습니다. 더 간단한 길은 그 워크스페이스 안에서 API 키를 "
                f"새로 만들어 {ANTHROPIC_KEY}에 넣는 것입니다 - 그러면 이 변수가 "
                "필요 없습니다."
            )
        headers["anthropic-workspace-id"] = workspace
    client = ChatAnthropic(
        model=model, api_key=key, max_tokens=ANTHROPIC_MAX_TOKENS,
        timeout=60, max_retries=2, default_headers=headers or None,
    )
    # `strict=`는 OpenAI 전용 인자입니다. Anthropic은 도구 호출로 스키마를 강제합니다 -
    # 공급자 교차에서 **불가피하게 다른 한 곳**이고, ADR-058이 그것을 적어 뒀습니다.
    structured = client.with_structured_output(QuestionVerdict, include_raw=True)

    def make(usage: dict[str, int]):
        return structured_judge(structured, usage)

    # **어디로 보냈는지도 적습니다.** `ANTHROPIC_BASE_URL`이 설정돼 있으면 호출이 다른
    # 게이트웨이로 갈 수 있고, 그러면 보고서의 모델 이름이 거짓이 됩니다.
    return make, f"{model}@{os.getenv('ANTHROPIC_BASE_URL') or 'api.anthropic.com'}"


def run(questions: list[dict[str, Any]], provider: str = "openai",
        model: str = "") -> tuple[list[dict[str, Any]], str]:
    """Call the judge once per question with real papers attached."""
    from src import candidate_validation, chatbot

    make_judge, judge_model = judge_for(provider, model)

    def fetch(ids: list[str]) -> list[dict[str, Any]]:
        return chatbot.run_read(
            "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids "
            "RETURN p.arxiv_id AS arxiv_id, p.title AS title, p.abstract AS abstract",
            {"ids": ids},
        )

    rows = []
    for item in questions:
        papers = fetch(item["paper_ids"])
        usage: dict[str, int] = {}
        try:
            verdict = candidate_validation.judge_content(
                item["question"], papers, make_judge(usage)
            )
        except Exception as exc:
            # **실패도 결과입니다**(ADR-058). 재시도로 메우거나 프롬프트를 이 공급자에 맞게
            # 고쳐서 숫자를 만들지 않습니다 - 고치면 더 이상 같은 심판이 아닙니다.
            hint = ""
            if "anthropic-workspace-id" in str(exc):
                hint = (
                    f" | 이 400은 **키가 워크스페이스에 묶여 있지 않다**는 뜻입니다. "
                    f"둘 중 하나를 하세요: (1) .env에 {ANTHROPIC_WORKSPACE_KEY}로 "
                    f"워크스페이스 ID를 넣기, (2) 콘솔에서 워크스페이스 전용 키를 새로 "
                    f"만들어 {ANTHROPIC_KEY}에 넣기. 프롬프트나 스키마 문제가 아닙니다."
                )
            raise SystemExit(
                f"{provider}/{judge_model}로는 같은 심판을 돌릴 수 없습니다. "
                f"{len(rows)}/{len(questions)}번째({item['id']})에서 "
                f"{type(exc).__name__}: {exc}{hint}"
            ) from None
        rows.append({
            **{key: item[key] for key in ("id", "family", "question")},
            "expect_content": item["expect_content"],
            "expect_graph": item["expect_graph"],
            "got_content": verdict["requires_content"],
            "got_graph": verdict["requires_graph"],
            "graph_conditions": verdict["graph_conditions"],
            "content_correct": verdict["requires_content"] == item["expect_content"],
            "graph_correct": verdict["requires_graph"] == item["expect_graph"],
            "tokens": dict(usage),
        })
    return rows, judge_model


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Report accuracy per judgment and per family, plus the two error directions."""
    def accuracy(selected: list[dict[str, Any]], key: str) -> float | None:
        if not selected:
            return None
        return round(sum(row[key] for row in selected) / len(selected), 4)

    families = sorted({row["family"] for row in rows})
    return {
        "questions": len(rows),
        "content_accuracy": accuracy(rows, "content_correct"),
        "graph_accuracy": accuracy(rows, "graph_correct"),
        "both_correct": accuracy(
            [{"ok": row["content_correct"] and row["graph_correct"]} for row in rows], "ok"
        ),
        "graph_false_positive": sum(
            1 for row in rows if row["got_graph"] and not row["expect_graph"]
        ),
        "graph_false_negative": sum(
            1 for row in rows if not row["got_graph"] and row["expect_graph"]
        ),
        "content_false_positive": sum(
            1 for row in rows if row["got_content"] and not row["expect_content"]
        ),
        "content_false_negative": sum(
            1 for row in rows if not row["got_content"] and row["expect_content"]
        ),
        "by_family": {
            family: {
                "questions": sum(1 for row in rows if row["family"] == family),
                "content_accuracy": accuracy(
                    [row for row in rows if row["family"] == family], "content_correct"
                ),
                "graph_accuracy": accuracy(
                    [row for row in rows if row["family"] == family], "graph_correct"
                ),
            }
            for family in families
        },
        "tokens": {
            "input": sum(row["tokens"].get("input_tokens", 0) for row in rows),
            "output": sum(row["tokens"].get("output_tokens", 0) for row in rows),
        },
    }


def sign_test_p(only_a: int, only_b: int) -> float | None:
    """불일치 쌍만 보는 양측 부호검정입니다. 갈린 쌍이 없으면 비교할 것이 없습니다."""
    total = only_a + only_b
    if total == 0:
        return None
    fewer = min(only_a, only_b)
    tail = sum(math.comb(total, i) for i in range(fewer + 1)) / (2 ** total)
    return round(min(1.0, 2 * tail), 4)


def needed_one_way() -> int:
    """한 방향으로 몇 건이 갈려야 "다르다"고 말할 수 있는지 세어 구합니다.

    ADR-058의 바는 "6건 이상"입니다. 그 숫자를 여기에 베껴 적으면 `ALPHA`를 고칠 때 한쪽만
    바뀝니다. 그래서 상수로 두지 않고 셉니다.
    """
    wrong = 1
    while True:
        value = sign_test_p(0, wrong)
        if value is not None and value < ALPHA:
            return wrong
        wrong += 1


def paired(rows: list[dict[str, Any]], baseline: list[dict[str, Any]],
           key: str) -> dict[str, Any]:
    """한 판정에 대해 쌍체 표를 만듭니다. 질문 id로 맞춥니다."""
    mine = {row["id"]: row[key] for row in rows}
    theirs = {row["id"]: row[key] for row in baseline}
    shared = sorted(set(mine) & set(theirs))
    only_mine_wrong = [qid for qid in shared if not mine[qid] and theirs[qid]]
    only_theirs_wrong = [qid for qid in shared if mine[qid] and not theirs[qid]]
    both_wrong = [qid for qid in shared if not mine[qid] and not theirs[qid]]
    probability = sign_test_p(len(only_theirs_wrong), len(only_mine_wrong))
    return {
        "questions": len(shared),
        "both_right": len(shared) - len(only_mine_wrong) - len(only_theirs_wrong)
        - len(both_wrong),
        "only_new_wrong": len(only_mine_wrong),
        "only_baseline_wrong": len(only_theirs_wrong),
        "both_wrong": len(both_wrong),
        "sign_test_p": probability,
        "verdict": _verdict(len(only_mine_wrong), len(only_theirs_wrong),
                            len(both_wrong), probability),
        "only_new_wrong_ids": only_mine_wrong,
        "only_baseline_wrong_ids": only_theirs_wrong,
        "both_wrong_ids": both_wrong,
    }


def _verdict(only_new: int, only_base: int, both: int,
             probability: float | None) -> str:
    """ADR-058에 미리 적은 세 구간을 그대로 적용합니다."""
    if only_new == 0 and only_base == 0:
        # 갈린 쌍이 없습니다. 같이 틀린 것이 있으면 공급자가 아니라 라벨이나 프롬프트를
        # 봐야 합니다 - 양쪽이 같은 질문에서 같이 틀리는 것은 공급자 차이가 아닙니다.
        return "전사됨" if both == 0 else "같이 틀림 - 라벨이나 프롬프트를 보세요"
    if probability is not None and probability < ALPHA:
        return "공급자 의존"
    # **"비슷하다"고 쓰지 않습니다.** 못 잡는다고 적는 쪽이 낫습니다(ADR-021).
    return f"구별 못 함 - 한 방향 {needed_one_way()}건부터 말할 수 있습니다"


def compare(rows: list[dict[str, Any]], path: Path) -> dict[str, Any]:
    """기준선 보고서와 쌍체로 견줍니다. 집계 하나로 덮지 않고 판정별로 봅니다."""
    report = json.loads(path.read_text(encoding="utf-8"))
    baseline = report["results"]
    missing = sorted({row["id"] for row in rows} - {row["id"] for row in baseline})
    return {
        # 기준선이 어느 집합이었는지 적습니다. 옛 보고서에는 없습니다.
        "baseline_questions_from": report.get("questions_from")
        or "기록되지 않았습니다",
        "baseline_prompt": report.get("prompt") or "기록되지 않았습니다",
        "baseline": {
            "path": str(path),
            "label": report.get("label", ""),
            # 옛 보고서에는 심판 이름이 없습니다. 비워 두고 그 사실을 적습니다 - 모르는
            # 것을 앱 기본값으로 채워 넣으면 "같은 심판으로 쟀다"는 거짓이 됩니다.
            "judge": report.get("judge") or "기록되지 않았습니다",
            "both_correct": report["summary"]["both_correct"],
        },
        "questions_missing_from_baseline": missing,
        # 2026-10-13 사고가 두 판정이 한 호출에 묶여 서로 번진다는 것을 보여줬습니다.
        # 집계 하나로 덮으면 한쪽의 악화가 다른 쪽의 개선에 상쇄돼 보입니다.
        "requires_content": paired(rows, baseline, "content_correct"),
        "requires_graph": paired(rows, baseline, "graph_correct"),
        "both": paired(
            [{"id": row["id"], "ok": row["content_correct"] and row["graph_correct"]}
             for row in rows],
            [{"id": row["id"], "ok": row["content_correct"] and row["graph_correct"]}
             for row in baseline],
            "ok",
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="조건 판정 심판의 정확도를 측정합니다.")
    parser.add_argument("--graph-cases", type=Path, default=Path("evals/cases_graph.json"))
    parser.add_argument("--base-cases", type=Path, default=Path("evals/cases.json"))
    parser.add_argument("--limit", type=int, default=0, help="0이면 전부 돌립니다.")
    parser.add_argument("--label", default="", help="보고서에 남길 이 실행의 이름입니다.")
    parser.add_argument("--provider", choices=PROVIDERS, default="openai",
                        help="심판의 공급자입니다. 프롬프트와 스키마는 그대로입니다.")
    parser.add_argument("--model", default="",
                        help=f"비우면 공급자의 기본값입니다(anthropic: {ANTHROPIC_MODEL}).")
    parser.add_argument("--compare-to", type=Path, default=None,
                        help="기준선 보고서와 쌍체로 견줍니다(ADR-058의 바를 적용합니다).")
    parser.add_argument("--holdout", type=Path, default=None,
                        help="보류 집합 파일입니다(ADR-059). 주면 사례 파일 대신 씁니다.")
    parser.add_argument("--split", choices=SPLITS, default="diagnostic",
                        help="--holdout과 함께 씁니다. 기본은 진단 조각입니다.")
    parser.add_argument("--prompt", type=Path, default=None,
                        help="후보 심판 프롬프트 파일입니다. 주면 그것으로 잽니다(ADR-059).")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    prompt = use_prompt(args.prompt)
    if args.holdout is not None:
        questions = holdout_questions(args.holdout, args.split)
    else:
        questions = labelled_questions(args.graph_cases, args.base_cases)
    if args.limit:
        questions = questions[: args.limit]
    rows, judge_model = run(questions, args.provider, args.model)
    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "label": args.label,
        # **심판 이름을 반드시 함께 적습니다**(ADR-021). 이것이 없으면 다른 심판으로 잰
        # 점수를 같은 표에 올려놓고 비교하게 됩니다.
        "judge": {"provider": args.provider, "model": judge_model},
        # **어느 프롬프트로 쟀는지 적습니다.** 지문이 없으면 "같은 심판이었다"가 기억이
        # 됩니다. 기준선의 `prompt_versions.JUDGE_CONTENT`와 같은 방식으로 셉니다.
        "prompt": prompt,
        # **어느 집합을 쟀는지 적습니다.** 튜닝 집합의 점수와 보류 집합의 점수를 같은 표에
        # 올리면 "유지됐다"를 거짓으로 말하게 됩니다.
        "questions_from": (
            {"holdout": str(args.holdout), "split": args.split}
            if args.holdout is not None else
            {"cases": [str(args.graph_cases), str(args.base_cases)]}
        ),
        "summary": summarize(rows),
        "mistakes": [
            {
                key: row[key]
                for key in ("id", "family", "question", "expect_content", "got_content",
                            "expect_graph", "got_graph", "graph_conditions")
            }
            for row in rows
            if not (row["content_correct"] and row["graph_correct"])
        ],
        "results": rows,
    }
    if args.compare_to is not None:
        report["comparison"] = compare(rows, args.compare_to)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"심판: {args.provider}/{judge_model} | 프롬프트: {prompt['sha256']}")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    if "comparison" in report:
        for name in ("requires_content", "requires_graph", "both"):
            table = report["comparison"][name]
            print(f"{name}: {table['verdict']} "
                  f"(이쪽만 틀림 {table['only_new_wrong']}, "
                  f"기준선만 틀림 {table['only_baseline_wrong']}, "
                  f"같이 틀림 {table['both_wrong']}, p={table['sign_test_p']})")
    print(f"틀린 질문 {len(report['mistakes'])}건: "
          + json.dumps(collections.Counter(
              row["family"] for row in report["mistakes"]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
