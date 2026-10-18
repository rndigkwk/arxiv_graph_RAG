"""문서의 수치가 출처와 어긋날 때 **잡히는지** 검증합니다.

2026-10-18에 ADR-016이 앱 상주를 259MB로 적고 있었는데 실제는 266.5MB였습니다. Langfuse
비용을 재다가 우연히 발견했습니다. 링크는 CI가 잡아 줬고 숫자는 아무도 안 봤습니다.
"""

import json

import pytest

from scripts.check_doc_numbers import ANCHOR, check, compare, decimals, lookup


def write(tmp_path, doc_text, report):
    (tmp_path / "reports").mkdir(exist_ok=True)
    (tmp_path / "reports" / "r.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    doc = tmp_path / "d.md"
    doc.write_text(doc_text, encoding="utf-8")
    return doc


def test_a_matching_number_passes(tmp_path, monkeypatch):
    doc = write(tmp_path, "피크 <!--n:reports/r.json#peak-->266.5<!--/n-->MB", {"peak": 266.5})
    monkeypatch.chdir(tmp_path)

    problems, anchored = check((doc.name,))

    assert problems == []
    assert anchored == 1


def test_a_stale_number_is_caught(tmp_path, monkeypatch):
    """실제로 일어난 실패입니다 - 259로 적혀 있고 266.5로 측정됐습니다."""
    doc = write(tmp_path, "피크 <!--n:reports/r.json#peak-->259<!--/n-->MB", {"peak": 266.5})
    monkeypatch.chdir(tmp_path)

    problems, _ = check((doc.name,))

    assert len(problems) == 1
    assert "259" in problems[0] and "266.5" in problems[0]


def test_a_missing_source_is_reported(tmp_path, monkeypatch):
    doc = tmp_path / "d.md"
    doc.write_text("<!--n:reports/none.json#x-->1<!--/n-->", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    problems, _ = check((doc.name,))

    assert problems and "출처가 없습니다" in problems[0]


def test_a_missing_key_is_reported(tmp_path, monkeypatch):
    doc = write(tmp_path, "<!--n:reports/r.json#nope-->1<!--/n-->", {"peak": 1})
    monkeypatch.chdir(tmp_path)

    problems, _ = check((doc.name,))

    assert problems and "키가 없습니다" in problems[0]


def test_precision_follows_what_the_document_claims():
    """문서가 보고서보다 더 정확할 수는 없습니다."""
    # 소수 첫째 자리까지만 적었으면 그 자리까지 맞으면 됩니다.
    assert compare("266.5", 266.54) is None
    assert compare("266.5", 266.44) is not None
    # 넷째 자리까지 적었으면 그만큼 봅니다.
    assert compare("0.9667", 0.96666) is None
    assert compare("0.9667", 0.9660) is not None
    assert decimals("266.5") == 1
    assert decimals("0.9667") == 4


def test_thousands_separators_are_accepted():
    assert compare("37,854", 37854) is None


def test_a_non_numeric_claim_is_reported():
    assert "숫자가 아닙니다" in compare("많음", 1)
    assert "숫자가 아닙니다" in compare("1", "많음")


def test_a_key_containing_a_dot_is_addressable():
    """`extra_imports.langfuse.langchain.delta`의 중간 키가 `langfuse.langchain`입니다."""
    report = {"extra": {"langfuse.langchain": {"delta": 17.7}}}

    assert lookup(report, "extra/langfuse.langchain/delta") == 17.7
    with pytest.raises(KeyError):
        lookup(report, "extra.langfuse.langchain.delta")


def test_list_indexes_work():
    assert lookup({"rows": [{"v": 3}, {"v": 4}]}, "rows.1.v") == 4


def test_the_anchor_pattern_tolerates_spacing():
    text = "<!-- n:reports/r.json#a.b --> 1.5 <!-- /n -->"
    match = ANCHOR.search(text)

    assert match and match.group("path") == "reports/r.json"
    assert match.group("key") == "a.b"
    assert match.group("value").strip() == "1.5"


def test_the_repo_docs_are_consistent_right_now():
    """저장소의 실제 문서도 통과해야 합니다. 안 그러면 CI가 빨갛게 남습니다."""
    from scripts.check_doc_numbers import ADR_DIR, DEFAULT_DOCS

    paths = (*DEFAULT_DOCS, *(str(adr) for adr in sorted(ADR_DIR.glob("*.md"))))
    problems, anchored = check(paths)

    assert problems == [], problems
    # 전수 검사가 아니므로 개수를 함께 고정합니다. 줄어들면 누가 떼어낸 것입니다.
    assert anchored >= 11, anchored
