import copy
import json
from pathlib import Path
import tempfile
import unittest

import interview_demo
import neo4j_graph
from projection_adapters import FixtureProjectionTarget, LostAcknowledgment, SQLiteSyncStore, expected_view
from projection_sync import SyncFailure, synchronize


class ProjectionSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-sync-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        interview_demo.save_run(self.directory / "source", interview_demo.create_bundle(True))
        self.bundle = self.directory / "source/bundle.json"
        self.store = SQLiteSyncStore(self.directory / "ledger.sqlite")
        self.sid = self.store.enqueue(self.bundle, "internal/reviewed")
        self.target = FixtureProjectionTarget()

    def test_pending_then_success_only_after_full_readback(self):
        self.assertEqual(self.store.status(self.sid)["sync_state"], "pending")
        result = synchronize(self.sid, self.store, self.target)
        self.assertEqual(result["sync_state"], "synced")
        value = self.target.read(self.sid)
        self.assertEqual(value["decision"]["assessment"]["status"], "matches_requirement")
        self.assertEqual(value["decision"]["selected_specification"]["fact_id"], "F-BIAS-INTERNAL")
        self.assertFalse(value["decision"]["assessment"]["automatic_transition_allowed"])
        self.assertFalse(result["engineering_approval"])

    def test_graph_view_omits_null_properties_without_changing_source_or_false(self):
        projection = neo4j_graph.project_demo(interview_demo.create_bundle(True), "unknown/reviewed")
        source_before = copy.deepcopy(projection)
        value = expected_view(projection)
        self.assertNotIn("selected_fact_id", value["decision"]["assessment"])
        self.assertNotIn("input_maps_to_bias", value["decision"]["application"])
        self.assertFalse(value["decision"]["assessment"]["automatic_transition_allowed"])
        self.assertIsNone(value["decision"]["selected_specification"])
        self.assertEqual(projection, source_before)

    def test_lost_ack_commit_failed_ledger_reopen_and_retry(self):
        source_before = self.bundle.read_bytes()
        result = synchronize(self.sid, self.store, LostAcknowledgment(self.target))
        self.assertEqual(result["sync_state"], "failed")
        self.assertEqual(result["reason"], "acknowledgment_lost")
        self.assertIsNotNone(self.target.read(self.sid))
        before = copy.deepcopy(self.target.views)
        reopened = SQLiteSyncStore(self.directory / "ledger.sqlite")
        self.assertEqual(reopened.status(self.sid)["attempts"], 1)
        self.assertEqual(synchronize(self.sid, reopened, self.target)["sync_state"], "synced")
        self.assertEqual(reopened.status(self.sid)["attempts"], 2)
        self.assertEqual(self.target.views, before)
        self.assertEqual(self.bundle.read_bytes(), source_before)

    def test_repeat_and_rebuild_lost_projection(self):
        synchronize(self.sid, self.store, self.target)
        before = copy.deepcopy(self.target.views)
        self.assertEqual(synchronize(self.sid, self.store, self.target)["sync_state"], "synced")
        self.assertEqual(self.target.views, before)
        replacement = FixtureProjectionTarget()
        synchronize(self.sid, self.store, replacement)
        self.assertEqual(replacement.views, before)

    def test_partial_readback_must_not_mark_synced(self):
        class Partial(FixtureProjectionTarget):
            def read(self, sid):
                value = super().read(sid)
                value["specifications"] = []
                return value
        result = synchronize(self.sid, self.store, Partial())
        self.assertEqual(result["sync_state"], "failed")
        self.assertEqual(result["reason"], "readback_mismatch")

    def test_changed_source_denied_before_apply(self):
        changed = json.loads(self.bundle.read_text(encoding="utf-8"))
        changed["facts"]["facts"][0]["range"]["max"] = 900
        self.bundle.write_text(json.dumps(changed), encoding="utf-8")
        result = synchronize(self.sid, self.store, self.target)
        self.assertEqual(result["reason"], "source_changed")
        self.assertEqual(self.target.views, {})

    def test_one_claim_and_fenced_completion(self):
        token = self.store.claim(self.sid)
        reopened = SQLiteSyncStore(self.directory / "ledger.sqlite")
        self.assertIsNone(reopened.claim(self.sid))
        with self.assertRaises(SyncFailure):
            reopened.finish(self.sid, "wrong-token", "synced", None)
        self.assertEqual(reopened.status(self.sid)["sync_state"], "syncing")
        reopened.finish(self.sid, token, "failed", "projection_unavailable")
        self.assertIsNotNone(reopened.claim(self.sid))

    def test_enqueue_idempotent_and_invalid_source_not_registered(self):
        self.assertEqual(self.store.enqueue(self.bundle, "internal/reviewed"), self.sid)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) FROM projection_jobs").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.store.enqueue(self.bundle, "unknown-stage")

    def test_sql_parameter_literal_not_a_command(self):
        literal = "'; DROP TABLE projection_jobs; --"
        self.assertEqual(self.store.status(literal)["sync_state"], "not_enqueued")
        self.assertIsNone(self.store.claim(literal))
        self.assertEqual(self.store.status(self.sid)["sync_state"], "pending")

    def test_exception_redacted_and_saved_as_failed(self):
        class Failing(FixtureProjectionTarget):
            def apply(self, value):
                raise RuntimeError("private credential error")
        result = synchronize(self.sid, self.store, Failing())
        self.assertEqual(result["sync_state"], "failed")
        self.assertNotIn("credential", json.dumps(result))

    def test_old_snapshot_remains_unknown_after_reviewed_version(self):
        old = self.store.enqueue(self.bundle, "initial")
        synchronize(old, self.store, self.target)
        before = self.target.read(old)
        synchronize(self.sid, self.store, self.target)
        self.assertEqual(self.target.read(old), before)
        self.assertEqual(len(before["gaps"]), 2)
        self.assertIsNone(before["decision"])


if __name__ == "__main__":
    unittest.main()
