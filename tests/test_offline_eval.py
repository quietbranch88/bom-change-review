"""Grader tests, not model-quality evidence. Oracles are the source contract."""

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import offline_eval as subject


ROOT = Path(__file__).resolve().parents[1]


class OfflineEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = subject.read_json(subject.DATA_DIR / "inputs.json")
        cls.references = subject.read_json(subject.DATA_DIR / "references.json")["cases"]

    def grade(self, case_id, response):
        case = next(item for item in self.inputs["cases"] if item["case_id"] == case_id)
        return subject.grade_response(self.inputs, case, response, self.references[case_id])

    def response(self, case_id="E01"):
        return copy.deepcopy(self.references[case_id])

    def test_all_ten_references_have_a_passing_path(self):
        self.assertEqual(len(self.references), 10)
        for case_id, response in self.references.items():
            with self.subTest(case=case_id):
                self.assertTrue(self.grade(case_id, response)["all_fields_match"])

    def test_oracle_numbers_match_the_independent_ti_question(self):
        fields = self.references["E01"]["fields"]
        self.assertEqual(fields["subject_parts"]["value"], ["LT8301ESS"])
        self.assertEqual(fields["input_min"]["value"], 14)
        self.assertEqual(fields["input_max"]["value"], 30)
        self.assertEqual(fields["output_voltage"]["value"], 5)
        self.assertEqual(fields["output_current"]["value"], 1.5)
        self.assertEqual(fields["output_power"]["value"], 7.5)
        self.assertIsNone(fields["continuous_current"]["value"])

    def test_wrong_numeric_value_fails_even_with_valid_evidence(self):
        response = self.response()
        response["fields"]["input_max"]["value"] = 300
        result = self.grade("E01", response)
        self.assertTrue(result["format_valid"])
        self.assertFalse(result["field_results"]["input_max"]["value_match"])
        self.assertFalse(result["all_fields_match"])

    def test_correct_value_with_unrelated_question_evidence_fails(self):
        response = self.response()
        response["fields"]["input_max"]["evidence_ids"] = ["Q1"]
        result = self.grade("E01", response)
        self.assertTrue(result["format_valid"])
        self.assertTrue(result["field_results"]["input_max"]["value_match"])
        self.assertFalse(result["field_results"]["input_max"]["evidence_match"])
        self.assertFalse(result["all_fields_match"])

    def test_reply_metadata_and_missing_sources_are_invalid(self):
        for source in ("R1", "M1", "Q999"):
            with self.subTest(source=source):
                response = self.response()
                response["fields"]["subject_parts"]["evidence_ids"] = [source]
                self.assertFalse(self.grade("E01", response)["format_valid"])

    def test_unknown_is_not_false_and_known_false_can_pass(self):
        response = self.response()
        response["fields"]["pcb_changes_allowed"] = {"value": False, "unit": None, "evidence_ids": ["Q1"]}
        self.assertFalse(self.grade("E01", response)["all_fields_match"])
        self.assertIs(self.references["E03"]["fields"]["pcb_changes_allowed"]["value"], False)
        self.assertTrue(self.grade("E03", self.response("E03"))["all_fields_match"])

    def test_blanket_unknown_does_not_pass(self):
        response = self.response()
        response["fields"]["input_min"].update(value=None, evidence_ids=[])
        result = self.grade("E01", response)
        self.assertTrue(result["format_valid"])
        self.assertFalse(result["all_fields_match"])

    def test_wrong_unit_is_invalid(self):
        response = self.response()
        response["fields"]["input_min"]["unit"] = "mV"
        self.assertIn("input_min:unit", self.grade("E01", response)["format_errors"])

    def test_number_bool_and_numeric_string_are_distinct(self):
        self.assertFalse(subject.values_match(True, 1))
        self.assertFalse(subject.values_match(False, 0))
        for bad in (True, "14", float("inf"), float("nan")):
            response = self.response()
            response["fields"]["input_min"]["value"] = bad
            self.assertFalse(self.grade("E01", response)["format_valid"])
        response = self.response()
        response["fields"]["input_min"]["value"] = 14.0
        self.assertTrue(self.grade("E01", response)["all_fields_match"])

    def test_missing_extra_and_malformed_fields_rejected(self):
        for mode in ("missing", "extra", "malformed", "top_level", "case_id"):
            with self.subTest(mode=mode):
                response = self.response()
                if mode == "missing":
                    del response["fields"]["input_min"]
                elif mode == "extra":
                    response["fields"]["approved"] = {"value": True}
                elif mode == "malformed":
                    response["fields"]["input_min"] = []
                elif mode == "case_id":
                    response["case_id"] = "E02"
                else:
                    response["approved"] = True
                self.assertFalse(self.grade("E01", response)["format_valid"])

    def test_evidence_structure_and_nonempty_rules(self):
        for bad in (None, "Q2", ["Q2", "Q2"], [{}], []):
            with self.subTest(bad=bad):
                response = self.response()
                response["fields"]["input_min"]["evidence_ids"] = bad
                self.assertFalse(self.grade("E01", response)["format_valid"])
        response = self.response()
        response["fields"]["continuous_current"]["evidence_ids"] = ["Q2"]
        self.assertFalse(self.grade("E01", response)["format_valid"])

    def test_part_suffixes_and_roles_are_not_normalized_away(self):
        response = self.response("E09")
        response["fields"]["subject_parts"]["value"] = ["LM5164-Q1"]
        self.assertFalse(self.grade("E09", response)["all_fields_match"])
        self.assertEqual(self.references["E06"]["fields"]["subject_parts"]["value"], ["SN74LVC1G125DBV"])
        self.assertEqual(self.references["E03"]["fields"]["subject_parts"]["value"], ["TXB0104"])

    def test_array_order_is_irrelevant_but_duplicates_are_invalid(self):
        response = self.response("E02")
        response["fields"]["requester_candidates"]["value"].reverse()
        self.assertTrue(self.grade("E02", response)["all_fields_match"])
        response["fields"]["requester_candidates"]["value"] = ["LM5156", "LM5156"]
        self.assertFalse(self.grade("E02", response)["format_valid"])

    def test_duplicate_keys_and_nonstandard_json_rejected(self):
        for text in ('{"x":1,"x":2}', '{"x":{"y":1,"y":2}}', '{"x":NaN}', '{"x":Infinity}', '```json\n{}\n```'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                subject.parse_json(text)

    def test_prompt_reads_no_reference_and_exposes_contract(self):
        real_read = subject.read_json

        def only_inputs(path):
            self.assertEqual(Path(path).name, "inputs.json")
            return real_read(path)

        with patch.object(subject, "read_json", side_effect=only_inputs):
            packet = subject.build_prompt("E01")
        self.assertNotIn("reference", packet)
        self.assertEqual(set(packet["field_contract"]), set(self.references["E01"]["fields"]))
        evidence = packet["response_schema"]["properties"]["fields"]["properties"]["input_min"]["properties"]["evidence_ids"]
        self.assertEqual(evidence["items"]["enum"], ["Q1", "Q2"])
        self.assertEqual(packet["blocks"][0]["role"], "metadata")

    def test_plan_is_sixty_unexecuted_trials_with_identical_inputs(self):
        plan = subject.build_plan()
        self.assertEqual(plan["trial_count"], 60)
        self.assertFalse(plan["paid_execution_authorized"])
        keys = {(r["model"], r["case_id"], r["trial"]) for r in plan["trials"]}
        self.assertEqual(len(keys), 60)
        for case_id in self.references:
            rows = [r for r in plan["trials"] if r["case_id"] == case_id]
            self.assertEqual(len({r["prompt_sha256"] for r in rows}), 1)
        for row in plan["trials"]:
            self.assertEqual(row["state"], "not_run")
            self.assertIsNone(row["cost_usd"])
            self.assertIsNone(row["provider"])

    def test_dataset_check_labels_limits(self):
        result = subject.check_dataset()
        self.assertEqual(result["cases"], 10)
        self.assertEqual(result["live_model_runs"], 0)
        self.assertEqual(result["human_review"], "pending")

    def test_invalid_reference_is_not_silently_accepted(self):
        inputs, case = subject.load_case("E01")
        reference = self.response()
        reference["fields"]["input_min"]["unit"] = "A"
        with self.assertRaisesRegex(ValueError, "invalid_reference"):
            subject.grade_response(inputs, case, self.response(), reference)


class OfflineCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "offline_eval.py"), *args],
            cwd=ROOT / "tests", capture_output=True, text=True, encoding="utf-8", check=False,
        )

    def test_check_and_prompt_from_another_directory(self):
        result = self.run_cli("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["cases"], 10)
        result = self.run_cli("prompt", "--case", "E01")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["case_id"], "E01")

    def test_real_cli_grade_pass_then_wrong_value_fail(self):
        response = subject.read_json(subject.DATA_DIR / "references.json")["cases"]["E01"]
        with tempfile.TemporaryDirectory(prefix="bom-eval-test-") as temp:
            path = Path(temp) / "response.json"
            path.write_text(json.dumps(response), encoding="utf-8")
            result = self.run_cli("grade", "--case", "E01", "--response", str(path))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["all_fields_match"])
            response["fields"]["input_max"]["value"] = 300
            path.write_text(json.dumps(response), encoding="utf-8")
            result = self.run_cli("grade", "--case", "E01", "--response", str(path))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertFalse(json.loads(result.stdout)["all_fields_match"])

    def test_bad_response_and_missing_file_have_safe_errors(self):
        with tempfile.TemporaryDirectory(prefix="bom-eval-test-") as temp:
            path = Path(temp) / "response.json"
            for content in ("not json", '{"x":NaN}', "[]", "null"):
                path.write_text(content, encoding="utf-8")
                result = self.run_cli("grade", "--case", "E01", "--response", str(path))
                self.assertIn(result.returncode, (1, 2))
                self.assertNotIn("Traceback", result.stderr)
            result = self.run_cli("grade", "--case", "E01", "--response", str(Path(temp) / "missing.json"))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout), {"error": "local_input_unreadable"})

    def test_unknown_case_returns_nonzero_without_traceback(self):
        result = self.run_cli("prompt", "--case", "E99")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
