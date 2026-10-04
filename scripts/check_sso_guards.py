"""Meaningful isolated mutations; original sources and test expectations unchanged."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
MUTATIONS = [
    ("tenant", "oauth_mcp.py", 'and user["tenant"] == case["tenant"]', "and True",
     "GrantsTests.test_own_case_allowed_cross_tenant_denied_even_with_grant"),
    ("active", "oauth_provider.py", 'active.get("active") is not True', "False",
     "CryptoVerifierTests.test_resource_token_and_no_active_cache"),
    ("nonce", "oauth_provider.py", 'claims.get("nonce") != nonce', "False",
     "CryptoVerifierTests.test_id_token_nonce_and_both_resource_requests"),
    ("state_cookie", "sso_portal.py", 'found["cookie"] != cookie', "False",
     "VaultTests.test_single_use_browser_bound_state_and_expiry"),
    ("post_read", "evidence_mcp.py", '            if authorize is not None:\n'
     '                # Suppress results when permission/session ended during the read.\n'
     '                await authorize(params.arguments)', '            # Isolated mutation: guard removed.',
     "HTTPBoundaryTests.test_metadata_missing_token_wrong_scope_and_case_guard_before_read"),
]


def run(directory, test):
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", "-m", "unittest", "test_sso_oauth." + test, "-v"],
                            cwd=directory, env=env, capture_output=True, text=True, timeout=180, check=False)
    # Relevant assertion failure, not an import/startup error, establishes detection.
    return result.returncode, "AssertionError" in result.stderr and "ERROR:" not in result.stderr


def main():
    receipts = []
    with tempfile.TemporaryDirectory(prefix="bom-sso-mutations-") as temp:
        directory = Path(temp)
        for filename in ("oauth_provider.py", "oauth_mcp.py", "sso_portal.py", "evidence_mcp.py"):
            shutil.copyfile(ROOT / filename, directory / filename)
        shutil.copyfile(ROOT / "tests/test_sso_oauth.py", directory / "test_sso_oauth.py")
        for name, filename, before, after, test in MUTATIONS:
            target = directory / filename
            original = target.read_text(encoding="utf-8")
            if original.count(before) != 1:
                raise RuntimeError("mutation_anchor_changed")
            baseline, _ = run(directory, test)
            if baseline != 0:
                raise RuntimeError("baseline_failed")
            try:
                target.write_text(original.replace(before, after), encoding="utf-8")
                broken, assertion = run(directory, test)
                if broken == 0 or not assertion:
                    raise RuntimeError("mutation_not_detected_by_assertion")
            finally:
                target.write_text(original, encoding="utf-8")
            restored, _ = run(directory, test)
            if restored != 0:
                raise RuntimeError("restored_failed")
            receipts.append({"guard": name, "baseline_passed": True, "mutant_assertion_failed": True,
                             "restored_passed": True})
            print(name + ": baseline pass, mutant assertion failure, restored pass", flush=True)
    print(json.dumps({"mutations": receipts, "original_sources_untouched": True}))


if __name__ == "__main__":
    main()
