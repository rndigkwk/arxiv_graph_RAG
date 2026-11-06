"""VM이 main을 따라가는 장치(ADR-080).

- fast-forward만 합니다. VM에서 누가 커밋했으면 덮어쓰지 않고 멈춥니다.
- 타이머가 겹치면 뒤의 것이 나갑니다(빌드가 2분을 넘을 수 있습니다).
- 코드는 마운트돼 있고 파일 감시를 껐으므로 API를 재시작합니다. 단, 앱이 읽지 않는 파일만
  바뀐 배포는 건너뜁니다 - 재시작이 진행 중인 답을 끊습니다(ADR-107).
- VM에는 Streamlit이 없습니다(ADR-083). 내린 서비스는 지우고, 예전 주소는 Vercel로 보냅니다.
- 의존성이 바뀐 때만 이미지를 다시 굽습니다.
- 배포 점검이 VM 주소를 봅니다.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import visit_deployed_app

DEPLOY = Path("deploy/vm/deploy.sh").read_text(encoding="utf-8")


def test_deploy_only_fast_forwards():
    assert "git merge -q --ff-only origin/main" in DEPLOY
    assert "reset" not in DEPLOY and "--force" not in DEPLOY


def test_overlapping_timer_runs_step_aside():
    assert "flock -n 9 || exit 0" in DEPLOY


NO_RESTART = [line for line in Path("deploy/vm/no-restart-paths").read_text(
    encoding="utf-8").splitlines() if line and not line.startswith("#")]


def unread(path: str) -> bool:
    return any(re.search(pattern, path) for pattern in NO_RESTART)


def test_the_api_restarts_unless_only_unread_files_changed():
    """재시작은 진행 중인 답을 끊습니다(ADR-107). 앱이 읽는 파일이 하나라도 바뀌면 재시작합니다."""
    assert re.search(r"^  \$C restart api$", DEPLOY, re.MULTILINE)
    assert "grep -vE -f <(grep -vE '^(#|$)' deploy/vm/no-restart-paths)" in DEPLOY
    # 빈 줄 패턴은 모든 파일에 맞아 재시작을 늘 건너뜁니다.
    assert all(line.strip() for line in Path("deploy/vm/no-restart-paths").read_text(
        encoding="utf-8").splitlines())


@pytest.mark.parametrize("path", [
    "src/api.py", "src/prompts.py", "pipeline/graph.py", "models/artifacts/x.json",
    "baselines/eval_baseline.json", "reports/related_study.json", "pyproject.toml", "uv.lock",
    "deploy/vm/compose.yml", "deploy/vm/Caddyfile", "deploy/vm/Dockerfile", "deploy/vm/deploy.sh",
    "app.py",
])
def test_files_the_api_reads_restart_it(path):
    assert not unread(path)


@pytest.mark.parametrize("path", [
    "docs/EVALUATION.md", "README.md", "evals/README.md", "tests/test_api.py",
    "web/app/page.tsx", "scripts/measure_vm_answer_latency.py", ".github/workflows/ci.yml",
    "reports/vm_answer_latency_2026-11-06.json", "deploy/vm/ingest.sh",
])
def test_files_the_api_does_not_read_skip_the_restart(path):
    assert unread(path)


def test_no_file_the_app_opens_is_on_the_no_restart_list():
    """`src/`가 문자열로 여는 경로가 목록에 걸리면, 그 파일을 바꾼 배포가 옛 내용을 계속 냅니다."""
    opened = set()
    for module in Path("src").glob("*.py"):
        opened |= set(re.findall(r'Path\("([^"]+)"\)', module.read_text(encoding="utf-8")))
    assert opened, "src가 여는 경로를 하나도 찾지 못했습니다 - 이 시험의 정규식을 보세요"
    assert not [path for path in opened if unread(path)]


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def commit(root, path, text):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", path)
    return subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=root, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_the_check_accepts_an_old_module_only_across_unread_changes(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "deploy/vm").mkdir(parents=True)
    (tmp_path / "deploy/vm/no-restart-paths").write_text(
        Path("deploy/vm/no-restart-paths").read_text(encoding="utf-8"), encoding="utf-8")
    first = commit(tmp_path, "src/api.py", "a")
    docs = commit(tmp_path, "docs/x.md", "b")
    code = commit(tmp_path, "src/api.py", "c")
    assert visit_deployed_app.restart_free(first, docs, root=tmp_path)
    assert not visit_deployed_app.restart_free(first, code, root=tmp_path)
    # git이 모르는 커밋(얕은 체크아웃)은 옛 모듈로 셉니다.
    assert not visit_deployed_app.restart_free("0000000", docs, root=tmp_path)


def test_settle_keeps_older_and_matching_modules_as_they_are():
    same = {"found": True, "app": "abc1234", "module": "abc1234"}
    assert visit_deployed_app.settle(same) == (same, "")
    older = {"found": True, "app": "abc1234", "module": visit_deployed_app.OLDER}
    assert visit_deployed_app.settle(older) == (older, "")


def test_the_image_runs_the_api_without_reload():
    # 이미지의 기본 명령이 API입니다(ADR-083). 파일 감시(--reload)는 켜지 않습니다.
    dockerfile = Path("deploy/vm/Dockerfile").read_text(encoding="utf-8")
    command = dockerfile.split("CMD")[-1]
    assert command.lstrip().startswith('["uvicorn", "src.api:app"')
    assert "--reload" not in command and "streamlit" not in command


def test_image_is_rebuilt_only_when_its_inputs_change():
    # Dockerfile이 복사하는 것과 .dockerignore가 허용하는 것이 같은 목록이어야 합니다.
    rebuild = re.search(r"if changed (.+); then\n  \$C build -q api", DEPLOY).group(1).split()
    copied = re.search(r"^COPY (.+) \./$", Path("deploy/vm/Dockerfile").read_text(
        encoding="utf-8"), re.MULTILINE).group(1).split()
    assert set(copied) <= set(rebuild)
    assert {".dockerignore", "deploy/vm/Dockerfile"} <= set(rebuild)


def test_setup_installs_the_timer():
    setup = Path("deploy/vm/setup.sh").read_text(encoding="utf-8")
    assert "deploy/vm/deploy.sh" in setup
    assert "systemctl enable --now arxiv-deploy.timer" in setup


def test_the_vm_runs_no_streamlit():
    compose = yaml.safe_load(Path("deploy/vm/compose.yml").read_text(encoding="utf-8"))
    assert set(compose["services"]) == {"neo4j", "api", "caddy"}
    # 내린 컨테이너가 남아 메모리를 쓰지 않게 합니다.
    assert re.search(r"^\$C up -d --remove-orphans$", DEPLOY, re.MULTILINE)
    caddy = Path("deploy/vm/Caddyfile").read_text(encoding="utf-8")
    assert "8501" not in caddy
    assert "redir https://arxiv-graphrag.vercel.app{uri}" in caddy


def test_deploy_check_looks_at_the_vm():
    assert visit_deployed_app.API_URL == "https://8-230-25-138.sslip.io"
    workflow = yaml.safe_load(Path(".github/workflows/deployed-app.yml").read_text(
        encoding="utf-8"))
    runs = [step.get("run", "") for step in workflow["jobs"]["check"]["steps"]]
    deploy = next(run for run in runs if "--expect-commit" in run)
    assert "visit_deployed_app.py --expect-commit" in deploy
    assert "streamlit" not in deploy


def test_deploy_check_also_reads_the_weekly_ingest():
    """VM 타이머의 수집은 GitHub이 못 봅니다. 배포 점검의 둘째 단계가 읽습니다(ADR-106)."""
    workflow = yaml.safe_load(Path(".github/workflows/deployed-app.yml").read_text(
        encoding="utf-8"))
    steps = workflow["jobs"]["check"]["steps"]
    ingest = next(step for step in steps if "--ingest" in step.get("run", ""))
    # 배포 점검이 실패해도 돕니다. 한 알림에 둘 다 적히게.
    assert ingest["if"] == "${{ !cancelled() }}"
    assert workflow[True]["schedule"]  # YAML은 `on`을 True로 읽습니다


def test_ingest_sh_writes_the_status_even_when_the_run_fails():
    script = Path("deploy/vm/ingest.sh").read_text(encoding="utf-8")
    # 수집이 실패해도 set -e로 끝나지 않고 종료 코드를 상태 파일에 적습니다.
    assert "set +e" in script and "ingest_exit=$?" in script
    assert "pipeline/state/last-ingest.json" in script
    assert script.rstrip().endswith('[ "$ingest_exit" -eq 0 ] && [ "$backfill_exit" = 0 ]')

