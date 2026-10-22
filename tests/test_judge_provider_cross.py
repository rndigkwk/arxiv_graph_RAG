"""조건 판정 심판을 다른 공급자로 교차할 때의 바를 지킵니다(ADR-058).

이 테스트들이 막는 것은 하나입니다 — **결과를 보고 "비슷하다"고 쓰는 것.** 기준선이 61/61로
천장에 붙어 있어서, 바를 적어 두지 않으면 몇 건이 갈려도 "거의 같다"로 읽게 됩니다. 그래서
구간을 코드로 고정하고, 그 구간이 ADR에 적힌 숫자와 갈라지지 않는지도 확인합니다.
"""

import re
import tomllib
from pathlib import Path

import pytest

from evals import judge_study

ADR = Path("docs/adr/ADR-058-the-independent-judge-only-means-something-with-labels.md")


def rows(wrong_content=(), wrong_graph=(), total=10):
    """id가 `q0`..인 판정 행을 만듭니다."""
    return [
        {
            "id": f"q{i}",
            "content_correct": f"q{i}" not in wrong_content,
            "graph_correct": f"q{i}" not in wrong_graph,
        }
        for i in range(total)
    ]


# ── 바의 산수 ────────────────────────────────────────────────────────────

def test_one_discordant_pair_is_a_coin_flip():
    """한 건이 갈린 것은 동전 한 번입니다. 그것으로 "다르다"고 말하면 안 됩니다."""
    assert judge_study.sign_test_p(0, 1) == 1.0
    assert judge_study.sign_test_p(1, 0) == 1.0


def test_five_one_way_is_not_significant_but_six_is():
    """ADR-058의 구간 경계입니다. b=0이면 p = 2 x 0.5^c입니다."""
    assert judge_study.sign_test_p(0, 5) == pytest.approx(0.0625)
    assert judge_study.sign_test_p(0, 6) == pytest.approx(0.0312, abs=1e-4)
    assert judge_study.sign_test_p(0, 5) >= judge_study.ALPHA
    assert judge_study.sign_test_p(0, 6) < judge_study.ALPHA


def test_no_discordant_pair_means_nothing_to_test():
    """갈린 쌍이 없으면 비교할 것이 없습니다. 0이 아니라 "없음"입니다."""
    assert judge_study.sign_test_p(0, 0) is None


def test_the_threshold_is_counted_not_copied():
    """바의 숫자를 상수로 베껴 두면 ALPHA를 고칠 때 한쪽만 바뀝니다."""
    needed = judge_study.needed_one_way()
    assert judge_study.sign_test_p(0, needed) < judge_study.ALPHA
    assert judge_study.sign_test_p(0, needed - 1) >= judge_study.ALPHA


def test_the_written_bar_matches_the_code():
    """ADR에 적은 "6 이상"과 코드가 갈라지면 둘 중 하나는 거짓말입니다."""
    text = ADR.read_text(encoding="utf-8")
    written = re.search(r"\|\s*(\d+) 이상\s*\|\s*\*\*공급자 의존\*\*", text)
    assert written, "ADR-058의 판정 표에서 공급자 의존 구간을 찾지 못했습니다"
    assert int(written.group(1)) == judge_study.needed_one_way()


# ── 판정 구간 ────────────────────────────────────────────────────────────

def test_zero_wrong_on_both_sides_is_transcribed():
    assert judge_study._verdict(0, 0, 0, None) == "전사됨"


def test_wrong_on_the_same_questions_is_not_a_provider_finding():
    """양쪽이 같은 질문에서 틀리면 공급자가 아니라 라벨이나 프롬프트 문제입니다."""
    said = judge_study._verdict(0, 0, 3, None)
    assert "전사됨" not in said
    assert "라벨" in said


def test_five_wrong_is_recorded_as_unresolved_not_as_similar():
    said = judge_study._verdict(5, 0, 0, judge_study.sign_test_p(0, 5))
    assert "구별 못 함" in said
    # **"비슷하다"는 말이 새어 나오면 안 됩니다.**
    assert "비슷" not in said


def test_six_wrong_is_provider_dependent():
    said = judge_study._verdict(6, 0, 0, judge_study.sign_test_p(0, 6))
    assert said == "공급자 의존"


# ── 쌍체 표 ──────────────────────────────────────────────────────────────

def test_paired_aligns_by_question_id_not_by_position():
    """순서가 다른 보고서를 자리로 맞추면 엉뚱한 쌍이 만들어집니다."""
    baseline = rows()
    mine = list(reversed(rows(wrong_content=("q3",))))
    table = judge_study.paired(mine, baseline, "content_correct")
    assert table["only_new_wrong_ids"] == ["q3"]
    assert table["only_baseline_wrong"] == 0


