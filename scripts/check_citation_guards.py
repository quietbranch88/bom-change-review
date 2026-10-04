"""Meaningful isolated citation-contract mutations; no model/network contact."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("mandatory_context", "openrouter_planner.py", '"required_tools": required_tools',
     '"required_tools": []', "test_citation_contract.CitationContractTests.test_finish_payload_defines_exact_required_and_allowed_reference_contract"),
    ("missing_count", "agent_control.py", '"missing_required_count": len(required - set(refs)) if shape else None',
     '"missing_required_count": 0 if shape else None',
     "test_citation_contract.CitationContractTests.test_missing_references_have_numeric_diagnostic_not_a_repaired_answer"),
    ("exact_bundle_binding", "openrouter_planner.py", "for ref in bundles[name].citations}",
     "for ref in bundles[name].citations[:-1]}",
     "test_evidence_bundle_finish.BundleFinishTests.test_two_mandatory_bundles_bind_all_five_exact_references")]


def run(directory, test):
    result = subprocess.run([sys.executable, "-B", "-m", "unittest",
        test, "-v"], cwd=directory,
        env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1"),
        capture_output=True, text=True, timeout=30, check=False)
    return result.returncode, "AssertionError" in result.stderr and "ERROR:" not in result.stderr


def main():
    with tempfile.TemporaryDirectory(prefix="bom-citation-guards-") as tmp:
        directory = Path(tmp)
        for name in ("openrouter_planner.py", "agent_control.py"):
            shutil.copyfile(ROOT / name, directory / name)
        shutil.copyfile(ROOT / "tests/test_citation_contract.py", directory / "test_citation_contract.py")
        shutil.copyfile(ROOT / "tests/test_evidence_bundle_finish.py", directory / "test_evidence_bundle_finish.py")
        for name, file, before, after, test in MUTATIONS:
            path = directory / file
            original = path.read_text(encoding="utf-8")
            if original.count(before) != 1 or run(directory, test)[0] != 0:
                raise RuntimeError("baseline_or_mutation_anchor_failed")
            try:
                path.write_text(original.replace(before, after, 1), encoding="utf-8")
                code, assertion = run(directory, test)
                if code == 0 or not assertion:
                    raise RuntimeError("expected_assertion_not_detected")
            finally:
                path.write_text(original, encoding="utf-8")
            if run(directory, test)[0] != 0:
                raise RuntimeError("restored_test_failed")
            print(name + ": baseline pass, mutant assertion failure, restored pass", flush=True)


if __name__ == "__main__":
    main()
