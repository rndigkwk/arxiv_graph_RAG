"""그래프를 두 배로 키웠을 때 무엇이 먼저 모자라는지 잽니다.

    uv run python scripts/measure_double_graph.py --runs 8

2026-10-26에 "Aura Free 한도를 넘겨 두 배로 키우려면 어디에 올리나"를 두고 추정 두 개가
열 배 차이로 맞섰습니다. 하나는 10-14 로컬 실측(`LOCAL_DOCKER_RESULT.md`)의 컨테이너 피크
1.745GiB를 두 배 해서 "DB 혼자 4GB"라 했고, 다른 하나는 원시 데이터 160MB를 두 배 해서
"2GB급이면 된다"고 했습니다. **둘 다 측정이 아닙니다.** 앞의 것은 heap 1G·page cache 1G로
**고정한 설정**의 그림자이고, 뒤의 것은 인덱스를 빼고 셌습니다.

그래서 같은 컨테이너에서 **1배를 재고, 그 위에 거울 사본을 얹어 2배를 잽니다.** 다른 날의
수를 빼지 않습니다 - 비교는 한 배치 안에서만 합니다.

## 무엇을 재는가

- **저장소 크기를 셋으로 나눠서.** 그래프 저장소, 스키마 인덱스(벡터 인덱스 포함), 트랜잭션
  로그. 10-14 측정은 `/data` 1,268MiB 중 771MiB가 로그였습니다 - 합쳐 두 배 하면 틀립니다.
  page cache가 담아야 하는 것은 저장소이고, 벡터 인덱스는 page cache 밖(OS 파일 캐시)에
  올라갑니다.
- **앱이 실제로 돌리는 두 질의**(`chatbot.PAPER_ROWS`, `CITATION_EDGES`)의 지연을 30초
  타임아웃에 대어서. 두 배가 용량보다 **타임아웃을 먼저** 건드리는지가 이 측정의 질문입니다.
- **앱 쪽 메모리.** 인용망 배열을 만든 뒤의 Python 상주 메모리를 Streamlit Cloud의
  보장치(690MB)에 대어서. DB를 옮겨도 앱은 Streamlit Cloud에 남으므로 이쪽도 한도입니다.

## 거울 사본이란

원본 행마다 `arxiv_id`와 저자 이름에 `mirror:`를 붙인 사본을 적재합니다. 노드·관계가 정확히
두 배가 되고 원본과 서로 이어지지 않습니다. **실제 두 배와 다른 점 셋**을 보고서에 적습니다 -
저자가 전부 새 사람이고(실제로는 겹칩니다, 그래서 상한), 인용망이 두 덩어리이고, 임베딩이 원본과
똑같습니다.

## 지키는 선

- **Aura에 접속하지 않습니다.** 10-14에 Aura에서 읽기 전용으로 받아 둔 `.local/` 스냅샷만
  씁니다. 체크섬이 맞지 않으면 시작하지 않습니다. OpenAI도 부르지 않습니다(0원).
- **쓰는 곳은 루프백 하나로 고정합니다**(`bolt://127.0.0.1:27687`). 1배 로컬 DB(17687)와
  다른 컨테이너·볼륨입니다(`compose.double.yml`).
- **빈 DB에서만 시작합니다.** 이미 무언가 있으면 1배 크기를 잴 수 없으므로 거부합니다. 지우는
  일은 이 스크립트가 하지 않습니다.
- 결과에는 개수·크기·초만 남깁니다. 논문 내용은 보고서에 들어가지 않습니다.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import local_graph_test as base  # noqa: E402
from scripts.measure_graph_load import STATEMENT_TIMEOUT, summarize, timed  # noqa: E402

URI = "bolt://127.0.0.1:27687"
CONTAINER = "arxiv-graphrag-double"
OUTPUT = Path("reports/double_graph.json")
AURA_BASELINE = Path("reports/graph_load_latency.json")
MIRROR = "mirror:"
# Streamlit Cloud가 보장하는 메모리(ROADMAP 8-1-1절, measure_app_memory.py와 같은 값).
STREAMLIT_GUARANTEED_MB = 690
PAGECACHE_MIB = 1024  # compose.double.yml의 server.memory.pagecache.size
DATA_PATHS = {
    "store": "/data/databases/neo4j",
    "schema_index": "/data/databases/neo4j/schema",
    "transaction_logs": "/data/transactions/neo4j",
}
APP_QUERIES = ("PAPER_ROWS", "CITATION_EDGES")
VECTOR_SEED = "2303.11366"


def mirror_row(label: str, row: dict[str, Any]) -> dict[str, Any]:
    """원본 행의 거울 사본입니다. 식별자만 바꾸고 나머지는 그대로 둡니다."""
    if label == "papers":
        return {**row, "arxiv_id": MIRROR + row["arxiv_id"]}
    if label == "authors":
        return {"name": MIRROR + row["name"]}
    if label == "authored":
        return {"author": MIRROR + row["author"], "paper": MIRROR + row["paper"]}
    if label == "references":
        return {"citing": MIRROR + row["citing"], "cited": MIRROR + row["cited"]}
    raise ValueError(f"모르는 종류입니다: {label}")


def require_empty(counts: dict[str, int]) -> None:
    """1배 크기는 빈 DB에서 시작해야 잴 수 있습니다."""
    if any(counts.values()):
        raise SystemExit(
            f"{CONTAINER}가 비어 있지 않습니다({counts}). 1배를 다시 재려면 볼륨을 직접 "
            "지운 뒤 컨테이너를 새로 띄우세요 - 이 스크립트는 지우지 않습니다."
        )


def memory_gib(usage: str) -> float:
    """`docker stats`의 `MemUsage`(예: "1.745GiB / 4GiB")에서 앞쪽을 GiB로 읽습니다."""
    used = usage.split("/")[0].strip()
    number, unit = re.fullmatch(r"([\d.]+)\s*([KMG]i?B|B)", used).groups()
    scale = {"B": 1 / 1024**3, "KiB": 1 / 1024**2, "MiB": 1 / 1024, "GiB": 1,
             "kB": 1e3 / 1024**3, "MB": 1e6 / 1024**3, "GB": 1e9 / 1024**3}
    return float(number) * scale[unit]


def parse_du(output: str) -> dict[str, int]:
    """`du -sk 경로...`의 출력을 {경로: KiB}로 읽습니다."""
    sizes = {}
    for line in output.strip().splitlines():
        kib, path = line.split(maxsplit=1)
        sizes[path.strip()] = int(kib)
    return sizes


def app_queries(source: Path = ROOT / "src" / "chatbot.py") -> dict[str, str]:
    """앱이 PageRank 탭에서 돌리는 두 질의를 **임포트하지 않고** 읽습니다.

    `chatbot`을 불러오면 `.env`와 드라이버가 따라옵니다. 재는 쪽 프로세스는 깨끗하게 두고,
    앱 경로는 따로 자식 프로세스에서 잽니다(`app_path`).
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            name = getattr(node.targets[0], "id", None)
            if name in APP_QUERIES and isinstance(node.value, ast.Constant):
                found[name] = node.value.value
    missing = set(APP_QUERIES) - set(found)
    if missing:
        raise ValueError(f"chatbot.py에서 질의를 찾지 못했습니다: {sorted(missing)}")
    return found


