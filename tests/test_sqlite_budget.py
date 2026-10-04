"""Real SQLite/process contracts with synthetic money; no provider requests."""

import asyncio
from contextlib import closing
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from agent_control import run
from examples.agent_control.provider_fixture import FixtureProvider
from model_budget import BudgetError
from model_demo import demonstrate
from openrouter_planner import ProviderPlanner
from sqlite_budget import SQLiteBudget, units
from test_agent_control import SID, Tools


def budget_worker(path, limit, operation, ready, start, results, ticket=None):
    try:
        budget = SQLiteBudget(path, limit)
        ready.put(os.getpid())
        if not start.wait(10): raise RuntimeError("barrier_timeout")
        value = budget.reserve("0.006") if operation == "reserve" else budget.settle(ticket, "0.001")
        results.put({"status": "ok", "ticket": value, "pid": os.getpid()})
    except BudgetError as error:
        results.put({"status": str(error), "pid": os.getpid()})


def workflow_worker(path, ready, start, release, results):
    class SlowFixture(FixtureProvider):
        async def complete(self, request):
            if not self.requests:
                if not await asyncio.to_thread(release.wait, 10): raise RuntimeError("barrier_timeout")
            return await super().complete(request)
    budget = SQLiteBudget(path, "0.01")
    ready.put(os.getpid())
    if not start.wait(10): raise RuntimeError("barrier_timeout")
    provider = SlowFixture()
    result = asyncio.run(run("Evidence?", SID, ProviderPlanner(provider, budget), Tools()))
    results.put({"result": result, "fixture_dispatches": len(provider.requests), "pid": os.getpid()})


class SQLiteBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-budget-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "budget.sqlite"
        self.budget = SQLiteBudget(self.path, "0.02")

    def rows(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT id,account,quote_units,state,cost_units FROM budget_reservations ORDER BY id").fetchall()

    def test_exact_units_no_silent_rounding(self):
        self.assertEqual(units("0.000001"), 1)
        self.assertEqual(units("0.0010000000000000000000000000000"), 1000)
        self.assertEqual(units("0E-1000000"), 0)
        for value in ("0.0000001", "0.0010000000000000000000000000001", "1000000.000001", True, "NaN"):
            with self.subTest(value=value), self.assertRaises(BudgetError): units(value)

    def test_reserve_settle_and_reopen_keep_exact_state(self):
        ticket = self.budget.reserve("0.006")
        self.assertEqual(self.rows(), [(ticket, "demo", 6000, "reserved", None)])
        self.budget.settle(ticket, "0.001")
        reopened = SQLiteBudget(self.path, "0.02")
        self.assertEqual(reopened.snapshot()["spent"], "0.001")
        self.assertEqual(reopened.snapshot()["held"], "0")
        self.assertEqual(reopened.snapshot()["available"], "0.019")
        self.assertEqual(self.rows(), [(ticket, "demo", 6000, "settled", 1000)])

    def test_insufficient_budget_preserves_ledger_and_refuses_fixture_dispatch(self):
        budget = SQLiteBudget(self.path, "0.005", account="small")
        provider, tools = FixtureProvider(), Tools()
        result = asyncio.run(run("Explain", SID, ProviderPlanner(provider, budget), tools))
        self.assertEqual(result["reason"], "model_budget_exhausted")
        self.assertEqual(provider.requests, [])
        self.assertEqual(tools.calls, [])
        self.assertEqual(self.rows(), [])

    def test_duplicate_settlement_idempotent_conflicting_cost_rejected(self):
        ticket = self.budget.reserve("0.006")
        self.budget.settle(ticket, "0.001")
        SQLiteBudget(self.path, "0.02").settle(ticket, "0.001")
        before = self.rows()
        with self.assertRaisesRegex(BudgetError, "model_settlement_conflict"):
            self.budget.settle(ticket, "0.002")
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.budget.snapshot()["spent"], "0.001")

    def test_unknown_survives_restart_and_never_refunds_or_unblocks_implicitly(self):
        ticket = self.budget.reserve("0.006")
        self.budget.unknown(ticket)
        reopened = SQLiteBudget(self.path, "0.02")
        self.assertEqual(reopened.snapshot()["held"], "0.006")
        self.assertTrue(reopened.snapshot()["blocked"])
        with self.assertRaisesRegex(BudgetError, "model_budget_blocked"): reopened.reserve("0.006")
        reopened.settle(ticket, "0.001")
        self.assertTrue(reopened.snapshot()["blocked"])
        self.assertEqual(reopened.snapshot()["held"], "0")

    def test_late_unknown_after_settlement_does_not_rehold_or_block(self):
        ticket = self.budget.reserve("0.006")
        self.budget.settle(ticket, "0.001")
        self.budget.unknown(ticket)
        self.assertFalse(self.budget.snapshot()["blocked"])
        self.assertEqual(self.budget.snapshot()["held"], "0")

    def test_overrun_commits_observed_cost_once_and_blocks_after_restart(self):
        ticket = self.budget.reserve("0.006")
        for _ in range(2):
            with self.assertRaisesRegex(BudgetError, "model_cost_overrun"):
                SQLiteBudget(self.path, "0.02").settle(ticket, "0.03")
        self.assertEqual(self.rows(), [(ticket, "demo", 6000, "settled", 30000)])
        snapshot = self.budget.snapshot()
        self.assertEqual(snapshot["spent"], "0.03")
        self.assertEqual(snapshot["held"], "0")
        self.assertTrue(snapshot["overrun"])
        self.assertTrue(snapshot["blocked"])

    def test_invalid_cost_retains_hold_and_blocks(self):
        ticket = self.budget.reserve("0.006")
        with self.assertRaisesRegex(BudgetError, "model_cost_unknown"):
            self.budget.settle(ticket, "0.0000001")
        self.assertEqual(self.rows(), [(ticket, "demo", 6000, "unknown", None)])
        self.assertEqual(self.budget.snapshot()["held"], "0.006")
        self.assertTrue(self.budget.snapshot()["blocked"])

    def test_ticket_account_boundaries_and_invalid_tickets(self):
        ticket = self.budget.reserve("0.006")
        other = SQLiteBudget(self.path, "0.02", account="other")
        for value in (ticket, True, "1", -1):
            with self.subTest(value=value), self.assertRaisesRegex(BudgetError, "invalid_model_ticket"):
                other.settle(value, "0.001")
        with self.assertRaisesRegex(BudgetError, "invalid_model_ticket"): other.unknown(ticket)
        self.assertEqual(other.snapshot()["held"], "0")
        self.assertEqual(self.budget.snapshot()["held"], "0.006")

    def test_configuration_cannot_reset_limit_or_mutate_foreign_database(self):
        self.budget.reserve("0.006")
        before = self.rows()
        with self.assertRaisesRegex(BudgetError, "model_budget_configuration_mismatch"):
            SQLiteBudget(self.path, "0.005")
        self.assertEqual(self.rows(), before)
        foreign = Path(self.temp.name) / "foreign.sqlite"
        with closing(sqlite3.connect(foreign)) as db: db.execute("CREATE TABLE unrelated(value TEXT)")
        before_hash = hashlib.sha256(foreign.read_bytes()).hexdigest()
        with self.assertRaisesRegex(BudgetError, "model_budget_configuration_mismatch"):
            SQLiteBudget(foreign, "0.02")
        self.assertEqual(hashlib.sha256(foreign.read_bytes()).hexdigest(), before_hash)

    def test_real_sql_abort_rolls_back_reservation_without_dispatch(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TRIGGER reject_insert AFTER INSERT ON budget_reservations
                BEGIN SELECT RAISE(ABORT,'synthetic refusal'); END""")
        provider = FixtureProvider()
        result = asyncio.run(run("Explain", SID, ProviderPlanner(provider, self.budget), Tools()))
        self.assertEqual(result["reason"], "model_budget_unavailable")
        self.assertEqual(provider.requests, [])
        self.assertEqual(self.rows(), [])

    def test_real_exclusive_lock_fails_closed_and_accounting_reports_unavailable(self):
        with closing(sqlite3.connect(self.path, isolation_level=None)) as lock:
            lock.execute("BEGIN EXCLUSIVE")
            provider = FixtureProvider()
            result = asyncio.run(run("Explain", SID, ProviderPlanner(provider, self.budget), Tools()))
            self.assertEqual(result["reason"], "model_budget_unavailable")
            self.assertEqual(provider.requests, [])
            self.assertEqual(result["model_usage"]["budget"], {"state": "unavailable"})
            self.assertNotIn(str(self.path), json.dumps(result))
            lock.rollback()
        self.assertEqual(self.rows(), [])

    def test_controller_deadline_persists_unknown_hold(self):
        class Slow(FixtureProvider):
            async def complete(self, request): await asyncio.sleep(10)
        result = asyncio.run(run("Explain", SID, ProviderPlanner(Slow(), self.budget), Tools(), timeout_seconds=.02))
        self.assertEqual(result["reason"], "deadline_exceeded")
        reopened = SQLiteBudget(self.path, "0.02")
        self.assertEqual(reopened.snapshot()["held"], "0.006")
        self.assertTrue(reopened.snapshot()["blocked"])

    def test_failed_unknown_write_reports_reconciliation_not_false_global_block(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                path = Path(self.temp.name) / ("cancel.sqlite" if cancel else "transport.sqlite")
                budget = SQLiteBudget(path, "0.02")
                lock = sqlite3.connect(path, isolation_level=None)
                class Interrupted(FixtureProvider):
                    async def complete(self, request):
                        lock.execute("BEGIN EXCLUSIVE")
                        if cancel: await asyncio.sleep(10)
                        raise RuntimeError("private transport body")
                try:
                    result = asyncio.run(run("Explain", SID, ProviderPlanner(Interrupted(), budget), Tools(),
                                             timeout_seconds=.02 if cancel else 90))
                    self.assertEqual(result["reason"], "deadline_exceeded" if cancel else "model_budget_unavailable")
                    self.assertTrue(result["model_usage"]["reconciliation_required"])
                    self.assertEqual(result["model_usage"]["budget"], {"state": "unavailable"})
                    self.assertNotIn("private", json.dumps(result))
                finally:
                    lock.rollback()
                    lock.close()
                self.assertEqual(budget.snapshot()["held"], "0.006")
                self.assertFalse(budget.snapshot()["blocked"])

    def test_transaction_released_before_fixture_await(self):
        class CheckLock(FixtureProvider):
            async def complete(inner, request):
                with closing(sqlite3.connect(self.path, timeout=0, isolation_level=None)) as db:
                    db.execute("BEGIN IMMEDIATE")
                    db.rollback()
                return await super().complete(request)
        result = asyncio.run(run("Explain", SID, ProviderPlanner(CheckLock(), self.budget), Tools()))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(self.budget.snapshot()["spent"], "0.002")

    def workers(self, limit, operation, ticket=None):
        context = multiprocessing.get_context("spawn")
        ready, results, start = context.Queue(), context.Queue(), context.Event()
        jobs = [context.Process(target=budget_worker, args=(self.path, limit, operation, ready, start, results, ticket))
                for _ in range(2)]
        try:
            for job in jobs: job.start()
            identities = {ready.get(timeout=15) for _ in jobs}
            self.assertEqual(len(identities), 2)
            self.assertNotIn(os.getpid(), identities)
            start.set()
            output = [results.get(timeout=15) for _ in jobs]
            for job in jobs:
                job.join(15)
                self.assertEqual(job.exitcode, 0)
            return output
        finally:
            start.set()
            for job in jobs:
                if job.is_alive(): job.terminate(); job.join(5)
            for queue in (ready, results): queue.close(); queue.join_thread()

    def test_two_spawned_processes_compete_for_last_reservation(self):
        path = Path(self.temp.name) / "last.sqlite"
        self.path = path
        budget = SQLiteBudget(path, "0.006")
        output = self.workers("0.006", "reserve")
        self.assertEqual(sorted(item["status"] for item in output), ["model_budget_exhausted", "ok"])
        self.assertEqual(budget.snapshot()["held"], "0.006")
        self.assertEqual(budget.snapshot()["spent"], "0")
        self.assertEqual(len(self.rows()), 1)

    def test_two_spawned_processes_settle_same_ticket_only_once(self):
        ticket = self.budget.reserve("0.006")
        output = self.workers("0.02", "settle", ticket)
        self.assertEqual([item["status"] for item in output], ["ok", "ok"])
        self.assertEqual(self.rows(), [(ticket, "demo", 6000, "settled", 1000)])
        self.assertEqual(self.budget.snapshot()["spent"], "0.001")

    def test_process_death_after_commit_retains_hold_for_later_process(self):
        code = "import os,sys; from sqlite_budget import SQLiteBudget; b=SQLiteBudget(sys.argv[1],'0.02'); b.reserve('0.006'); os._exit(7)"
        crashed = subprocess.run([sys.executable, "-S", "-c", code, str(self.path)], capture_output=True, timeout=15)
        self.assertEqual(crashed.returncode, 7)
        code = ("import json,sys; from sqlite_budget import SQLiteBudget; from model_budget import BudgetError; "
                "b=SQLiteBudget(sys.argv[1],'0.02'); print(json.dumps(b.snapshot())); "
                "\ntry: b.reserve('0.015')\nexcept BudgetError as e: print(str(e))")
        reopened = subprocess.run([sys.executable, "-S", "-c", code, str(self.path)], capture_output=True, text=True, timeout=15)
        self.assertEqual(reopened.returncode, 0, reopened.stderr)
        lines = reopened.stdout.splitlines()
        self.assertEqual(json.loads(lines[0])["held"], "0.006")
        self.assertEqual(lines[1], "model_budget_exhausted")
        self.assertEqual(self.rows()[0][3:], ("reserved", None))

    def test_two_controller_processes_share_admission_before_fixture_dispatch(self):
        path = Path(self.temp.name) / "workflow.sqlite"
        SQLiteBudget(path, "0.01")
        context = multiprocessing.get_context("spawn")
        ready, results, start, release = context.Queue(), context.Queue(), context.Event(), context.Event()
        jobs = [context.Process(target=workflow_worker, args=(path, ready, start, release, results)) for _ in range(2)]
        try:
            for job in jobs: job.start()
            pids = {ready.get(timeout=15) for _ in jobs}
            self.assertEqual(len(pids), 2)
            start.set()
            denied = results.get(timeout=15)
            self.assertEqual(denied["result"]["reason"], "model_budget_exhausted")
            self.assertEqual(denied["fixture_dispatches"], 0)
            self.assertEqual(SQLiteBudget(path, "0.01").snapshot()["held"], "0.006")
            release.set()
            admitted = results.get(timeout=15)
            self.assertEqual(admitted["result"]["status"], "completed")
            self.assertEqual(admitted["fixture_dispatches"], 2)
            self.assertEqual(admitted["result"]["paid_model_calls"], 0)
            for job in jobs: job.join(15); self.assertEqual(job.exitcode, 0)
            self.assertEqual(SQLiteBudget(path, "0.01").snapshot()["spent"], "0.002")
        finally:
            start.set(); release.set()
            for job in jobs:
                if job.is_alive(): job.terminate(); job.join(5)
            for queue in (ready, results): queue.close(); queue.join_thread()

    def test_public_cli_reuses_balance_and_persistent_unknown_block(self):
        root = Path(__file__).resolve().parents[1]
        command = [sys.executable, "-S", str(root / "model_demo.py"), "--simulate-provider", "--budget-db", str(self.path)]
        for spent in ("0.002", "0.004"):
            completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["model_usage"]["budget"]["spent"], spent)
            self.assertEqual(result["model_usage"]["budget"]["scope"], "sqlite_same_host_shared_file")
            self.assertEqual(result["paid_model_calls"], 0)
        failed = subprocess.run([*command, "--scenario", "unknown_cost"], capture_output=True, text=True, timeout=15)
        self.assertEqual(failed.returncode, 2)
        self.assertEqual(json.loads(failed.stdout)["reason"], "model_cost_unknown")
        later = subprocess.run(command, capture_output=True, text=True, timeout=15)
        result = json.loads(later.stdout)
        self.assertEqual(later.returncode, 2)
        self.assertEqual(result["reason"], "model_budget_blocked")
        self.assertEqual(result["model_usage"]["provider_attempts"], 0)
        self.assertEqual(result["model_usage"]["budget"]["spent"], "0.004")
        self.assertEqual(result["model_usage"]["budget"]["held"], "0.006")

    def test_public_cli_configuration_failure_is_fixed_and_never_resets(self):
        root = Path(__file__).resolve().parents[1]
        self.budget.reserve("0.006")
        command = [sys.executable, "-S", str(root / "model_demo.py"), "--simulate-provider", "--budget-db", str(self.path),
                   "--budget-usd", "0.005"]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(result["reason"], "model_budget_configuration_mismatch")
        self.assertEqual(result["counts"]["tool_calls"], 0)
        self.assertEqual(result["paid_model_calls"], 0)
        self.assertNotIn(str(self.path), completed.stdout)
        self.assertEqual(self.budget.snapshot()["held"], "0.006")
