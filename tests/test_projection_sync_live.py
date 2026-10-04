"""Opt-in real SQLite -> workflow -> Neo4j import/read/retry tests on isolated fixtures."""

import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import interview_demo
import neo4j_graph
from projection_adapters import LostAcknowledgment, Neo4jProjectionTarget, SQLiteSyncStore
from projection_sync import synchronize


@unittest.skipUnless(os.environ.get("BOM_NEO4J_LIVE") == "isolated-new-container", "isolated Neo4j not enabled")
class LiveProjectionSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-sync-live-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.bundle = interview_demo.create_bundle(True)
        interview_demo.save_run(self.directory / "source", self.bundle)
        self.path = self.directory / "source/bundle.json"
        self.store = SQLiteSyncStore(self.directory / "sync.sqlite")
        self.client = neo4j_graph.Client(os.environ["BOM_NEO4J_PASSWORD"], int(os.environ["BOM_NEO4J_PORT"]))
        self.target = Neo4jProjectionTarget(self.client)

    def durable_graph(self):
        return {"nodes": self.client.query("MATCH (n) RETURN n.id AS id, properties(n) AS props ORDER BY id"),
                "edges": self.client.query("MATCH (a)-[r]->(b) RETURN a.id AS a,type(r) AS kind,b.id AS b,properties(r) AS props ORDER BY a,kind,b")}

    def test_lost_ack_restart_retry_full_readback_graph_equal(self):
        sid = self.store.enqueue(self.path, "internal/reviewed")
        before = self.path.read_bytes()
        failed = synchronize(sid, self.store, LostAcknowledgment(self.target))
        self.assertEqual(failed["sync_state"], "failed")
        self.assertEqual(failed["reason"], "acknowledgment_lost")
        committed = self.client.show(sid)
        self.assertEqual(committed["decision"]["assessment"]["status"], "matches_requirement")
        graph_before = self.durable_graph()
        result = synchronize(sid, SQLiteSyncStore(self.directory / "sync.sqlite"), self.target)
        self.assertEqual(result["sync_state"], "synced")
        self.assertEqual(self.client.show(sid), committed)
        self.assertEqual(self.durable_graph(), graph_before)
        self.assertEqual(self.path.read_bytes(), before)

    def test_older_snapshot_and_failed_mismatched_readback_preserved(self):
        old = self.store.enqueue(self.path, "initial")
        self.assertEqual(synchronize(old, self.store, self.target)["sync_state"], "synced")
        before = self.client.show(old)
        sid = self.store.enqueue(self.path, "tied/reviewed")
        class WrongReadback(Neo4jProjectionTarget):
            def read(self, snapshot_id):
                value = copy.deepcopy(super().read(snapshot_id))
                value["gaps"].append({"id": "invented"})
                return value
        failed = synchronize(sid, self.store, WrongReadback(self.client))
        self.assertEqual(failed["sync_state"], "failed")
        self.assertEqual(failed["reason"], "readback_mismatch")
        recovered = synchronize(sid, self.store, self.target)
        self.assertEqual(recovered["sync_state"], "synced", recovered)
        self.assertEqual(self.client.show(sid)["decision"]["assessment"]["status"], "violates_requirement")
        self.assertEqual(self.client.show(old), before)

    def test_wrong_auth_is_failed_never_synced(self):
        sid = self.store.enqueue(self.path, "unknown/reviewed")
        bad = Neo4jProjectionTarget(neo4j_graph.Client("synthetic-denied-credential", self.client.port))
        self.assertEqual(synchronize(sid, self.store, bad)["sync_state"], "failed")
        self.assertEqual(self.store.status(sid)["reason"], "projection_unavailable")
        self.assertEqual(synchronize(sid, self.store, self.target)["sync_state"], "synced")

    def test_public_real_cli_persists_and_reports_recovery(self):
        import json
        process = subprocess.run([sys.executable, "system_demo.py", "--simulate-review", "--real-neo4j", "--isolated",
                                  "--out", str(self.directory / "cli")], capture_output=True,
                                 text=True, encoding="utf-8", timeout=90, check=False)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(result["mode"], "real_isolated_neo4j")
        self.assertEqual(result["sync"]["recovered"]["sync_state"], "synced")
        self.assertTrue(result["sync"]["old_snapshot_unchanged"])
        self.assertEqual(result["concurrency"]["mode"], "controlled_latency_scripted_planner_not_model_benchmark")


if __name__ == "__main__":
    unittest.main()
