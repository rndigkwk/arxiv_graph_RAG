"""Aura 인스턴스를 깨어 있게 유지하고, 멈췄으면 재개합니다.

Aura Free는 3일 동안 접속이 없으면 멈춥니다. 멈추면 인스턴스의 DNS 레코드가 내려가서
`13b8cb33.databases.neo4j.io`가 아예 해석되지 않고, 앱과 `evals.run`이 모두 연결에
실패합니다(2026-10-12에 평가를 돌리려다 겪었습니다). 주간 회귀 평가를 cron으로 걸려면
그 전에 이 작업이 있어야 합니다.

`chatbot`을 import하지 않습니다. 그 모듈은 import 시점에 드라이버를 만들고 벡터 인덱스
상태까지 확인하므로, Aura가 멈춘 동안에는 import 자체가 실패합니다. 이 스크립트가 필요한
상황이 바로 그때입니다.

    python scripts/keep_awake.py [--app-url URL]

NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD가 필요합니다. CLIENT_ID·CLIENT_SECRET이 있으면
멈춘 인스턴스를 재개까지 하고, 없으면 연결 실패를 그대로 보고합니다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

import requests

AURA_API = "https://api.neo4j.io"
RESUME_TIMEOUT_SECONDS = 600
POLL_SECONDS = 15

# Windows 콘솔은 기본이 cp949라 한글·기호를 쓰면 print가 UnicodeEncodeError로 죽습니다.
# CI(리눅스)에서는 문제가 없지만, 로컬에서 돌릴 때 출력 때문에 실패하지 않게 맞춰 둡니다.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")


def env(*names: str, default: str = "") -> str:
    """같은 뜻으로 쓰이는 이름들 중 먼저 채워진 값을 씁니다."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def bearer(client_id: str, client_secret: str) -> dict[str, str]:
    """Aura API 토큰을 받습니다. 쓰기 호출에는 Content-Type이 반드시 필요합니다."""
    token = requests.post(
        f"{AURA_API}/oauth/token", auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"}, timeout=30,
    )
    token.raise_for_status()
    return {
        "Authorization": f"Bearer {token.json()['access_token']}",
        # 이 헤더가 없으면 resume이 415 invalid-content-type으로 거부됩니다.
        "Content-Type": "application/json",
    }


def instance(instance_id: str, head: dict[str, str]) -> dict:
    reply = requests.get(f"{AURA_API}/v1/instances/{instance_id}", headers=head, timeout=30)
    reply.raise_for_status()
    return reply.json().get("data", {})


def only_instance(head: dict[str, str]) -> str | None:
    """AURA_INSTANCEID가 없을 때, 인스턴스가 하나면 그것을 씁니다."""
    reply = requests.get(f"{AURA_API}/v1/instances", headers=head, timeout=30)
    reply.raise_for_status()
    rows = reply.json().get("data", [])
    return rows[0]["id"] if len(rows) == 1 else None


def resume(instance_id: str, head: dict[str, str]) -> None:
    """멈춘 인스턴스를 재개하고 running이 될 때까지 기다립니다."""
    reply = requests.post(
        f"{AURA_API}/v1/instances/{instance_id}/resume", headers=head, json={}, timeout=30,
    )
    # 이미 깨어 있거나 재개 중이면 거부될 수 있습니다. 그건 실패가 아닙니다.
    if reply.status_code >= 400:
        print(f"  resume 호출 응답 {reply.status_code}: {reply.text[:200]}")
    deadline = time.monotonic() + RESUME_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = instance(instance_id, head).get("status")
        print(f"  상태 {status}")
        if status == "running":
            return
        time.sleep(POLL_SECONDS)
    raise TimeoutError(f"{RESUME_TIMEOUT_SECONDS}초 안에 running이 되지 않았습니다.")


def ping(uri: str, user: str, password: str, database: str) -> int:
    """읽기 쿼리 하나로 유휴 타이머를 되돌리고 연결을 확인합니다."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session(database=database) as session:
            return session.run("MATCH (p:ArxivPaper) RETURN count(p) AS papers").single()["papers"]
    finally:
        driver.close()


def visit(url: str) -> None:
    """배포 앱에 트래픽을 보냅니다.

    잠들기를 늦추는 효과는 있지만, **이미 잠든 앱을 깨우지는 못합니다.** 깨우기는 화면의
    버튼을 눌러야 하고 그건 헤드리스 브라우저가 필요합니다. 앱에는 연결 실패 시 보여 줄
    대체 화면이 있고 README에 깨우는 방법이 적혀 있으므로, 여기서는 실패해도 넘어갑니다.
    """
    try:
        reply = requests.get(url, timeout=60)
        print(f"앱 방문 {url} → HTTP {reply.status_code}")
    except requests.RequestException as exc:
        print(f"앱 방문 실패(치명적이지 않음): {type(exc).__name__}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", help="배포된 Streamlit 앱 주소. 주면 트래픽도 보냅니다.")
    args = parser.parse_args()

    uri = env("NEO4J_URI")
    user = env("NEO4J_USERNAME", "NEO4J_USER")
    password = os.getenv("NEO4J_PASSWORD") or ""
    database = env("NEO4J_DATABASE", default="neo4j")
    if not (uri and user and password):
        print("NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD가 필요합니다.", file=sys.stderr)
        return 2

    # ping만 감쌉니다. else에 두지 않으면 출력·방문 단계의 오류까지 연결 실패로 오인합니다.
    try:
        papers = ping(uri, user, password, database)
    except Exception as exc:
        print(f"Aura 연결 실패: {type(exc).__name__}: {str(exc)[:200]}")
    else:
        print(f"Aura 연결 OK. ArxivPaper {papers:,}")
        if args.app_url:
            visit(args.app_url)
        return 0

    client_id, client_secret = env("CLIENT_ID"), env("CLIENT_SECRET")
    if not (client_id and client_secret):
        print("CLIENT_ID·CLIENT_SECRET이 없어 재개를 시도할 수 없습니다.", file=sys.stderr)
        return 1

    head = bearer(client_id, client_secret)
    instance_id = env("AURA_INSTANCEID") or only_instance(head)
    if not instance_id:
        print("재개할 인스턴스를 특정할 수 없습니다. AURA_INSTANCEID를 설정하세요.",
              file=sys.stderr)
        return 1

    status = instance(instance_id, head).get("status")
    print(f"인스턴스 {instance_id} 상태 {status}. 재개합니다.")
    resume(instance_id, head)

    papers = ping(uri, user, password, database)
    print(f"재개 후 Aura 연결 OK. ArxivPaper {papers:,}")
    if args.app_url:
        visit(args.app_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
