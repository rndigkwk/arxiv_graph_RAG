"""배포 표시와 배포 점검, 실패 알림의 시험입니다.

배포 점검이 지키는 약속은 하나입니다 - **옛 코드를 새 코드로 보고하지 않는다.** 그 약속이 조용히
깨지는 길을 하나씩 막습니다.

- 화면의 표시 모양과 점검이 읽는 모양이 갈라지면 점검은 늘 "표시 없음"이거나, 더 나쁘게
  늘 통과합니다.
- 검색 모듈에 커밋 속성이 없을 때 `None`으로 넘기면 이 기능의 첫 배포에서 옛 모듈을 놓칩니다.
- 알림이 감시하는 워크플로 이름은 손으로 적은 목록입니다. 이름이 바뀌면 알림이 조용히 꺼집니다.
"""

import ast
import re
from pathlib import Path

import pytest
import yaml

from scripts.visit_deployed_app import error_in, parse_stamp, verdict
from src import release

WORKFLOWS = Path(".github/workflows")
SHA = "6fd3cde" + "0" * 33


def git_dir(tmp_path, head, refs=None, packed=None):
    git = tmp_path / ".git"
    git.mkdir()
    (git / "HEAD").write_text(head, encoding="utf-8")
    for ref, value in (refs or {}).items():
        path = git / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value + "\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(packed, encoding="utf-8")
    return tmp_path


def test_a_branch_commit_is_read_from_its_ref(tmp_path):
    root = git_dir(tmp_path, "ref: refs/heads/main\n", refs={"refs/heads/main": SHA})
    assert release.commit(root) == "6fd3cde"


def test_a_packed_ref_is_read_too(tmp_path):
    """Streamlit Cloud의 클론은 ref가 packed-refs에만 있을 수 있습니다."""
    root = git_dir(tmp_path, "ref: refs/heads/main\n",
                   packed=f"# pack-refs with: peeled\n{SHA} refs/heads/main\n")
    assert release.commit(root) == "6fd3cde"


def test_a_detached_head_is_the_commit_itself(tmp_path):
    assert release.commit(git_dir(tmp_path, SHA + "\n")) == "6fd3cde"


def test_no_git_directory_is_none_not_a_crash(tmp_path):
    assert release.commit(tmp_path) is None


def test_the_same_code_on_both_sides_passes():
    caption, warning = release.stamp("6fd3cde", "6fd3cde")
    assert warning is None
    assert verdict(SHA, parse_stamp(caption)) is None


def test_an_old_search_module_fails_even_when_the_page_is_new():
    """세 번 겪은 사고입니다. 화면은 기대한 커밋이라 커밋만 보면 통과합니다."""
    caption, warning = release.stamp("6fd3cde", "1a2b3c4")
    problem = verdict(SHA, parse_stamp(caption + "\n" + warning))
    assert problem and "재부팅" in problem


def test_a_module_without_the_attribute_counts_as_old():
    """이 기능의 첫 배포에서 옛 모듈에는 `LOADED_COMMIT`이 없습니다. 없음은 옛 코드입니다."""
    caption, warning = release.stamp("6fd3cde", release.OLDER)
    assert warning is not None
    assert verdict(SHA, parse_stamp(caption + "\n" + warning)) is not None


def test_a_page_from_another_commit_fails():
    caption, _ = release.stamp("1a2b3c4", "1a2b3c4")
    assert "기대" in verdict(SHA, parse_stamp(caption))


def test_an_unreadable_commit_is_not_a_pass():
    """`.git`이 없으면 새 코드인지 모릅니다. 모르는 것을 통과로 적지 않습니다."""
    caption, _ = release.stamp(None, None)
    assert verdict(SHA, parse_stamp(caption)) is not None


def test_a_page_without_any_stamp_fails():
    assert verdict(SHA, parse_stamp("arXiv GraphRAG 챗봇\n적재된 논문 38,665편")) is not None


@pytest.mark.parametrize("texts, boxes, expected", [
    (["실시간 그래프 연결을 준비하지 못했습니다 (ServiceUnavailable)."], 0, "그래프 DB"),
    (["Traceback (most recent call last):\n  File ..."], 0, "Traceback"),
    (["적재된 논문 38,665편"], 1, "예외 상자"),
])
def test_errors_on_the_page_are_reported(texts, boxes, expected):
    assert expected in error_in(texts, boxes)


def test_ordinary_paper_text_is_not_an_error():
    """논문 제목에 error·traceback이 들어갈 수 있습니다. 예외 문구 전체만 봅니다."""
    assert error_in(["Error Traceback Analysis for Large Language Models"]) is None


def test_the_search_module_stamps_its_commit_when_imported():
    """`chatbot`을 임포트하지 않습니다(CI에는 .env가 없습니다). 모듈 최상단 대입인지 봅니다.

    함수 안에서 읽으면 부를 때마다 디스크를 읽어 화면과 늘 같아지고, 옛 모듈을 못 잡습니다.
    """
    tree = ast.parse(Path("src/chatbot.py").read_text(encoding="utf-8"))
    stamps = [node for node in tree.body if isinstance(node, ast.Assign)
              and any(getattr(t, "id", None) == "LOADED_COMMIT" for t in node.targets)]
    assert len(stamps) == 1
    assert ast.unparse(stamps[0].value) == "release.commit()"


def test_the_page_treats_a_missing_attribute_as_old():
    source = Path("app.py").read_text(encoding="utf-8")
    assert 'getattr(chatbot, "LOADED_COMMIT", release.OLDER)' in source
    main = source[source.index("def main("):]
    assert main.index("release.stamp(") < main.index("st.tabs(")


def names():
    found = {}
    for path in WORKFLOWS.glob("*.yml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        found[doc["name"]] = doc
    return found


def triggers(doc):
    # YAML 1.1은 `on:`을 True로 읽습니다.
    return doc.get("on", doc.get(True)) or {}


def test_the_alert_watches_workflows_that_exist():
    """이름이 하나라도 틀리면 그 워크플로의 실패는 알림 없이 지나갑니다."""
    workflows = names()
    watched = triggers(workflows["Alert on failure"])["workflow_run"]["workflows"]
    assert set(watched) <= set(workflows), set(watched) - set(workflows)


def test_every_scheduled_workflow_is_watched():
    """정기 실행은 보는 사람이 없을 때 돕니다. 새로 생긴 정기 실행도 알림 목록에 들어가야 합니다."""
    workflows = names()
    watched = set(triggers(workflows["Alert on failure"])["workflow_run"]["workflows"])
    scheduled = {name for name, doc in workflows.items() if "schedule" in triggers(doc)}
    assert scheduled <= watched, scheduled - watched


def test_only_the_alert_job_can_write_and_only_issues():
    doc = names()["Alert on failure"]
    assert doc["permissions"] == {}
    assert doc["jobs"]["alert"]["permissions"] == {"issues": "write"}
    assert "pull_request" in doc["jobs"]["alert"]["if"]


def test_the_check_is_given_the_commit_and_the_pinned_browser():
    doc = names()["Check deployed app"]
    steps = " ".join(step.get("run", "") for step in doc["jobs"]["check"]["steps"])
    assert "--expect-commit" in steps and "github.sha" in steps
    pinned = set(re.findall(r"playwright==([\d.]+)", steps))
    script = Path("scripts/visit_deployed_app.py").read_text(encoding="utf-8")
    assert pinned == set(re.findall(r"playwright==([\d.]+)", script))
    assert "push" in triggers(doc), "머지 직후 옛 모듈을 잡으려면 push에서도 돌아야 합니다"