def growth(one: float | None, two: float | None) -> float | None:
    if not one or two is None:
        return None
    return round(two / one, 2)


def project(aura: dict[str, Any], ratio: float | None) -> dict[str, Any] | None:
    """Aura에서 잰 지연에 **같은 배치의** 로컬 증가율을 곱합니다. 측정이 아니라 추정입니다."""
    if not ratio or "p95" not in aura:
        return None
    p95 = round(aura["p95"] * ratio, 2)
    return {"aura_p95_measured": aura["p95"], "local_growth": ratio,
            "aura_p95_projected": p95,
            "headroom_ratio_projected": round(STATEMENT_TIMEOUT / p95, 2)}


class Sampler:
    """적재하는 동안 DB 컨테이너의 메모리를 몇 초마다 기록합니다. 정확한 순간 최대가 아닙니다."""

    def __init__(self, interval: float = 2.0):
        self.interval = interval
        self.samples: list[float] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self._stop.is_set():
            stats = docker_stats()
            if "MemUsage" in stats:
                self.samples.append(memory_gib(stats["MemUsage"]))
            self._stop.wait(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=30)

    def summary(self) -> dict[str, Any]:
        return {"samples": len(self.samples),
                "max_gib": round(max(self.samples), 3) if self.samples else None}


def docker_stats() -> dict[str, Any]:
    proc = subprocess.run(
        ["docker", "stats", CONTAINER, "--no-stream", "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(proc.stdout) if proc.returncode == 0 and proc.stdout.strip() else {}


def data_sizes() -> dict[str, float]:
    # 경로마다 따로 부릅니다. 한 번에 넘기면 du가 이미 센 하위 디렉터리(schema)를 다시
    # 적지 않습니다 - 2026-10-26 첫 실행이 그 자리에서 KeyError로 멈췄습니다.
    kib = {}
    for path in DATA_PATHS.values():
        proc = subprocess.run(["docker", "exec", CONTAINER, "du", "-sk", path],
                              capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            raise RuntimeError(f"du 실패: {proc.stderr.strip()[:200]}")
        kib.update(parse_du(proc.stdout))
    sizes = {name: round(kib[path] / 1024, 1) for name, path in DATA_PATHS.items()}
    # schema는 store 안에 있습니다. 그래프 저장소만 따로 봅니다.
    sizes["graph_store"] = round(sizes["store"] - sizes["schema_index"], 1)
    return sizes


def driver():
    from neo4j import GraphDatabase

    # 쓰는 명령이라 목적지를 받지 않습니다(local_graph_test.local_driver와 같은 선).
    return GraphDatabase.driver(URI, auth=("neo4j", base.local_password()),
                                connection_timeout=10)


def load(session, manifest: dict[str, Any], mirrored: bool) -> dict[str, float]:
    from neo4j import Query

    timings = {}
    for label, cypher in base.IMPORTS.items():
        started = time.perf_counter()
        size = 100 if label == "papers" else 1000
        batch = []
        for row in base.read_rows(base.LOCAL / manifest["files"][label]["path"]):
            batch.append(mirror_row(label, row) if mirrored else row)
            if len(batch) >= size:
                session.run(Query(cypher, timeout=120), rows=batch).consume()
                batch = []
        if batch:
            session.run(Query(cypher, timeout=120), rows=batch).consume()
        timings[label] = round(time.perf_counter() - started, 1)
        print(f"  {'2배' if mirrored else '1배'} {label}: {timings[label]}초", flush=True)
    return timings


def prepare_schema(session) -> None:
    for statement in (
        "CREATE CONSTRAINT local_paper_id IF NOT EXISTS "
        "FOR (p:ArxivPaper) REQUIRE p.arxiv_id IS UNIQUE",
        "CREATE CONSTRAINT local_author_name IF NOT EXISTS "
        "FOR (a:ArxivAuthorName) REQUIRE a.name IS UNIQUE",
        "CREATE INDEX local_primary_category IF NOT EXISTS "
        "FOR (p:ArxivPaper) ON (p.primary_category)",
        f"""CREATE VECTOR INDEX {base.INDEX} IF NOT EXISTS
            FOR (p:ArxivPaper) ON (p.embedding)
            OPTIONS {{indexConfig: {{`vector.dimensions`: 768,
                                    `vector.similarity_function`: 'cosine'}}}}""",
    ):
        session.run(statement).consume()


def query_latency(session, queries: dict[str, str], runs: int) -> dict[str, Any]:
    from neo4j import Query

    out = {}
    for name, cypher in queries.items():
        samples = [timed(session, Query(cypher, timeout=STATEMENT_TIMEOUT), f"{name}:{i}")
                   for i in range(runs)]
        out[name] = summarize(samples, STATEMENT_TIMEOUT)
    vector = session.run("MATCH (p:ArxivPaper {arxiv_id: $id}) RETURN p.embedding AS v",
                         id=VECTOR_SEED).single()["v"]
    vector_query = Query(
        "CALL db.index.vector.queryNodes($index, 5, $vector) YIELD node RETURN node.arxiv_id",
        timeout=STATEMENT_TIMEOUT)
    samples = [_timed_with(session, vector_query, {"index": base.INDEX, "vector": vector},
                           f"vector:{i}") for i in range(runs)]
    out["vector_top5"] = summarize(samples, STATEMENT_TIMEOUT)
    return out


def _timed_with(session, query, params, label):
    """`measure_graph_load.timed`에 매개변수가 없어서 같은 모양으로 하나 둡니다."""
    start = time.perf_counter()
    rows = sum(1 for _ in session.run(query, params))
    return {"label": label, "failed": False, "rows": rows,
            "seconds": round(time.perf_counter() - start, 3)}


APP_PATH = """
import json, sys, time
sys.path.insert(0, {root!r})
from scripts.measure_app_memory import resident_mb
from src import chatbot
assert chatbot.neo4j_uri == {uri!r}, "앱이 측정용 DB를 고르지 않았습니다"
imported = resident_mb()
start = time.perf_counter()
chatbot.citation_graph()
load = time.perf_counter() - start
loaded = resident_mb()
start = time.perf_counter()
chatbot.category_pagerank("cs.AI", limit=5)
pagerank = time.perf_counter() - start
print(json.dumps({{"citation_graph_seconds": round(load, 2),
                  "pagerank_seconds": round(pagerank, 3),
                  "chatbot_imported_mb": imported, "citation_graph_loaded_mb": loaded,
                  "peak_mb": max(imported, loaded, resident_mb())}}))
"""


def app_path() -> dict[str, Any]:
    """앱이 부팅 때 하는 일(인용망 예열, ADR-067)을 자식 프로세스에서 한 번 돌립니다.

    같은 프로세스에서 두 번 재면 `chatbot.graph_cache`가 1배 배열을 돌려줍니다.
    """
    env = {**os.environ, "NEO4J_URI": URI, "NEO4J_USER": "neo4j", "NEO4J_USERNAME": "neo4j",
           "NEO4J_PASSWORD": base.local_password(), "NEO4J_DATABASE": "neo4j"}
    proc = subprocess.run([sys.executable, "-c", APP_PATH.format(root=str(ROOT), uri=URI)],
                          capture_output=True, text=True, timeout=600, env=env, cwd=ROOT)
    if proc.returncode != 0:
        raise RuntimeError(f"앱 경로 측정 실패: {proc.stderr.strip()[-400:]}")
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    result["streamlit_headroom_mb"] = round(STREAMLIT_GUARANTEED_MB - result["peak_mb"], 1)
    return result


def measure_stage(session, manifest, mirrored: bool, queries, runs) -> dict[str, Any]:
    started = time.perf_counter()
    with Sampler() as sampler:
        timings = load(session, manifest, mirrored)
        session.run("CALL db.awaitIndexes(900)").consume()
    stage = {"load_seconds": timings,
             "load_total_seconds": round(time.perf_counter() - started, 1),
             "memory_during_load": sampler.summary()}
    try:
        session.run("CALL db.checkpoint()").consume()  # 저장소 파일에 내려 쓴 뒤 크기를 봅니다
        stage["checkpointed"] = True
    except Exception as exc:  # Community 판에서 막혀 있으면 그대로 적고 넘어갑니다
        stage["checkpointed"] = f"{type(exc).__name__}"
    stage["counts"] = dict(session.run(base.COUNTS).single())
    stage["data_mib"] = data_sizes()
    stage["fits_pagecache"] = stage["data_mib"]["graph_store"] <= PAGECACHE_MIB
    stage["queries"] = query_latency(session, queries, runs)
    stage["app_path"] = app_path()
    stats = docker_stats()
    stage["memory_after_gib"] = round(memory_gib(stats["MemUsage"]), 3) if stats else None
    return stage


def compare(one: dict[str, Any], two: dict[str, Any], aura: dict[str, Any]) -> dict[str, Any]:
    keys = ("store", "graph_store", "schema_index", "transaction_logs")
    out = {
        "data_mib": {k: growth(one["data_mib"][k], two["data_mib"][k]) for k in keys},
        "queries_p95": {k: growth(one["queries"][k].get("p95"), two["queries"][k].get("p95"))
                        for k in one["queries"]},
        "load_total_seconds": growth(one["load_total_seconds"], two["load_total_seconds"]),
        "citation_graph_seconds": growth(one["app_path"]["citation_graph_seconds"],
                                         two["app_path"]["citation_graph_seconds"]),
        "app_peak_mb": growth(one["app_path"]["peak_mb"], two["app_path"]["peak_mb"]),
    }
    out["aura_projection"] = {
        name: project(aura.get(name, {}), out["queries_p95"].get(name))
        for name in ("paper_rows", "citation_edges")
    }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="그래프를 두 배로 키운 크기·지연을 잽니다.")
    parser.add_argument("--runs", type=int, default=8)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    manifest = json.loads((base.LOCAL / "snapshot.json").read_text(encoding="utf-8"))
    for spec in manifest["files"].values():
        if base.digest(base.LOCAL / spec["path"]) != spec["sha256"]:
            raise SystemExit("스냅샷 체크섬이 맞지 않습니다")
    found = app_queries()
    queries = {"paper_rows": found["PAPER_ROWS"], "citation_edges": found["CITATION_EDGES"]}
    aura = json.loads(AURA_BASELINE.read_text(encoding="utf-8"))["summary"]

    report: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "target": URI,
        "container": {"cpus": 2, "mem_limit_gib": 4, "heap_gib": 1,
                      "pagecache_mib": PAGECACHE_MIB},
        "snapshot": {"generated_at": manifest["generated_at"], "counts": manifest["counts"]},
        "statement_timeout_seconds": STATEMENT_TIMEOUT,
        "runs": args.runs,
        "mirror": {
            "prefix": MIRROR,
            "differs_from_real_growth": [
                "저자가 전부 새 사람입니다. 실제 수집에서는 저자가 겹치므로 "
                "노드·AUTHORED의 상한입니다.",
                "인용망이 서로 이어지지 않는 두 덩어리입니다. 읽는 행 수는 맞고 "
                "PageRank 의미는 다릅니다.",
                "임베딩이 원본과 똑같습니다. 벡터 인덱스 크기는 맞고 검색 품질은 재지 않습니다.",
            ],
        },
        "scope": "로컬 루프백 지연입니다. 클라우드 VM·인터넷 왕복·동시 접속은 들어 있지 않습니다.",
    }
    with driver() as db, db.session(database="neo4j") as session:
        report["settings"] = {r["name"]: r["value"] for r in session.run(
            "SHOW SETTINGS YIELD name, value WHERE name STARTS WITH 'server.memory' "
            "RETURN name, value")}
        require_empty(dict(session.run(base.COUNTS).single()))
        prepare_schema(session)
        stages = {}
        for name, mirrored in (("1x", False), ("2x", True)):
            print(f"{name} 적재와 측정", flush=True)
            stages[name] = measure_stage(session, manifest, mirrored, queries, args.runs)
        expected = {k: v * 2 for k, v in manifest["counts"].items()}
        if stages["2x"]["counts"] != expected:
            raise SystemExit(f"2배 개수가 맞지 않습니다: {stages['2x']['counts']} != {expected}")
    report["stages"] = stages
    report["growth"] = compare(stages["1x"], stages["2x"], aura)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(report["growth"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
