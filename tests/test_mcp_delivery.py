"""Optional SDK in-process checks; not counted as real database/stdio verification."""

import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest

SDK = importlib.util.find_spec("mcp") is not None
if SDK:
    import anyio
    from mcp import Client
    import evidence_mcp
    from evidence_queries import envelope


@unittest.skipUnless(SDK, "optional MCP SDK not installed")
class DeliveryTests(unittest.TestCase):
    def run_case(self, service, expected_error):
        async def run():
            async with Client(evidence_mcp.create_server(service)) as client:
                result = await client.call_tool("get_case_gaps", {"snapshot_id": "case:" + "a" * 64})
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content["error"], expected_error)
                self.assertIsNone(result.structured_content["data"])
                self.assertLess(len(json.dumps(result.structured_content)), 1024)
                self.assertNotIn("sensitive", result.content[0].text)
        anyio.run(run)

    def test_exception_text_not_exposed(self):
        class Broken:
            def execute(self, name, args):
                raise RuntimeError("sensitive secret exception")
        self.run_case(Broken(), "internal_error")

    def test_oversize_output_rejected_not_truncated_into_false_success(self):
        class Oversize:
            def execute(self, name, args):
                return envelope("historical_snapshot", data={"text": "x" * (256 * 1024)})
        self.run_case(Oversize(), "response_limit")

    def test_scripted_controller_cli_real_stdio_handles_unavailable_database(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            env = dict(os.environ, BOM_NEO4J_PASSWORD="synthetic-unreachable-test",
                       BOM_NEO4J_PORT=str(reserved.getsockname()[1]))
            process = subprocess.run([sys.executable, str(root / "agent_demo.py"), "--simulate-planner",
                                      "--question", "Explain", "--snapshot-id", "case:" + "a" * 64],
                                     env=env, capture_output=True, text=True, encoding="utf-8", timeout=100, check=False)
        self.assertEqual(process.returncode, 2, "expected controlled stop")
        result = json.loads(process.stdout)
        self.assertEqual(result["reason"], "evidence_unavailable")
        self.assertEqual(result["counts"], {"planner_calls": 1, "tool_calls": 1})
        self.assertIsNone(result["answer"])
        self.assertNotIn("synthetic-unreachable-test", process.stdout + process.stderr)

    def test_scripted_controller_cli_requires_explicit_simulation(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / "agent_demo.py"), "--question", "Explain",
                                 "--snapshot-id", "case:" + "a" * 64], capture_output=True,
                                text=True, encoding="utf-8", timeout=10, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--simulate-planner", result.stderr)
