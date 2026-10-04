"""Isolated source mutations; unchanged auth/attempt oracles, no paid contact."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("trial_attempts", "live_model.py", "if count >= 2:", "if False:",
     "test_trial_attempt_limit_is_durable_after_settlement"),
    ("case_permission", "sso_agent.py", "or not self.grants.allowed(token, snapshot_id)", "or False",
     "test_cross_case_role_inactive_and_subject_denial_before_factory"),
    ("before_model_revocation", "live_model.py", "await self.check()  # Revoked/foreign case never dispatches a model request.",
     "pass  # Isolated mutation only", "test_live_planner_revocation_precedes_reservation_and_dispatch"),
]


def run(directory, name):
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_live_agent.LiveAgentTests." + name, "-v"],
        cwd=directory, env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, timeout=30, check=False)
    return result.returncode, "AssertionError" in result.stderr and "ERROR:" not in result.stderr


def main():
    with tempfile.TemporaryDirectory(prefix="bom-live-guards-") as temp:
        directory = Path(temp)
        for name in ("live_model.py", "sso_agent.py"):
            shutil.copyfile(ROOT / name, directory / name)
        shutil.copyfile(ROOT / "tests/test_live_agent.py", directory / "test_live_agent.py")
        for guard, file, before, after, test in MUTATIONS:
            target = directory / file
            original = target.read_text(encoding="utf-8")
            if original.count(before) != 1 or run(directory, test)[0] != 0:
                raise RuntimeError("baseline_or_anchor_failed")
            try:
                target.write_text(original.replace(before, after, 1), encoding="utf-8")
                code, assertion = run(directory, test)
                if code == 0 or not assertion:
                    raise RuntimeError("relevant_assertion_not_detected")
            finally:
                target.write_text(original, encoding="utf-8")
            if run(directory, test)[0] != 0:
                raise RuntimeError("restored_failed")
            print(guard + ": baseline pass, mutant assertion failure, restored pass", flush=True)


if __name__ == "__main__":
    main()
