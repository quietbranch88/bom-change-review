"""Expected outcomes trace to TI facts and the pre-implementation C01-C04 contract."""

import json
from pathlib import Path
import subprocess
import sys
import unittest

from lesson1 import CATALOG_PATH, check_hiccup, evaluate


class LessonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

    def test_c01_different_pinout_blocks_unchanged_pcb(self):
        result = evaluate(self.catalog, "LM5156H", "LM5155")
        self.assertEqual(result["checks"]["unchanged_pcb_footprint"]["status"], "fail")
        self.assertEqual(result["overall"], "blocked_for_requested_scope")
        self.assertIn("different_package_pin_count", result["reason_codes"])
        self.assertEqual(result["next_action"], "review_pcb_and_pin_mapping")

    def test_c02_pin_match_never_approves_design(self):
        result = evaluate(self.catalog, "LM51551", "LM5155")
        self.assertEqual(result["checks"]["catalog_pin_for_pin"]["status"], "pass")
        self.assertEqual(result["checks"]["design_behavior_acceptance"]["status"], "unknown")
        self.assertEqual(result["overall"], "needs_review")
        self.assertIn("hiccup_behavior_changed", result["reason_codes"])
        self.assertIn("hiccup_requirement", result["missing_fields"])
        self.assertIn("hardware_validation", result["unchecked"])

    def test_c03_required_hiccup_fails_for_lm5155(self):
        result = evaluate(self.catalog, "LM5155", hiccup="required")
        self.assertEqual(result["checks"]["required_integrated_hiccup"]["status"], "fail")
        self.assertEqual(result["overall"], "blocked_for_requested_scope")
        self.assertIn("required_feature_disabled", result["reason_codes"])

    def test_c04_required_hiccup_passes_only_this_check(self):
        result = evaluate(self.catalog, "LM51551", hiccup="required")
        self.assertEqual(result["checks"]["required_integrated_hiccup"]["status"], "pass")
        self.assertEqual(result["overall"], "needs_review")

    def test_forbidden_hiccup_is_an_explicit_requirement(self):
        with_feature = evaluate(self.catalog, "LM51551", hiccup="forbidden")
        without_feature = evaluate(self.catalog, "LM5155", hiccup="forbidden")
        self.assertEqual(with_feature["checks"]["forbidden_integrated_hiccup"]["status"], "fail")
        self.assertEqual(without_feature["checks"]["forbidden_integrated_hiccup"]["status"], "pass")

    def test_unspecified_is_not_forbidden(self):
        self.assertEqual(check_hiccup(True, "unspecified"), "unknown")
        self.assertEqual(check_hiccup(False, "unspecified"), "unknown")

    def test_unknown_model_does_not_inherit_prefix_facts(self):
        for model in ("LM51551-Q1", "LM51551-UNKNOWN", "UNKNOWN"):
            with self.subTest(model=model):
                result = evaluate(self.catalog, model, "LM5155", "required")
                self.assertEqual(result["checks"]["catalog_pin_for_pin"]["status"], "unknown")
                self.assertEqual(result["checks"]["required_integrated_hiccup"]["status"], "unknown")
                self.assertEqual(result["overall"], "needs_review")
                self.assertIsNone(result["observed_candidate_hiccup"])

    def test_known_model_without_feature_evidence_stays_unknown(self):
        result = evaluate(self.catalog, "LM5156H", hiccup="required")
        self.assertEqual(result["checks"]["required_integrated_hiccup"]["status"], "unknown")
        self.assertIn("candidate_hiccup_evidence", result["missing_fields"])

    def test_unlisted_pair_is_not_inferred_from_equal_packages(self):
        result = evaluate(self.catalog, "LM5155", "LM51551")
        self.assertEqual(result["checks"]["catalog_pin_for_pin"]["status"], "unknown")
        self.assertIn("catalog_pin_for_pin_evidence", result["missing_fields"])

    def test_feature_fact_and_citation_match_the_datasheet(self):
        result = evaluate(self.catalog, "LM51551", hiccup="required")
        self.assertEqual(result["checks"]["required_integrated_hiccup"]["source_ids"], ["S03"])
        source = next(item for item in result["evidence"] if item["id"] == "S03")
        self.assertEqual(source["locator"], "Section 6 Device Comparison Table")
        self.assertIn("www.ti.com/document-viewer/LM5155", source["url"])

    def test_every_asserted_check_has_resolvable_evidence(self):
        for model in ("LM5155", "LM51551", "LM5156H", "UNKNOWN"):
            for requirement in ("required", "forbidden", "unspecified"):
                result = evaluate(self.catalog, model, "LM5155", requirement)
                ids = {source["id"] for source in result["evidence"]}
                for check in result["checks"].values():
                    if check["status"] in ("pass", "fail"):
                        self.assertTrue(check["source_ids"])
                        self.assertTrue(set(check["source_ids"]) <= ids)

    def test_invalid_requirement_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(self.catalog, "LM51551", hiccup="maybe")


class CliTests(unittest.TestCase):
    script = Path(__file__).resolve().parents[1] / "lesson1.py"

    def test_cli_runs_from_another_directory(self):
        process = subprocess.run(
            [sys.executable, str(self.script), "--original", "LM5155", "--candidate", "LM51551"],
            cwd=self.script.parent / "tests", capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 0, process.stderr)
        report = json.loads(process.stdout)
        self.assertEqual(report["checks"]["catalog_pin_for_pin"]["status"], "pass")
        self.assertEqual(report["overall"], "needs_review")

    def test_cli_rejects_invalid_requirement_without_traceback(self):
        process = subprocess.run(
            [sys.executable, str(self.script), "--hiccup", "maybe"],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(process.returncode, 2)
        self.assertNotIn("Traceback", process.stderr)
        self.assertEqual(process.stdout, "")


if __name__ == "__main__":
    unittest.main()
