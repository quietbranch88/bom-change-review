"""Real-SQLite authorization mutations in isolated copies; never alter delivered code."""

import verify_demo_guards as guards

guards.CASES = (
    ("sqlite_auth.py", "AND c.tenant_id=?", "AND ? IS NOT NULL",
     "test_local_auth.LocalAuthTests.test_cross_tenant_grant_still_denied"),
    ("sqlite_auth.py", "now >= row[3]", "False",
     "test_local_auth.LocalAuthTests.test_absolute_expiry_despite_activity_at_boundary"),
    ("sqlite_auth.py", 'def logout(self, token):\n        key = verifier(token)\n        with self.transaction() as connection:\n            connection.execute("DELETE FROM sessions WHERE verifier=?", (key,))',
     'def logout(self, token):\n        key = verifier(token)\n        with self.transaction() as connection:\n            connection.execute("SELECT 1")',
     "test_local_auth.LocalAuthTests.test_logout_replay_denied_and_physical_session_deleted"),
    ("authenticated_review.py", "self.check()  # A read already in flight cannot be undone; withhold its result.",
     "pass  # Deliberate isolated mutation: return the revoked read.",
     "test_local_auth.LocalAuthTests.test_guard_blocks_result_after_logout_even_without_application_wrapper"),
    ("sqlite_auth.py", "now >= row[4] + row[6]", "now >= row[4] + self.idle_seconds",
     "test_local_auth.LocalAuthTests.test_reopening_with_longer_ttl_cannot_extend_issued_session"),
)

if __name__ == "__main__":
    raise SystemExit(guards.main())
