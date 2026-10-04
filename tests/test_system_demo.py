import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from system_demo import demonstrate


class SystemDemoTests(unittest.TestCase):
    def test_fresh_checkout_cli_creates_missing_output_parent(self):
        with tempfile.TemporaryDirectory(prefix="bom-fresh-output-") as temp:
            destination = Path(temp) / "missing-parent" / "run"
            process = subprocess.run([sys.executable, "system_demo.py", "--simulate-review", "--out", str(destination)],
                                     capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["sync"]["recovered"]["sync_state"], "synced")

    def test_full_offline_story_and_independent_reader(self):
        with tempfile.TemporaryDirectory(prefix="bom-system-test-") as temp:
            directory = Path(temp) / "run"
            report = demonstrate(directory)
            self.assertEqual(report["mode"], "offline_fake_graph")
            sync = report["sync"]
            self.assertEqual(sync["lost_ack"]["sync_state"], "failed")
            self.assertTrue(sync["commit_observed_after_failed_ack"])
            self.assertEqual(sync["recovered"]["sync_state"], "synced")
            self.assertTrue(sync["old_snapshot_unchanged"])
            concurrency = report["concurrency"]
            self.assertEqual(concurrency["statuses"], {"completed": 6, "busy": 4})
            self.assertEqual(concurrency["peak_active"], 2)
            self.assertEqual(concurrency["unauthorized_status"], "denied")
            self.assertEqual(concurrency["illegal_tool_reason"], "tool_not_allowed")
            self.assertEqual(concurrency["fake_citation_reason"], "invalid_citations")
            self.assertEqual(concurrency["active_after"], 0)
            for item in concurrency["results"]:
                if item["status"] == "completed":
                    self.assertEqual(item["answer"]["status"], "completed")
                    self.assertEqual(item["answer"]["answer"]["result"], "matches_requirement")
                    self.assertEqual(item["answer"]["answer"]["overall"], "needs_engineering_review")
            readback = subprocess.run([sys.executable, "-c",
                "import json,sys;print(json.dumps(json.load(open(sys.argv[1],encoding='utf-8'))))",
                str(directory / "report.json")], capture_output=True, text=True, encoding="utf-8", timeout=10, check=False)
            self.assertEqual(readback.returncode, 0)
            self.assertEqual(json.loads(readback.stdout), report)

    def test_cli_requires_simulation_and_preserves_existing_directory(self):
        with tempfile.TemporaryDirectory(prefix="bom-system-cli-") as temp:
            directory = Path(temp) / "run"
            missing = subprocess.run([sys.executable, "system_demo.py", "--out", str(directory)],
                                     capture_output=True, timeout=10, check=False)
            self.assertEqual(missing.returncode, 2)
            self.assertFalse(directory.exists())
            first = subprocess.run([sys.executable, "system_demo.py", "--simulate-review", "--out", str(directory)],
                                   capture_output=True, timeout=20, check=False)
            self.assertEqual(first.returncode, 0)
            original = (directory / "report.json").read_bytes()
            second = subprocess.run([sys.executable, "system_demo.py", "--simulate-review", "--out", str(directory)],
                                    capture_output=True, timeout=10, check=False)
            self.assertEqual(second.returncode, 2)
            self.assertEqual((directory / "report.json").read_bytes(), original)

    def test_real_mode_requires_isolated_flag_before_output(self):
        with tempfile.TemporaryDirectory(prefix="bom-system-denial-") as temp:
            directory = Path(temp) / "run"
            with self.assertRaises(ValueError):
                demonstrate(directory, real_neo4j=True)
            self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
