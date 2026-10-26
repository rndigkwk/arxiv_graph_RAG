"""Aura 그래프를 VM의 Neo4j로 옮깁니다 - 내보내기는 읽기 전용, 싣기는 빈 DB에만.

    uv run python scripts/move_graph.py export --out .local/move
    # VM에서, 앱 컨테이너 안에서(docs/GCP_VM.md)
    python scripts/move_graph.py load --dir /snapshot
    python scripts/move_graph.py verify --dir /snapshot
    # 같은 사례를 양쪽에서 `evals.run --mode retrieval`로 돌린 뒤
    uv run python scripts/move_graph.py diff --before aura.json --after vm.json

**스키마도 Aura에서 읽습니다.** `local_graph_test.py import`는 로컬 실험용 이름
(`local_paper_id`)으로 인덱스 셋과 벡터 인덱스만 만듭니다. 앱이 제목·저자를 찾는
전문 검색 인덱스 둘(`chatbot.TITLE_FULLTEXT_INDEX`)은 만들지 않습니다. 그대로 옮기면
앱이 뜨기는 하지만 제목 해석이 `CONTAINS`로 떨어집니다(ADR-024). 그래서 내보낼 때
`SHOW INDEXES`·`SHOW CONSTRAINTS`를 함께 적고, 실을 때 **같은 이름·같은 옵션으로**
다시 만듭니다.

**한 가지는 똑같이 만들 수 없습니다.** Aura의 벡터 인덱스는 `vector.quantization.type: BINARY`이고,
Neo4j 5.26 Community에는 그 설정이 없습니다. 차원·유사도만 옮기고 나머지는 Community 기본값을
씁니다. 어떤 옵션을 버렸는지 `load_report.json`의 `dropped_options`에 남깁니다.

**쓰는 곳을 가립니다.** `load`는 Aura 주소(`*.databases.neo4j.io`)에 쓰지 않고, 비어 있지 않은
DB에도 쓰지 않습니다. 다시 실으려면 VM의 볼륨을 사람이 직접 비웁니다 - 이 스크립트는 지우는
명령을 담지 않습니다.

0원입니다. OpenAI를 부르지 않습니다(임베딩은 Aura에 저장된 값을 그대로 옮깁니다).
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import READ_ACCESS, GraphDatabase, Query

sys.path.insert(0, str(Path(__file__).resolve().parent))

from local_graph_test import (  # noqa: E402
    COUNTS,
    EXPORTS,
    IMPORTS,
    digest,
    read_rows,
    remote_driver,
    save_json,
    serialize,
)

LABELS = ("ArxivPaper", "ArxivAuthorName")
RELATIONSHIPS = ("AUTHORED", "REFERENCES")
# 5.26 Community가 받는 벡터 인덱스 설정(2026-10-26, 거절 메시지가 나열한 것). 나머지
# (`vector.quantization.type: BINARY`, `vector.default_search_expansion_factor`)는 Aura 전용이라
# 버립니다. `vector.quantization.enabled`는 Aura가 주지 않으므로 Community 기본값(켜짐)입니다.
VECTOR_OPTIONS = ("vector.dimensions", "vector.similarity_function",
                  "vector.hnsw.m", "vector.hnsw.ef_construction")
INVENTORY = """
CALL () { CALL db.labels() YIELD label RETURN collect(label) AS labels }
CALL () { CALL db.relationshipTypes() YIELD relationshipType
          RETURN collect(relationshipType) AS relationships }
