"""CI는 `scripts/check_*.py`를 모두 문서 검사기로 돌립니다(`.github/workflows/ci.yml`).

배포 앱 점검이 한때 `check_deployed_app.py`라서 CI가 그것까지 돌렸습니다 - 실제 배포 앱을 여는
스크립트입니다. 검사기는 저장소 안만 읽어야 하므로 브라우저나 네트워크를 쓰는 것은 그 이름을 받지
못하게 둡니다.
"""

from pathlib import Path

NETWORK = ("playwright", "requests", "httpx", "urllib.request", "neo4j", "openai")


def test_no_doc_checker_reaches_outside_the_repository():
    for path in Path("scripts").glob("check_*.py"):
        source = path.read_text(encoding="utf-8")
        used = [name for name in NETWORK
                if f"import {name}" in source or f"from {name}" in source]
        assert not used, f"{path}: {used}"
