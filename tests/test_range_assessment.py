"""Source-derived range examples with synthetic receipts, not human/engineering approval."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import local_review as review
import range_assessment as assessment
import supplemental_intake as intake
from test_local_review import new_review


ROOT = Path(__file__).resolve().parents[1]


class RangeAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.req = new_review()
        for field in ("input_min", "input_max"):
            self.req = review.decide(self.req, field, "accept", "synthetic", "synthetic source-check receipt")
        self.facts = intake.read_json(ROOT / "examples/ti-1222180/candidate-facts-v1.json")
        self.context = {"schema_version": 1, "origin": "synthetic_fixture",
                        "requirements_sha256": review.fingerprint(self.req), "facts_sha256": review.fingerprint(self.facts),
                        "candidate_model": "LM5155",
                        "fact_review": {"status": "accepted", "reviewer": "synthetic", "reference": "test fixture only"},
                        "application": {"input_maps_to_bias": True, "vcc_supply": "internal_vcc_regulator",
                                        "junction_temperature": {"min": -20, "max": 85, "unit": "degC"},
                                        "reviewer": "synthetic", "reference": "assumed test wiring, not the actual case"}}

    def run_case(self):
        return assessment.evaluate(self.req, self.facts, self.context)

    def rebind(self):
        self.context["requirements_sha256"] = review.fingerprint(self.req)
        self.context["facts_sha256"] = review.fingerprint(self.facts)

    def set_range(self, low, high):
        for field, value in (("input_min", low), ("input_max", high)):
            item = {"value": value, "unit": "V", "evidence_ids": ["Q2"]}
            self.req = review.decide(self.req, field, "correct", "synthetic", "test range only", item)
        self.rebind()

    def test_internal_matches_and_tied_violates(self):
        a = self.run_case()
        self.assertEqual(a["status"], "matches_requirement")
        self.assertEqual(a["selected_fact_id"], "F-BIAS-INTERNAL")
        self.context["application"]["vcc_supply"] = "vcc_directly_connected_to_bias"
        b = self.run_case()
        self.assertEqual(b["status"], "violates_requirement")
        self.assertEqual(b["selected_fact_id"], "F-BIAS-TIED")
        for result in (a, b):
            self.assertFalse(result["automatic_transition_allowed"])
            self.assertEqual(result["overall"], "needs_engineering_review")
            self.assertEqual(result["context_origin"], "synthetic_fixture")
            self.assertEqual(result["identity_scope"], "product_model_not_orderable_mpn")

    def test_inclusive_endpoints_and_outside(self):
        for low, high, expected in ((3.5,45,"matches_requirement"),(3.49,45,"violates_requirement"),
                                     (3.5,45.01,"violates_requirement")):
            with self.subTest(low=low, high=high):
                self.setUp()
                self.set_range(low, high)
                self.assertEqual(self.run_case()["status"], expected)

    def test_no_receipt_or_application_is_unknown(self):
        no_context = assessment.evaluate(self.req, self.facts)
        self.assertEqual(no_context["status"], "unknown")
        self.assertIn("fact_review_missing", no_context["reason_codes"])
        for field in ("fact_review", "application"):
            old = self.context[field]
            self.context[field] = None
            self.assertEqual(self.run_case()["status"], "unknown")
            self.context[field] = old
        self.context["fact_review"]["status"] = "pending"
        self.assertEqual(self.run_case()["status"], "unknown")

    def test_unknown_false_mapping_and_missing_supply_temperature(self):
        baseline = copy.deepcopy(self.context)
        for field, value in (("input_maps_to_bias", None),("input_maps_to_bias",False),
                              ("vcc_supply",None),("vcc_supply","external_unknown_mode"),("junction_temperature",None)):
            self.context = copy.deepcopy(baseline)
            self.context["application"][field] = value
            self.assertEqual(self.run_case()["status"], "unknown")

    def test_temperature_applicability_is_not_voltage_violation(self):
        self.context["application"]["junction_temperature"] = {"min": -40, "max": 125, "unit": "degC"}
        self.assertEqual(self.run_case()["status"], "matches_requirement")
        self.context["application"]["junction_temperature"]["max"] = 126
        result = self.run_case()
        self.assertEqual(result["status"], "unknown")
        self.assertIn("junction_temperature_outside_fact_applicability", result["reason_codes"])

    def test_absolute_maximum_cannot_supply_a_pass(self):
        # Even a row otherwise shaped exactly like the good one must be excluded by class.
        self.facts["facts"] = [copy.deepcopy(self.facts["facts"][0])]
        self.facts["facts"][0]["specification_class"] = "absolute_maximum"
        self.rebind()
        result = self.run_case()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["reason_codes"], ["no_applicable_recommended_fact"])

    def test_multiple_matching_facts_are_ambiguous_not_first_wins(self):
        duplicate = copy.deepcopy(self.facts["facts"][0])
        duplicate["fact_id"] = "SECOND-SOURCE-CLAIM"
        duplicate["range"]["max"] = 20
        self.facts["facts"].append(duplicate)
        self.rebind()
        self.assertEqual(self.run_case()["reason_codes"], ["ambiguous_recommended_facts"])

    def test_model_mismatch_and_stale_receipts(self):
        for field, value in (("candidate_model","LM5155-Q1"),("facts_sha256","old"),("requirements_sha256","old")):
            original = self.context[field]
            self.context[field] = value
            self.assertEqual(self.run_case()["status"], "unknown")
            self.context[field] = original
        self.facts["source"]["applicable_models_in_header"] = ["LM51551"]
        self.rebind()
        self.assertEqual(self.run_case()["status"], "unknown")

    def test_pending_requirements_block_but_unrelated_unknowns_do_not(self):
        self.assertEqual(self.run_case()["status"], "matches_requirement")
        self.req = new_review()
        self.rebind()
        result = self.run_case()
        self.assertEqual(result["status"], "unknown")
        self.assertIn("reviewed_input_min_required", result["reason_codes"])

    def test_invalid_ranges_units_conditions_and_identity_are_rejected(self):
        base_facts, base_context = copy.deepcopy(self.facts), copy.deepcopy(self.context)
        for mode in ("bool", "nan", "reverse", "unit", "extra_condition", "duplicate_id", "empty_reference", "string_mapping"):
            self.facts, self.context = copy.deepcopy(base_facts), copy.deepcopy(base_context)
            row = self.facts["facts"][0]
            if mode == "bool": row["range"]["min"] = True
            elif mode == "nan": row["range"]["max"] = float('nan')
            elif mode == "reverse": row["range"]["min"] = 46
            elif mode == "unit": row["range"]["unit"] = "mV"
            elif mode == "extra_condition": row["conditions"]["unhandled_requirement"] = "must not ignore"
            elif mode == "duplicate_id": self.facts["facts"][1]["fact_id"] = row["fact_id"]
            elif mode == "empty_reference": self.context["application"]["reference"] = " "
            else: self.context["application"]["input_maps_to_bias"] = "true"
            with self.subTest(mode=mode), self.assertRaises((ValueError, TypeError)):
                self.run_case()

    def test_report_recomputes_and_changed_inputs_are_stale(self):
        saved = self.run_case()
        self.assertEqual(assessment.show(saved,self.req,self.facts,self.context)["report_currency"],"current")
        forged = copy.deepcopy(saved)
        forged["status"] = "violates_requirement"
        with self.assertRaises(ValueError): assessment.show(forged,self.req,self.facts,self.context)
        self.context["application"]["vcc_supply"] = "vcc_directly_connected_to_bias"
        shown = assessment.show(saved,self.req,self.facts,self.context)
        self.assertEqual(shown["report_currency"],"stale")
        self.assertEqual(shown["status"],"unknown")

    def test_real_cli_persistence_and_base_source_staleness(self):
        with tempfile.TemporaryDirectory(prefix="bom-range-") as temp:
            root = Path(temp)
            for name in ("range_assessment.py","local_review.py","supplemental_intake.py","pcb_followup.py","offline_eval.py"):
                shutil.copyfile(ROOT/name, root/name)
            shutil.copytree(ROOT/"evaluation", root/"evaluation")
            for name, value in (("req.json",self.req),("facts.json",self.facts),("context.json",self.context)):
                (root/name).write_text(json.dumps(value,ensure_ascii=False),encoding="utf-8")
            before = (root/"req.json").read_bytes()
            def cli(command, *extra, expected=0):
                p = subprocess.run([sys.executable,str(root/"range_assessment.py"),command,"--requirements","req.json",
                                    "--facts","facts.json","--context","context.json",*extra],cwd=root,
                                   capture_output=True,text=True,encoding="utf-8",timeout=10,check=False)
                self.assertEqual(p.returncode,expected,p.stdout+p.stderr)
                self.assertNotIn("Traceback",p.stderr)
                return json.loads(p.stdout)
            cli("assess","--out","report.json")
            self.assertEqual(cli("show","--assessment","report.json")["status"],"matches_requirement")
            saved = (root/"report.json").read_bytes()
            cli("assess","--out","report.json",expected=2)
            self.assertEqual((root/"report.json").read_bytes(),saved)
            data = intake.read_json(root/"evaluation/inputs.json")
            data["cases"][0]["blocks"][1]["text"] += " changed"
            (root/"evaluation/inputs.json").write_text(json.dumps(data),encoding="utf-8")
            self.assertEqual(cli("show","--assessment","report.json")["report_currency"],"stale")
            self.assertEqual(cli("assess")["status"],"unknown")
            self.assertEqual((root/"req.json").read_bytes(),before)
            (root/"facts.json").write_text('PRIVATE_BAD_JSON',encoding="utf-8")
            self.assertEqual(cli("assess",expected=2),{"error":"invalid_assessment_input_no_result_written"})


if __name__ == "__main__":
    unittest.main()
