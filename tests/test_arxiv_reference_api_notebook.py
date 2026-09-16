import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


NOTEBOOK_PATH = Path("notebooks/arxiv_citation_references_arxiv_api_to_jsonl.ipynb")


def load_helpers():
    notebook = json.loads(NOTEBOOK_PATH.read_text(encoding="utf-8"))
    namespace = {"__name__": "notebook_helpers"}
    for cell in notebook["cells"]:
        if cell.get("cell_type") != "code":
            continue
        if "helpers" in cell.get("metadata", {}).get("tags", []):
            exec("".join(cell["source"]), namespace)
    return namespace


class ArxivReferenceApiNotebookTests(unittest.TestCase):
    def test_collect_references_groups_shared_reference_by_citing_ids(self):
        helpers = load_helpers()
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "citations.jsonl"
            source.write_text(
                "\n".join(
                    [
                        json.dumps({"arxiv_id": "2401.00001v2", "references": [{"arxiv_id": "arXiv:2301.00001"}]}),
                        json.dumps({"arxiv_id": "2401.00002", "references": [{"arxiv_id": "2301.00001v3"}]}),
                    ]
                ) + "\n",
                encoding="utf-8",
            )
            records, skipped = helpers["collect_references"]([source])

        self.assertEqual(skipped, 0)
        self.assertEqual(records, [{"arxiv_id": "2301.00001", "cit_arxiv_id": ["2401.00001", "2401.00002"]}])

    def test_final_record_keeps_citing_ids(self):
        helpers = load_helpers()
        reference = {"arxiv_id": "2301.00001", "cit_arxiv_id": ["2401.00001"]}
        metadata = {"id": "https://arxiv.org/abs/2301.00001", "title": "Example", "abstract": "Abstract"}
        record = helpers["make_output_record"](reference, metadata, found=True)

        self.assertEqual(record["cit_arxiv_id"], ["2401.00001"])
        self.assertEqual(record["title"], "Example")

    def test_parse_atom_entry_reads_primary_category(self):
        helpers = load_helpers()
        entry = ET.fromstring(
            '''<entry xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
                 <id>http://arxiv.org/abs/2301.00001v2</id><title>Example</title><summary>Abstract</summary>
                 <category term="cs.AI"/><arxiv:primary_category term="cs.AI"/>
               </entry>'''
        )
        arxiv_id, metadata = helpers["parse_atom_entry"](entry)

        self.assertEqual(arxiv_id, "2301.00001")
        self.assertEqual(metadata["primary_category"], "cs.AI")


if __name__ == "__main__":
    unittest.main()
