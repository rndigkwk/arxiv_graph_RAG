"""ADR 규칙 검사기가 **규칙을 재는지, 어휘를 재는지**를 고정합니다.

처음에 `민감도`라는 글자만 셌다가 틀렸습니다 — ADR-046·049·051은 `이 결정이 바뀌는 조건`,
ADR-030은 `한계 — 가장 큰 가정`, ADR-037은 `이 측정이 말하지 않는 것`으로 같은 일을 합니다.
글자를 세면 24개가 빠진 것으로 보이고, 규칙을 세면 19개입니다. 아래 테스트가 그 구분을
묶어 둡니다.

**반대 방향도 묶습니다.** 범위를 적는 절(`넘지 않은 선`·`아직 아닌 것`)이 민감도로 통과하면
검사기가 통과만 시키는 장치가 됩니다.
"""

from pathlib import Path

from scripts.check_adr_rules import (
    SENSITIVITY_MARKERS,
    STATUSES,
    WITHOUT_SENSITIVITY,
    adrs,
    check,
    has_sensitivity,
    index_gaps,
)


def test_the_repository_passes_its_own_rule():
    """빚 목록이 정확하면 문제가 0건입니다. 하나라도 틀리면 어느 파일인지 말합니다."""
    assert check() == []


def test_the_canonical_heading_counts():
    assert has_sensitivity("## 민감도\n\n본문") is True


def test_the_other_names_for_the_same_section_count():
    """**이 테스트가 "글자가 아니라 규칙"을 고정합니다.** 저장소에 실제로 쓰인 제목들입니다."""
    for heading in (
        "## 이 결정이 바뀌는 조건",       # ADR-046·049·051
        "## 한계 — 가장 큰 가정",          # ADR-030
        "## 이 측정이 말하지 않는 것",     # ADR-037
    ):
        assert has_sensitivity(f"{heading}\n\n본문") is True, heading


def test_a_scope_section_is_not_a_sensitivity_section():
    """"무엇을 안 했는가"와 "무엇이 바뀌면 뒤집히는가"는 다릅니다.

    이 둘을 섞으면 032 이후 ADR이 거의 전부 통과합니다 - 검사기가 아무것도 막지 않습니다.
    """
    for heading in (
        "## 넘지 않은 선",
        "## 아직 아닌 것",
        "## 하지 않은 것",
        "## 고치지 않은 것",
        "## 막지 않는 것 — 그리고 무엇이 막는지",
        "## 재현",
        "## 결정",
    ):
        assert has_sensitivity(f"{heading}\n\n본문") is False, heading


def test_a_sensitivity_word_in_the_body_is_not_a_section():
    """본문에서 다른 ADR의 민감도를 **가리키는** 것은 자기 절이 아닙니다(ADR-030이 그럽니다)."""
    body = "## 맥락\n\nADR-027의 민감도에 적어 둔 가정이 여기서 무게를 받습니다.\n"

    assert has_sensitivity(body) is False


def test_the_debt_list_only_shrinks():
    """목록에 있는 ADR은 **지금** 절이 없어야 합니다.

    절이 생겼는데 목록에 남아 있으면 그 목록이 낡은 것이고, 낡은 목록이 이 검사기가
    막으려는 바로 그 병입니다. `check()`가 그 경우를 실패로 적습니다.
    """
    numbers = dict(adrs())
    for number in sorted(WITHOUT_SENSITIVITY):
        assert number in numbers, f"ADR-{number} 파일이 없습니다"
        text = numbers[number].read_text(encoding="utf-8")
        assert has_sensitivity(text) is False, (
            f"ADR-{number}에 절이 생겼습니다. WITHOUT_SENSITIVITY에서 지우세요"
        )


def test_the_debt_is_a_minority_and_counted():
    """빚이 몇 개인지 수로 들고 있어야 "줄었나"를 물을 수 있습니다.

    **"절이 있는 ADR의 수"는 박지 않습니다.** 처음에 32를 박아 뒀는데, 그 수는 ADR이 하나
    늘면 틀립니다 - 빚과 무관한 이유로 테스트가 깨집니다. 박아야 하는 것은 **빚의 수**이고,
    그것은 갚을 때만 줄어야 합니다. 이 저장소에서 손으로 박은 수가 갈라진 자리를 또 만들지
    않습니다.
    """
    total = len(adrs())

    assert len(WITHOUT_SENSITIVITY) == 19
    # 빚이 소수여야 "규약이 산다"고 말할 수 있습니다. 절반을 넘으면 규약이 아닙니다.
    assert len(WITHOUT_SENSITIVITY) * 2 < total


def test_the_status_vocabulary_matches_the_index():
    """색인의 상태 표가 네 값을 선언합니다. 다섯 번째가 조용히 생기면 안 됩니다."""
    index = (adrs()[0][1].parent / "README.md").read_text(encoding="utf-8")
    for status in STATUSES:
        assert f"| {status} |" in index, status


def test_the_markers_are_documented_in_the_index():
    """검사기가 허용하는 제목을 색인에도 적어야 합니다 - 코드에만 있으면 아무도 안 봅니다."""
    index = (adrs()[0][1].parent / "README.md").read_text(encoding="utf-8")
    for marker in SENSITIVITY_MARKERS:
        assert marker in index, marker


def test_ci_picks_up_checkers_by_glob_not_by_hand():
    """검사기 목록이 손으로 적힌 목록으로 되돌아가면 안 됩니다.

    세 번째 검사기를 더하려다 그 두 줄이 같은 병임을 알았습니다. 글로브로 바꿨으니
    개별 파일 이름이 다시 나타나면 되돌아간 것입니다.
    """
    ci = Path(".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "for checker in scripts/check_*.py" in ci
    for named in ("scripts/check_doc_links.py", "scripts/check_adr_rules.py"):
        assert f"python {named}" not in ci, named


# ── 색인 표에 줄이 빠진 것 (2026-10-24) ─────────────────────────────────────


def test_the_index_lists_every_adr():
    """**이 테스트가 막는 것이 실제로 일어났습니다** — 063·064·065가 색인에 없었습니다."""
    assert index_gaps({number for number, _ in adrs()}) == []


def test_an_adr_missing_from_the_index_is_named():
    """파일을 쓰는 것과 색인에 줄을 넣는 것은 다른 동작입니다. 뒤쪽만 빠지기 쉽습니다."""
    gaps = index_gaps({number for number, _ in adrs()} | {"777"})

    assert len(gaps) == 1
    assert "ADR-777" in gaps[0]
    assert "줄이 없습니다" in gaps[0]


def test_a_row_without_a_file_is_named_too():
    """번호를 바꿨는데 표만 남으면 색인이 없는 파일을 가리킵니다."""
    numbers = {number for number, _ in adrs()}
    gaps = index_gaps(numbers - {"066"})

    assert len(gaps) == 1
    assert "ADR-066" in gaps[0]
    assert "파일이 없습니다" in gaps[0]


def test_the_index_documents_the_coverage_rule():
    """검사기가 보는 규칙은 색인에도 적혀 있어야 합니다 - 코드에만 있으면 아무도 안 봅니다."""
    index = (adrs()[0][1].parent / "README.md").read_text(encoding="utf-8")

    assert "빠진 ADR이 없습니다" in index
