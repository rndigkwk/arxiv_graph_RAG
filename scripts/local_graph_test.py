"""Isolated local Neo4j trial. Aura is read-only; writes use a fixed loopback URI.

python scripts/local_graph_test.py init|export|import|benchmark|app-smoke|app
Snapshots and passwords stay in the git-ignored .local directory.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import itertools
import json
import os
import re
import secrets
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from neo4j import READ_ACCESS, GraphDatabase, Query

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local"
URI = "bolt://127.0.0.1:17687"
INDEX = "arxiv_paper_embedding_index"
COUNTS = """
RETURN COUNT { (:ArxivPaper) } AS papers,
       COUNT { (:ArxivAuthorName) } AS authors,
       COUNT { ()-[:AUTHORED]->() } AS authored,
       COUNT { ()-[:REFERENCES]->() } AS references
"""
EXPORTS = {
    "papers": "MATCH (p:ArxivPaper) RETURN properties(p) AS row",
    "authors": "MATCH (a:ArxivAuthorName) RETURN a.name AS name",
    "authored": """MATCH (a:ArxivAuthorName)-[:AUTHORED]->(p:ArxivPaper)
                    RETURN a.name AS author, p.arxiv_id AS paper""",
    "references": """MATCH (p:ArxivPaper)-[:REFERENCES]->(q:ArxivPaper)
                       RETURN p.arxiv_id AS citing, q.arxiv_id AS cited""",
}
IMPORTS = {
    "papers": """UNWIND $rows AS row
        MERGE (p:ArxivPaper {arxiv_id: row.arxiv_id}) SET p += row
        FOREACH (_ IN CASE WHEN row.published IS NULL THEN [] ELSE [1] END |
                 SET p.published = datetime(row.published))
        FOREACH (_ IN CASE WHEN row.updated_at IS NULL THEN [] ELSE [1] END |
                 SET p.updated_at = datetime(row.updated_at))""",
    "authors": "UNWIND $rows AS row MERGE (:ArxivAuthorName {name: row.name})",
    "authored": """UNWIND $rows AS row
        MATCH (a:ArxivAuthorName {name: row.author}), (p:ArxivPaper {arxiv_id: row.paper})
        MERGE (a)-[:AUTHORED]->(p)""",
    "references": """UNWIND $rows AS row
        MATCH (p:ArxivPaper {arxiv_id: row.citing}), (q:ArxivPaper {arxiv_id: row.cited})
        MERGE (p)-[:REFERENCES]->(q)""",
}


def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def init():
    LOCAL.mkdir(exist_ok=True)
    path = LOCAL / "neo4j.env"
    if not path.exists():
        path.write_text(f"LOCAL_NEO4J_PASSWORD={secrets.token_hex(20)}\n", encoding="utf-8")
    print("Local credentials prepared (values not printed).", flush=True)


def local_password():
    return dotenv_values(LOCAL / "neo4j.env")["LOCAL_NEO4J_PASSWORD"]


def local_driver():
    # Do not accept arbitrary destinations for a command that writes data.
    return GraphDatabase.driver(URI, auth=("neo4j", local_password()), connection_timeout=10)


def remote_driver():
    cfg = dotenv_values(ROOT / ".env")
    uri = cfg.get("NEO4J_URI", "")
    if not uri.startswith("neo4j+s://"):
        raise ValueError("Export expects the existing encrypted Aura URI in .env")
    driver = GraphDatabase.driver(
        uri, auth=(cfg.get("NEO4J_USER") or cfg.get("NEO4J_USERNAME"), cfg["NEO4J_PASSWORD"]),
        connection_timeout=15,
    )
    return driver, cfg.get("NEO4J_DATABASE") or "neo4j"


def serialize(value):
    if hasattr(value, "iso_format"):
        return value.iso_format()
    raise TypeError(f"Unsupported property type: {type(value).__name__}")


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def export_snapshot():
    init()
    manifest_path = LOCAL / "snapshot.json"
    if manifest_path.exists():
        raise ValueError("Snapshot already exists; reuse it or choose a new experiment directory")
    driver, database = remote_driver()
    started = time.perf_counter()
    manifest = {"generated_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    with driver, driver.session(database=database, default_access_mode=READ_ACCESS,
                                fetch_size=1000) as session:
        manifest["counts"] = dict(session.run(COUNTS).single())
        for label, cypher in EXPORTS.items():
            path = LOCAL / f"{label}.jsonl.gz"
            temporary = path.with_suffix(path.suffix + ".partial")
            count = 0
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as out:
                for record in session.run(Query(cypher, timeout=600)):
                    row = record["row"] if label == "papers" else dict(record)
                    out.write(json.dumps(row, ensure_ascii=False, default=serialize) + "\n")
                    count += 1
                    if count % 5000 == 0:
                        print(f"export {label}: {count}", flush=True)
            temporary.replace(path)
            if count != manifest["counts"][label]:
                raise ValueError(f"Export count mismatch for {label}")
            manifest["files"][label] = {
                "path": path.name, "rows": count, "bytes": path.stat().st_size,
                "sha256": digest(path),
            }
            print(f"export {label} complete: {count}", flush=True)
        if dict(session.run(COUNTS).single()) != manifest["counts"]:
            raise ValueError("Aura counts changed during export; snapshot needs review")
    manifest["elapsed_seconds"] = time.perf_counter() - started
    save_json(manifest_path, manifest)
    print("Snapshot exported. Aura was queried read-only; no embedding API called.", flush=True)


def read_rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def import_snapshot():
    manifest = json.loads((LOCAL / "snapshot.json").read_text(encoding="utf-8"))
    for spec in manifest["files"].values():
        if digest(LOCAL / spec["path"]) != spec["sha256"]:
            raise ValueError("Snapshot checksum mismatch")
    started = time.perf_counter()
    timings = {}
    with local_driver() as driver, driver.session(database="neo4j") as session:
        session.run("CREATE CONSTRAINT local_paper_id IF NOT EXISTS "
                    "FOR (p:ArxivPaper) REQUIRE p.arxiv_id IS UNIQUE").consume()
        session.run("CREATE CONSTRAINT local_author_name IF NOT EXISTS "
                    "FOR (a:ArxivAuthorName) REQUIRE a.name IS UNIQUE").consume()
        session.run("CREATE INDEX local_primary_category IF NOT EXISTS "
                    "FOR (p:ArxivPaper) ON (p.primary_category)").consume()
        for label, cypher in IMPORTS.items():
            t = time.perf_counter()
            batch = []
            count = 0
            size = 100 if label == "papers" else 1000
            for row in read_rows(LOCAL / manifest["files"][label]["path"]):
                batch.append(row)
                if len(batch) >= size:
                    session.run(Query(cypher, timeout=120), rows=batch).consume()
                    count += len(batch)
                    batch = []
                    if count % 5000 == 0:
                        print(f"import {label}: {count}", flush=True)
            if batch:
                session.run(Query(cypher, timeout=120), rows=batch).consume()
            timings[label] = time.perf_counter() - t
        session.run(f"""CREATE VECTOR INDEX {INDEX} IF NOT EXISTS
            FOR (p:ArxivPaper) ON (p.embedding)
            OPTIONS {{indexConfig: {{`vector.dimensions`: 768,
                                    `vector.similarity_function`: 'cosine'}}}}""").consume()
        session.run("CALL db.awaitIndexes(600)").consume()
        counts = dict(session.run(COUNTS).single())
        if counts != manifest["counts"]:
            raise ValueError(f"Local counts differ: {counts}")
    save_json(LOCAL / "import_report.json", {
        "counts": counts, "stage_seconds": timings,
        "elapsed_seconds": time.perf_counter() - started,
    })
    print("Local import verified:", counts, flush=True)


def timed_query(session, cypher, params, runs=12):
    def execute():
        return [r.data() for r in session.run(Query(cypher, timeout=90), params)]
    start = time.perf_counter()
    result = execute()
    first = time.perf_counter() - start
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        execute()
        times.append(time.perf_counter() - start)
    ordered = sorted(times)
    return {"first_seconds": first, "warm_runs": runs,
            "warm_p50_seconds": statistics.median(times),
            "warm_p95_seconds": ordered[min(len(ordered)-1, int(len(ordered)*.95))],
            "sample_result": result}


def docker_stats():
    proc = subprocess.run(
        ["docker", "stats", "arxiv-graphrag-local", "--no-stream", "--format", "{{json .}}"],
        capture_output=True, text=True, timeout=30,
    )
    return json.loads(proc.stdout) if proc.returncode == 0 else {"unavailable": True}


def sampled_resources():
    path = LOCAL / "resource_samples.jsonl"
    if not path.exists():
        return {"available": False}
    rows = []
    ansi = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        try:
            rows.append(json.loads(ansi.sub("", line)))
        except json.JSONDecodeError:
            continue  # The concurrently written last line might be incomplete.
    def gib(value):
        number, unit = re.match(r"([\d.]+)(\w+)", value.strip()).groups()
        return float(number) * {"GiB": 1, "MiB": 1/1024, "KiB": 1/1024**2,
                              "B": 1/1024**3}[unit]
    return {
        "available": bool(rows), "samples": len(rows),
        "max_sampled_memory_gib": max((gib(r["MemUsage"].split("/")[0]) for r in rows),
                                      default=None),
        "max_sampled_cpu_percent": max((float(r["CPUPerc"].rstrip("%")) for r in rows),
                                       default=None),
        "scope": "DB container only; sampled, not a guaranteed exact peak",
    }


def benchmark():
    queries = {
        "primary_category_count": ("MATCH (p:ArxivPaper) WHERE p.primary_category=$code "
                                   "RETURN count(p) AS count", {"code": "cs.AI"}),
        "secondary_category_count": ("MATCH (p:ArxivPaper) WHERE $code IN p.categories "
                                     "RETURN count(p) AS count", {"code": "cs.AI"}),
        "citation_ranking": ("""MATCH (p:ArxivPaper) WHERE 'cs.AI' IN p.categories
            RETURN p.arxiv_id AS arxiv_id, COUNT { (p)<-[:REFERENCES]-() } AS cited_by
            ORDER BY cited_by DESC, arxiv_id LIMIT 5""", {}),
        "author_lookup": ("""MATCH (a:ArxivAuthorName {name:$name})-[:AUTHORED]->(p)
            RETURN p.arxiv_id AS arxiv_id ORDER BY p.published DESC LIMIT 5""",
                          {"name": "Yoshua Bengio"}),
    }
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "target": URI, "limits": {"cpu": 2, "memory_gib": 4},
              "scope": "DB queries and existing application PageRank; no new LLM/embedding calls",
              "stats_before": docker_stats(), "queries": {}}
    with local_driver() as driver, driver.session(database="neo4j") as session:
        report["counts"] = dict(session.run(COUNTS).single())
        manifest = json.loads((LOCAL / "snapshot.json").read_text(encoding="utf-8"))
        if report["counts"] != manifest["counts"]:
            raise ValueError("Benchmark requires the complete original graph")
        sample = list(itertools.islice(read_rows(LOCAL / "papers.jsonl.gz"), 25))
        actual = {r["id"]: r["props"] for r in session.run(
            "MATCH (p:ArxivPaper) WHERE p.arxiv_id IN $ids "
            "RETURN p.arxiv_id AS id, properties(p) AS props",
            ids=[p["arxiv_id"] for p in sample],
        )}
        fields = ["arxiv_id", "title", "abstract", "primary_category", "categories", "embedding"]
        if any(actual.get(p["arxiv_id"], {}).get(k) != p.get(k) for p in sample for k in fields):
            raise ValueError("Sample properties or stored embeddings differ from Aura snapshot")
        report["sample_property_checks"] = {"papers": len(sample), "fields": fields, "passed": True}
        report["embedding_dimensions"] = dict(session.run("""MATCH (p:ArxivPaper)
            RETURN count(p.embedding) AS embedded, min(size(p.embedding)) AS min_dimensions,
                   max(size(p.embedding)) AS max_dimensions""").single())
        dimensions = report["embedding_dimensions"]
        if (dimensions["embedded"] != report["counts"]["papers"]
                or dimensions["min_dimensions"] != 768 or dimensions["max_dimensions"] != 768):
            raise ValueError("Missing or incompatible stored embeddings")
        report["indexes"] = [r.data() for r in session.run(
            "SHOW INDEXES YIELD name,type,state RETURN name,type,state")]
        vector = session.run("MATCH (p:ArxivPaper {arxiv_id:'2303.11366'}) "
                             "RETURN p.embedding AS v").single()["v"]
        queries["vector_lookup_existing_embedding"] = (
            """CALL db.index.vector.queryNodes($index,5,$vector) YIELD node,score
            RETURN node.arxiv_id AS arxiv_id, score ORDER BY score DESC""",
            {"index": INDEX, "vector": vector},
        )
        for name, (cypher, params) in queries.items():
            report["queries"][name] = timed_query(session, cypher, params)
            print(name, report["queries"][name]["warm_p50_seconds"], flush=True)
    set_local_environment()
    sys.path.insert(0, str(ROOT))
    import chatbot

    if chatbot.neo4j_uri != URI:
        raise ValueError("Application did not select the local database")
    start = time.perf_counter()
    ranking = chatbot.category_pagerank("cs.AI", limit=5)
    report["pagerank"] = {"graph_load_and_query_seconds": time.perf_counter()-start,
                          "papers": ranking["papers"]}
    start = time.perf_counter()
    related = chatbot.rank_related_papers.invoke({"arxiv_ids": ["2303.11366"], "top_k": 5})
    report["personalized_pagerank"] = {"elapsed_seconds": time.perf_counter()-start,
                                      "result": related}
    report["stats_after"] = docker_stats()
    report["resource_sampling"] = sampled_resources()
    size = subprocess.run(["docker", "exec", "arxiv-graphrag-local", "du", "-sk", "/data"],
                          capture_output=True, text=True, timeout=30)
    if size.returncode == 0:
        report["data_directory_kib"] = int(size.stdout.split()[0])
    save_json(LOCAL / "benchmark_report.json", report)
    print("Benchmark report saved in .local/benchmark_report.json", flush=True)


def set_local_environment():
    os.environ.update(NEO4J_URI=URI, NEO4J_USER="neo4j", NEO4J_USERNAME="neo4j",
                      NEO4J_PASSWORD=local_password(), NEO4J_DATABASE="neo4j")


def app():
    set_local_environment()
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
                    "--server.address", "127.0.0.1", "--server.port", "18501",
                    "--server.headless", "true", "--server.fileWatcherType", "none"],
                   cwd=ROOT, check=True)


def app_smoke():
    """Run actual Streamlit rendering against local data without sending a chat prompt."""
    set_local_environment()
    temporary = LOCAL / "tmp"
    temporary.mkdir(exist_ok=True)
    os.environ["TMP"] = os.environ["TEMP"] = str(temporary)
    import tempfile

    tempfile.tempdir = str(temporary)
    sys.path.insert(0, str(ROOT))
    from streamlit.testing.v1 import AppTest

    test = AppTest.from_file(str(ROOT / "app.py")).run(timeout=120)
    if test.exception or test.error:
        raise ValueError("Streamlit local rendering failed")
    labels = [tab.label for tab in test.tabs]
    if labels != ["홈", "챗봇", "PageRank 순위"]:
        raise ValueError(f"Unexpected rendered tabs: {labels}")
    metrics = {metric.label: metric.value for metric in test.metric}
    manifest = json.loads((LOCAL / "snapshot.json").read_text(encoding="utf-8"))
    if metrics.get("논문") != f'{manifest["counts"]["papers"]:,}':
        raise ValueError("Home did not display the expected local paper count")
    test.session_state["pagerank_ready"] = True
    test.run(timeout=120)
    if test.exception or test.error:
        raise ValueError("Streamlit local PageRank rendering failed")
    save_json(LOCAL / "app_smoke_report.json", {
        "tabs": labels, "home_metrics": metrics, "exceptions": 0,
        "pagerank_rendered": True,
        "scope": "Streamlit AppTest rendering; no browser interaction or chat API request",
    })
    print("Local Streamlit AppTest passed:", labels, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["init", "export", "import", "benchmark", "app-smoke", "app"]
    )
    args = parser.parse_args()
    commands = {"init": init, "export": export_snapshot, "import": import_snapshot,
                "benchmark": benchmark, "app-smoke": app_smoke, "app": app}
    commands[args.command]()


if __name__ == "__main__":
    main()