RETURN labels, relationships
"""
PROPERTY_TYPES = """
MATCH (p:ArxivPaper) WITH p LIMIT 500
UNWIND keys(p) AS key
RETURN key, collect(DISTINCT valueType(p[key])) AS types ORDER BY key
"""


def schema(session) -> dict[str, Any]:
    """옮길 스키마: 인덱스·제약(자동 LOOKUP 제외), 라벨·관계 이름, 논문 속성의 값 타입."""
    indexes = [dict(r) for r in session.run(
        "SHOW INDEXES YIELD name, type, labelsOrTypes, properties, options, owningConstraint "
        "WHERE type <> 'LOOKUP' RETURN * ORDER BY name")]
    constraints = [dict(r) for r in session.run(
        "SHOW CONSTRAINTS YIELD name, type, labelsOrTypes, properties RETURN * ORDER BY name")]
    names = session.run(INVENTORY).single()
    return {
        "indexes": indexes,
        "constraints": constraints,
        "labels": sorted(names["labels"]),
        "relationships": sorted(names["relationships"]),
        "paper_property_types": {r["key"]: sorted(r["types"]) for r in session.run(PROPERTY_TYPES)},
    }


def export(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if (out / "snapshot.json").exists():
        raise SystemExit(f"{out}에 이미 스냅샷이 있습니다. 다른 디렉터리를 고르세요.")
    driver, database = remote_driver()
    started = time.perf_counter()
    manifest: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(),
                                "files": {}}
    with driver, driver.session(database=database, default_access_mode=READ_ACCESS,
                                fetch_size=1000) as session:
        manifest["counts"] = dict(session.run(COUNTS).single())
        manifest["schema"] = schema(session)
        unexpected = (set(manifest["schema"]["labels"]) - set(LABELS)
                      | set(manifest["schema"]["relationships"]) - set(RELATIONSHIPS))
        if unexpected:
            raise SystemExit(f"옮기지 않는 라벨·관계가 Aura에 있습니다: {sorted(unexpected)}")
        for label, cypher in EXPORTS.items():
            path = out / f"{label}.jsonl.gz"
            partial = path.with_suffix(path.suffix + ".partial")
            count = 0
            with gzip.open(partial, "wt", encoding="utf-8", compresslevel=1) as handle:
                for record in session.run(Query(cypher, timeout=600)):
                    row = record["row"] if label == "papers" else dict(record)
                    handle.write(json.dumps(row, ensure_ascii=False, default=serialize) + "\n")
                    count += 1
                    if count % 10000 == 0:
                        print(f"export {label}: {count}", flush=True)
            partial.replace(path)
            if count != manifest["counts"][label]:
                raise SystemExit(f"{label}: 센 수 {manifest['counts'][label]}, 내보낸 수 {count}")
            manifest["files"][label] = {"path": path.name, "rows": count,
                                        "bytes": path.stat().st_size, "sha256": digest(path)}
        if dict(session.run(COUNTS).single()) != manifest["counts"]:
            raise SystemExit("내보내는 동안 Aura가 바뀌었습니다(주간 수집?). 다시 내보내세요.")
    manifest["elapsed_seconds"] = round(time.perf_counter() - started, 1)
    save_json(out / "snapshot.json", manifest)
    print(f"내보냄: {manifest['counts']} ({manifest['elapsed_seconds']}초)", flush=True)


def quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def vector_value(value: Any) -> str:
    """정수는 정수로, 유사도 이름은 따옴표 친 소문자로(SHOW INDEXES는 'COSINE'을 돌려줍니다)."""
    if isinstance(value, str):
        return f"'{value.lower()}'"
    return str(int(value))


def schema_statements(spec: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Aura 스키마를 같은 이름의 CREATE 문으로 바꿉니다. 두 번째 값은 버린 옵션입니다."""
    statements: list[str] = []
    dropped: list[str] = []
    for c in spec["constraints"]:
        if c["type"] != "NODE_PROPERTY_UNIQUENESS" or len(c["properties"]) != 1:
            raise SystemExit(f"옮길 줄 모르는 제약입니다: {c}")
        label, prop = c["labelsOrTypes"][0], c["properties"][0]
        statements.append(f"CREATE CONSTRAINT {quote(c['name'])} IF NOT EXISTS "
                          f"FOR (n:{quote(label)}) REQUIRE n.{quote(prop)} IS UNIQUE")
    for index in spec["indexes"]:
        if index.get("owningConstraint"):
            continue  # 제약이 만듭니다.
        name, label = quote(index["name"]), quote(index["labelsOrTypes"][0])
        props = index["properties"]
        config = (index.get("options") or {}).get("indexConfig") or {}
        if index["type"] == "RANGE":
            statements.append(f"CREATE INDEX {name} IF NOT EXISTS FOR (n:{label}) "
                              f"ON ({', '.join('n.' + quote(p) for p in props)})")
        elif index["type"] == "FULLTEXT":
            analyzer = config.get("fulltext.analyzer", "standard-no-stop-words")
            statements.append(
                f"CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR (n:{label}) "
                f"ON EACH [{', '.join('n.' + quote(p) for p in props)}] "
                f"OPTIONS {{indexConfig: {{`fulltext.analyzer`: '{analyzer}'}}}}")
        elif index["type"] == "VECTOR":
            dropped += sorted(f"{index['name']}.{k}={v}" for k, v in config.items()
                              if k not in VECTOR_OPTIONS)
            kept = ", ".join(f"`{k}`: {vector_value(config[k])}"
                             for k in VECTOR_OPTIONS if k in config)
            statements.append(f"CREATE VECTOR INDEX {name} IF NOT EXISTS FOR (n:{label}) "
                              f"ON n.{quote(props[0])} OPTIONS {{indexConfig: {{{kept}}}}}")
        else:
            raise SystemExit(f"옮길 줄 모르는 인덱스입니다: {index['name']} ({index['type']})")
    return statements, dropped