def test_paired_counts_the_three_cells_separately():
    baseline = rows(wrong_content=("q1", "q2"))
    mine = rows(wrong_content=("q2", "q7"))
    table = judge_study.paired(mine, baseline, "content_correct")
    assert table["only_new_wrong_ids"] == ["q7"]
    assert table["only_baseline_wrong_ids"] == ["q1"]
    assert table["both_wrong_ids"] == ["q2"]
    assert table["both_right"] == 7
    assert table["questions"] == 10


def test_paired_uses_only_the_shared_questions():
    """기준선에 없는 질문을 "맞음"으로 세면 정확도가 올라가 버립니다."""
    table = judge_study.paired(rows(total=10), rows(total=6), "content_correct")
    assert table["questions"] == 6


def test_compare_looks_at_each_judgment_and_not_only_the_aggregate(tmp_path):
    """2026-10-13 사고가 두 판정이 서로 번진다는 것을 보여줬습니다.

    한쪽이 나빠지고 다른 쪽이 좋아지면 집계에서 상쇄돼 보입니다. 그래서 따로 봅니다.
    """
    import json

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "label": "openai-baseline",
        "summary": {"both_correct": 1.0},
        "results": rows(),
    }), encoding="utf-8")
    report = judge_study.compare(rows(wrong_content=("q1",), wrong_graph=("q2",)),
                                 baseline)
    assert report["requires_content"]["only_new_wrong_ids"] == ["q1"]
    assert report["requires_graph"]["only_new_wrong_ids"] == ["q2"]
    assert report["both"]["only_new_wrong"] == 2
    # 옛 보고서에는 심판 이름이 없습니다. 앱 기본값으로 채워 넣으면 "같은 심판으로 쟀다"는
    # 거짓이 됩니다.
    assert "기록되지 않았습니다" in report["baseline"]["judge"]


def test_compare_names_the_questions_the_baseline_does_not_have(tmp_path):
    import json

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "summary": {"both_correct": 1.0}, "results": rows(total=8),
    }), encoding="utf-8")
    report = judge_study.compare(rows(total=10), baseline)
    assert report["questions_missing_from_baseline"] == ["q8", "q9"]


# ── 공급자 ───────────────────────────────────────────────────────────────

def test_the_openai_judge_refuses_a_model_the_app_does_not_use():
    """다른 모델로 잰 점수를 기준선과 같은 표에 올리는 사고를 막습니다(ADR-021)."""
    # 이 경로만 앱의 모델 이름을 보므로 `chatbot`이 필요합니다. CI의 `test` job에는
    # `.env`가 없어 임포트가 실패합니다 - 저장소의 기존 관례대로 건너뛰고 적습니다.
    try:
        import chatbot  # noqa: F401
    except Exception as exc:
        pytest.skip(f"chatbot을 가져올 수 없습니다: {type(exc).__name__}")
    with pytest.raises(SystemExit) as caught:
        judge_study.judge_for("openai", "gpt-4o-mini")
    assert "OPENAI_MODEL" in str(caught.value)


def test_the_anthropic_judge_asks_for_the_key_by_the_name_in_env(monkeypatch):
    monkeypatch.delenv(judge_study.ANTHROPIC_KEY, raising=False)
    with pytest.raises(SystemExit) as caught:
        judge_study.judge_for("anthropic", "")
    said = str(caught.value)
    assert judge_study.ANTHROPIC_KEY in said
    # 값은 절대 출력하지 않습니다.
    assert "sk-" not in said


def test_an_organization_id_is_refused_before_the_call(monkeypatch):
    """조직 ID와 워크스페이스 ID는 다른 값입니다. 2026-10-22에 그것으로 400을 받았습니다.

    61문항을 보내고 나서 알게 되면 비용과 시간을 버립니다. 모양이 아니면 먼저 막습니다.
    """
    monkeypatch.setenv(judge_study.ANTHROPIC_KEY, "sk-ant-not-a-real-key")
    # 콘솔의 조직 ID는 UUID입니다.
    monkeypatch.setenv(judge_study.ANTHROPIC_WORKSPACE_KEY,
                       "970ebbbb-0000-0000-0000-000000000000")
    with pytest.raises(SystemExit) as caught:
        judge_study.judge_for("anthropic", "")
    said = str(caught.value)
    assert judge_study.ANTHROPIC_WORKSPACE_PREFIX in said
    assert "조직" in said


