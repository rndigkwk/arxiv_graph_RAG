"""Offline checks: no AuraDB connection or paid model calls."""
import ast
import asyncio
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK = ROOT / "notebooks" / "arxiv_cleaned_to_aura_graphrag.ipynb"


def load_helpers():
    ns = {"__name__": "notebook_test"}
    nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
    for cell in nb["cells"]:
        if "offline-definitions" in cell.get("metadata", {}).get("tags", []):
            exec("".join(cell["source"]), ns)
    return ns


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(NOTEBOOK.exists(), "Requested notebook has not been created")
        self.ns = load_helpers()
        self.row = {
            "id": "http://arxiv.org/abs/2609.11929v1", "title": "Example",
            "abstract": "Method A solves Task B.", "authors": ["Kim", "Lee"],
            "categories": ["cs.AI"], "primary_category": "cs.AI",
            "published": "2026-09-10T17:59:55Z",
        }

    def test_all_code_cells_compile_in_jupyter(self):
        nb = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
        import nbformat
        nbformat.validate(nbformat.from_dict(nb))
        for i, cell in enumerate(nb["cells"]):
            if cell["cell_type"] == "code":
                compile("".join(cell["source"]), f"cell-{i}", "exec",
                        flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
                self.assertEqual(cell["outputs"], [])

    def test_arxiv_url_preserved_and_version_separated(self):
        row = self.ns["normalize_paper"](self.row, "part1.jsonl", 1)
        self.assertEqual(row["paper_id"], "2609.11929")
        self.assertEqual(row["source_url"], self.row["id"])
        self.assertEqual(row["version"], 1)
        self.assertEqual(row["source_line"], 1)
        self.assertNotIn("source_url", self.row)

    def test_bad_record_stops_instead_of_silently_losing_papers(self):
        for field, bad in [("abstract", ""), ("authors", "Kim"),
                           ("id", "https://example.com/2609.11929v1"),
                           ("published", "not a date"), ("primary_category", "cs.CV")]:
            row = dict(self.row, **{field: bad})
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.ns["normalize_paper"](row, "part1.jsonl", 4)

    def test_duplicate_versions_choose_latest_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "part.jsonl"
            newer = dict(self.row, id="https://arxiv.org/abs/2609.11929v2")
            p.write_text("\n".join(map(json.dumps, [newer, self.row, newer])), encoding="utf-8")
            rows, stats = self.ns["load_papers"]([p])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["version"], 2)
            self.assertEqual(stats[0]["records"], 3)
            conflicting = dict(newer, abstract="Conflicting content")
            p.write_text("\n".join(map(json.dumps, [newer, conflicting])), encoding="utf-8")
            with self.assertRaises(ValueError):
                self.ns["load_papers"]([p])

    def graph_fixture(self):
        N, R, G = (self.ns[k] for k in ("Neo4jNode", "Neo4jRelationship", "Neo4jGraph"))
        return G(nodes=[
            N(id="d", label="Document", properties={"path": self.row["id"]}),
            N(id="c", label="Chunk", properties={"text": self.row["abstract"], "index": 0},
              embedding_properties={"embedding": [0.6, 0.8]}),
            N(id="m", label="Method", properties={"name": "Method A"}),
            N(id="t", label="Task", properties={"name": "Task B"}),
        ], relationships=[
            R(start_node_id="c", end_node_id="d", type="FROM_DOCUMENT"),
            R(start_node_id="m", end_node_id="c", type="FROM_CHUNK"),
            R(start_node_id="t", end_node_id="c", type="FROM_CHUNK"),
            R(start_node_id="m", end_node_id="t", type="APPLIED_TO",
              properties={"evidence": self.row["abstract"]}),
        ])

    def test_graph_validation_requires_embeddings_and_grounded_relationships(self):
        graph = self.graph_fixture()
        validated, rejected = self.ns["validate_graph"](graph, 2)
        self.assertEqual(rejected, 0)
        rel = next(r for r in validated.relationships if r.type == "APPLIED_TO")
        self.assertEqual(rel.properties["evidence_chunk_ids"], ["c"])
        graph.relationships[-1].properties["evidence"] = "Invented result"
        validated, rejected = self.ns["validate_graph"](graph, 2)
        self.assertEqual(rejected, 1)
        self.assertFalse(any(r.type == "APPLIED_TO" for r in validated.relationships))
        graph.nodes[1].embedding_properties["embedding"] = [1.0]
        with self.assertRaises(ValueError):
            self.ns["validate_graph"](graph, 2)

    def test_missing_document_or_orphan_chunk_is_not_completed(self):
        graph = self.graph_fixture()
        graph.relationships = [r for r in graph.relationships if r.type != "FROM_DOCUMENT"]
        with self.assertRaises(ValueError):
            self.ns["validate_graph"](graph, 2)

    def test_signature_detects_changed_data_and_configuration(self):
        row = self.ns["normalize_paper"](self.row, "part1.jsonl", 1)
        sig = self.ns["paper_signature"]
        self.assertNotEqual(sig(row, "config-a"), sig(row, "config-b"))
        self.assertNotEqual(sig(row, "config-a"), sig(dict(row, abstract="New text"), "config-a"))
        self.assertEqual(sig(row, "config-a"), sig(dict(row, source_file="moved.jsonl"), "config-a"))

    def test_real_builder_preserves_source_and_embeddings_without_network(self):
        # Paid models are external; the splitter, pipeline, schema pruning and buffer are real.
        from neo4j_graphrag.llm.base import LLMInterface
        from neo4j_graphrag.llm.types import LLMResponse
        from neo4j_graphrag.embeddings.base import Embedder

        payload = {
            "nodes": [
                {"id": "0", "label": "Method", "properties": {"name": "Method A"}},
                {"id": "1", "label": "Task", "properties": {"name": "Task B"}},
            ],
            "relationships": [{"start_node_id": "0", "end_node_id": "1", "type": "APPLIED_TO",
                               "properties": {"evidence": "Method A solves Task B."}}],
        }
        class FixtureLLM(LLMInterface):
            def invoke(self, input, message_history=None, system_instruction=None):
                return LLMResponse(content=json.dumps(payload))

            async def ainvoke(self, input, message_history=None, system_instruction=None):
                return self.invoke(input)

        class FixtureEmbedder(Embedder):
            def embed_query(self, text, **kwargs):
                return [0.6, 0.8]

        async def run():
            driver = self.ns["GraphDatabase"].driver("bolt://localhost:1", auth=("offline", "offline"))
            buffer = self.ns["BufferWriter"]()
            splitter = self.ns["LangChainTextSplitterAdapter"](
                self.ns["RecursiveCharacterTextSplitter"](chunk_size=4000, chunk_overlap=200))
            try:
                builder = self.ns["SimpleKGPipeline"](
                    llm=FixtureLLM("fixture"), driver=driver, embedder=FixtureEmbedder(),
                    schema=self.ns["SCHEMA"], prompt_template=self.ns["PROMPT"],
                    text_splitter=splitter, from_file=False, on_error="RAISE",
                    perform_entity_resolution=False, kg_writer=buffer)
                result = await builder.run_async(
                    text="Title: Example\n\nAbstract: Method A solves Task B.",
                    file_path=self.row["id"], document_metadata={"source_doc_id": "2609.11929"})
                self.assertEqual(result.result["writer"]["status"], "SUCCESS")
                self.assertIsNotNone(buffer.graph)
                graph, rejected = self.ns["validate_graph"](buffer.graph, 2)
                self.assertEqual(rejected, 0)
                self.assertEqual(sum(n.label == "Chunk" for n in graph.nodes), 1)
                doc = next(n for n in graph.nodes if n.label == "Document")
                self.assertEqual(doc.properties["source_doc_id"], "2609.11929")
                self.assertEqual(doc.properties["path"], self.row["id"])
                self.assertTrue(any(r.type == "APPLIED_TO" for r in graph.relationships))
            finally:
                driver.close()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
