"""VM이 main을 따라가는 장치(ADR-080).

- fast-forward만 합니다. VM에서 누가 커밋했으면 덮어쓰지 않고 멈춥니다.
- 타이머가 겹치면 뒤의 것이 나갑니다(빌드가 2분을 넘을 수 있습니다).
- 코드는 마운트돼 있고 파일 감시를 껐으므로, 배포마다 앱을 재시작합니다.
- 의존성이 바뀐 때만 이미지를 다시 굽습니다.
- 배포 점검이 VM 주소를 봅니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts import visit_deployed_app

DEPLOY = Path("deploy/vm/deploy.sh").read_text(encoding="utf-8")


def test_deploy_only_fast_forwards():
    assert "git merge -q --ff-only origin/main" in DEPLOY
    assert "reset" not in DEPLOY and "--force" not in DEPLOY


def test_overlapping_timer_runs_step_aside():
    assert "flock -n 9 || exit 0" in DEPLOY


def test_every_deploy_restarts_the_app():
    assert re.search(r"^\$C restart app$", DEPLOY, re.MULTILINE)
    assert "--server.fileWatcherType=none" in Path("deploy/vm/Dockerfile").read_text(
        encoding="utf-8")


def test_image_is_rebuilt_only_when_its_inputs_change():
    # Dockerfile이 복사하는 것과 .dockerignore가 허용하는 것이 같은 목록이어야 합니다.
    rebuild = re.search(r"if changed (.+); then\n  \$C build -q app", DEPLOY).group(1).split()
    copied = re.search(r"^COPY (.+) \./$", Path("deploy/vm/Dockerfile").read_text(
        encoding="utf-8"), re.MULTILINE).group(1).split()
    assert set(copied) <= set(rebuild)
    assert {".dockerignore", "deploy/vm/Dockerfile"} <= set(rebuild)


def test_setup_installs_the_timer():
    setup = Path("deploy/vm/setup.sh").read_text(encoding="utf-8")
    assert "deploy/vm/deploy.sh" in setup
    assert "systemctl enable --now arxiv-deploy.timer" in setup


def test_deploy_check_looks_at_the_vm():
    assert visit_deployed_app.APP_URL == "https://8-230-25-138.sslip.io/"
    workflow = yaml.safe_load(Path(".github/workflows/deployed-app.yml").read_text(
        encoding="utf-8"))
    step = workflow["jobs"]["check"]["steps"][-1]["run"]
    assert "visit_deployed_app.py --expect-commit" in step
    assert "streamlit.app" not in step

