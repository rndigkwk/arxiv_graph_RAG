"""제목·저자명 전문 검색 인덱스를 만들거나 지웁니다.

    uv run python scripts/fulltext_index.py --create
    uv run python scripts/fulltext_index.py            # 상태만 봅니다
    uv run python scripts/fulltext_index.py --drop     # 되돌립니다

**왜 필요한가.** 모델이 질문에 적힌 제목을 arxiv_id로 바꿀 때 지금은
`toLower(p.title) CONTAINS "구절"`을 씁니다. 그 구절이 짧으면 **여러 편이 걸립니다** —
2026-10-18에 `coauthor-04`가 `CONTAINS "when to plan"`으로 두 편을 잡고(의도한 논문과
"Learning When to Plan: ...") 두 시드의 공저자를 모두 펼쳐 **25편**을 인용했습니다
(근거 precision 1.000 → 0.040, ADR-024). 같은 결함이 `reciprocal_rank` 상한 0.7000과
`citing`의 RAGAS Context Precision 0.5046도 만들었습니다.

`CONTAINS`는 **순위가 없습니다.** 걸리거나 안 걸리거나입니다. 전문 검색 인덱스는 관련도
점수를 주므로 "둘 중 어느 쪽이 질문이 말한 논문인가"에 답할 수 있습니다.

**Aura 한도에 노드·관계를 더하지 않습니다.** 한도는 노드 200,000·관계 400,000 카운트이고
(관계가 84.6%로 먼저 찹니다, [ADR-001](../docs/adr/ADR-001-neo4j-graph-and-vector.md))
인덱스는 그 카운트를 늘리지 않습니다. 저장 용량은 늘어나므로 `--drop`으로 되돌릴 수 있게
해 둡니다.

**앱 메모리는 0입니다.** 서버 쪽 인덱스라 Streamlit 상주에 아무것도 더하지 않습니다
([ADR-016](../docs/adr/ADR-016-stay-on-streamlit.md)의 690MB 제약과 무관합니다).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# `scripts/`에서 돌리므로 저장소 최상위를 경로에 넣어야 `chatbot`을 가져올 수 있습니다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TITLE_INDEX = "arxiv_paper_title_fulltext"
AUTHOR_INDEX = "arxiv_author_name_fulltext"

# 분석기는 기본값(`standard-no-stop-words`)을 쓰지 않습니다. 논문 제목에는 "When to Plan"처럼
# 불용어가 의미를 가르는 경우가 있어서, 불용어를 지우면 바로 그 구별이 사라집니다.
CREATE = {
    TITLE_INDEX: (
        f"CREATE FULLTEXT INDEX {TITLE_INDEX} IF NOT EXISTS "
        "FOR (p:ArxivPaper) ON EACH [p.title] "
        "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard'}}"
    ),
    AUTHOR_INDEX: (
        f"CREATE FULLTEXT INDEX {AUTHOR_INDEX} IF NOT EXISTS "
        "FOR (a:ArxivAuthorName) ON EACH [a.name] "
        "OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard'}}"
    ),
}

SHOW = (
    "SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, state, options "
    "WHERE name IN $names RETURN *"
)


def status(chatbot) -> list[dict]:
    rows = chatbot.run_read(SHOW, {"names": list(CREATE)})
    return [
        {
            "name": row["name"],
            "type": row["type"],
            "labels": row["labelsOrTypes"],
            "properties": row["properties"],
            "state": row["state"],
            "analyzer": (row.get("options") or {}).get("indexConfig", {}).get(
                "fulltext.analyzer"
            ),
        }
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="전문 검색 인덱스를 만들거나 지웁니다.")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--drop", action="store_true")
    parser.add_argument("--only", choices=sorted(CREATE), help="하나만 다룹니다.")
    args = parser.parse_args()
    if args.create and args.drop:
        parser.error("--create와 --drop을 함께 줄 수 없습니다.")

    from pipeline.console import use_utf8_stdout

    use_utf8_stdout()
    import chatbot

    names = [args.only] if args.only else list(CREATE)
    print("전:", json.dumps(status(chatbot), ensure_ascii=False))

    if args.create:
        for name in names:
            # 인덱스 생성은 쓰기이므로 `run_read`가 아니라 드라이버로 직접 보냅니다.
            chatbot.driver.execute_query(
                CREATE[name], database_=chatbot.neo4j_database
            )
            print(f"만들었습니다(또는 이미 있었습니다): {name}")
    elif args.drop:
        for name in names:
            chatbot.driver.execute_query(
                f"DROP INDEX {name} IF EXISTS", database_=chatbot.neo4j_database
            )
            print(f"지웠습니다: {name}")

    after = status(chatbot)
    print("후:", json.dumps(after, ensure_ascii=False, indent=2))
    if args.create:
        not_online = [row["name"] for row in after if row["state"] != "ONLINE"]
        if not_online:
            print(f"아직 ONLINE이 아닙니다: {not_online}. 잠시 뒤 다시 확인하세요.")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