def target_driver():
    """앱과 같은 환경변수로 VM의 DB에 붙습니다. Aura에는 쓰지 않습니다."""
    uri = os.environ["NEO4J_URI"]
    if "databases.neo4j.io" in uri:
        raise SystemExit("NEO4J_URI가 Aura를 가리킵니다. load는 VM의 DB에만 씁니다.")
    user = os.environ.get("NEO4J_USERNAME") or os.environ.get("NEO4J_USER") or "neo4j"
    driver = GraphDatabase.driver(uri, auth=(user, os.environ["NEO4J_PASSWORD"]),
                                  connection_timeout=10)
    return driver, os.environ.get("NEO4J_DATABASE") or "neo4j"


def load_manifest(directory: Path) -> dict[str, Any]:
    manifest = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    for spec in manifest["files"].values():
        if digest(directory / spec["path"]) != spec["sha256"]:
            raise SystemExit(f"체크섬이 다릅니다: {spec['path']}")
    return manifest


def load(directory: Path) -> None:
    manifest = load_manifest(directory)
    statements, dropped = schema_statements(manifest["schema"])
    driver, database = target_driver()
    started = time.perf_counter()
    stages: dict[str, float] = {}
    with driver, driver.session(database=database) as session:
        if session.run("MATCH (n) RETURN count(n) > 0 AS any").single()["any"]:
            raise SystemExit("대상 DB가 비어 있지 않습니다. 볼륨을 직접 비운 뒤 다시 실으세요.")
        for statement in statements:
            session.run(statement).consume()
        for label, cypher in IMPORTS.items():
            stage = time.perf_counter()
            size = 100 if label == "papers" else 1000
            batch: list[dict[str, Any]] = []
            count = 0
            for row in read_rows(directory / manifest["files"][label]["path"]):
                batch.append(row)
                if len(batch) >= size:
                    session.run(Query(cypher, timeout=300), rows=batch).consume()
                    count += len(batch)
                    batch = []
                    if count % 20000 == 0:
                        print(f"load {label}: {count}", flush=True)
            if batch:
                session.run(Query(cypher, timeout=300), rows=batch).consume()
            stages[label] = round(time.perf_counter() - stage, 1)
        session.run("CALL db.awaitIndexes(1800)").consume()
        counts = dict(session.run(COUNTS).single())
    if counts != manifest["counts"]:
        raise SystemExit(f"실은 수가 다릅니다: {counts} != {manifest['counts']}")
    save_json(directory / "load_report.json", {
        "counts": counts, "stage_seconds": stages, "dropped_options": dropped,
        "elapsed_seconds": round(time.perf_counter() - started, 1)})
    print(f"실음: {counts}. 버린 옵션: {dropped or '없음'}", flush=True)


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    """스키마 차이를 사람이 읽을 문장으로. 벡터 인덱스의 버린 옵션은 차이로 세지 않습니다."""
    problems = []

    def shape(spec):
        return {i["name"]: (i["type"], tuple(i["labelsOrTypes"]), tuple(i["properties"]))
                for i in spec["indexes"]}

    want, have = shape(expected), shape(actual)
    for name in sorted(want.keys() - have.keys()):
        problems.append(f"인덱스 없음: {name}")
    for name in sorted(want.keys() & have.keys()):
        if want[name] != have[name]:
            problems.append(f"인덱스 모양이 다름: {name} {want[name]} != {have[name]}")
    want_fulltext = {i["name"]: i["options"]["indexConfig"].get("fulltext.analyzer")
                     for i in expected["indexes"] if i["type"] == "FULLTEXT"}
    for index in actual["indexes"]:
        name = index["name"]
        if name in want_fulltext and \
                index["options"]["indexConfig"].get("fulltext.analyzer") != want_fulltext[name]:
            problems.append(f"분석기가 다름: {name}")
    if {c["name"] for c in expected["constraints"]} != {c["name"] for c in actual["constraints"]}:
        problems.append("제약 이름이 다름")
    if expected["paper_property_types"] != actual["paper_property_types"]:
        want_types, have_types = expected["paper_property_types"], actual["paper_property_types"]
        for key in sorted(set(want_types) | set(have_types)):
            a, b = want_types.get(key), have_types.get(key)
            if a != b:
                problems.append(f"속성 타입이 다름: {key} {a} != {b}")
    return problems


