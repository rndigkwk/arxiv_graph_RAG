"""회귀 평가가 답변 경로를 바꾼 PR에서 실제로 도는지 봅니다.

2026-10-25에 모듈을 `src/`로 옮긴 뒤, 워크플로의 감시 목록(`chatbot.py` 같은 루트 경로)이 아무것도
맞추지 못했습니다. 필수 검사는 초록색이었지만 평가 단계는 건너뛰었습니다. 이름만 맞는지 보는
테스트로는 못 잡으므로, **실제 파일 목록**에 식을 대 봅니다.
"""

import re
from pathlib import Path

WORKFLOW = Path(".github/workflows/regression-eval.yml")


def watched() -> re.Pattern[str]:
    line = next(line for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
                if line.strip().startswith("watched="))
    return re.compile(line.split("=", 1)[1].strip().strip("'"))


def test_every_app_module_triggers_the_evaluation():
    pattern = watched()
    modules = sorted(p.as_posix() for p in Path("src").glob("*.py"))
    assert modules, "src/에 모듈이 없습니다"
    assert [m for m in modules if not pattern.search(m)] == []


def test_the_answer_path_modules_exist_where_the_gate_looks():
    # 루트로 되돌아가거나 하위 폴더로 옮기면 이 테스트가 먼저 깨집니다.
    for name in ("chatbot", "agent_graph", "prompts", "routing", "answer_validation",
                 "candidate_validation", "pre_scope"):
        assert Path(f"src/{name}.py").exists(), name
        assert watched().search(f"src/{name}.py"), name


def test_unrelated_files_do_not_trigger_it():
    pattern = watched()
    for path in ("docs/adr/ADR-001.md", "reports/redteam.json", "web/app/page.tsx",
                 "src/sub/module.py", "README.md"):
        assert not pattern.search(path), path
