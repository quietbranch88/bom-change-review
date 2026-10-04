"""Harness failure bookkeeping; Docker/network boundaries are deliberately mocked."""

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("isolated_harness", Path(__file__).resolve().parents[1] / "scripts/verify_neo4j.py")
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


class HarnessTests(unittest.TestCase):
    def test_stop_timeout_still_persists_safe_failed_report(self):
        def command(argv, env, timeout=60):
            if "stop" in argv:
                raise subprocess.TimeoutExpired(argv, timeout)
            return SimpleNamespace(returncode=0, stdout="started", stderr="")
        with tempfile.TemporaryDirectory(prefix="bom-harness-test-") as temp:
            with (patch.object(harness, "ROOT", Path(temp)), patch.object(harness, "command", command),
                  patch.object(harness.graph, "Client") as client, patch.object(harness.socket, "socket"),
                  patch.object(harness.sys, "argv", ["verify", "--run-isolated"]),
                  contextlib.redirect_stdout(io.StringIO())):
                client.return_value.verify_engine.side_effect = RuntimeError("private startup exception")
                result = harness.main()
            self.assertEqual(result, 1)
            reports = list(Path(temp).glob("output/*/verification.json"))
            self.assertEqual(len(reports), 1)
            record = json.loads(reports[0].read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed")
            self.assertFalse(record["container_stopped"])
            self.assertEqual(record["cleanup_error"], "container_stop_failed")
            self.assertNotIn("private startup", json.dumps(record))


if __name__ == "__main__":
    unittest.main()