def test_the_anthropic_path_does_not_drag_in_the_app(monkeypatch):
    """공급자를 바꿔 재는 코드가 앱 자격증명을 요구하면 안 됩니다.

    `chatbot`은 `.env`의 Neo4j·OpenAI 값이 없으면 **임포트 자체가** 실패합니다. 토큰 누적
    껍데기를 거기 두고 있었더니 2026-10-22에 CI의 `test` job에서 네 건이 터졌습니다 -
    앱 설정과 아무 상관 없는 함수였습니다. 그래서 `candidate_validation`으로 옮겼고,
    이 테스트가 다시 묶이는 것을 막습니다.
    """
    import subprocess
    import sys

    probe = (
        "import os, sys;"
        "os.environ['CLAUDE']='sk-ant-not-a-real-key';"
        "os.environ.pop('CLAUDE_WORKSPACE_ID', None);"
        "from evals import judge_study;"
        "judge_study.judge_for('anthropic', '');"
        "print('chatbot' in sys.modules)"
    )
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-500:]
    assert done.stdout.strip() == "False", "anthropic 경로가 chatbot을 끌고 옵니다"


def test_a_workspace_scoped_key_needs_no_header(monkeypatch):
    """헤더를 안 쓰는 길이 막히면 안 됩니다 - 전용 키 쪽이 더 간단합니다."""
    monkeypatch.setenv(judge_study.ANTHROPIC_KEY, "sk-ant-not-a-real-key")
    monkeypatch.delenv(judge_study.ANTHROPIC_WORKSPACE_KEY, raising=False)
    make, judge = judge_study.judge_for("anthropic", "")
    assert callable(make)
    assert judge.startswith(judge_study.ANTHROPIC_MODEL)


def test_the_output_limit_leaves_room_for_thinking():
    """이 모델은 사고가 기본으로 켜지고 그 토큰이 출력 한도를 먹습니다.

    한도가 작으면 구조화 도구 호출이 잘려 "판정이 불완전합니다"로 죽습니다 - 심판이 틀린
    것이 아니라 한도가 작은 것인데, 보고서에는 구별이 남지 않습니다.
    """
    assert judge_study.ANTHROPIC_MAX_TOKENS >= 4096


def test_the_cheap_judge_is_the_one_the_adr_names():
    text = ADR.read_text(encoding="utf-8")
    assert f"`{judge_study.ANTHROPIC_MODEL}`" in text


# ── 보류 집합과 후보 프롬프트 (ADR-059) ─────────────────────────────────

def holdout_file(tmp_path, items):
    import json

    path = tmp_path / "holdout.json"
    path.write_text(json.dumps({"questions": items}), encoding="utf-8")
    return path


def test_the_holdout_split_is_honoured(tmp_path):
    path = holdout_file(tmp_path, [
        {"id": "a", "split": "diagnostic"},
        {"id": "b", "split": "final"},
        {"id": "c", "split": "diagnostic"},
    ])
    assert [i["id"] for i in judge_study.holdout_questions(path, "diagnostic")] \
        == ["a", "c"]
    assert [i["id"] for i in judge_study.holdout_questions(path, "final")] == ["b"]
    assert len(judge_study.holdout_questions(path, "all")) == 3


def test_an_empty_split_is_refused_instead_of_measuring_nothing(tmp_path):
    path = holdout_file(tmp_path, [{"id": "a", "split": "diagnostic"}])
    with pytest.raises(SystemExit) as caught:
        judge_study.holdout_questions(path, "final")
    assert "final" in str(caught.value)


def test_a_file_that_is_not_a_holdout_set_is_refused(tmp_path):
    import json

    path = tmp_path / "other.json"
    path.write_text(json.dumps({"summary": {}}), encoding="utf-8")
    with pytest.raises(SystemExit) as caught:
        judge_study.holdout_questions(path, "all")
    assert "holdout_questions" in str(caught.value)


def test_opening_the_final_split_says_so_out_loud(tmp_path, capsys):
    """최종 조각을 보고 프롬프트를 고치면 그 조각도 튜닝 집합이 됩니다(ADR-059)."""
    path = holdout_file(tmp_path, [{"id": "b", "split": "final"}])
    judge_study.holdout_questions(path, "final")
    said = capsys.readouterr().out
    assert "최종 확인 조각" in said and "ADR-059" in said


def test_the_live_prompt_digest_matches_what_the_baseline_pins():
    """프롬프트를 고치고 기준선을 그대로 두면 게이트가 막습니다. 여기서 먼저 잡습니다."""
    import json

    recorded = judge_study.use_prompt(None)
    pinned = json.loads(
        Path("baselines/eval_baseline.json").read_text(encoding="utf-8")
    )["prompt_versions"]["JUDGE_CONTENT"]
    assert recorded["sha256"] == pinned, (
        "JUDGE_CONTENT가 기준선과 다릅니다. 프롬프트를 바꿨다면 기준선을 다시 재야 "
        "합니다(게이트의 prompt_version 규칙)."
    )


