"""New context projection contracts; no mock is used as real DB evidence."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import interview_demo as demo
import neo4j_graph as graph


class LifecycleProjectionTests(unittest.TestCase):
    def setUp(self):
        self.bundle = demo.create_bundle(True)

    def project(self, stage):
        return graph.project_demo(self.bundle, stage)

    def test_demo_routes_all_stages_without_mutating_inputs(self):
        before = copy.deepcopy(self.bundle)
        expected = {
            "initial": (2, None, None),
            "internal/received": (1, "unknown", None),
            "internal/reviewed": (0, "matches_requirement", "F-BIAS-INTERNAL"),
            "tied/received": (1, "unknown", None),
            "tied/reviewed": (0, "violates_requirement", "F-BIAS-TIED"),
            "unknown/received": (2, "unknown", None),
            "unknown/reviewed": (1, "unknown", None),
        }
        ids = []
        for stage, (gap_count, status, selected) in expected.items():
            with self.subTest(stage=stage):
                projected = self.project(stage)
                ids.append(projected["snapshot_id"])
                self.assertEqual(len(projected["nodes"]["Gap"]), gap_count)
                self.assertEqual({s["review_status"] for s in projected["nodes"]["Specification"]}, {"pending"})
                self.assertEqual(projected["nodes"]["CaseSnapshot"][0]["origin"], "synthetic_fixture")
                if status is not None:
                    a = projected["nodes"]["Assessment"][0]
                    self.assertEqual((a["status"], a["selected_fact_id"]), (status, selected))
                    self.assertEqual(a["overall"], "needs_engineering_review")
                    self.assertFalse(a["automatic_transition_allowed"])
                    self.assertEqual(len(projected["edges"]["USES_SPEC"]), int(selected is not None))
        self.assertEqual(len(set(ids)), 7)
        self.assertEqual(self.bundle, before)

    def test_review_application_bindings_and_exact_selected_fact(self):
        projected = self.project("internal/reviewed")
        receipt = projected["nodes"]["FactReviewReceipt"][0]
        self.assertEqual(receipt["status"], "accepted")
        self.assertEqual(receipt["reviewer"], "SIM-reviewer-not-an-engineer")
        self.assertEqual(receipt["facts_sha256"], graph.review.fingerprint(self.bundle["facts"]))
        app = projected["nodes"]["ApplicationContext"][0]
        self.assertEqual((app["input_maps_to_bias"], app["tj_min"], app["tj_max"]), (True, -20, 85))
        self.assertEqual(app["vcc_supply"], "internal_vcc_regulator")
        self.assertEqual(app["reference"], self.bundle["scenarios"]["scenarios"][0]["application"]["reference"])
        selected_id = projected["edges"]["USES_SPEC"][0]["end"]
        selected = next(x for x in projected["nodes"]["Specification"] if x["id"] == selected_id)
        self.assertEqual(selected["fact_id"], "F-BIAS-INTERNAL")
        self.assertEqual(selected["specification_class"], "recommended_operating")

    def test_false_and_missing_mapping_remain_distinct_unknown_declarations(self):
        req, facts = self.bundle["requirements"], self.bundle["facts"]
        context = copy.deepcopy(self.bundle["stages"][2]["context"])
        for value in (False, None):
            context["application"]["input_maps_to_bias"] = value
            saved = graph.assessment.evaluate(req, facts, context)
            projected = graph.project(req, facts, saved, context)
            app = projected["nodes"]["ApplicationContext"][0]
            self.assertIs(app["input_maps_to_bias"], value)
            self.assertEqual(app["mapping_status"], "unknown" if value is None else "declared")
            self.assertEqual(projected["nodes"]["Assessment"][0]["status"], "unknown")
            self.assertEqual(projected["edges"]["USES_SPEC"], [])

    def test_synthetic_context_cannot_be_presented_as_real_case(self):
        req = copy.deepcopy(self.bundle["requirements"])
        req["draft_origin_declared"] = "manual_draft"
        context = copy.deepcopy(self.bundle["stages"][2]["context"])
        context["requirements_sha256"] = graph.review.fingerprint(req)
        facts = self.bundle["facts"]
        saved = graph.assessment.evaluate(req, facts, context)
        projected = graph.project(req, facts, saved, context)
        for label in ("CaseSnapshot", "Assessment", "ApplicationContext", "FactReviewReceipt"):
            self.assertEqual(projected["nodes"][label][0]["origin"], "synthetic_fixture")

    def test_stale_context_forged_assessment_and_wrong_candidate_rejected(self):
        req, facts = self.bundle["requirements"], self.bundle["facts"]
        for field, value in (("facts_sha256", "0" * 64), ("requirements_sha256", "0" * 64),
                             ("candidate_model", "LM5155-Q1")):
            context = copy.deepcopy(self.bundle["stages"][2]["context"])
            context[field] = value
            saved = graph.assessment.evaluate(req, facts, context)
            with self.subTest(field=field), self.assertRaises(ValueError):
                graph.project(req, facts, saved, context)
        context = self.bundle["stages"][2]["context"]
        saved = copy.deepcopy(self.bundle["stages"][2]["assessment"])
        saved["status"] = "violates_requirement"
        with self.assertRaises(ValueError):
            graph.project(req, facts, saved, context)

    def test_context_receipt_and_application_missing_do_not_disappear(self):
        req, facts = self.bundle["requirements"], self.bundle["facts"]
        context = copy.deepcopy(self.bundle["stages"][2]["context"])
        context["application"] = None
        saved = graph.assessment.evaluate(req, facts, context)
        projected = graph.project(req, facts, saved, context)
        self.assertEqual(projected["nodes"]["ApplicationContext"], [])
        self.assertEqual([g["code"] for g in projected["nodes"]["Gap"]], ["application_conditions_missing"])
        self.assertEqual(len(projected["nodes"]["FactReviewReceipt"]), 1)

    def test_legacy_bundle_readable_not_silently_approved_for_graph(self):
        legacy = copy.deepcopy(self.bundle)
        # Construct the previously documented v1 shape; remove only its unsupported identity decision.
        legacy["version"] = demo.LEGACY_VERSION
        legacy["requirements"]["events"] = legacy["requirements"]["events"][1:]
        for i, event in enumerate(legacy["requirements"]["events"], 1):
            event["sequence"] = i
        legacy["stages"] = demo.make_stages(legacy["requirements"], legacy["facts"], legacy["scenarios"])
        legacy["bundle_sha256"] = graph.review.fingerprint({k: v for k, v in legacy.items() if k != "bundle_sha256"})
        self.assertEqual(demo.inspect_bundle(legacy)["report_currency"], "current")
        with self.assertRaisesRegex(ValueError, "reviewed_original_identity_required"):
            graph.project_demo(legacy, "initial")

    def test_saved_legacy_manifest_does_not_gain_context_identity(self):
        projected = self.project("initial")
        self.assertEqual(projected["manifest"]["projection_version"], "evidence-graph-v1")
        self.assertNotIn("context", projected["manifest"])
        self.assertEqual(projected["nodes"]["Assessment"], [])

    def test_offline_cli_bundle_stage_and_conflicting_arguments(self):
        with tempfile.TemporaryDirectory(prefix="bom-context-cli-") as temp:
            bundle_path = Path(temp) / "bundle.json"
            graph.review.write_new(bundle_path, self.bundle)
            original = bundle_path.read_bytes()
            args = [sys.executable, str(graph.ROOT / "neo4j_graph.py"), "plan", "--bundle", str(bundle_path),
                    "--stage", "internal/reviewed"]
            run = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            self.assertEqual(json.loads(run.stdout)["nodes"]["Assessment"][0]["status"], "matches_requirement")
            bad = subprocess.run(args + ["--context", str(bundle_path)], capture_output=True, text=True,
                                 encoding="utf-8", timeout=20, check=False)
            self.assertEqual(bad.returncode, 2)
            self.assertNotIn("Traceback", bad.stderr)
            self.assertEqual(bundle_path.read_bytes(), original)

    def test_missing_selected_dependency_is_not_silently_returned(self):
        projected = self.project("internal/reviewed")
        client = graph.Client("synthetic-local-password")
        responses = [[{"snapshot": projected["nodes"]["CaseSnapshot"][0], "gap": None}], [],
                     [{"assessment": projected["nodes"]["Assessment"][0], "application": None,
                       "fact_review": None, "selected_specification": None, "selected_document": None}]]
        with patch.object(client, "query", side_effect=responses):
            with self.assertRaisesRegex(graph.GraphError, "historical_decision_dependency_invalid"):
                client.show(projected["snapshot_id"])


if __name__ == "__main__":
    unittest.main()
