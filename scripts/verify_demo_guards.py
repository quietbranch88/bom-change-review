"""Meaningful mutations in temporary copies; original code/tests never change."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CASES = (
    ("projection_sync.py", "if target.read(snapshot_id) != value.expected_view:", "if False:",
     "test_projection_sync.ProjectionSyncTests.test_partial_readback_must_not_mark_synced"),
    ("demo_service.py", "if snapshot_id not in self.permissions.get(user, ()):", "if False:",
     "test_demo_service.DemoServiceTests.test_denied_never_executes_work"),
    ("demo_service.py", "and len(self.queue) >= self.limits.waiting:", "and False:",
     "test_demo_service.DemoServiceTests.test_finite_queue_peak_and_fifo"),
    ("evidence_mcp.py", "elif not validators[params.name].is_valid(result):", "elif False:",
     "test_mcp_contracts.ContractTests.test_missing_gap_completion_criterion_cannot_be_success"),
    ("evidence_mcp.py", "if params.name not in TOOLS:", "if False:",
     "test_mcp_contracts.ContractTests.test_unknown_tool_is_protocol_error_without_service_call"),
    ("scripts/recover_neo4j_verification.py", "if not 30 <= args.readiness_seconds <= 900:", "if False:",
     "test_recovery_harness.RecoveryHarnessTests.test_invalid_wait_is_rejected_before_docker_access"),
)


def main():
    results = []
    with tempfile.TemporaryDirectory(prefix="bom-guard-mutation-") as temp:
        directory = Path(temp) / "project"
        shutil.copytree(ROOT, directory, ignore=shutil.ignore_patterns(
            ".git", ".venv", ".spec", "output", "articles", "__pycache__", ".env", ".env.*"))
        for name, before, after, test in CASES:
            path = directory / name
            original = path.read_bytes()
            if original.decode("utf-8").count(before) != 1:
                raise RuntimeError("mutation_target_ambiguous")
            baseline = execute(directory, test)
            if baseline.returncode:
                raise RuntimeError("baseline_not_passing")
            if "skipped" in baseline.stdout + baseline.stderr:
                raise RuntimeError("mutation_test_not_exercised")
            path.write_text(original.decode("utf-8").replace(before, after), encoding="utf-8")
            mutated = execute(directory, test)
            path.write_bytes(original)
            # Avoid stale timestamp/size bytecode after rapid restores.
            for cache in directory.glob("__pycache__/*.pyc"):
                cache.unlink()
            restored = execute(directory, test)
            output = mutated.stdout + mutated.stderr
            detected = mutated.returncode != 0 and "AssertionError" in output and "ImportError" not in output
            results.append({"module": name, "test": test, "baseline_passed": baseline.returncode == 0,
                            "mutation_assertion_failed": detected, "restored_passed": restored.returncode == 0})
    print(json.dumps({"isolated_copies_only": True, "results": results}, indent=2))
    return 0 if all(item["mutation_assertion_failed"] and item["restored_passed"] for item in results) else 1


def execute(directory, test):
    return subprocess.run([sys.executable, "-c",
        "import sys,unittest;sys.path.insert(0,'tests');unittest.main(module=None,argv=['guards',sys.argv[1]],verbosity=2)",
        test], cwd=directory, capture_output=True, text=True, encoding="utf-8", timeout=20, check=False)


if __name__ == "__main__":
    raise SystemExit(main())
