"""Local review invariants; no actual human approval or live model boundary."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_review as review
import offline_eval


ROOT = Path(__file__).resolve().parents[1]


def draft():
    # Explicit source-derived fixture, independent of references.json / grader.
    values = {
        "subject_parts": (["LT8301ESS"], None, ["Q1"]),
        "requester_candidates": ([], None, []),
        "input_min": (14, "V", ["Q2"]),
        "input_max": (30, "V", ["Q2"]),
        "output_voltage": (5, "V", ["Q2"]),
        "output_current": (1.5, "A", ["Q2"]),
        "output_power": (7.5, "W", ["Q2"]),
        "topology": ("flyback", None, ["Q2"]),
        "qualification": ("AEC-Q100", None, ["Q2"]),
        "continuous_current": (None, None, []),
        "pcb_changes_allowed": (None, None, []),
    }
    return {"case_id": "E01", "fields": {
        name: {"value": value, "unit": unit, "evidence_ids": ids}
        for name, (value, unit, ids) in values.items()
    }}


def new_review():
    return review.create_review("E01", draft(), "demo")


def accept(record, field):
    return review.decide(record, field, "accept", "demo-reviewer", "Checked against source; demo only.")


class LocalReviewTests(unittest.TestCase):
    def test_creation_preserves_draft_and_reads_no_reference(self):
        real_read = offline_eval.read_json

        def read_inputs_only(path):
            self.assertEqual(Path(path).name, "inputs.json")
            return real_read(path)

        original = draft()
        with patch.object(offline_eval, "read_json", side_effect=read_inputs_only):
            record = review.create_review("E01", original, "demo")
            result = review.report(record)
        self.assertEqual(record["draft"], original)
        self.assertEqual(result["extraction_status"], "pending_review")
        self.assertEqual(result["engineering_suitability"], "not_evaluated")
        original["fields"]["input_max"]["value"] = 300
        self.assertEqual(record["draft"]["fields"]["input_max"]["value"], 30)

    def test_import_is_structure_check_not_semantic_auto_approval(self):
        wrong = draft()
        wrong["fields"]["input_max"]["value"] = 300
        result = review.report(review.create_review("E01", wrong, "demo"))
        self.assertEqual(result["fields"]["input_max"]["item"]["value"], 300)
        self.assertEqual(result["fields"]["input_max"]["status"], "pending")

    def test_accept_one_field_does_not_complete_review(self):
        original = new_review()
        changed = accept(original, "input_min")
        result = review.report(changed)
        self.assertEqual(result["extraction_status"], "pending_review")
        self.assertEqual(result["fields"]["input_min"]["status"], "accepted")
        self.assertEqual(original["events"], [])
        self.assertEqual(changed["events"][0]["before"], changed["events"][0]["after"])

    def test_all_fields_reviewed_still_not_engineering_approval(self):
        record = new_review()
        for field in draft()["fields"]:
            record = accept(record, field)
        result = review.report(record)
        self.assertEqual(result["extraction_status"], "reviewed")
        self.assertEqual(result["engineering_suitability"], "not_evaluated")
        self.assertEqual(set(result["unknown_fields"]), {"continuous_current", "pcb_changes_allowed"})
        self.assertEqual(result["reviewer_identity"], "self_declared_not_authenticated")

    def test_needs_input_prevents_completion_even_when_others_reviewed(self):
        record = new_review()
        for field in draft()["fields"]:
            if field != "continuous_current":
                record = accept(record, field)
        record = review.decide(record, "continuous_current", "needs_input", "demo-reviewer", "Is 1.5 A continuous or peak?")
        result = review.report(record)
        self.assertEqual(result["extraction_status"], "needs_input")
        self.assertIsNone(result["fields"]["continuous_current"]["item"]["value"])

    def test_correction_preserves_old_new_reason_and_identity(self):
        wrong = draft()
        wrong["fields"]["input_max"]["value"] = 300
        record = review.create_review("E01", wrong, "demo")
        changed = review.decide(record, "input_max", "correct", "demo-reviewer", "Q2 says 30 V.", draft()["fields"]["input_max"])
        result = review.report(changed)
        self.assertEqual(result["fields"]["input_max"]["item"]["value"], 30)
        self.assertEqual(result["fields"]["input_max"]["status"], "corrected")
        event = result["history"][0]
        self.assertEqual(event["before"]["value"], 300)
        self.assertEqual(event["after"]["value"], 30)
        self.assertEqual(event["reviewer"], "demo-reviewer")
        self.assertEqual(event["reason"], "Q2 says 30 V.")
        self.assertEqual(changed["draft"]["fields"]["input_max"]["value"], 300)
        self.assertIsNotNone(review.timestamp(event["at"]).tzinfo)

    def test_reopen_and_resolve_retains_each_decision(self):
        record = accept(new_review(), "continuous_current")
        record = review.decide(record, "continuous_current", "needs_input", "demo-reviewer", "Confirm continuous current?")
        record = review.decide(record, "continuous_current", "accept", "demo-reviewer", "Accept unknown as faithful extraction, not a confirmed load condition.")
        result = review.report(record)
        self.assertEqual([e["action"] for e in result["history"]], ["accept", "needs_input", "accept"])
        self.assertIsNone(result["fields"]["continuous_current"]["item"]["value"])
        self.assertEqual(result["fields"]["continuous_current"]["status"], "accepted")

    def test_invalid_decisions_and_missing_metadata_are_rejected(self):
        record = new_review()
        for field, action, actor, reason, replacement in (
            ("unknown", "accept", "demo", "reason", None),
            ("input_max", "approve_component", "demo", "reason", None),
            ("input_max", "accept", " ", "reason", None),
            ("input_max", "needs_input", "demo", "", None),
            ("input_max", "correct", "demo", "reason", None),
            ("input_max", "accept", "demo", "reason", draft()["fields"]["input_max"]),
            ("input_max", "correct", "demo", "reason", draft()["fields"]["input_max"]),
        ):
            with self.subTest(action=action, field=field, actor=actor), self.assertRaises(ValueError):
                review.decide(record, field, action, actor, reason, replacement)
        self.assertEqual(record["events"], [])

    def test_correction_must_pass_structure_units_types_and_evidence(self):
        for key, value in (("unit", "mV"), ("value", True), ("evidence_ids", ["R1"]), ("value", "30")):
            item = copy.deepcopy(draft()["fields"]["input_max"])
            item[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                review.decide(new_review(), "input_max", "correct", "demo", "reason", item)

    def test_invalid_import_is_rejected(self):
        invalid = draft()
        invalid["fields"]["input_max"]["value"] = True
        with self.assertRaises(ValueError):
            review.create_review("E01", invalid, "demo")
        with self.assertRaises(ValueError):
            review.create_review("E01", draft(), "verified_human_gold")

    def test_source_change_marks_review_stale_and_blocks_next_decision(self):
        record = new_review()
        for field in draft()["fields"]:
            record = accept(record, field)
        with tempfile.TemporaryDirectory(prefix="bom-review-source-") as temp:
            inputs = offline_eval.read_json(offline_eval.DATA_DIR / "inputs.json")
            inputs["cases"][0]["blocks"][1]["text"] += " Updated source."
            Path(temp, "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
            self.assertEqual(review.report(record, temp)["extraction_status"], "stale")
            with self.assertRaisesRegex(ValueError, "stale_source"):
                review.decide(record, "input_max", "accept", "demo", "reason", data_dir=temp)

    def test_contract_change_also_invalidates_review(self):
        record = new_review()
        with tempfile.TemporaryDirectory(prefix="bom-review-contract-") as temp:
            inputs = offline_eval.read_json(offline_eval.DATA_DIR / "inputs.json")
            inputs["field_definitions"]["input_max"]["unit"] = "mV"
            Path(temp, "inputs.json").write_text(json.dumps(inputs), encoding="utf-8")
            self.assertTrue(review.report(record, temp)["source_stale"])

    def test_corrupted_draft_source_or_history_fails_replay(self):
        baseline = accept(new_review(), "input_min")
        for mode in ("draft", "source", "before", "sequence", "after", "time", "extra"):
            broken = copy.deepcopy(baseline)
            if mode == "draft":
                broken["draft"]["fields"]["input_min"]["value"] = 13
            elif mode == "source":
                broken["source"]["case"]["title"] = "changed"
            elif mode == "before":
                broken["events"][0]["before"]["value"] = 13
            elif mode == "sequence":
                broken["events"][0]["sequence"] = 2
            elif mode == "after":
                broken["events"][0]["after"]["value"] = 15
            elif mode == "time":
                broken["events"][0]["at"] = "2000-01-01T00:00:00+00:00"
            else:
                broken["approved"] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                review.replay(broken)


class LocalReviewCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-review-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        # Real isolated entrypoint + actual data path; no service or IO mocks.
        for name in ("local_review.py", "offline_eval.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        (self.root / "evaluation").mkdir()
        shutil.copyfile(offline_eval.DATA_DIR / "inputs.json", self.root / "evaluation" / "inputs.json")
        wrong = draft()
        wrong["fields"]["input_max"]["value"] = 300
        self.save("draft.json", wrong)

    def save(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.root / "local_review.py"), *args],
                              cwd=self.root, capture_output=True, text=True, encoding="utf-8", check=False)

    def init(self):
        result = self.run_cli("init", "--case", "E01", "--draft", "draft.json", "--origin", "demo", "--out", "r0.json")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def test_real_cli_durable_correction_question_and_complete_review(self):
        self.init()
        initial_bytes = (self.root / "r0.json").read_bytes()
        self.save("replacement.json", draft()["fields"]["input_max"])
        result = self.run_cli("decide", "--review", "r0.json", "--field", "input_max", "--action", "correct", "--reviewer", "demo-reviewer", "--reason", "Q2 says 30 V.", "--replacement", "replacement.json", "--out", "r1.json")
        self.assertEqual(result.returncode, 0, result.stdout)
        result = self.run_cli("decide", "--review", "r1.json", "--field", "continuous_current", "--action", "needs_input", "--reviewer", "demo-reviewer", "--reason", "Continuous or peak?", "--out", "r2.json")
        self.assertEqual(result.returncode, 0, result.stdout)
        reloaded = self.run_cli("show", "--review", "r2.json")
        report = json.loads(reloaded.stdout)
        self.assertEqual(reloaded.returncode, 0)
        self.assertEqual(report["extraction_status"], "needs_input")
        self.assertEqual(report["fields"]["input_max"]["item"]["value"], 30)
        self.assertEqual(report["history"][0]["before"]["value"], 300)
        self.assertEqual(report["history"][1]["reason"], "Continuous or peak?")
        previous = "r2.json"
        for index, field in enumerate(draft()["fields"], 3):
            target = f"r{index}.json"
            result = self.run_cli("decide", "--review", previous, "--field", field, "--action", "accept", "--reviewer", "demo-reviewer", "--reason", "Accept extraction including unknowns; not engineering sign-off.", "--out", target)
            self.assertEqual(result.returncode, 0, result.stdout)
            previous = target
        report = json.loads(self.run_cli("show", "--review", previous).stdout)
        self.assertEqual(report["extraction_status"], "reviewed")
        self.assertEqual(report["engineering_suitability"], "not_evaluated")
        self.assertEqual(len(report["history"]), 13)
        self.assertEqual((self.root / "r0.json").read_bytes(), initial_bytes)
        self.assertEqual(json.loads((self.root / "draft.json").read_text())["fields"]["input_max"]["value"], 300)

    def test_existing_destination_is_unchanged(self):
        self.init()
        before = (self.root / "r0.json").read_bytes()
        result = self.run_cli("decide", "--review", "r0.json", "--field", "input_max", "--action", "accept", "--reviewer", "demo", "--reason", "reason", "--out", "r0.json")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["error"], "destination_exists_choose_new_snapshot")
        self.assertEqual((self.root / "r0.json").read_bytes(), before)

    def test_invalid_decision_does_not_create_snapshot(self):
        self.init()
        self.save("bad-item.json", {"value": 30, "unit": "mV", "evidence_ids": ["Q2"]})
        result = self.run_cli("decide", "--review", "r0.json", "--field", "input_max", "--action", "correct", "--reviewer", "demo", "--reason", "reason", "--replacement", "bad-item.json", "--out", "invalid.json")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "invalid.json").exists())
        self.assertNotIn("Traceback", result.stderr)

    def test_changed_source_reloads_stale_and_blocks_write(self):
        self.init()
        path = self.root / "evaluation" / "inputs.json"
        inputs = json.loads(path.read_text(encoding="utf-8"))
        inputs["cases"][0]["blocks"][1]["text"] += " New revision."
        path.write_text(json.dumps(inputs), encoding="utf-8")
        result = self.run_cli("show", "--review", "r0.json")
        self.assertEqual(json.loads(result.stdout)["extraction_status"], "stale")
        result = self.run_cli("decide", "--review", "r0.json", "--field", "input_max", "--action", "accept", "--reviewer", "demo", "--reason", "reason", "--out", "stale.json")
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.root / "stale.json").exists())

    def test_malformed_input_returns_safe_error_without_snapshot(self):
        for text in ('{"case_id":"E01","case_id":"E02"}', "not json", "null", '{"x":NaN}'):
            (self.root / "bad.json").write_text(text, encoding="utf-8")
            result = self.run_cli("init", "--case", "E01", "--draft", "bad.json", "--origin", "demo", "--out", "bad-review.json")
            self.assertEqual(result.returncode, 2)
            self.assertFalse((self.root / "bad-review.json").exists())
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
