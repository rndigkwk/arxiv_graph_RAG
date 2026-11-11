"""VM API의 질문별 SLI 줄을 읽어 지연 분포와 목표 달성률을 셉니다(ADR-126).

VM에서, 저장소 폴더에서:

    docker logs arxiv-graphrag-vm-api-1 2>&1 | python3 scripts/summarize_sli.py
    docker logs --since 24h arxiv-graphrag-vm-api-1 2>&1 | python3 scripts/summarize_sli.py

표준 라이브러리만 쓰므로 호스트의 `python3`로 돕니다. 컨테이너를 다시 만들면(이미지를
다시 구운 배포) 그 전의 로그는 사라집니다. 재시작만 한 배포는 로그가 남습니다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src import sli  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("log", nargs="?", type=Path, help="로그 파일. 없으면 표준 입력")
    parser.add_argument("--target", type=float, default=sli.TARGET_SECONDS)
    parser.add_argument("--commit", help="이 커밋이 답한 질문만")
    args = parser.parse_args(argv)
    lines = (args.log.read_text(encoding="utf-8", errors="replace").splitlines()
             if args.log else sys.stdin)
    rows = sli.parse(lines)
    if args.commit:
        rows = [row for row in rows if row.get("commit") == args.commit]
    print(json.dumps(sli.summarize(rows, args.target), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
