"""스냅샷 계층과 품질 게이트를 검증합니다.

게이트는 통과가 아니라 **실패를 잡는지**가 중요합니다. 각 검사마다 그 검사만 깨지는 데이터를
넣어 보고, 그 이름이 실패 목록에 뜨는지 확인합니다.
"""

import gzip
import json

from pipeline import quality
from pipeline.snapshot import Snapshot, file_digest, month_index, month_label


def paper(arxiv_id, *, embedding_length=768, title="T", abstract="A"):
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "abstract": abstract,
        "primary_category": "cs.AI",
        "categories": ["cs.AI"],
        "embedding": [0.1] * embedding_length,
    }


def run_gate(papers, references):
    expected = {"papers": len(papers), "references": len(references)}
    return quality.run(iter(papers), iter(references), expected)


def test_month_index_reads_the_submission_month_from_the_id():
    assert month_index("2507.22951") == 2025 * 12 + 6
    assert month_label(month_index("2507.22951")) == "2025-07"
    assert month_index("9107.00001") == 1991 * 12 + 6

    # 구식 ID와 잘못된 월은 기준으로 쓸 수 없으므로 None이어야 합니다.
    assert month_index("cs.AI/0701001") is None
    assert month_index("2513.00001") is None
    assert month_index("") is None


def test_gate_passes_on_consistent_data():
    papers = [paper("2601.00001"), paper("2602.00002")]
    references = [{"citing": "2602.00002", "cited": "2601.00001"}]

    report = run_gate(papers, references)

    assert report["passed"] is True
    assert report["failed"] == []
    assert len(report["checks"]) == 10


def test_gate_catches_a_missing_embedding():
    papers = [paper("2601.00001"), {**paper("2602.00002"), "embedding": None}]

    report = run_gate(papers, [])

    # 임베딩이 빠진 논문이 들어오면 링크 예측의 텍스트 피처가 비므로 반드시 막아야 합니다.
    assert report["passed"] is False
    assert "embedding_coverage" in report["failed"]


def test_gate_catches_a_wrong_embedding_width():
    papers = [paper("2601.00001"), paper("2602.00002", embedding_length=1536)]

    report = run_gate(papers, [])

    assert "embedding_dimensions_uniform" in report["failed"]
    assert "embedding_coverage" not in report["failed"]


def test_gate_catches_duplicate_ids_and_blank_text():
    duplicate = run_gate([paper("2601.00001"), paper("2601.00001")], [])
    blank = run_gate([paper("2601.00001"), paper("2602.00002", abstract="  ")], [])

    assert "arxiv_id_unique" in duplicate["failed"]
    assert "text_present" in blank["failed"]


def test_gate_catches_dangling_edges_and_self_citation():
    papers = [paper("2601.00001"), paper("2602.00002")]

    dangling = run_gate(papers, [{"citing": "2602.00002", "cited": "9999.99999"}])
    loop = run_gate(papers, [{"citing": "2601.00001", "cited": "2601.00001"}])

    assert "no_dangling_references" in dangling["failed"]
    assert "no_self_citation" in loop["failed"]


def test_gate_catches_a_citation_that_runs_backwards_in_time():
    papers = [paper("2601.00001"), paper("2602.00002")]
    # 2026-01 논문이 2026-02 논문을 인용할 수는 없습니다.
    references = [{"citing": "2601.00001", "cited": "2602.00002"}]

    report = run_gate(papers, references)

    assert "citation_time_order" in report["failed"]
    violation = next(
        check for check in report["checks"] if check["name"] == "citation_time_order"
    )
    assert violation["value"] == 1.0


def test_gate_catches_counts_that_disagree_with_the_manifest():
    papers = [paper("2601.00001")]

    report = quality.run(iter(papers), iter([]), {"papers": 99, "references": 0})

    assert "paper_count_matches_manifest" in report["failed"]


def test_snapshot_verify_reports_a_digest_mismatch(tmp_path):
    path = tmp_path / "papers.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps({"row": paper("2601.00001")}) + "\n")
    manifest = {
        "files": {
            "papers": {
                "path": "papers.jsonl.gz",
                "bytes": path.stat().st_size,
                "sha256": file_digest(path),
            }
        },
        "counts": {"papers": 1},
        "cut": {"month_index": 1},
    }
    snapshot = Snapshot(version="t", manifest=manifest, data_root=tmp_path)

    assert snapshot.verify() == []
    assert [row["arxiv_id"] for row in snapshot.rows("papers")] == ["2601.00001"]

    manifest["files"]["papers"]["sha256"] = "0" * 64
    problems = snapshot.verify()
    assert len(problems) == 1
    assert "sha256" in problems[0]


def test_snapshot_verify_reports_a_missing_file(tmp_path):
    manifest = {
        "files": {"papers": {"path": "absent.jsonl.gz", "bytes": 1, "sha256": "x"}},
        "counts": {},
        "cut": {"month_index": 0},
    }

    problems = Snapshot(version="t", manifest=manifest, data_root=tmp_path).verify()

    assert len(problems) == 1
    assert "파일이 없습니다" in problems[0]


def test_gate_requires_both_scans_to_see_the_same_paper_ids():
    # 논문 표를 먼저 훑어 ID 집합을 만든 뒤에야 간선을 검사할 수 있습니다.
    stats = quality.scan_papers(iter([paper("2601.00001")]))
    edges = quality.scan_references(
        iter([{"citing": "2601.00001", "cited": "2601.00001"}]), stats.ids
    )

    assert stats.ids == {"2601.00001"}
    assert edges.self_loops == 1
    assert edges.dangling == 0


def test_rate_helper_never_divides_by_zero():
    assert quality._rate(1, 0) == 0.0
    assert quality._rate(0, 0) == 0.0
    assert quality._rate(1, 4) == 0.25
