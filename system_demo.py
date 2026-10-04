"""One-command synthetic lifecycle, durable sync recovery and concurrent control demo."""

import argparse
import asyncio
from collections import Counter
import json
import os
from pathlib import Path

from agent_control import run
from agent_tool_adapter import decode
from demo_service import DemoService, Limits
from evidence_queries import EvidenceQueries, SnapshotEvidence
from examples.agent_control.scripted_planner import ScriptedPlanner
import interview_demo
import local_review
import neo4j_graph
from projection_adapters import (FixtureProjectionTarget, LostAcknowledgment,
                                 Neo4jProjectionTarget, SQLiteSyncStore)
from projection_sync import synchronize


class FixtureReader:
    def __init__(self, target):
        self.target = target

    def read_snapshot(self, snapshot_id):
        value = self.target.read(snapshot_id)
        return None if value is None else SnapshotEvidence(**value)


class DelayedTools:
    def __init__(self, queries, delay):
        self.queries, self.delay = queries, delay

    async def call(self, name, arguments):
        await asyncio.sleep(self.delay)
        return decode(name, arguments["snapshot_id"], self.queries.execute(name, arguments))


async def capacity_demo(target, snapshot_id):
    identities = {"SIM-user-" + str(index): {snapshot_id} for index in range(10)}
    service = DemoService(identities, Limits(active=2, waiting=4, queue_seconds=2, task_seconds=5))
    queries = EvidenceQueries(FixtureReader(target))

    async def submit(index, scenario="success"):
        return await service.execute("SIM-user-" + str(index), snapshot_id,
            lambda: run("What evidence and gaps remain?", snapshot_id,
                        ScriptedPlanner(scenario), DelayedTools(queries, .02)))

    results = await asyncio.gather(*(submit(index) for index in range(10)))
    def blocked_work():
        raise RuntimeError("denied_work_must_not_execute")
    denied = await service.execute("SIM-intruder", snapshot_id, blocked_work)
    invalid = await submit(0, "invalid_tool")
    fake = await submit(1, "fake_citation")
    return {"mode": "controlled_latency_scripted_planner_not_model_benchmark", "submissions": 10,
            "limits": {"active": 2, "waiting": 4}, "peak_active": service.peak_active,
            "statuses": dict(Counter(item["status"] for item in results)), "results": results,
            "unauthorized_status": denied["status"],
            "illegal_tool_reason": invalid["answer"]["reason"],
            "fake_citation_reason": fake["answer"]["reason"],
            "outstanding_after": len(service.users), "active_after": service.active,
            "queue_after": len(service.queue), "paid_model_calls": 0}


def demonstrate(destination, *, real_neo4j=False, isolated=False):
    if real_neo4j and not isolated:
        raise ValueError("explicit_isolated_database_required")
    directory = Path(destination)
    bundle = interview_demo.create_bundle(True)
    lifecycle = interview_demo.save_run(directory, bundle)
    store = SQLiteSyncStore(directory / "sync.sqlite")
    target = FixtureProjectionTarget()
    if real_neo4j:
        client = neo4j_graph.Client(os.environ.get("BOM_NEO4J_PASSWORD"),
                                    int(os.environ.get("BOM_NEO4J_PORT", "18747")))
        client.initialize()  # Refuses foreign-label data; only use a disposable test DB.
        target = Neo4jProjectionTarget(client)
    path = directory / "bundle.json"
    old = store.enqueue(path, "initial")
    initial = synchronize(old, store, target)
    new = store.enqueue(path, "internal/reviewed")
    before = target.read(old)
    lost = synchronize(new, store, LostAcknowledgment(target))
    # Read after the failed acknowledgment: target committed, ledger did not claim success.
    committed_despite_failure = target.read(new) is not None
    store = SQLiteSyncStore(directory / "sync.sqlite")  # Reopen durable intent, as after restart.
    recovered = synchronize(new, store, target)
    duplicate = synchronize(new, store, target)
    fixture_target = FixtureProjectionTarget()
    fixture_target.apply(store.load(new))
    summary = {"mode": "real_isolated_neo4j" if real_neo4j else "offline_fake_graph",
               "origin": "synthetic_fixture", "review_policy": interview_demo.POLICY,
               "lifecycle": lifecycle, "sync": {"initial": initial, "lost_ack": lost,
               "commit_observed_after_failed_ack": committed_despite_failure,
               "recovered": recovered, "duplicate": duplicate,
               "old_snapshot_unchanged": target.read(old) == before},
               "concurrency": asyncio.run(capacity_demo(fixture_target, new)),
               "engineering_approval": False, "paid_model_calls": 0}
    local_review.write_new(directory / "report.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="New directory; never overwrite previous runs")
    parser.add_argument("--simulate-review", action="store_true", required=True)
    parser.add_argument("--real-neo4j", action="store_true")
    parser.add_argument("--isolated", action="store_true")
    args = parser.parse_args()
    try:
        result = demonstrate(args.out, real_neo4j=args.real_neo4j, isolated=args.isolated)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if (result["sync"]["recovered"]["sync_state"] == "synced"
                     and result["sync"]["old_snapshot_unchanged"]) else 1
    except (OSError, ValueError, KeyError, TypeError, neo4j_graph.GraphError):
        print(json.dumps({"error": "demo_failed_or_destination_exists", "engineering_approval": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
