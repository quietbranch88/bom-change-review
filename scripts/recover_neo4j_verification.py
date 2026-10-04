"""Explicit recovery of an exited, labeled test container; no deletion or global restart."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import verify_neo4j as harness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--container", required=True)
    parser.add_argument("--report-name", default="recovery-verification.json")
    parser.add_argument("--readiness-seconds", type=int, default=420,
                        help="Explicit bounded readiness wait, between 30 and 900 seconds")
    args = parser.parse_args()
    if not 30 <= args.readiness_seconds <= 900:
        parser.error("readiness-seconds must be between 30 and 900")
    name = args.container
    if re.fullmatch(r"bom-neo4j-test-[0-9a-f]{12}", name) is None:
        parser.error("exact project test container required")
    if re.fullmatch(r"recovery-[a-z0-9-]+\.json", args.report_name) is None:
        parser.error("report-name must be a recovery-*.json basename")
    directory = harness.ROOT / "output" / name
    report_path = directory / args.report_name
    if report_path.exists():
        parser.error("report already exists; preserve previous verification")
    record = {"container": name, "mode": "explicit_owned_test_recovery", "status": "failed",
              "started_at": datetime.now(timezone.utc).isoformat(), "model_calls": 0,
              "readiness_limit_seconds": args.readiness_seconds, "phase": "inspect"}
    started = False
    try:
        inspected = harness.command([*harness.DOCKER, "inspect", name], os.environ)
        config = json.loads(inspected.stdout)[0]
        # Validate before extracting credentials or starting anything.
        binding = config["HostConfig"]["PortBindings"]["7474/tcp"]
        if (config["Config"]["Image"] != harness.IMAGE
                or config["Config"]["Labels"].get("bom-change-review") != "isolated-test"
                or config["State"]["Status"] != "exited"
                or binding != [{"HostIp": "127.0.0.1", "HostPort": "18747"}]):
            raise ValueError("container_not_matching_isolated_target")
        auth = next(value.removeprefix("NEO4J_AUTH=neo4j/") for value in config["Config"]["Env"]
                    if value.startswith("NEO4J_AUTH=neo4j/"))
        env = dict(os.environ, BOM_NEO4J_PASSWORD=auth, BOM_NEO4J_PORT="18747",
                   BOM_NEO4J_LIVE="isolated-new-container", PYTHONIOENCODING="utf-8")
        record["phase"] = "start"
        launch = harness.command([*harness.DOCKER, "start", name], env)
        if launch.returncode:
            raise RuntimeError("recovery_start_failed")
        started = True
        client = harness.graph.Client(auth)
        record["phase"] = "readiness"
        ready_started = time.monotonic()
        while True:
            try:
                client.verify_engine()
                break
            except harness.graph.GraphError:
                if time.monotonic() - ready_started > args.readiness_seconds:
                    raise RuntimeError("recovery_readiness_failed") from None
                time.sleep(2)
        record["readiness_elapsed_seconds"] = round(time.monotonic() - ready_started, 3)
        record["phase"] = "initialize"
        client.initialize()
        for pattern in ("test_projection_sync_live.py", "test_neo4j_live.py", "test_mcp_live.py"):
            record["phase"] = pattern
            completed = harness.command([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", pattern, "-v"], env, 240)
            output = (completed.stdout + completed.stderr).replace(auth, "[REDACTED]")
            record[pattern] = {"exit_code": completed.returncode, "output": output}
            print(output, flush=True)
            if completed.returncode:
                raise RuntimeError("recovered_suite_failed")
        destination = directory / args.report_name.removesuffix(".json") / "system-demo"
        record["phase"] = "system_demo"
        completed = harness.command([sys.executable, "system_demo.py", "--simulate-review", "--real-neo4j", "--isolated",
                                     "--out", str(destination)], env, 120)
        if completed.returncode:
            raise RuntimeError("recovered_demo_failed")
        record["system_demo"] = json.loads(completed.stdout)
        record["status"] = "passed"
        record["phase"] = "complete"
    except (OSError, ValueError, RuntimeError, KeyError, StopIteration, subprocess.SubprocessError, harness.graph.GraphError) as error:
        record["error_type"] = type(error).__name__
    finally:
        if started:
            try:
                record["container_stopped"] = harness.command([*harness.DOCKER, "stop", "--time", "20", name], os.environ, 60).returncode == 0
            except (OSError, subprocess.SubprocessError):
                record["container_stopped"] = False
        directory.mkdir(parents=True, exist_ok=True)
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        harness.graph.review.write_new(report_path, record)
    print(json.dumps({"status": record["status"], "container_stopped": record.get("container_stopped", False),
                      "report": str(report_path)}, ensure_ascii=False))
    return 0 if record["status"] == "passed" and record.get("container_stopped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
