"""기존 내보내기를 버전이 박힌 스냅샷으로 등록하고 품질 게이트를 돌립니다.

    uv run python -m pipeline.build_snapshot
    uv run python -m pipeline.build_snapshot --data-root .local --skip-digests

하는 일은 네 가지입니다. 파일별 sha256과 행 수를 적고, 시간 분할 기준이 될 마지막 월을
데이터에서 계산하고, 품질 게이트를 돌려 결과를 매니페스트에 함께 남기고, 실행을 추적에
기록합니다.

게이트가 논문 표를 전부 읽으므로 2~3분 걸립니다. 임베딩 768개를 행마다 파싱하는 비용이며,
주 1회 도는 파이프라인에서는 문제가 되지 않습니다.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from pipeline import quality
from pipeline.snapshot import (
    MANIFEST_NAME,
    SNAPSHOT_ROOT,
    TABLES,
    Snapshot,
    file_digest,
    month_index,
    month_label,
)

EXPORT_MANIFEST = "snapshot.json"


def count_rows(snapshot: Snapshot, table: str) -> int:
    return sum(1 for _ in snapshot.rows(table))


def describe_files(data_root: Path, *, digests: bool) -> dict[str, dict[str, object]]:
    """Record each table's filename, size and digest."""
    files = {}
    for table in TABLES:
        path = data_root / f"{table}.jsonl.gz"
        if not path.exists():
            raise FileNotFoundError(f"{table} 내보내기 파일이 없습니다: {path}")
        files[table] = {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": file_digest(path) if digests else "",
        }
    return files


def cut_from_papers(snapshot: Snapshot) -> dict[str, object]:
    """Derive the snapshot's last month from arXiv IDs, not from the `published` property.

    `published` carries the OAI harvest datestamp for part of the recent batch, so it reports a
    2026 date for papers whose ID says 2025. The ID month never moves.
    """
    months = [
        month for row in snapshot.rows("papers")
        if (month := month_index(row.get("arxiv_id") or "")) is not None
    ]
    if not months:
        raise ValueError("월 기준을 계산할 수 있는 arXiv ID가 없습니다")
    return {
        "key": "arxiv_id_yymm",
        "month_index": max(months),
        "month": month_label(max(months)),
        "first_month": month_label(min(months)),
        "papers_with_month_key": len(months),
        "note": "published 속성은 OAI 수집 시점이 섞여 있어 기준으로 쓰지 않습니다",
    }


def pick_version(root: Path, generated_at: str) -> str:
    """Name the version by export date so that lexical order is chronological."""
    base = (generated_at or datetime.now(UTC).isoformat())[:10]
    candidate, suffix = base, 2
    while (root / candidate / MANIFEST_NAME).exists():
        candidate, suffix = f"{base}-{suffix}", suffix + 1
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description="내보내기를 버전 스냅샷으로 등록합니다.")
    parser.add_argument("--data-root", type=Path, default=Path(".local"))
    parser.add_argument("--root", type=Path, default=SNAPSHOT_ROOT)
    parser.add_argument("--version", help="생략하면 내보내기 날짜로 정합니다.")
    parser.add_argument("--skip-digests", action="store_true",
                        help="sha256을 비워 둡니다. 개발 중 반복 실행에만 쓰세요.")
    parser.add_argument("--skip-gate", action="store_true",
                        help="품질 게이트를 건너뜁니다. 매니페스트에 skipped로 남습니다.")
    args = parser.parse_args()

    export_path = args.data_root / EXPORT_MANIFEST
    export = json.loads(export_path.read_text(encoding="utf-8")) if export_path.exists() else {}
    version = args.version or pick_version(args.root, export.get("generated_at", ""))

    files = describe_files(args.data_root, digests=not args.skip_digests)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "version": version,
        "created_at": datetime.now(UTC).isoformat(),
        "exported_at": export.get("generated_at", ""),
        "data_root": str(args.data_root),
        "source": "neo4j-aura:13b8cb33",
        "files": files,
        "counts": {},
    }
    staged = Snapshot(version=version, manifest=manifest, data_root=args.data_root)

    counts = {table: count_rows(staged, table) for table in TABLES}
    manifest["counts"] = counts
    for table in TABLES:
        files[table]["rows"] = counts[table]
    manifest["cut"] = cut_from_papers(staged)

    if args.skip_gate:
        manifest["gate"] = {"passed": None, "skipped": True, "checks": []}
    else:
        manifest["gate"] = quality.run(
            staged.rows("papers"), staged.rows("references"), counts
        )

    destination = args.root / version
    destination.mkdir(parents=True, exist_ok=True)
    (destination / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    from pipeline.tracking import track

    gate = manifest["gate"]
    with track(f"snapshot-{version}") as run:
        run.log_params({
            "snapshot_version": version,
            "data_root": str(args.data_root),
            "cut_month": manifest["cut"]["month"],
            "digests": not args.skip_digests,
        })
        run.log_metrics({f"count_{table}": value for table, value in counts.items()})
        run.log_metrics({"gate_passed": bool(gate.get("passed"))})
        run.log_metrics({
            f"gate_{check['name']}": check["value"]
            for check in gate.get("checks", [])
            if isinstance(check.get("value"), (int, float))
        })
        run.log_tags({"snapshot_sha256_papers": files["papers"]["sha256"][:16]})
        run.log_artifact(destination / MANIFEST_NAME)

    print(json.dumps({
        "version": version,
        "counts": counts,
        "cut": manifest["cut"]["month"],
        "gate_passed": gate.get("passed"),
        "gate_failed": gate.get("failed", []),
    }, ensure_ascii=False, indent=2))
    return 0 if gate.get("passed") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
