"""Offline contracts for the batched arXiv API citation-reference notebook."""
import ast
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "arxiv_citation_references_batched_api_to_jsonl.ipynb"


def load_helpers():
    ns = {"__name__": "notebook_test"}
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for cell in notebook["cells"]:
        if "offline-definitions" in cell.get("metadata", {}).get("tags", []):
            exec("".join(cell["source"]), ns)
    return ns


class BatchedApiNotebookTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(NOTEBOOK.exists(), "Requested batched notebook has not been created")
        self.ns = load_helpers()

    def test_code_cells_compile(self):
        import nbformat
        notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        nbformat.validate(nbformat.from_dict(notebook))
        for index, cell in enumerate(notebook["cells"]):
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"cell-{index}", "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)

    def test_batches_cover_each_id_once_without_exceeding_requested_size(self):
        batches = list(self.ns["batched"](["a", "b", "c", "d", "e"], 2))
        self.assertEqual(batches, [["a", "b"], ["c", "d"], ["e"]])

    def test_atom_entries_are_mapped_by_arxiv_id(self):
        xml = b'''<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><id>http://arxiv.org/abs/2503.06963v2</id><title>A survey</title><summary> Abstract text </summary><author><name>A. Author</name></author><category term="cs.IR"/><arxiv:doi>10.1/example</arxiv:doi><published>2025-03-09T12:00:00Z</published><updated>2025-03-12T12:00:00Z</updated></entry></feed>'''
        result = self.ns["parse_atom_entries"](xml)
        self.assertEqual(result["2503.06963"]["title"], "A survey")
        self.assertEqual(result["2503.06963"]["authors"], ["A. Author"])
        self.assertEqual(result["2503.06963"]["doi"], "10.1/example")

    def test_one_reference_row_keeps_all_citing_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "citations.jsonl"
            path.write_text(json.dumps({"arxiv_id": "2608.22899", "references": [{"arxiv_id": "2503.06963"}]}) + "\n" + json.dumps({"arxiv_id": "2608.22920", "references": [{"arxiv_id": "2503.06963"}]}), encoding="utf-8")
            references, skipped = self.ns["collect_references"]([path])
        self.assertEqual(skipped, 0)
        self.assertEqual(references, [{"arxiv_id": "2503.06963", "cit_arxiv_id": ["2608.22899", "2608.22920"]}])


if __name__ == "__main__":
    unittest.main()
