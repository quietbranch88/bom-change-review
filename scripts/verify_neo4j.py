"""Create a fresh local test container, exercise the real graph path, then stop it.

No container/volume deletion. Credentials exist only in child-process memory and
Docker's local container configuration; they are not written into report files.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import neo4j_graph as graph  # noqa: E402


IMAGE = "neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e"
DOCKER = ["rtk", "docker"] if shutil.which("rtk") else ["docker"]


def command(args, env, timeout=60):
    return subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout, check=False)


def lifecycle_demo(env, directory):
    """Real public CLI path; fixed outcomes are an independent demo acceptance oracle."""
    run_dir = directory / "interview-run"
    started = command([sys.executable, "interview_demo.py", "run", "--simulate-review", "--out", str(run_dir)], env)
    if started.returncode:
        raise RuntimeError("interview_demo_creation_failed")
    bundle_path = run_dir / "bundle.json"
    expected = [("initial", "unknown", 2, None),
                ("internal/received", "unknown", 1, None),
                ("internal/reviewed", "matches_requirement", 0, "F-BIAS-INTERNAL"),
                ("tied/received", "unknown", 1, None),
                ("tied/reviewed", "violates_requirement", 0, "F-BIAS-TIED"),
                ("unknown/received", "unknown", 2, None),
                ("unknown/reviewed", "unknown", 1, None)]
    observed = []
    original = None
    for index, (stage, status, gap_count, selected_id) in enumerate(expected):
        imported = command([sys.executable, "neo4j_graph.py", "import", "--isolated", "--bundle", str(bundle_path),
                            "--stage", stage, "--out", str(directory / f"lifecycle-import-{index}.json")], env)
        if imported.returncode:
            raise RuntimeError("lifecycle_import_failed")
        value = json.loads(imported.stdout)
        sid = value["snapshot"]["id"]
        readback = command([sys.executable, "neo4j_graph.py", "show", "--id", sid,
                            "--out", str(directory / f"lifecycle-readback-{index}.json")], env)
        if readback.returncode or json.loads(readback.stdout) != value:
            raise RuntimeError("lifecycle_readback_mismatch")
        decision = value.get("decision")
        result_status = "unknown" if decision is None else decision["assessment"]["status"]
        selected = None if decision is None else decision["selected_specification"]
        if (result_status != status or len(value["gaps"]) != gap_count
                or (None if selected is None else selected["fact_id"]) != selected_id
                or value["snapshot"]["overall"] != "needs_engineering_review"
                or value["snapshot"]["origin"] != "synthetic_fixture"
                or {r["specification"]["review_status"] for r in value["specifications"]} != {"pending"}):
            raise RuntimeError("lifecycle_demo_assertion_failed")
        if original is None:
            original = value
        observed.append({"stage": stage, "snapshot_id": sid, "status": status,
                         "gaps": gap_count, "selected_fact_id": selected_id, "separate_readback_equal": True})
    old = command([sys.executable, "neo4j_graph.py", "show", "--id", original["snapshot"]["id"]], env)
    if old.returncode or json.loads(old.stdout) != original:
        raise RuntimeError("historical_snapshot_changed")
    report = {"origin": "synthetic_fixture", "stages": observed,
              "old_snapshot_unchanged": True, "model_calls": 0, "engineering_approval": False,
              "atomicity": "per_snapshot_not_entire_demo"}
    graph.review.write_new(directory / "lifecycle-demo.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-isolated", action="store_true", required=True)
    parser.add_argument("--demo", action="store_true", help="Also import/read existing saved case through actual CLI")
    parser.add_argument("--lifecycle-demo", action="store_true", help="Run seven synthetic interview stages through real DB CLI")
    parser.add_argument("--mcp", action="store_true", help="Also exercise the optional real stdio MCP boundary")
    parser.add_argument("--system-demo", action="store_true", help="Run SQLite/Neo4j sync recovery tests and new public CLI")
    parser.add_argument("--readiness-seconds", type=int, default=150,
                        help="Bounded readiness wait; record a larger value on resource-constrained hosts")
    args = parser.parse_args()
    if not 30 <= args.readiness_seconds <= 900:
        parser.error("readiness-seconds must be between 30 and 900")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18747))
    name = "bom-neo4j-test-" + uuid.uuid4().hex[:12]
    password = secrets.token_hex(24)
    env = dict(os.environ, NEO4J_AUTH="neo4j/" + password, BOM_NEO4J_PASSWORD=password,
               BOM_NEO4J_PORT="18747", BOM_NEO4J_LIVE="isolated-new-container", PYTHONIOENCODING="utf-8")
    directory = ROOT / "output" / name
    directory.mkdir(parents=True, exist_ok=False)
    record = {"container": name, "image": IMAGE, "started_at": datetime.now(timezone.utc).isoformat(),
              "limits": {"memory_bytes": 2147483648, "cpus": 2, "heap_max": "512m", "pagecache": "256m"},
              "endpoint": "http://127.0.0.1:18747", "status": "starting", "database": "neo4j",
              "test_data": "synthetic review events; version/injection fixtures are not engineering evidence"}
    record["readiness_limit_seconds"] = args.readiness_seconds
    created = False
    try:
        launch = command([*DOCKER, "run", "--detach", "--pull", "never", "--name", name,
                          "--label", "bom-change-review=isolated-test", "--memory", "2g", "--cpus", "2",
                          "--publish", "127.0.0.1:18747:7474", "--env", "NEO4J_AUTH",
                          "--env", "NEO4J_server_memory_heap_initial__size=256m",
                          "--env", "NEO4J_server_memory_heap_max__size=512m",
                          "--env", "NEO4J_server_memory_pagecache_size=256m",
                          "--env", "NEO4J_dbms_usage__report_enabled=false", IMAGE], env)
        if launch.returncode:
            raise RuntimeError("container_launch_failed")
        created = True
        client = graph.Client(password)
        ready_started = time.monotonic()
        deadline = ready_started + args.readiness_seconds
        while True:
            try:
                client.verify_engine()
                break
            except graph.GraphError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("database_readiness_timeout") from None
                time.sleep(2)
        record["readiness_elapsed_seconds"] = round(time.monotonic() - ready_started, 3)
        record["engine"] = client.query("CALL dbms.components() YIELD versions, edition RETURN versions[0] AS version, edition")
        client.initialize()
        print("Neo4j ready; constraints initialized; running real-engine tests.", flush=True)
        tests = command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_neo4j_live.py", "-v"], env, 240)
        record["tests"] = {"exit_code": tests.returncode, "output": tests.stdout + tests.stderr}
        print(tests.stdout + tests.stderr, flush=True)
        if tests.returncode:
            raise RuntimeError("live_tests_failed")
        if args.mcp:
            mcp_tests = command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_mcp_live.py", "-v"], env, 240)
            record["mcp_tests"] = {"exit_code": mcp_tests.returncode, "output": mcp_tests.stdout + mcp_tests.stderr}
            print(mcp_tests.stdout + mcp_tests.stderr, flush=True)
            if mcp_tests.returncode:
                raise RuntimeError("mcp_live_tests_failed")
        if args.demo:
            imported = command([sys.executable, "neo4j_graph.py", "import", "--isolated",
                                "--out", str(directory / "actual-import.json")], env)
            if imported.returncode:
                raise RuntimeError("actual_demo_import_failed")
            result = json.loads(imported.stdout)
            snapshot_id = result["snapshot"]["id"]
            shown = command([sys.executable, "neo4j_graph.py", "show", "--id", snapshot_id,
                             "--out", str(directory / "actual-readback.json")], env)
            if shown.returncode or json.loads(shown.stdout) != result:
                raise RuntimeError("actual_demo_readback_failed")
            if (len(result["specifications"]) != 3 or len(result["gaps"]) != 2
                    or {s["specification"]["review_status"] for s in result["specifications"]} != {"pending"}):
                raise RuntimeError("actual_demo_assertion_failed")
            record["demo"] = {"snapshot_id": snapshot_id, "specifications": 3, "open_gaps": 2,
                              "separate_process_readback_equal": True, "engineering_approval": False}
        if args.lifecycle_demo:
            record["lifecycle_demo"] = lifecycle_demo(env, directory)
        if args.system_demo:
            sync_tests = command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_projection_sync_live.py", "-v"], env, 240)
            record["sync_tests"] = {"exit_code": sync_tests.returncode, "output": sync_tests.stdout + sync_tests.stderr}
            print(sync_tests.stdout + sync_tests.stderr, flush=True)
            if sync_tests.returncode:
                raise RuntimeError("sync_live_tests_failed")
            demo = command([sys.executable, "system_demo.py", "--simulate-review", "--real-neo4j", "--isolated",
                            "--out", str(directory / "system-demo")], env, 120)
            if demo.returncode:
                raise RuntimeError("system_demo_failed")
            record["system_demo"] = json.loads(demo.stdout)
        record["status"] = "passed"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, graph.GraphError) as error:
        record["status"] = "failed"
        record["error_type"] = type(error).__name__
        # Exception text may carry command/env/server information; do not persist it.
    finally:
        if created:
            try:
                stopped = command([*DOCKER, "stop", "--time", "20", name], env, 45)
                record["container_stopped"] = stopped.returncode == 0
            except (OSError, subprocess.SubprocessError):
                record["container_stopped"] = False
                record["cleanup_error"] = "container_stop_failed"
                record["status"] = "failed"
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        graph.review.write_new(directory / "verification.json", record)
    print(json.dumps({"status": record["status"], "report": str(directory / "verification.json"),
                      "container_stopped": record.get("container_stopped", False)}, ensure_ascii=False))
    return 0 if record["status"] == "passed" and record.get("container_stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