def test_a_candidate_prompt_is_swapped_in_and_recorded(tmp_path, monkeypatch):
    import prompts

    # **되돌립니다.** 이 함수는 모듈 전역을 바꾸므로 복구하지 않으면 다른 테스트로 샙니다.
    monkeypatch.setattr(prompts, "JUDGE_CONTENT", prompts.JUDGE_CONTENT)
    path = tmp_path / "candidate.txt"
    path.write_text("  judge differently  ", encoding="utf-8")
    recorded = judge_study.use_prompt(path)
    assert prompts.JUDGE_CONTENT == "judge differently"
    assert recorded["source"] == str(path)
    assert len(recorded["sha256"]) == 12


def test_an_empty_candidate_prompt_is_refused(tmp_path, monkeypatch):
    import prompts

    monkeypatch.setattr(prompts, "JUDGE_CONTENT", prompts.JUDGE_CONTENT)
    path = tmp_path / "candidate.txt"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(SystemExit):
        judge_study.use_prompt(path)


def test_the_final_split_is_still_unopened():
    """예비 조각을 **기계로** 지킵니다(ADR-059).

    최종 조각을 보고 프롬프트를 고치면 그 조각도 튜닝 집합이 되고, 보류 집합이 소진됩니다.
    정말 열었다면 이 테스트를 지우고 ADR-059에 "소진됐다"를 적으세요 - 조용히 열리는 것만
    막습니다.
    """
    import json

    for path in sorted(Path("reports").glob("judge*.json")):
        came = json.loads(path.read_text(encoding="utf-8")).get("questions_from") or {}
        assert came.get("split") != "final", (
            f"{path}가 최종 확인 조각으로 잰 보고서입니다. ADR-059의 예비가 소진됐다면 "
            "그것을 ADR에 적고 이 테스트를 지우세요."
        )


def test_the_candidate_prompt_is_the_one_the_adr_measured():
    """ADR이 적은 지문과 파일이 갈라지면 둘 중 하나는 거짓말입니다."""
    import prompts
    from pipeline.prompt_registry import digest

    candidate = Path("evals/judge_prompts/judge-content-v4-parallel-title.txt")
    text = candidate.read_text(encoding="utf-8").strip()
    assert text != prompts.JUDGE_CONTENT, "후보가 현재 프롬프트와 같습니다"
    adr = Path(
        "docs/adr/ADR-059-the-judge-prompt-is-measured-on-held-out-questions.md"
    ).read_text(encoding="utf-8")
    assert f"`{digest(text)}`" in adr, (
        f"ADR-059에 후보의 지문 {digest(text)}이 적혀 있지 않습니다"
    )
    # 채택하지 않았으므로 앱의 프롬프트는 그대로여야 합니다.
    assert "채택하지 않습니다" in adr


def test_the_report_records_the_set_and_the_prompt():
    """튜닝 집합의 점수와 보류 집합의 점수를 같은 표에 올리면 거짓이 됩니다."""
    source = Path("evals/judge_study.py").read_text(encoding="utf-8")
    assert '"questions_from"' in source
    assert '"prompt": prompt,' in source


# ── 의존성은 평가 전용입니다 ─────────────────────────────────────────────

def test_the_anthropic_client_stays_out_of_the_app():
    """ADR-016의 690MB 천장입니다. 앱 의존성에 들어가면 배포가 그 패키지를 깔게 됩니다."""
    config = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    app = " ".join(config["project"]["dependencies"])
    assert "anthropic" not in app
    extras = " ".join(
        name for group in config["project"]["optional-dependencies"].values()
        for name in group
    )
    assert "anthropic" not in extras
    dev = " ".join(config["dependency-groups"]["dev"])
    assert "langchain-anthropic" in dev


def test_both_providers_share_one_usage_wrapper():
    """토큰 누적을 공급자별로 베껴 두면 다른 방식으로 센 비용을 같은 표에 올립니다."""
    source = Path("evals/judge_study.py").read_text(encoding="utf-8")
    assert "structured_judge(structured, usage)" in source
    assert "usage_metadata" not in source, (
        "토큰 누적이 judge_study로 복제됐습니다. "
        "candidate_validation.structured_judge를 쓰세요."
    )


def test_the_report_records_which_judge_and_where():
    """`ANTHROPIC_BASE_URL`이 다른 게이트웨이면 모델 이름이 거짓이 됩니다."""
    source = Path("evals/judge_study.py").read_text(encoding="utf-8")
    assert '"judge": {"provider"' in source
    assert "ANTHROPIC_BASE_URL" in source
