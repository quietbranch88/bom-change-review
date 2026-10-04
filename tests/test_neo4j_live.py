"""Opt-in real Neo4j tests; run only via scripts/verify_neo4j.py on its new container."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import neo4j_graph as graph
import interview_demo as demo
from test_neo4j_graph import inputs


@unittest.skipUnless(os.environ.get("BOM_NEO4J_LIVE") == "isolated-new-container", "isolated Neo4j not enabled")
class LiveNeo4jTests(unittest.TestCase):
    def setUp(self):
        self.client = graph.Client(os.environ["BOM_NEO4J_PASSWORD"], int(os.environ["BOM_NEO4J_PORT"]))
        self.req, self.facts, self.saved = inputs()
        self.projection = graph.project(self.req, self.facts, self.saved)

    def counts(self):
        nodes = self.client.query("MATCH (n) RETURN count(n) AS count")[0]["count"]
        edges = self.client.query("MATCH ()-[r]->() RETURN count(r) AS count")[0]["count"]
        return nodes, edges

    def test_import_read_and_repeat_idempotence(self):
        self.client.import_projection(self.projection)
        result = self.client.show(self.projection["snapshot_id"])
        self.assertEqual(result["status"], "historical_snapshot")
        self.assertEqual(len(result["specifications"]), 3)
        self.assertEqual({g["code"] for g in result["gaps"]}, {"fact_review_missing", "application_conditions_missing"})
        self.assertEqual({s["specification"]["review_status"] for s in result["specifications"]}, {"pending"})
        specs = {s["specification"]["fact_id"]: s["specification"] for s in result["specifications"]}
        self.assertEqual(specs["F-BIAS-INTERNAL"]["max"], 45)
        self.assertEqual(specs["F-BIAS-TIED"]["max"], 16)
        self.assertEqual(specs["F-BIAS-ABS"]["specification_class"], "absolute_maximum")
        before = self.counts()
        self.client.import_projection(self.projection)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.client.show(self.projection["snapshot_id"]), result)

    def test_mid_import_failure_rolls_back_case_and_relationships(self):
        broken = copy.deepcopy(self.projection)
        # A later node group fails MERGE after earlier groups would have written.
        broken["nodes"]["Specification"][0]["id"] = None
        before = self.counts()
        with self.assertRaises(graph.GraphError): self.client.import_projection(broken)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.client.show(self.projection["snapshot_id"])["status"], "not_found")

    def test_unique_id_constraint_rejects_duplicate(self):
        self.client.import_projection(self.projection)
        original = self.projection["nodes"]["Component"][0]
        before = self.counts()
        with self.assertRaises(graph.GraphError):
            self.client.query("CREATE (:EvidenceV1:Component {id: $id})", {"id": original["id"]})
        self.assertEqual(self.counts(), before)

    def test_new_version_does_not_pollute_old_snapshot_and_impact_is_scoped(self):
        old = self.projection
        self.client.import_projection(old)
        self.facts["source"]["pdf_sha256"] = "b" * 64
        self.facts["facts"][0]["range"]["max"] = 44
        self.saved = graph.assessment.evaluate(self.req, self.facts)
        new = graph.project(self.req, self.facts, self.saved)
        self.client.import_projection(new)
        old_rows = self.client.show(old["snapshot_id"])["specifications"]
        new_rows = self.client.show(new["snapshot_id"])["specifications"]
        self.assertEqual(len(old_rows), 3)
        self.assertEqual(len(new_rows), 3)
        self.assertEqual({r["document"]["pdf_sha256"] for r in old_rows}, {old["nodes"]["DocumentRevision"][0]["pdf_sha256"]})
        self.assertEqual({r["document"]["pdf_sha256"] for r in new_rows}, {"b" * 64})
        impacts = self.client.query(graph.IMPACT, {"id": old["document_id"]})
        self.assertIn(old["snapshot_id"], [r["snapshot_id"] for r in impacts])
        self.assertNotIn(new["snapshot_id"], [r["snapshot_id"] for r in impacts])
        self.assertEqual(self.client.query(graph.IMPACT, {"id": "unrelated-document"}), [])

    def test_literal_injection_and_missing_snapshot(self):
        literal = "fact') DETACH DELETE n //"
        self.facts["facts"][0]["fact_id"] = literal
        saved = graph.assessment.evaluate(self.req, self.facts)
        projected = graph.project(self.req, self.facts, saved)
        self.client.import_projection(projected)
        rows = self.client.show(projected["snapshot_id"])["specifications"]
        self.assertEqual(len(rows), 3)
        self.assertIn(literal, [r["specification"]["fact_id"] for r in rows])
        self.assertEqual(self.client.show("does-not-exist")["status"], "not_found")

    def test_wrong_password_is_denied(self):
        with self.assertRaises(graph.GraphError):
            graph.Client("incorrect-synthetic-password", self.client.port).query("RETURN 1")

    def test_actual_cli_import_then_separate_read(self):
        with tempfile.TemporaryDirectory(prefix="bom-graph-live-cli-") as temp:
            directory = Path(temp)
            for name, value in (("req", self.req), ("facts", self.facts), ("assessment", self.saved)):
                (directory / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
            source_before = (directory / "req.json").read_bytes()
            args = [sys.executable, str(graph.ROOT / "neo4j_graph.py"), "import", "--isolated",
                    "--requirements", str(directory / "req.json"), "--facts", str(directory / "facts.json"),
                    "--assessment", str(directory / "assessment.json")]
            process = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            written = json.loads(process.stdout)
            process = subprocess.run([sys.executable, str(graph.ROOT / "neo4j_graph.py"), "show", "--id",
                                      written["snapshot"]["id"]], capture_output=True, text=True,
                                     encoding="utf-8", timeout=60, check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(json.loads(process.stdout), written)
            self.assertEqual((directory / "req.json").read_bytes(), source_before)

    def test_context_lifecycle_preserves_old_gaps_and_exact_selected_dependencies(self):
        bundle = demo.create_bundle(True)
        old = graph.project_demo(bundle, "initial")
        self.client.import_projection(old)
        before = self.client.show(old["snapshot_id"])
        expected = {"internal/reviewed": ("matches_requirement", "F-BIAS-INTERNAL", 0),
                    "tied/reviewed": ("violates_requirement", "F-BIAS-TIED", 0),
                    "unknown/reviewed": ("unknown", None, 1)}
        for stage, (status, fact_id, gaps) in expected.items():
            projected = graph.project_demo(bundle, stage)
            self.client.import_projection(projected)
            shown = self.client.show(projected["snapshot_id"])
            a = shown["decision"]["assessment"]
            self.assertEqual(a["status"], status)
            self.assertEqual(len(shown["gaps"]), gaps)
            self.assertEqual(a["overall"], "needs_engineering_review")
            self.assertFalse(a["automatic_transition_allowed"])
            self.assertEqual(shown["snapshot"]["origin"], "synthetic_fixture")
            selected = shown["decision"]["selected_specification"]
            self.assertEqual(None if selected is None else selected["fact_id"], fact_id)
            if selected:
                self.assertEqual(selected["facts_sha256"], a["facts_sha256"])
                self.assertEqual(shown["decision"]["selected_document"]["document_id"], "SNVSB75E")
            self.assertEqual({r["specification"]["review_status"] for r in shown["specifications"]}, {"pending"})
            counts = self.counts()
            self.client.import_projection(projected)
            self.assertEqual(self.counts(), counts)
            self.assertEqual(self.client.show(old["snapshot_id"]), before)
        self.assertEqual(len(before["gaps"]), 2)

    def test_context_versioned_decision_impact_excludes_unselected_and_new_document(self):
        bundle = demo.create_bundle(True)
        old = graph.project_demo(bundle, "internal/reviewed")
        unknown = graph.project_demo(bundle, "unknown/reviewed")
        initial = graph.project_demo(bundle, "initial")
        for item in (old, unknown, initial):
            self.client.import_projection(item)
        req, facts = bundle["requirements"], copy.deepcopy(bundle["facts"])
        facts["source"]["pdf_sha256"] = "c" * 64
        facts["facts"][0]["range"]["max"] = 20
        context = copy.deepcopy(bundle["stages"][2]["context"])
        context["facts_sha256"] = graph.review.fingerprint(facts)
        saved = graph.assessment.evaluate(req, facts, context)
        new = graph.project(req, facts, saved, context)
        self.client.import_projection(new)
        old_result = self.client.show(old["snapshot_id"])["decision"]
        new_result = self.client.show(new["snapshot_id"])["decision"]
        self.assertEqual(old_result["selected_specification"]["max"], 45)
        self.assertEqual(new_result["selected_specification"]["max"], 20)
        self.assertEqual(new_result["assessment"]["status"], "violates_requirement")
        rows = self.client.query(graph.DECISION_IMPACT, {"id": old["document_id"]})
        ids = {r["snapshot_id"] for r in rows}
        self.assertIn(old["snapshot_id"], ids)
        for excluded in (unknown, initial, new):
            self.assertNotIn(excluded["snapshot_id"], ids)
        self.assertEqual(self.client.query(graph.DECISION_IMPACT, {"id": "unrelated-document"}), [])

    def test_context_late_node_failure_rolls_back_entire_new_snapshot(self):
        bundle = demo.create_bundle(True)
        old = graph.project_demo(bundle, "initial")
        self.client.import_projection(old)
        old_result, before = self.client.show(old["snapshot_id"]), self.counts()
        broken = graph.project_demo(bundle, "internal/reviewed")
        broken["nodes"]["FactReviewReceipt"][0]["id"] = None
        with self.assertRaises(graph.GraphError):
            self.client.import_projection(broken)
        self.assertEqual(self.counts(), before)
        self.assertEqual(self.client.show(broken["snapshot_id"])["status"], "not_found")
        self.assertEqual(self.client.show(old["snapshot_id"]), old_result)

    def test_context_declarations_with_missing_values_and_literal_text_survive(self):
        bundle = demo.create_bundle(True)
        req, facts = bundle["requirements"], bundle["facts"]
        ctx = copy.deepcopy(bundle["stages"][-1]["context"])
        literal = "reference') DETACH DELETE n //"
        ctx["application"]["reference"] = literal
        ctx["application"]["junction_temperature"] = None
        ctx["application"]["vcc_supply"] = None
        saved = graph.assessment.evaluate(req, facts, ctx)
        projected = graph.project(req, facts, saved, ctx)
        self.client.import_projection(projected)
        shown = self.client.show(projected["snapshot_id"])
        app = shown["decision"]["application"]
        self.assertEqual(app["mapping_status"], "unknown")
        self.assertEqual(app["temperature_status"], "missing")
        self.assertEqual(app["vcc_supply_status"], "missing")
        self.assertEqual(app["reference"], literal)
        self.assertIsNone(shown["decision"]["selected_specification"])
        self.assertEqual(len(shown["gaps"]), 3)

    def test_context_cli_bundle_import_and_independent_reader(self):
        bundle = demo.create_bundle(True)
        with tempfile.TemporaryDirectory(prefix="bom-context-live-") as temp:
            bundle_path = Path(temp) / "bundle.json"
            graph.review.write_new(bundle_path, bundle)
            before = bundle_path.read_bytes()
            cmd = [sys.executable, str(graph.ROOT / "neo4j_graph.py"), "import", "--isolated",
                   "--bundle", str(bundle_path), "--stage", "internal/reviewed"]
            process = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            first = json.loads(process.stdout)
            self.assertEqual(first["decision"]["assessment"]["status"], "matches_requirement")
            process = subprocess.run([sys.executable, str(graph.ROOT / "neo4j_graph.py"), "show", "--id",
                                      first["snapshot"]["id"]], capture_output=True, text=True,
                                     encoding="utf-8", timeout=60, check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            self.assertEqual(json.loads(process.stdout), first)
            self.assertEqual(bundle_path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
