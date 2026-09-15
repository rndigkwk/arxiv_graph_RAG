"""Offline contract checks for the citation-reference OAI-PMH notebook."""
import ast
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "arxiv_citation_references_to_jsonl_oai_pmh.ipynb"


def load_helpers():
    ns = {"__name__": "notebook_test"}
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for cell in nb["cells"]:
        if "offline-definitions" in cell.get("metadata", {}).get("tags", []):
            exec("".join(cell["source"]), ns)
    return ns


class CitationReferenceNotebookTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(NOTEBOOK.exists(), "Requested notebook has not been created")
        self.ns = load_helpers()

    def test_all_code_cells_compile_in_jupyter(self):
        import nbformat

        nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        nbformat.validate(nbformat.from_dict(nb))
        for index, cell in enumerate(nb["cells"]):
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"cell-{index}", "exec",
                        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    def test_references_group_citing_arxiv_ids_per_referenced_paper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.jsonl"
            rows = [
                {"arxiv_id": "2608.22899", "references": [
                    {"arxiv_id": "2503.06963"}, {"arxiv_id": None},
                ]},
                {"arxiv_id": "2608.22920", "references": [
                    {"arxiv_id": "2503.06963"}, {"arxiv_id": "hep-th/9901001"},
                ]},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
            references, skipped = self.ns["collect_references"]([path])

        self.assertEqual(skipped, 1)
        self.assertEqual(references, [
            {"arxiv_id": "2503.06963", "cit_arxiv_id": ["2608.22899", "2608.22920"]},
            {"arxiv_id": "hep-th/9901001", "cit_arxiv_id": ["2608.22920"]},
        ])

    def test_output_record_keeps_cited_metadata_and_citing_id_separate(self):
        reference = {"arxiv_id": "2503.06963", "cit_arxiv_id": ["2608.22899", "2608.22920"]}
        metadata = {
            "id": "https://arxiv.org/abs/2503.06963", "title": "A survey",
            "abstract": "Text", "authors": ["A. Author"], "categories": ["cs.IR"],
            "primary_category": "cs.IR", "published": "2025-03-09T00:00:00Z",
            "updated": "2025-03-12T00:00:00Z", "doi": None,
            "pdf_url": "https://arxiv.org/pdf/2503.06963", "source": "arxiv",
        }
        record = self.ns["make_output_record"](reference, metadata, found=True)

        self.assertEqual(record["arxiv_id"], "2503.06963")
        self.assertEqual(record["cit_arxiv_id"], ["2608.22899", "2608.22920"])
        self.assertEqual(record["title"], "A survey")
        self.assertTrue(record["found"])
        self.assertNotIn("cit_arxiv_id", metadata)

    def test_missing_oai_record_is_preserved_for_auditable_completeness(self):
        reference = {"arxiv_id": "2503.06963", "cit_arxiv_id": ["2608.22899"]}
        record = self.ns["make_output_record"](reference, None, found=False)

        self.assertEqual(record, {
            "arxiv_id": "2503.06963", "cit_arxiv_id": ["2608.22899"], "found": False,
            "id": None, "title": None, "abstract": None, "authors": [], "categories": [],
            "primary_category": None, "published": None, "updated": None, "doi": None,
            "pdf_url": None, "source": "arxiv",
        })


if __name__ == "__main__":
    unittest.main()
