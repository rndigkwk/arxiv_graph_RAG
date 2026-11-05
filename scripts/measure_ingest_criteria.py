"""주간 수집의 적재 기준을 고르기 전에, 기준마다 그래프가 얼마나 커지고 얼마나 이어지는지 잽니다.

    uv run --locked python scripts/measure_ingest_criteria.py            # 받기 + 시뮬레이션
    uv run --locked python scripts/measure_ingest_criteria.py --offline  # 받은 것으로 시뮬레이션만

쓰지 않습니다. arXiv OAI와 S2만 부르고(0원), "이미 그래프에 있는 논문"은 2026-10-14 스냅샷으로
판정합니다. 그 뒤 주간 수집이 들인 논문은 10월 논문이라 8월 논문의 참고문헌에 나올 수 없습니다.

2026-10-27의 측정(`measure_reference_growth.py`, EXPERIMENTS 11-56)은 **한 주, 갓 나온
논문**이었습니다. 그래서 누적 문턱(주가 쌓여야 의미가 있습니다)과 늦게 붙는 참고문헌(갓 나온 논문은
절반이 0개)을 말하지 못했습니다. 그래서 **두 달 넘게 지난 연속한 주**를 받아 주 순서대로 쌓으며
시뮬레이션합니다.

**기준은 세 축입니다.**

- 범위 - 어느 분류의 새 논문을 받는가 (`SCOPES`)
- 새 논문 - 전부 싣는가, 그래프에 참고문헌 간선이 하나라도 닿는 것만 싣는가, S2 참고문헌 수
  (arXiv가 아닌 것까지 센 전체)가 문턱을 넘는 것만 싣는가 (`NEW_PAPER_MODES`). 마지막은 "AI로 찍어
  낸 의미 없는 논문"을 거르는 신호입니다. 시드 6,467편에서 피인용 0이 74%였지만 대부분 갓
  나와서였고, 참고문헌 수는 신선도와 무관합니다(5편 미만 442편). 그래서 새 논문은 피인용이 아니라
  참고문헌 수로 거릅니다.
- 그래프 밖 인용 논문 - 어떤 규칙으로 노드로 들이는가 (`OUTSIDE_RULES`)

**잴 것은 비용과 쓸모입니다.** 비용은 노드·간선·저장소 증가, 쓸모는 고립된 새 논문 비율,
참고문헌이 간선이 되는 비율, 그리고 **들인 밖 논문이 그 뒤 주에도 다시 인용되는가**(들일 때의
판단이 맞았는가)입니다.

**잰 값과 추정을 나눕니다.** 새 논문의 저자는 OAI에서 잰 값입니다. 밖 논문의 저자 간선과 저장소
크기는 추정입니다. 밖 논문이 다시 인용하는 논문은 받지 않습니다(2026-10-27 결정).
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import oai, s2  # noqa: E402
from pipeline import snapshot as snapshot_module  # noqa: E402
from pipeline.snapshot import month_index  # noqa: E402

OUTPUT = Path("reports/ingest_criteria.json")
CACHE = Path("derived/ingest_criteria/raw.json.gz")
SNAPSHOT = "2026-10-14"
# 스냅샷 뒤이고, S2가 참고문헌을 채울 시간(두 달 가까이)이 지난 연속한 여섯 주입니다. 뒤 두 주는
# "들인 논문이 다시 인용되는가"를 보는 데 씁니다.
DEFAULT_WEEKS = ("2026-08-03", "2026-08-10", "2026-08-17", "2026-08-24", "2026-08-31",
                 "2026-09-07")

# VM에서 잰 값(2026-10-29): Neo4j 저장소 505MB, 페이지캐시 1GB, 디스크 남은 12GB.
VM_STORE_MB = 505
VM_PAGECACHE_MB = 1024
VM_DISK_FREE_MB = 12 * 1024
DISK_MARGIN_MB = 4 * 1024  # 백업 덤프·트랜잭션 로그·OS 갱신 몫
GRAPH_AT_STORE = {"papers": 38665, "relationships": 343126, "authors": 111171}
EMBEDDING_BYTES = 768 * 8 + 512  # 노드에 실린 768차원 실수 목록 + 노드 몫

SCOPES: dict[str, Callable[[dict], bool]] = {
    "cs.AI 1차": lambda p: p["primary_category"] == "cs.AI",
    "cs.AI 교차 포함": lambda p: "cs.AI" in p["categories"],
    "cs.AI·CL·LG 1차": lambda p: p["primary_category"] in {"cs.AI", "cs.CL", "cs.LG"},
}
NEW_PAPER_MODES = ("전부", "닿는 것만", "참고문헌 ≥5", "참고문헌 ≥10")
CURRENT = ("cs.AI 1차", "전부", "누적 ≥3")  # 2026-10-27 결정


def months_old(arxiv_id: str, week: date) -> int | None:
    month = month_index(arxiv_id)
    return None if month is None else (week.year * 12 + week.month - 1) - month


# 밖 논문 규칙. 인자: 그 논문을 인용한 실린 새 논문 수, S2 전체 인용 수, 나이(달), 이번 주
OUTSIDE_RULES: dict[str, Callable[[int, int, int | None], bool] | None] = {
    "안 들임": None,
    "누적 ≥2": lambda n, pop, age: n >= 2,
    "누적 ≥3": lambda n, pop, age: n >= 3,
    "누적 ≥5": lambda n, pop, age: n >= 5,
    "누적 ≥2 + 3년 이내": lambda n, pop, age: n >= 2 and age is not None and age <= 36,
    "S2 인용 ≥200": lambda n, pop, age: pop >= 200,
    "누적 ≥3 또는 S2 인용 ≥500": lambda n, pop, age: n >= 3 or pop >= 500,
}
WEEKLY_BUDGET = 50  # "주 예산" 규칙: 누적 2회 이상 중 많이 인용된 순으로 주마다 이만큼
BUDGET_RULE = f"주 예산 {WEEKLY_BUDGET}편"


# --- 받기 -------------------------------------------------------------------------------------

def new_in_week(records: list[dict], start: date, end: date) -> list[dict]:
    """그 주에 **처음 제출된** 논문만 남깁니다.

    OAI 날짜는 개정에도 바뀌므로 `created`로 거릅니다.
    """
    kept = {}
    for record in records:
        created = str(record.get("created") or "")
        if created and start.isoformat() <= created <= end.isoformat():
            kept[record["arxiv_id"]] = {key: record[key] for key in
                                        ("arxiv_id", "primary_category", "categories", "authors")}
    return list(kept.values())


def post_batch(url: str, chunk: list[str], key: str | None, interval: float) -> list | None:
    """한 묶음. 재시도를 다 써도 429·5xx이면 None을 돌려줘 부른 쪽이 저장하고 멈추게 합니다."""
    headers = {"Content-Type": "application/json", "User-Agent": "arxiv-graphrag-ingest/1.0"}
    if key:
        headers["x-api-key"] = key
    body = json.dumps({"ids": [f"ARXIV:{i}" for i in chunk]}).encode()
    for attempt in range(s2.MAX_RETRIES + 1):
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code not in s2.RETRYABLE_STATUS:
                raise
            retry_after = error.headers.get("Retry-After") if error.headers else None
            error.close()
        except s2.RETRYABLE_NETWORK:
            retry_after = None
        if attempt < s2.MAX_RETRIES:
            time.sleep(s2.backoff_delay(attempt, retry_after, interval))
    return None


def fetch_counts(raw: dict, slot: str, arxiv_ids: list[str], field: str) -> bool:
    """S2의 수 하나(`citationCount`·`referenceCount`)를 `raw[slot]`에 이어 받습니다.

    `s2.http_fetch`와 같은 재시도 규칙이고 필드만 다릅니다. S2에 없는 논문은 -1입니다 - 빠진 것
    자체가 신호입니다(색인조차 안 된 논문). 묶음마다 저장하므로, 키 없는 공용 한도에 막혀 멈춰도
    다시 돌리면 받은 데부터 잇습니다. 다 받았으면 True입니다.
    """
    url = f"{s2.BATCH_URL}?fields=externalIds,{field}"
    key = s2.api_key()
    # 키 없이 수만 편을 받으면 공용 한도에 걸립니다(2026-10-30, 1만 편째에서 429 소진).
    # 그래서 천천히 갑니다.
    interval = s2.INTERVAL_WITH_KEY if key else 3 * s2.INTERVAL_WITHOUT_KEY
    out = raw.setdefault(slot, {})
    todo = [i for i in arxiv_ids if i not in out]
    for start in range(0, len(todo), s2.BATCH_SIZE):
        chunk = todo[start:start + s2.BATCH_SIZE]
        if start:
            time.sleep(interval)
        records = post_batch(url, chunk, key, interval)
        if records is None:
            save(raw)
            print(f"  S2가 계속 막습니다 - {len(out)}/{len(arxiv_ids)}까지 저장했습니다. "
                  "다시 돌리면 잇습니다.", flush=True)
            return False
        for arxiv_id, record in zip(chunk, records, strict=True):
            out[arxiv_id] = -1 if record is None else int(record.get(field) or 0)
        if (start // s2.BATCH_SIZE) % 10 == 9:
            save(raw)
        print(f"  S2 {field} {len(out)}/{len(arxiv_ids)}", flush=True)
    save(raw)
    return True


def save(raw: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(CACHE, "wt", encoding="utf-8") as handle:
        json.dump(raw, handle)


def load_cache(weeks: list[date]) -> dict:
    if not CACHE.exists():
        return {"weeks": []}
    with gzip.open(CACHE, "rt", encoding="utf-8") as handle:
        raw = json.load(handle)
    wanted = [w.isoformat() for w in weeks]
    if [w["week"] for w in raw["weeks"]] != wanted[:len(raw["weeks"])]:
        raise SystemExit(f"받아 둔 주가 다릅니다: {[w['week'] for w in raw['weeks']]}")
    return raw


def scoped_ids(raw: dict) -> list[str]:
    return sorted({p["arxiv_id"] for week in raw["weeks"] for p in week["papers"]
                   if any(rule(p) for rule in SCOPES.values())})


def window_ids(raw: dict) -> set[str]:
    return {p["arxiv_id"] for week in raw["weeks"] for p in week["papers"]}


def fetch_raw(weeks: list[date], snap) -> dict:
    """단계마다 저장하고, 받아 둔 단계는 건너뜁니다."""
    raw = load_cache(weeks)
    for start in weeks[len(raw["weeks"]):]:
        end = start + timedelta(days=6)
        # 제출 뒤 공지까지 며칠 걸리므로 OAI 창은 주 끝에서 닷새 더 봅니다.
        result = oai.harvest(start.isoformat(), (end + timedelta(days=5)).isoformat())
        papers = new_in_week(result.records, start, end)
        meta = {"week": start.isoformat(), "oai_records": len(result.records),
                "oai_pages": result.pages, "new_cs": len(papers),
                "truncated": result.truncated_by_page_limit}
        raw["weeks"].append({**meta, "papers": papers})
        save(raw)
        print(meta, flush=True)

    union = scoped_ids(raw)
    in_graph, _ = graph_before(snap, window_ids(raw), weeks[0])
    if "references" not in raw:
        batch = s2.fetch_references(union)
        raw["references"] = batch.references
        raw["s2"] = {"requests": batch.requests, "targets": len(union),
                     "with_references": len(batch.references), "empty": len(batch.empty),
                     "not_found": len(batch.not_found), "failed": len(batch.failures),
                     "non_arxiv_references_dropped": batch.non_arxiv}
        save(raw)
        print("S2", raw["s2"], flush=True)
    done = fetch_counts(raw, "new_reference_counts", union, "referenceCount")
    if done and "references_retried" not in raw:
        # 첫 조회에서 실패한 묶음은 빈 것과 구별되지 않아, S2가 참고문헌이 있다는데 비어 있는
        # 논문을 다시 묻습니다(2026-10-30 첫 조회에서 1,500편이 실패).
        retry = [i for i in union if i not in raw["references"]
                 and raw["new_reference_counts"].get(i, -1) > 0]
        batch = s2.fetch_references(retry)
        raw["references"].update(batch.references)
        raw["references_retried"] = {"targets": len(retry), "filled": len(batch.references),
                                     "failed": len(batch.failures)}
        save(raw)
        print("S2 다시", raw["references_retried"], flush=True)
    cited = {c for refs in raw["references"].values() for c in refs}
    outside = sorted(cited - in_graph - set(union))
    done = done and fetch_counts(raw, "popularity", outside, "citationCount")
    if not done:
        raise SystemExit(1)
    return raw


# --- 시뮬레이션 --------------------------------------------------------------------------------

def simulate(weeks: list[tuple[date, list[dict]]], references: dict[str, list[str]],
             in_graph: set[str], known_authors: set[str], popularity: dict[str, int], *,
             new_mode: str, rule_name: str,
             reference_counts: dict[str, int] | None = None) -> dict:
    """주 순서대로 쌓습니다. 밖 논문은 규칙을 처음 만족하는 주에 노드가 되고, 그때까지 그 논문을
    인용한 실린 새 논문의 간선이 함께 붙습니다. 실리지 않은 새 논문의 인용은 세지 않습니다."""
    rule = OUTSIDE_RULES.get(rule_name)
    graph = set(in_graph)
    authors = set(known_authors)
    citers: dict[str, set[str]] = defaultdict(set)
    degree: Counter[str] = Counter()  # 실린 새 논문의 참고문헌 간선 수
    total_refs = 0
    admitted_at: dict[str, int] = {}
    later_citations: Counter[str] = Counter()
    outside_edges = 0
    weekly = []
    for index, (week, papers) in enumerate(weeks):
        new = [p for p in papers if p["arxiv_id"] not in graph]
        if new_mode == "닿는 것만":
            new = [p for p in new if any(c in graph for c in references.get(p["arxiv_id"], []))]
        elif new_mode.startswith("참고문헌 ≥"):
            floor = int(new_mode.removeprefix("참고문헌 ≥"))
            new = [p for p in new if (reference_counts or {}).get(p["arxiv_id"], -1) >= floor]
        graph.update(p["arxiv_id"] for p in new)
        edges = 0
        for paper in new:
            refs = references.get(paper["arxiv_id"], [])
            total_refs += len(refs)
            for cited in refs:
                if cited in graph:
                    edges += 1
                    degree[paper["arxiv_id"]] += 1
                    if cited in admitted_at:
                        outside_edges += 1
                        if admitted_at[cited] < index:
                            later_citations[cited] += 1
                else:
                    citers[cited].add(paper["arxiv_id"])
        waiting = [c for c in citers if c not in graph]
        if rule_name == BUDGET_RULE:
            ranked = sorted((c for c in waiting if len(citers[c]) >= 2),
                            key=lambda c: (-len(citers[c]), -popularity.get(c, 0), c))
            admitted = ranked[:WEEKLY_BUDGET]
        elif rule is None:
            admitted = []
        else:
            admitted = [c for c in waiting
                        if rule(len(citers[c]), popularity.get(c, 0), months_old(c, week))]
        for cited in admitted:
            graph.add(cited)
            admitted_at[cited] = index
            for citing in citers.pop(cited):
                degree[citing] += 1
                edges += 1
                outside_edges += 1
        new_authors = {name for p in new for name in p["authors"]} - authors
        authors |= new_authors
        weekly.append({
            "new_papers": len(new), "outside_admitted": len(admitted),
            "references_edges": edges,
            "authored_edges_measured": sum(len(p["authors"]) for p in new),
            "author_nodes_measured": len(new_authors),
            "outside_waiting": sum(1 for c in citers if c not in graph),
        })
    loaded = [p["arxiv_id"] for _, papers in weeks for p in papers if p["arxiv_id"] in graph
              and p["arxiv_id"] not in in_graph]
    early = [c for c, at in admitted_at.items() if at < len(weeks) - 1]
    return {"weekly": weekly, "loaded": loaded, "degree": degree, "total_refs": total_refs,
            "admitted": len(admitted_at), "early_admitted": early, "outside_edges": outside_edges,
            "later_citations": later_citations}


def summarize(run: dict, weeks: int, authors_per_paper: float, new_author_share: float) -> dict:
    rows = run["weekly"]
    total = {key: sum(row[key] for row in rows) for key in rows[0] if key != "outside_waiting"}
    outside = total["outside_admitted"]
    authored_est = round(outside * authors_per_paper)
    author_nodes_est = round(authored_est * new_author_share)
    nodes = total["new_papers"] + outside + total["author_nodes_measured"] + author_nodes_est
    relationships = total["references_edges"] + total["authored_edges_measured"] + authored_est
    paper_nodes = total["new_papers"] + outside
    store_mb = (paper_nodes * EMBEDDING_BYTES
                + (relationships + total["author_nodes_measured"] + author_nodes_est)
                * per_item_bytes()) / 1024**2
    per_week = store_mb / weeks
    loaded = run["loaded"]
    isolated = sum(1 for paper in loaded if run["degree"][paper] == 0)
    early = run["early_admitted"]
    reused = sum(1 for c in early if run["later_citations"][c] > 0)
    return {
        "per_week": {
            "new_papers": round(total["new_papers"] / weeks, 1),
            "outside_admitted": round(outside / weeks, 1),
            "nodes": round(nodes / weeks, 1),
            "references_edges": round(total["references_edges"] / weeks, 1),
            "authored_edges": round((total["authored_edges_measured"] + authored_est) / weeks, 1),
            "relationships": round(relationships / weeks, 1),
            "store_mb_estimated": round(per_week, 1),
        },
        "isolated_new_share": round(isolated / max(len(loaded), 1), 3),
        "reference_edge_share": round(total["references_edges"] / max(run["total_refs"], 1), 3),
        "edges_per_outside_node": round(run["outside_edges"] / outside, 2) if outside else None,
        "outside_reused_later": {"admitted_before_last_week": len(early), "cited_again": reused,
                                 "share": round(reused / len(early), 3) if early else None},
        "outside_waiting_at_end": rows[-1]["outside_waiting"],
        "weeks_until_pagecache_full": round((VM_PAGECACHE_MB - VM_STORE_MB) / per_week)
        if per_week else None,
        "weeks_until_disk_margin": round((VM_DISK_FREE_MB - DISK_MARGIN_MB) / per_week)
        if per_week else None,
        "weekly": rows,
    }


def per_item_bytes() -> float:
    """VM 저장소 505MB에서 임베딩 몫을 빼고 남은 것을 관계·저자 노드 하나당으로 나눈 추정치."""
    rest = VM_STORE_MB * 1024**2 - GRAPH_AT_STORE["papers"] * EMBEDDING_BYTES
    return rest / (GRAPH_AT_STORE["relationships"] + GRAPH_AT_STORE["authors"])


def graph_before(snap, excluded: set[str], first_week: date) -> tuple[set[str], set[str]]:
    """스냅숏(10-14)은 측정 창의 주들을 이미 적재했습니다. 그대로 쓰면 현행 범위의 새 논문이
    전부 "이미 있음"이 되므로, 창의 논문과 창이 시작한 달 이후 ID를 빼서 창 직전 그래프로
    되돌립니다. 저자는 남은 논문을 하나라도 쓴 사람만 남깁니다."""
    cutoff = f"{first_week:%y%m}"
    papers = {row["arxiv_id"] for row in snap.rows("papers")
              if row["arxiv_id"] not in excluded
              and not ("." in row["arxiv_id"] and row["arxiv_id"][:4] >= cutoff)}
    authors = {row["author"] for row in snap.rows("authored") if row["paper"] in papers}
    return papers, authors


def main() -> int:
    parser = argparse.ArgumentParser(description="적재 기준마다 그래프가 얼마나 커지는지 잽니다.")
    parser.add_argument("--weeks", nargs="+", default=list(DEFAULT_WEEKS),
                        help="각 주의 월요일(YYYY-MM-DD), 연속이어야 누적 문턱이 맞습니다.")
    parser.add_argument("--offline", action="store_true",
                        help="받아 둔 것으로 시뮬레이션만 (기본도 받아 둔 데서 잇습니다)")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    snap = snapshot_module.load(SNAPSHOT)
    authors_per_paper = snap.counts["authored"] / snap.counts["papers"]

    starts = sorted(date.fromisoformat(w) for w in args.weeks)
    raw = load_cache(starts) if args.offline else fetch_raw(starts, snap)
    in_graph, known_authors = graph_before(snap, window_ids(raw), starts[0])
    references, popularity = raw["references"], raw["popularity"]
    reference_counts = raw["new_reference_counts"]

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "snapshot": SNAPSHOT,
        "graph_before_window": {"papers": len(in_graph), "authors": len(known_authors)},
        "weeks": [{k: v for k, v in w.items() if k != "papers"} for w in raw["weeks"]],
        "s2": {**raw["s2"], "retried": raw.get("references_retried")},
        "assumptions": {
            "authors_per_outside_paper": round(authors_per_paper, 2),
            "store_bytes_per_paper_node": EMBEDDING_BYTES,
            "store_bytes_per_relationship_or_author": round(per_item_bytes()),
            "vm": {"store_mb": VM_STORE_MB, "pagecache_mb": VM_PAGECACHE_MB,
                   "disk_free_mb": VM_DISK_FREE_MB, "disk_margin_mb": DISK_MARGIN_MB},
        },
        "current": dict(zip(("scope", "new_papers", "outside"), CURRENT, strict=True)),
        "scopes": {},
    }
    rules = [*OUTSIDE_RULES, BUDGET_RULE]
    for name, keep in SCOPES.items():
        weeks = [(date.fromisoformat(w["week"]), [p for p in w["papers"] if keep(p)])
                 for w in raw["weeks"]]
        ids = [p["arxiv_id"] for _, papers in weeks for p in papers]
        refs = [len(references.get(i, [])) for i in ids]
        names = [a for _, papers in weeks for p in papers for a in p["authors"]]
        new_author_share = len(set(names) - known_authors) / max(len(names), 1)
        outside_counts = Counter(c for i in ids for c in references.get(i, [])
                                 if c not in in_graph)
        scope = {
            "papers_per_week": [len(papers) for _, papers in weeks],
            "empty_reference_share_by_week": [
                round(sum(1 for p in papers if not references.get(p["arxiv_id"])) /
                      max(len(papers), 1), 3) for _, papers in weeks],
            "arxiv_references_per_paper_p50": statistics.median(refs) if refs else 0,
            "authors_per_new_paper": round(len(names) / max(len(ids), 1), 2),
            "new_author_share": round(new_author_share, 3),
            "s2_reference_count": {
                "not_in_s2": round(sum(1 for i in ids if reference_counts.get(i, -1) < 0) /
                                   max(len(ids), 1), 3),
                **{f"<{k}": round(sum(1 for i in ids if 0 <= reference_counts.get(i, -1) < k) /
                                  max(len(ids), 1), 3) for k in (1, 5, 10)},
                "p50": statistics.median(reference_counts[i] for i in ids
                                         if reference_counts.get(i, -1) >= 0) if ids else 0,
            },
            "outside_distinct": len(outside_counts),
            "outside_by_citing_count": {f">={k}": sum(1 for v in outside_counts.values() if v >= k)
                                        for k in (1, 2, 3, 5, 10)},
            "options": {},
        }
        for new_mode in NEW_PAPER_MODES:
            for rule_name in rules:
                run = simulate(weeks, references, in_graph, known_authors, popularity,
                               new_mode=new_mode, rule_name=rule_name,
                               reference_counts=reference_counts)
                scope["options"][f"{new_mode} | {rule_name}"] = summarize(
                    run, len(weeks), authors_per_paper, new_author_share)
        report["scopes"][name] = scope

    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8", newline="\n")
    for name, scope in report["scopes"].items():
        print(f"\n== {name}  주당 {scope['papers_per_week']}  "
              f"빈 참고문헌 {scope['empty_reference_share_by_week']}")
        for key, value in scope["options"].items():
            week = value["per_week"]
            reuse = value["outside_reused_later"]["share"]
            print(f"  {key:28s} 노드 {week['nodes']:7.1f} 관계 {week['relationships']:8.1f} "
                  f"{week['store_mb_estimated']:5.1f}MB 고립 {value['isolated_new_share']:.3f} "
                  f"간선화 {value['reference_edge_share']:.3f} 재인용 {reuse}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
