"""버전이 박힌 그래프 스냅샷을 읽고 검증합니다.

모델 학습과 평가는 살아 있는 DB가 아니라 이 스냅샷만 봅니다. Semantic Scholar 인용 보강이
`T` 이전 논문 쌍에도 간선을 역채움하기 때문에, 라이브 DB에 직접 학습하면 학습 네거티브가
나중에 테스트 포지티브로 바뀌어 시간 분할이 조용히 무너집니다(`ROADMAP.md` 10절).

매니페스트(`snapshots/<version>/manifest.json`)는 Git에 넣고, 실제 데이터 파일은 넣지
않습니다. 매니페스트의 sha256이 데이터의 신원이므로 재현에 필요한 것은 전부 저장소에
남습니다.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SNAPSHOT_ROOT = Path("snapshots")
MANIFEST_NAME = "manifest.json"
TABLES = ("papers", "authors", "authored", "references")

# arXiv 신식 ID는 YYMM.NNNNN 꼴이고 YYMM은 최초 제출 시점입니다. 이 값은 나중에 바뀌지
# 않으므로 시간 분할의 기준으로 쓸 수 있습니다. `published` 속성은 OAI 수집 시점이 섞여
# 들어와 신뢰할 수 없습니다(`advanced.md`의 "발행일 필드를 믿을 수 없습니다").
NEW_STYLE_ID = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}$")


def month_index(arxiv_id: str) -> int | None:
    """Return a monotonic month number from an arXiv ID, or None for old-style IDs."""
    matched = NEW_STYLE_ID.match(arxiv_id)
    if not matched:
        return None
    year_part, month_part = matched.groups()
    month = int(month_part)
    if not 1 <= month <= 12:
        return None
    year = 1900 + int(year_part) if int(year_part) >= 91 else 2000 + int(year_part)
    return year * 12 + month - 1


def month_label(index: int) -> str:
    """Format a month number produced by `month_index` as YYYY-MM."""
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def file_digest(path: Path, *, chunk: int = 1 << 20) -> str:
    """Hash a file in streaming chunks so a 200MB snapshot does not need to fit in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class Snapshot:
    """One pinned export of the graph, addressed by version and verified by digest."""

    version: str
    manifest: dict[str, Any]
    data_root: Path

    @property
    def cut_month(self) -> int:
        """The last month included; the temporal split is defined relative to this."""
        return int(self.manifest["cut"]["month_index"])

    @property
    def counts(self) -> dict[str, int]:
        return dict(self.manifest["counts"])

    def path(self, table: str) -> Path:
        return self.data_root / self.manifest["files"][table]["path"]

    def rows(self, table: str) -> Iterator[dict[str, Any]]:
        """Stream one table's rows, unwrapping the `properties(p) AS row` export shape."""
        with gzip.open(self.path(table), "rt", encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                yield row.get("row", row) if isinstance(row, dict) else row

    def verify(self, *, digests: bool = True) -> list[str]:
        """Return a list of problems; an empty list means the snapshot matches its manifest."""
        problems = []
        for table, entry in self.manifest["files"].items():
            path = self.data_root / entry["path"]
            if not path.exists():
                problems.append(f"{table}: 파일이 없습니다 ({path})")
                continue
            size = path.stat().st_size
            if size != entry["bytes"]:
                problems.append(f"{table}: 크기 불일치 {size} != {entry['bytes']}")
            if digests and (found := file_digest(path)) != entry["sha256"]:
                problems.append(f"{table}: sha256 불일치 {found[:12]} != {entry['sha256'][:12]}")
        return problems


def load(version: str, *, root: Path = SNAPSHOT_ROOT, data_root: Path | None = None) -> Snapshot:
    """Load a snapshot manifest by version, resolving the data root from the manifest."""
    manifest_path = root / version / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    resolved = data_root or Path(manifest["data_root"])
    return Snapshot(version=version, manifest=manifest, data_root=resolved)


def latest(*, root: Path = SNAPSHOT_ROOT) -> Snapshot:
    """Load the highest-sorting snapshot version, which is the newest by naming convention."""
    versions = sorted(
        path.parent.name for path in root.glob(f"*/{MANIFEST_NAME}")
    )
    if not versions:
        raise FileNotFoundError(f"{root} 안에 스냅샷 매니페스트가 없습니다")
    return load(versions[-1], root=root)
