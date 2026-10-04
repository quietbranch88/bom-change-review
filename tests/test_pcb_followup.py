"""Contract examples through real isolated CLI/files; canned replies, no model."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import local_review
import offline_eval


ROOT = Path(__file__).resolve().parents[1]


class PcbFollowupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-pcb-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ("pcb_followup.py", "local_review.py", "offline_eval.py"):
            shutil.copyfile(ROOT / name, self.root / name)
        (self.root / "evaluation").mkdir()
        shutil.copyfile(ROOT / "evaluation/inputs.json", self.root / "evaluation/inputs.json")
        # Explicit fixture from the E01 question, not from references or the implementation.
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
        self.draft = {"case_id": "E01", "fields": {
            field: {"value": v, "unit": u, "evidence_ids": e}
            for field, (v, u, e) in values.items()
        }}
        self.save("base.json", local_review.create_review("E01", self.draft, "demo"))
        self.original = (self.root / "base.json").read_bytes()

    def save(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def read(self, name):
        return offline_eval.read_json(self.root / name)

    def cli(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, str(self.root / "pcb_followup.py"), *args],
            cwd=self.root, capture_output=True, text=True, encoding="utf-8", timeout=10, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        data = json.loads(result.stdout)
        if expected == 0:
            self.assertIs(data["synthetic"], True)
            self.assertEqual(data["engineering_suitability"], "not_evaluated")
        return data

    def start(self):
        result = self.cli("init", "--review", "base.json", "--out", "r0.json")
        self.assertEqual(result["state"], "needs_question")
        self.assertEqual(result["question_to_ask"], "是否允許修改 PCB？")
        return self.cli("ask", "--record", "r0.json", "--out", "r1.json")

    def reply(self, answer, source="SIM-1", previous="r1.json", out="r2.json"):
        return self.cli("reply", "--record", previous, "--answer", answer,
                        "--source-id", source, "--out", out)

    def test_allow_proposal_and_base_review_preserved(self):
        self.start()
        self.reply("allow")
        result = self.cli("show", "--record", "r2.json")
        self.assertEqual(result["state"], "pending_review")
        self.assertIs(result["value_proposal"], True)
        self.assertIsNone(result["base_value_unchanged"])
        self.assertEqual(result["base_field_status"], "pending")
        self.assertEqual(result["reply_source_ids"], ["SIM-1"])
        self.assertEqual(result["history"][1]["text"], "可以修改 PCB。")
        self.assertEqual(self.read("r2.json")["base_review"], self.read("base.json"))
        self.assertEqual((self.root / "base.json").read_bytes(), self.original)
        self.assertEqual(len(self.read("r1.json")["events"]), 1)

    def test_false_is_known_and_does_not_ask_again(self):
        self.start()
        result = self.reply("deny")
        self.assertEqual(result["state"], "pending_review")
        self.assertIs(result["value_proposal"], False)
        self.assertIsNone(result["question_to_ask"])
        self.cli("ask", "--record", "r2.json", "--out", "again.json", expected=2)
        self.assertFalse((self.root / "again.json").exists())

    def test_waiting_is_durable_and_duplicate_question_is_rejected(self):
        result = self.start()
        self.assertEqual(result["state"], "waiting_for_confirmation")
        self.assertIsNone(result["question_to_ask"])
        before = (self.root / "r1.json").read_bytes()
        for _ in range(2):
            self.assertEqual(self.cli("show", "--record", "r1.json")["state"], "waiting_for_confirmation")
        self.cli("ask", "--record", "r1.json", "--out", "again.json", expected=2)
        self.assertFalse((self.root / "again.json").exists())
        self.assertEqual((self.root / "r1.json").read_bytes(), before)

    def test_unknown_waits_without_asking_again(self):
        self.start()
        result = self.reply("unknown")
        self.assertEqual(result["state"], "waiting_for_confirmation")
        self.assertIsNone(result["value_proposal"])
        self.assertIsNone(result["question_to_ask"])
        self.assertEqual(result["history"][1]["text"], "我不知道，要再確認。")
        self.cli("ask", "--record", "r2.json", "--out", "again.json", expected=2)
        self.assertFalse((self.root / "again.json").exists())

    def test_later_known_reply_after_unknown_without_reasking(self):
        self.start()
        self.reply("unknown")
        result = self.reply("deny", "SIM-2", "r2.json", "r3.json")
        self.assertIs(result["value_proposal"], False)
        self.assertEqual(result["state"], "pending_review")
        self.assertEqual([event["action"] for event in result["history"]], ["ask", "reply", "reply"])
        self.assertEqual(result["reply_source_ids"], ["SIM-1", "SIM-2"])

    def test_conflicting_replies_never_use_last_value(self):
        self.start()
        self.reply("allow")
        result = self.reply("deny", "SIM-2", "r2.json", "r3.json")
        self.assertEqual(result["state"], "conflict")
        self.assertIsNone(result["value_proposal"])
        self.assertIsNone(result["question_to_ask"])
        result = self.reply("allow", "SIM-3", "r3.json", "r4.json")
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(len(result["history"]), 4)

    def test_unknown_does_not_erase_an_existing_known_reply(self):
        self.start()
        self.reply("deny")
        result = self.reply("unknown", "SIM-2", "r2.json", "r3.json")
        self.assertIs(result["value_proposal"], False)
        self.assertEqual(result["state"], "pending_review")

    def test_initial_false_is_known_with_independent_synthetic_source(self):
        inputs = self.read("evaluation/inputs.json")
        inputs["dataset_id"] = "synthetic-pcb-test"
        inputs["adaptation"] = "Synthetic test-only PCB constraint."
        inputs["cases"][0]["blocks"].append({"id": "SIM-BASE", "role": "question", "text": "不能修改 PCB。"})
        self.save("evaluation/inputs.json", inputs)
        self.draft["fields"]["pcb_changes_allowed"] = {"value": False, "unit": None, "evidence_ids": ["SIM-BASE"]}
        self.save("false-base.json", local_review.create_review("E01", self.draft, "demo", self.root / "evaluation"))
        result = self.cli("init", "--review", "false-base.json", "--out", "r0.json")
        self.assertEqual(result["state"], "existing_value")
        self.assertIs(result["value_proposal"], False)
        self.assertIsNone(result["question_to_ask"])
        self.cli("ask", "--record", "r0.json", "--out", "bad.json", expected=2)
        self.assertFalse((self.root / "bad.json").exists())

    def test_duplicate_or_non_simulated_source_is_rejected(self):
        self.start()
        self.reply("allow")
        for source in ("SIM-1", "Q3", "SIM-", "SIM- bad"):
            with self.subTest(source=source):
                self.cli("reply", "--record", "r2.json", "--answer", "deny",
                         "--source-id", source, "--out", "bad.json", expected=2)
                self.assertFalse((self.root / "bad.json").exists())

    def test_reply_before_question_is_rejected(self):
        self.cli("init", "--review", "base.json", "--out", "r0.json")
        self.cli("reply", "--record", "r0.json", "--answer", "allow",
                 "--source-id", "SIM-1", "--out", "bad.json", expected=2)
        self.assertFalse((self.root / "bad.json").exists())

    def test_destination_reuse_cannot_overwrite_base_or_snapshot(self):
        self.start()
        for name in ("base.json", "r1.json"):
            before = (self.root / name).read_bytes()
            result = self.cli("reply", "--record", "r1.json", "--answer", "deny",
                              "--source-id", "SIM-1", "--out", name, expected=2)
            self.assertEqual(result["error"], "destination_exists_choose_new_snapshot")
            self.assertEqual((self.root / name).read_bytes(), before)

    def test_source_change_reports_stale_and_blocks_transition(self):
        self.start()
        inputs = self.read("evaluation/inputs.json")
        inputs["cases"][0]["blocks"][1]["text"] += " Updated source."
        self.save("evaluation/inputs.json", inputs)
        result = self.cli("show", "--record", "r1.json")
        self.assertEqual(result["state"], "stale")
        self.assertIsNone(result["question_to_ask"])
        self.cli("reply", "--record", "r1.json", "--answer", "allow",
                 "--source-id", "SIM-1", "--out", "bad.json", expected=2)
        self.assertFalse((self.root / "bad.json").exists())
        self.cli("init", "--review", "base.json", "--out", "bad.json", expected=2)
        self.assertFalse((self.root / "bad.json").exists())

    def test_malformed_or_tampered_record_yields_safe_error(self):
        self.start()
        self.reply("deny")
        for mode in ("synthetic", "hash", "sequence", "text", "time", "extra"):
            broken = copy.deepcopy(self.read("r2.json"))
            if mode == "synthetic":
                broken["synthetic"] = False
            elif mode == "hash":
                broken["base_review"]["draft"]["fields"]["input_min"]["value"] = 999
            elif mode == "sequence":
                broken["events"][1]["sequence"] = 1
            elif mode == "text":
                broken["events"][1]["text"] = "PRIVATE-ERROR-MARKER"
            elif mode == "time":
                broken["events"][1]["at"] = "2000-01-01T00:00:00+00:00"
            else:
                broken["approved"] = True
            self.save("bad.json", broken)
            with self.subTest(mode=mode):
                result = self.cli("show", "--record", "bad.json", expected=2)
                self.assertEqual(result, {"error": "invalid_or_stale_followup_no_snapshot_written"})
        for content in ("null", "not json", '{"x":NaN}', '{"a":1,"a":2}'):
            (self.root / "bad.json").write_text(content, encoding="utf-8")
            self.cli("show", "--record", "bad.json", expected=2)


if __name__ == "__main__":
    unittest.main()
