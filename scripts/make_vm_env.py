"""VM에 올릴 `.env`를 로컬에서 만듭니다 - 값은 화면에 찍지 않습니다.

    uv run python scripts/make_vm_env.py --domain 34-64-12-34.sslip.io

`.local/vm.env`에 씁니다(Git 밖). VM으로는 `gcloud compute scp`로 옮깁니다(docs/GCP_VM.md).

- `OPENAI_API_KEY`는 로컬 `.env`에서 가져옵니다.
- `NEO4J_PASSWORD`는 **새로 만듭니다.** Aura 비밀번호를 VM에 두지 않습니다 - VM의 앱이 Aura에
  닿을 일이 없고, 있으면 `move_graph.py load`의 가드만 남습니다.
- `LANGFUSE_TRACING="0"`. 무료 한도 때문에 꺼 둡니다(0단계와 같은 선). 켜려면 키 둘과 함께 직접
  바꿉니다(`pipeline/tracing.py`).
- GDS 세션 키(`CLIENT_*`)는 넣지 않습니다. Aura 전용이고, 기본 엔진(직접 계산)은 없어도 됩니다.

이미 있으면 덮어쓰지 않습니다. 비밀번호가 바뀌면 VM의 Neo4j 볼륨과 맞지 않게 됩니다.
"""

from __future__ import annotations

import argparse
import re
import secrets
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / ".local" / "vm.env"


def render(openai_key: str, domain: str, password: str) -> str:
    return "\n".join([
        f"OPENAI_API_KEY={openai_key}",
        f"NEO4J_PASSWORD={password}",
        f"APP_DOMAIN={domain}",
        'LANGFUSE_TRACING="0"',
        "",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="VM용 .env를 .local/vm.env에 만듭니다.")
    parser.add_argument("--domain", required=True, help="예: 34-64-12-34.sslip.io")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9.-]+", args.domain):
        parser.error("도메인에는 소문자·숫자·점·하이픈만 씁니다")
    if OUTPUT.exists():
        parser.error(f"{OUTPUT}가 이미 있습니다. VM의 Neo4j 비밀번호와 맞추려면 그대로 쓰세요.")
    key = dotenv_values(ROOT / ".env").get("OPENAI_API_KEY")
    if not key:
        parser.error(".env에 OPENAI_API_KEY가 없습니다")
    OUTPUT.parent.mkdir(exist_ok=True)
    # Windows에서 그냥 쓰면 CRLF가 되고, VM의 compose가 비밀번호 끝에 CR을 붙여 읽습니다.
    OUTPUT.write_text(render(key, args.domain, secrets.token_hex(20)), encoding="utf-8",
                      newline="\n")
    print(f"{OUTPUT}를 만들었습니다(값은 찍지 않습니다).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
