"""Recovery CLI/report contract; mocked Docker/DB, not real-boundary evidence."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("recovery_harness", SCRIPTS / "recover_neo4j_verification.py")
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)
NAME = "bom-neo4j-test-" + "a" * 12


def inspected(owner="isolated-test"):
    return [{"Config": {"Image": recovery.harness.IMAGE,
                         "Labels": {"bom-change-review": owner},
                         "Env": ["NEO4J_AUTH=neo4j/synthetic-recovery-credential"]},
             "State": {"Status": "exited"},
             "HostConfig": {"PortBindings": {"7474/tcp": [
                 {"HostIp": "127.0.0.1", "HostPort": "18747"}]}}}]


class RecoveryHarnessTests(unittest.TestCase):
    def run_harness(self, options=(), owner="isolated-test", failure=False):
        def command(argv, env, timeout=60):
            output = json.dumps(inspected(owner)) if "inspect" in argv else "{}"
            return SimpleNamespace(returncode=0, stdout=output, stderr="")
        with tempfile.TemporaryDirectory(prefix="bom-recovery-contract-") as temp:
            with (patch.object(recovery.harness, "ROOT", Path(temp)),
                  patch.object(recovery.harness, "command", side_effect=command) as calls,
                  patch.object(recovery.harness.graph, "Client") as client,
                  patch.object(recovery.time, "monotonic", side_effect=[0, 901, 902]),
                  patch.object(recovery.time, "sleep"),
                  patch.object(recovery.sys, "argv", ["recovery", "--container", NAME, *options]),
                  contextlib.redirect_stdout(io.StringIO())):
                self.last_calls = calls
                if failure:
                    client.return_value.verify_engine.side_effect = recovery.harness.graph.GraphError(
                        "private driver body synthetic-recovery-credential")
                result = recovery.main()
            report = json.loads((Path(temp) / "output" / NAME / "recovery-verification.json").read_text())
            return result, report, calls.call_args_list

    def test_invalid_wait_is_rejected_before_docker_access(self):
        for value in ("0", "29", "901"):
            with self.subTest(value=value):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        self.run_harness(("--readiness-seconds", value))
                self.assertEqual(raised.exception.code, 2)
                self.last_calls.assert_not_called()

    def test_default_and_maximum_are_recorded_without_retries(self):
        for options, expected in (((), 420), (("--readiness-seconds", "900"), 900),
                                  (("--readiness-seconds", "30"), 30)):
            with self.subTest(limit=expected):
                result, report, calls = self.run_harness(options)
                self.assertEqual(result, 0)
                self.assertEqual(report["readiness_limit_seconds"], expected)
                self.assertEqual(report["phase"], "complete")
                self.assertEqual(report["status"], "passed")
                self.assertTrue(report["container_stopped"])
                self.assertEqual(sum("start" in c.args[0] for c in calls), 1)
                self.assertEqual(sum("stop" in c.args[0] for c in calls), 1)

    def test_readiness_failure_retains_phase_and_stops_owned_target(self):
        result, report, calls = self.run_harness(("--readiness-seconds", "900"), failure=True)
        self.assertEqual(result, 1)
        self.assertEqual(report["phase"], "readiness")
        self.assertEqual(report["error_type"], "RuntimeError")
        self.assertTrue(report["container_stopped"])
        self.assertNotIn("private", json.dumps(report))
        self.assertNotIn("synthetic-recovery-credential", json.dumps(report))
        self.assertFalse(any("test_mcp_live.py" in c.args[0] for c in calls))

    def test_unsafe_owner_is_not_started_or_stopped(self):
        result, report, calls = self.run_harness(owner="unrelated-service")
        self.assertEqual(result, 1)
        self.assertEqual(report["phase"], "inspect")
        self.assertEqual(report["error_type"], "ValueError")
        self.assertEqual(len(calls), 1)
        self.assertIn("inspect", calls[0].args[0])
        self.assertNotIn("container_stopped", report)
