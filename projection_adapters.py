"""SQLite sync ledger, immutable bundle reader, and local Neo4j projection adapter."""

import copy
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import uuid

import interview_demo
import local_review
import neo4j_graph
import supplemental_intake
from projection_sync import SyncFailure, SyncInput


def expected_view(projection):
    """Projection-specific view contract; real query readback must equal these records."""
    # Neo4j properties(n) omits null properties; preserve null in source JSON.
    # Only node properties are normalized, not nullable fields in query result maps.
    nodes = {label: [{key: value for key, value in item.items() if value is not None}
                     for item in items] for label, items in projection["nodes"].items()}
    documents = {item["id"]: item for item in nodes["DocumentRevision"]}
    spec_documents = {edge["start"]: documents[edge["end"]] for edge in projection["edges"]["EXTRACTED_FROM"]}
    model = next(item["model"] for item in nodes["Component"] if item["manufacturer"] == "TI")
    specs = [{"model": model, "specification": item, "document": spec_documents[item["id"]]}
             for item in sorted(nodes["Specification"], key=lambda item: item["id"])]
    decision = None
    if nodes["Assessment"]:
        selected = next((item for item in nodes["Specification"] if any(
            edge["end"] == item["id"] for edge in projection["edges"]["USES_SPEC"])), None)
        decision = {"assessment": nodes["Assessment"][0],
                    "application": next(iter(nodes["ApplicationContext"]), None),
                    "fact_review": next(iter(nodes["FactReviewReceipt"]), None),
                    "selected_specification": selected,
                    "selected_document": None if selected is None else spec_documents[selected["id"]]}
    return copy.deepcopy({"snapshot": nodes["CaseSnapshot"][0], "specifications": specs,
                          "gaps": sorted(nodes["Gap"], key=lambda item: item["id"]), "decision": decision})


class SQLiteSyncStore:
    """Local synthetic-data ledger; each claim/finish is a SQLite transaction.

    The source bundle stays authoritative. A crash between saving it and enqueueing
    can leave an orphan bundle; explicit enqueue is idempotent. A crashed syncing
    job remains blocked until an operator establishes the previous worker stopped.
    """

    def __init__(self, path):
        self.path = Path(path)
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS projection_jobs (
                snapshot_id TEXT PRIMARY KEY, bundle_path TEXT NOT NULL, bundle_sha TEXT NOT NULL,
                stage TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','syncing','failed','synced')),
                attempts INTEGER NOT NULL DEFAULT 0, token TEXT, reason TEXT)""")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def enqueue(self, bundle_path, stage):
        path = Path(bundle_path).resolve(strict=True)
        bundle = supplemental_intake.read_json(path)
        projection = neo4j_graph.project_demo(bundle, stage)
        sid = projection["snapshot_id"]
        with self.connect() as connection:
            connection.execute("""INSERT INTO projection_jobs(snapshot_id,bundle_path,bundle_sha,stage,state)
                VALUES(?,?,?,?, 'pending') ON CONFLICT(snapshot_id) DO NOTHING""",
                (sid, str(path), local_review.fingerprint(bundle), stage))
        return sid

    def claim(self, snapshot_id):
        token = uuid.uuid4().hex
        with self.connect() as connection:
            changed = connection.execute("""UPDATE projection_jobs SET state='syncing', token=?, attempts=attempts+1,
                reason=NULL WHERE snapshot_id=? AND state IN ('pending','failed','synced')""", (token, snapshot_id)).rowcount
        return token if changed == 1 else None

    def load(self, snapshot_id):
        with self.connect() as connection:
            row = connection.execute("SELECT bundle_path,bundle_sha,stage FROM projection_jobs WHERE snapshot_id=?",
                                     (snapshot_id,)).fetchone()
        if row is None:
            raise SyncFailure("source_changed")
        try:
            bundle = supplemental_intake.read_json(Path(row[0]))
            if local_review.fingerprint(bundle) != row[1]:
                raise SyncFailure("source_changed")
            projection = neo4j_graph.project_demo(bundle, row[2])
            if projection["snapshot_id"] != snapshot_id:
                raise SyncFailure("source_changed")
            return SyncInput(snapshot_id, projection, expected_view(projection))
        except (OSError, ValueError, KeyError, TypeError):
            raise SyncFailure("source_changed") from None

    def finish(self, snapshot_id, token, state, reason):
        if state not in {"failed", "synced"}:
            raise ValueError("invalid_terminal_state")
        with self.connect() as connection:
            changed = connection.execute("""UPDATE projection_jobs SET state=?, reason=?, token=NULL
                WHERE snapshot_id=? AND token=? AND state='syncing'""", (state, reason, snapshot_id, token)).rowcount
        if changed != 1:
            raise SyncFailure("claim_changed")

    def status(self, snapshot_id):
        with self.connect() as connection:
            row = connection.execute("SELECT state,attempts,reason FROM projection_jobs WHERE snapshot_id=?",
                                     (snapshot_id,)).fetchone()
        return {"snapshot_id": snapshot_id, "sync_state": "not_enqueued" if row is None else row[0],
                "attempts": 0 if row is None else row[1], "reason": None if row is None else row[2],
                "engineering_approval": False}


class Neo4jProjectionTarget:
    def __init__(self, client):
        self.client = client

    def apply(self, value):
        try:
            self.client.import_projection(value.projection)
        except neo4j_graph.GraphError:
            raise SyncFailure("projection_unavailable") from None

    def read(self, snapshot_id):
        try:
            value = self.client.show(snapshot_id)
            if value["status"] == "not_found":
                return None
            return {key: value.get(key) for key in ("snapshot", "specifications", "gaps", "decision")}
        except (neo4j_graph.GraphError, KeyError, TypeError):
            raise SyncFailure("projection_unavailable") from None


class LostAcknowledgment:
    """Explicit fault injection after apply; delegates actual storage to its target."""
    def __init__(self, target):
        self.target = target

    def apply(self, value):
        self.target.apply(value)
        raise SyncFailure("acknowledgment_lost")

    def read(self, snapshot_id):
        return self.target.read(snapshot_id)


class FixtureProjectionTarget:
    """In-memory fake graph for the zero-network demo, NOT Neo4j verification."""
    def __init__(self):
        self.views = {}

    def apply(self, value):
        self.views.setdefault(value.snapshot_id, copy.deepcopy(value.expected_view))

    def read(self, snapshot_id):
        return copy.deepcopy(self.views.get(snapshot_id))