def verify(directory: Path) -> int:
    manifest = json.loads((directory / "snapshot.json").read_text(encoding="utf-8"))
    driver, database = target_driver()
    with driver, driver.session(database=database, default_access_mode=READ_ACCESS) as session:
        counts = dict(session.run(COUNTS).single())
        actual = schema(session)
        # 저장된 벡터로 자기 자신을 찾는지 - 인덱스가 실제로 쓰이는지 봅니다.
        probe = session.run("MATCH (p:ArxivPaper) WHERE p.embedding IS NOT NULL "
                            "RETURN p.arxiv_id AS id, p.embedding AS v LIMIT 1").single()
        vector_index = next(i["name"] for i in actual["indexes"] if i["type"] == "VECTOR")
        top = session.run("CALL db.index.vector.queryNodes($index, 1, $v) YIELD node "
                          "RETURN node.arxiv_id AS id", index=vector_index, v=probe["v"]).single()
    problems = compare(manifest["schema"], actual)
    if counts != manifest["counts"]:
        problems.append(f"수가 다름: {counts} != {manifest['counts']}")
    if top is None or top["id"] != probe["id"]:
        problems.append("벡터 인덱스가 저장된 벡터로 자기 논문을 찾지 못합니다")
    report = {"counts": counts, "problems": problems,
              "checked_at": datetime.now(timezone.utc).isoformat()}
    save_json(directory / "verify_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if problems else 0


def retrieval_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """같은 사례를 두 DB에서 검색한 결과가 얼마나 같은지. 순위 지표는 보고서의 요약을 씁니다."""
    pairs = list(zip(before["results"], after["results"], strict=True))
    if any(a["id"] != b["id"] for a, b in pairs):
        raise SystemExit("두 보고서의 사례 순서가 다릅니다")
    k = before["top_k"]
    changed = [a["id"] for a, b in pairs if a["retrieved_ids"][:1] != b["retrieved_ids"][:1]]
    return {
        "cases": len(pairs),
        "top_k": k,
        "identical_lists": sum(a["retrieved_ids"] == b["retrieved_ids"] for a, b in pairs),
        "same_top1": len(pairs) - len(changed),
        "top1_changed": changed,
        "mean_overlap_at_k": round(statistics.fmean(
            len(set(a["retrieved_ids"]) & set(b["retrieved_ids"])) / k for a, b in pairs), 3),
        "summary": {key: {"before": round(before["summary"][key], 4),
                          "after": round(after["summary"][key], 4)}
                    for key in ("hit_at_k", "recall_at_k", "reciprocal_rank")},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Aura 그래프를 VM의 Neo4j로 옮깁니다.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("export").add_argument("--out", type=Path, required=True)
    sub.add_parser("load").add_argument("--dir", type=Path, required=True)
    sub.add_parser("verify").add_argument("--dir", type=Path, required=True)
    diff = sub.add_parser("diff")
    diff.add_argument("--before", type=Path, required=True)
    diff.add_argument("--after", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "diff":
        result = retrieval_diff(*(json.loads(p.read_text(encoding="utf-8"))
                                  for p in (args.before, args.after)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "export":
        export(args.out)
        return 0
    if args.command == "load":
        load(args.dir)
        return 0
    return verify(args.dir)


if __name__ == "__main__":
    raise SystemExit(main())
