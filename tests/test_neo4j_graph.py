"""Projection/transport tests; synthetic review events, not Neo4j execution evidence."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import neo4j_graph as graph
from test_local_review import new_review


ROOT = Path(__file__).resolve().parents[1]


def inputs():
    req = new_review()
    for name in ("subject_parts", "input_min", "input_max"):
        req = graph.review.decide(req, name, "accept", "synthetic fixture", "test extraction only")
    facts = graph.intake.read_json(ROOT / "examples/ti-1222180/candidate-facts-v1.json")
    return req, facts, graph.assessment.evaluate(req, facts)


class ProjectionTests(unittest.TestCase):
    def setUp(self):
        self.req, self.facts, self.saved = inputs()

    def project(self):
        return graph.project(self.req, self.facts, self.saved)

    def test_preserves_three_facts_two_gaps_and_scope(self):
        result = self.project()
        self.assertEqual(sum(map(len, result["nodes"].values())), 10)
        self.assertEqual(sum(map(len, result["edges"].values())), 13)
        specs = {s["fact_id"]: s for s in result["nodes"]["Specification"]}
        self.assertEqual(set(specs), {"F-BIAS-INTERNAL", "F-BIAS-TIED", "F-BIAS-ABS"})
        self.assertEqual((specs["F-BIAS-INTERNAL"]["min"], specs["F-BIAS-INTERNAL"]["max"]), (3.5, 45))
        self.assertEqual((specs["F-BIAS-TIED"]["min"], specs["F-BIAS-TIED"]["max"]), (2.97, 16))
        self.assertEqual(specs["F-BIAS-ABS"]["specification_class"], "absolute_maximum")
        self.assertEqual(specs["F-BIAS-ABS"]["reference_node"], "AGND")
        self.assertEqual(specs["F-BIAS-INTERNAL"]["tj_min"], -40)
        self.assertEqual(specs["F-BIAS-INTERNAL"]["tj_max"], 125)
        self.assertEqual({s["review_status"] for s in specs.values()}, {"pending"})
        self.assertEqual({g["code"] for g in result["nodes"]["Gap"]},
                         {"fact_review_missing", "application_conditions_missing"})
        self.assertEqual(result["nodes"]["CaseSnapshot"][0]["origin"], "synthetic_fixture")
        req = result["nodes"]["Requirement"][0]
        self.assertEqual((req["property"], req["min"], req["max"], req["unit"]), ("board_input_voltage", 14, 30, "V"))
        self.assertEqual(req["min_evidence_ids"], ["Q2"])
        self.assertNotIn("REPLACES", result["edges"])
        self.assertNotEqual(result["edges"]["ABOUT_ORIGINAL"][0]["end"], result["edges"]["CONSIDERS"][0]["end"])

    def test_projection_repeat_is_identical_and_preserves_inputs(self):
        before = copy.deepcopy((self.req, self.facts, self.saved))
        self.assertEqual(self.project(), self.project())
        self.assertEqual((self.req, self.facts, self.saved), before)

    def test_pending_requirements_and_unreviewed_original_rejected(self):
        self.req = new_review()
        self.saved = graph.assessment.evaluate(self.req, self.facts)
        with self.assertRaises(ValueError): self.project()
        for name in ("input_min", "input_max"):
            self.req = graph.review.decide(self.req, name, "accept", "synthetic", "fixture")
        self.saved = graph.assessment.evaluate(self.req, self.facts)
        with self.assertRaises(ValueError): self.project()

    def test_stale_and_forged_saved_results_rejected(self):
        self.facts["facts"][0]["range"]["max"] = 44
        with self.assertRaises(ValueError): self.project()
        self.setUp()
        self.saved["status"] = "matches_requirement"
        with self.assertRaises(ValueError): self.project()

    def test_no_approval_from_fact_flag_or_application_claim(self):
        for mode in ("accepted", "application", "orderable"):
            self.setUp()
            if mode == "accepted": self.facts["human_fact_review"] = "accepted"
            elif mode == "application": self.facts["facts"][0]["case_condition_verified"] = True
            else: self.facts["candidate"]["full_orderable_part"] = "invented"
            self.saved = graph.assessment.evaluate(self.req, self.facts)
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.project()

    def test_unknown_conditions_and_invalid_source_rejected(self):
        for mode in ("condition", "unknown_supply", "source", "hash", "bool"):
            self.setUp()
            if mode == "condition": self.facts["facts"][2]["conditions"]["unhandled"] = "never drop"
            elif mode == "unknown_supply": self.facts["facts"][0]["conditions"]["vcc_supply"] = "unknown"
            elif mode == "source": self.facts["source"]["url"] = "https://example.invalid/doc"
            elif mode == "hash": self.facts["source"]["pdf_sha256"] = "missing"
            else: self.facts["facts"][0]["range"]["max"] = True
            with self.subTest(mode=mode), self.assertRaises((ValueError, TypeError)):
                self.saved = graph.assessment.evaluate(self.req, self.facts)
                self.project()

    def test_version_and_model_suffix_get_distinct_ids(self):
        a = self.project()
        self.facts["source"]["pdf_sha256"] = "a" * 64
        self.saved = graph.assessment.evaluate(self.req, self.facts)
        b = self.project()
        self.assertNotEqual(a["snapshot_id"], b["snapshot_id"])
        self.assertNotEqual(a["document_id"], b["document_id"])
        self.assertNotEqual(a["nodes"]["Specification"][0]["id"], b["nodes"]["Specification"][0]["id"])
        self.facts["candidate"]["model"] = "LM51551"
        self.saved = graph.assessment.evaluate(self.req, self.facts)
        c = self.project()
        self.assertNotEqual(b["edges"]["CONSIDERS"][0]["end"], c["edges"]["CONSIDERS"][0]["end"])

    def test_source_text_is_data_not_cypher(self):
        malicious = "injection') DETACH DELETE n //"
        self.facts["facts"][0]["fact_id"] = malicious
        self.saved = graph.assessment.evaluate(self.req, self.facts)
        result = self.project()
        self.assertEqual(result["nodes"]["Specification"][0]["fact_id"], malicious)
        self.assertNotIn(malicious, graph.import_statement())

    def test_real_offline_cli_no_credentials_or_original_writes(self):
        with tempfile.TemporaryDirectory(prefix="bom-graph-plan-") as temp:
            root = Path(temp)
            for filename, data in (("req.json", self.req), ("facts.json", self.facts), ("assessment.json", self.saved)):
                (root / filename).write_text(json.dumps(data), encoding="utf-8")
            before = (root / "req.json").read_bytes()
            args = [sys.executable, str(ROOT / "neo4j_graph.py"), "plan", "--requirements", str(root / "req.json"),
                    "--facts", str(root / "facts.json"), "--assessment", str(root / "assessment.json"),
                    "--out", str(root / "plan.json")]
            run = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=15, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            saved = (root / "plan.json").read_bytes()
            again = subprocess.run(args, capture_output=True, text=True, timeout=15, check=False)
            self.assertEqual(again.returncode, 2)
            self.assertEqual((root / "plan.json").read_bytes(), saved)
            self.assertEqual((root / "req.json").read_bytes(), before)


class TransportTests(unittest.TestCase):
    def client(self):
        return graph.Client("synthetic-local-test-password")

    def test_202_errors_are_not_success_and_text_is_not_exposed(self):
        with patch.object(graph.http.client, "HTTPConnection") as ctor:
            response = ctor.return_value.getresponse.return_value
            response.status = 202
            response.read.return_value = json.dumps({"errors": [{"message": "PRIVATE_INTERNAL_MESSAGE"}]}).encode()
            with self.assertRaisesRegex(graph.GraphError, "^database_query_failed$"):
                self.client().query("RETURN 1")
            ctor.assert_called_once_with("127.0.0.1", 18747, timeout=20)
            self.assertEqual(ctor.return_value.request.call_count, 1)

    def test_redirect_and_oversized_response_rejected_without_retry(self):
        for status, data in ((302, b"redirect"), (202, b"x" * (1024 * 1024 + 1))):
            with patch.object(graph.http.client, "HTTPConnection") as ctor:
                response = ctor.return_value.getresponse.return_value
                response.status, response.read.return_value = status, data
                with self.assertRaises(graph.GraphError): self.client().query("RETURN 1")
                self.assertEqual(ctor.return_value.request.call_count, 1)

    def test_missing_snapshot_is_not_zero_gap_success(self):
        client = self.client()
        with patch.object(client, "query", return_value=[]):
            self.assertEqual(client.show("missing"), {"status": "not_found", "snapshot_id": "missing"})

    def test_requires_exact_engine_and_schema(self):
        client = self.client()
        with patch.object(client, "query", return_value=[{"version": "other", "edition": "community"}]):
            with self.assertRaises(graph.GraphError): client.verify_engine()
        with patch.object(client, "verify_engine"), patch.object(client, "query", return_value=[]):
            with self.assertRaisesRegex(graph.GraphError, "initialize_constraints_first"):
                client.import_projection({})


if __name__ == "__main__":
    unittest.main()
