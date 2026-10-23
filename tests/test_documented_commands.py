"""문서에 적힌 명령 검사기를 지킵니다.

**통과만 시키는 검사기는 검사기가 아닙니다.** 그래서 잡아야 하는 모양마다 실제로 잡는지
확인합니다 - `tests/fixtures/eval_gate/`가 게이트에 하는 것과 같은 일입니다.

2026-10-24에 이 검사기가 처음 돌면서 **몰랐던 결함 하나**를 찾았습니다
(`models.embedding_tuning.train --ridge`, 실제 플래그는 `--ridge-multiplier`). 손으로 훑어
찾은 것이 아닙니다.
"""

import textwrap
from pathlib import Path

from scripts import check_documented_commands as cdc

CHECKER = Path("scripts/check_documented_commands.py")


def write(tmp_path, name, text):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


# ── 파서를 임포트 없이 읽습니다 ─────────────────────────────────────────

def test_the_parser_is_read_without_importing_the_module(tmp_path):
    """임포트하면 `.env`가 필요한 모듈이 있어 CI에서 못 돕니다."""
    module = write(tmp_path, "m.py", """
        import os
        raise SystemExit("임포트되면 이 줄이 터집니다")
        parser.add_argument("--alpha")
    """)
    assert cdc.option_strings(module) == {"--alpha", "--help"}


def test_a_module_without_a_parser_is_skipped_not_failed(tmp_path):
    """플래그를 변수로 만드는 모듈이 있습니다. 모른다고 적는 쪽이 정확합니다."""
    module = write(tmp_path, "plain.py", "x = 1\n")
    assert cdc.option_strings(module) is None


def test_a_parser_with_no_arguments_still_allows_help(tmp_path):
    module = write(tmp_path, "bare.py", """
        import argparse
        parser = argparse.ArgumentParser()
    """)
    assert cdc.option_strings(module) == {"--help"}


# ── 명령을 뽑는 규칙 ────────────────────────────────────────────────────

def test_uv_flags_before_python_are_not_checked():
    """`uv run --group dev`의 `--group`은 uv의 것입니다. 섞으면 전부 틀려 보입니다."""
    target, flags = cdc.flags_after_python(
        "uv run --group dev --locked python -m evals.judge_study --provider anthropic"
    )
    assert target == "evals.judge_study"
    assert flags == ["--provider"]


def test_a_line_continuation_is_joined_first():
    """잇지 않으면 둘째 줄부터를 못 봅니다 - 플래그가 거기 삽니다."""
    text = "uv run python -m evals.run \\\n  --mode compare \\\n  --top-k 5\n"
    found = cdc.commands(text)

    assert len(found) == 1
    target, flags = cdc.flags_after_python(found[0])
    assert flags == ["--mode", "--top-k"]


def test_a_flag_with_an_equals_sign_is_split():
    _, flags = cdc.flags_after_python("uv run python -m evals.run --mode=compare")
    assert flags == ["--mode"]


def test_foreign_tools_are_left_alone():
    """`python -m pytest`의 플래그를 검사할 파서가 우리에게 없습니다."""
    assert cdc.flags_after_python("uv run python -m pytest -q --maxfail=1") is None


def test_a_line_that_is_not_a_command_is_ignored():
    assert cdc.commands("이 문장은 uv run을 말로만 언급합니다\n") == []


# ── 잡아야 하는 것을 잡습니다 ───────────────────────────────────────────

def test_it_fails_on_a_documented_flag_that_does_not_exist(tmp_path, monkeypatch):
    """**이 테스트가 이 파일의 이유입니다.** 2026-10-24에 그 모양이 셋 있었습니다."""
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "pkg/tool.py", """
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--real")
    """)
    doc = write(tmp_path, "GUIDE.md", """
        uv run python -m pkg.tool --real 1
        uv run python -m pkg.tool --imaginary 2
    """)
    problems, checked, _, unchecked = cdc.check([doc], [])

    assert checked == 2
    assert not unchecked
    assert len(problems) == 1
    assert "--imaginary" in problems[0]


def test_it_passes_when_every_flag_exists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "pkg/tool.py", """
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument("--real")
        parser.add_argument("--other", action="store_true")
    """)
    doc = write(tmp_path, "GUIDE.md", "uv run python -m pkg.tool --real 1 --other\n")
    problems, checked, _, _ = cdc.check([doc], [])

    assert problems == []
    assert checked == 1


def test_it_counts_what_it_could_not_check(tmp_path, monkeypatch):
    """전수 검사가 아닙니다. "전부 검증됨"처럼 읽히면 안 됩니다."""
    monkeypatch.chdir(tmp_path)
    write(tmp_path, "pkg/plain.py", "x = 1\n")
    doc = write(tmp_path, "GUIDE.md", "uv run python -m pkg.plain --whatever\n")
    problems, checked, _, unchecked = cdc.check([doc], [])

    assert problems == []
    assert checked == 0
    assert unchecked == {"pkg.plain"}


# ── 저장소 상태 ─────────────────────────────────────────────────────────

def test_the_repository_has_no_documented_command_that_cannot_parse():
    """CI도 `scripts/check_*.py` 글로브로 이것을 돕니다. 여기서도 한 번 봅니다."""
    docs = sorted({path for pattern in cdc.DOC_GLOBS for path in Path().glob(pattern)})
    code = sorted(
        path for folder in cdc.CODE_DIRS for path in Path(folder).rglob("*.py")
    )
    problems, checked, _, _ = cdc.check(docs, code)

    assert problems == [], problems
    # 표면이 작지 않다는 것도 적어 둡니다 - 작으면 검사기를 둘 이유가 약합니다.
    assert checked > 150, checked
    # **자기 파일도 검사 범위에 있습니다.** 예외로 두면 그 파일의 진짜 문제를 숨깁니다.
    assert CHECKER in code
