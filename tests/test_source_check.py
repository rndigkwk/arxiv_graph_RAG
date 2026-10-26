"""옛 모듈은 커밋이 아니라 `src/`의 내용으로 가립니다(ADR-072 고침).

2026-10-26 PR #55는 `src/`를 건드리지 않았는데, 커밋을 비교한 점검이 재부팅하라는 이슈(#56)를
열었습니다. 반대 방향도 지킵니다 - 내용이 바뀌었으면, 그리고 모르면 여전히 경고합니다.
"""

import ast
from pathlib import Path

from src import source_check


def tree(tmp_path, files):
    (tmp_path / "src").mkdir()
    for name, text in files.items():
        (tmp_path / "src" / name).write_text(text, encoding="utf-8")
    return tmp_path


def test_unchanged_sources_are_the_same_code_whatever_the_commit(tmp_path):
    root = tree(tmp_path, {"chatbot.py": "A = 1\n", "release.py": "B = 2\n"})
    assert source_check.same_code(source_check.digest(root), root)


def test_a_changed_module_is_old_code(tmp_path):
    root = tree(tmp_path, {"chatbot.py": "A = 1\n", "release.py": "B = 2\n"})
    loaded = source_check.digest(root)
    (root / "src" / "release.py").write_text("B = 3\n", encoding="utf-8")
    assert not source_check.same_code(loaded, root)


def test_an_added_or_renamed_module_is_old_code(tmp_path):
    root = tree(tmp_path, {"chatbot.py": "A = 1\n"})
    loaded = source_check.digest(root)
    (root / "src" / "new.py").write_text("", encoding="utf-8")
    assert not source_check.same_code(loaded, root)


def test_unknown_is_not_the_same():
    """지문 없는 모듈은 이 기능보다 오래된 것입니다. 같다고 넘기면 옛 모듈 사고를 놓칩니다."""
    assert not source_check.same_code(None)


def test_the_search_module_records_the_digest_when_imported():
    body = ast.parse(Path("src/chatbot.py").read_text(encoding="utf-8")).body
    stamps = [node for node in body if isinstance(node, ast.Assign)
              and any(getattr(t, "id", None) == "LOADED_SOURCE" for t in node.targets)]
    assert [ast.unparse(node.value) for node in stamps] == ["source_check.digest()"]


def test_the_page_clears_the_warning_only_for_the_same_code():
    main = Path("app.py").read_text(encoding="utf-8")
    main = main[main.index("def main("):]
    clear = main.index('source_check.same_code(getattr(chatbot, "LOADED_SOURCE", None))')
    assert main.index("release.stamp(") < clear < main.index("st.warning(stale)")


def test_the_page_does_not_call_anything_new_on_release():
    """머지 뒤 `src.release`는 옛 모듈로 남습니다. 새 함수를 부르면 그 배포에서 화면이 깨집니다."""
    main = Path("app.py").read_text(encoding="utf-8")
    called = {node.func.attr for node in ast.walk(ast.parse(main))
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and getattr(node.func.value, "id", None) == "release"}
    assert called <= {"commit", "stamp"}
