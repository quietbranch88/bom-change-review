"""Isolated real-SQLite guard mutation; no paid requests or delivered mutations."""

import verify_demo_guards as guards

guards.CASES = (
    ("sqlite_budget.py", "if spent + held + quote > limit:", "if False:",
     "test_sqlite_budget.SQLiteBudgetTests.test_insufficient_budget_preserves_ledger_and_refuses_fixture_dispatch"),
    ("sqlite_budget.py", 'if state == "settled":', "if False:",
     "test_sqlite_budget.SQLiteBudgetTests.test_duplicate_settlement_idempotent_conflicting_cost_rejected"),
    ("sqlite_budget.py", "UPDATE budget_accounts SET blocked=1 WHERE name=?", "UPDATE budget_accounts SET blocked=0 WHERE name=?",
     "test_sqlite_budget.SQLiteBudgetTests.test_unknown_survives_restart_and_never_refunds_or_unblocks_implicitly"),
)

if __name__ == "__main__":
    raise SystemExit(guards.main())
