"""Same-host durable synthetic budget; not auth or provider billing enforcement."""

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
import re
import sqlite3

from model_budget import BudgetError, money

SCALE = 1000000


def units(value):
    amount = money(value)
    if amount > 1000000:
        raise BudgetError("model_cost_unknown")
    if amount == 0:
        return 0
    _, digits, exponent = amount.as_tuple()
    while digits[-1] == 0:
        digits, exponent = digits[:-1], exponent + 1
    if exponent < -6:
        raise BudgetError("model_cost_unknown")
    return int("".join(map(str, digits))) * 10 ** (exponent + 6)


def dollars(value):
    return format(Decimal(value) / SCALE, "f")


class SQLiteBudget:
    def __init__(self, path, limit_usd, *, account="demo"):
        if not isinstance(account, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", account) is None:
            raise BudgetError("model_budget_configuration_mismatch")
        self.path, self.account = Path(path).resolve(), account
        limit = units(limit_usd)
        with self.transaction(write=True) as db:
            identity = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if identity == 0 and not tables and version == 0:
                db.execute("PRAGMA application_id = 1112493378")
                db.execute("PRAGMA user_version = 1")
                db.execute("""CREATE TABLE budget_accounts (
                    name TEXT PRIMARY KEY, limit_units INTEGER NOT NULL CHECK(limit_units >= 0),
                    blocked INTEGER NOT NULL DEFAULT 0 CHECK(blocked IN (0,1)),
                    overrun INTEGER NOT NULL DEFAULT 0 CHECK(overrun IN (0,1)))""")
                db.execute("""CREATE TABLE budget_reservations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT NOT NULL REFERENCES budget_accounts(name),
                    quote_units INTEGER NOT NULL CHECK(quote_units > 0),
                    state TEXT NOT NULL CHECK(state IN ('reserved','unknown','settled')),
                    cost_units INTEGER CHECK(cost_units >= 0),
                    CHECK((state = 'settled' AND cost_units IS NOT NULL)
                       OR (state != 'settled' AND cost_units IS NULL)))""")
                db.execute("CREATE INDEX budget_by_account ON budget_reservations(account)")
            elif identity != 1112493378 or version != 1:
                raise BudgetError("model_budget_configuration_mismatch")
            if db.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
                raise BudgetError("model_budget_configuration_mismatch")
            db.execute("INSERT OR IGNORE INTO budget_accounts(name,limit_units) VALUES (?,?)", (account, limit))
            if db.execute("SELECT limit_units FROM budget_accounts WHERE name=?", (account,)).fetchone()[0] != limit:
                raise BudgetError("model_budget_configuration_mismatch")

    @contextmanager
    def transaction(self, *, write=False):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=0.1, isolation_level=None)
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("PRAGMA synchronous = FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except sqlite3.Error:
            if db is not None: db.rollback()
            raise BudgetError("model_budget_unavailable") from None
        except BaseException:
            if db is not None: db.rollback()
            raise
        finally:
            if db is not None: db.close()

    def totals(self, db):
        spent, held = db.execute("""SELECT
            COALESCE(SUM(CASE WHEN state='settled' THEN cost_units ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN state!='settled' THEN quote_units ELSE 0 END),0)
            FROM budget_reservations WHERE account=?""", (self.account,)).fetchone()
        return spent, held

    def reserve(self, maximum_charge_usd):
        quote = units(maximum_charge_usd)
        if quote <= 0: raise BudgetError("invalid_model_quote")
        with self.transaction(write=True) as db:
            limit, blocked = db.execute("SELECT limit_units,blocked FROM budget_accounts WHERE name=?",
                                        (self.account,)).fetchone()
            if blocked: raise BudgetError("model_budget_blocked")
            spent, held = self.totals(db)
            if spent + held + quote > limit:
                raise BudgetError("model_budget_exhausted")
            ticket = db.execute("INSERT INTO budget_reservations(account,quote_units,state) VALUES (?,?,'reserved')",
                                (self.account, quote)).lastrowid
        return ticket

    def reservation(self, db, ticket):
        if type(ticket) is not int:
            raise BudgetError("invalid_model_ticket")
        row = db.execute("SELECT quote_units,state,cost_units FROM budget_reservations WHERE id=? AND account=?",
                         (ticket, self.account)).fetchone()
        if row is None: raise BudgetError("invalid_model_ticket")
        return row

    def unknown(self, ticket):
        with self.transaction(write=True) as db:
            _, state, _ = self.reservation(db, ticket)
            if state != "settled":
                db.execute("UPDATE budget_reservations SET state='unknown' WHERE id=?", (ticket,))
                db.execute("UPDATE budget_accounts SET blocked=1 WHERE name=?", (self.account,))

    def settle(self, ticket, actual_charge_usd):
        try:
            cost = units(actual_charge_usd)
        except BudgetError:
            self.unknown(ticket)
            raise
        with self.transaction(write=True) as db:
            quote, state, old_cost = self.reservation(db, ticket)
            if state == "settled":
                if cost != old_cost: raise BudgetError("model_settlement_conflict")
            else:
                db.execute("UPDATE budget_reservations SET state='settled',cost_units=? WHERE id=?", (cost, ticket))
                if cost > quote:
                    db.execute("UPDATE budget_accounts SET blocked=1,overrun=1 WHERE name=?", (self.account,))
        # Commit an observed overrun before reporting it; don't roll it back.
        if cost > quote: raise BudgetError("model_cost_overrun")

    def snapshot(self):
        with self.transaction() as db:
            limit, blocked, overrun = db.execute("SELECT limit_units,blocked,overrun FROM budget_accounts WHERE name=?",
                                               (self.account,)).fetchone()
            spent, held = self.totals(db)
        return {"currency": "USD", "limit": dollars(limit), "spent": dollars(spent), "held": dollars(held),
                "available": dollars(max(0, limit - spent - held)), "blocked": bool(blocked), "overrun": bool(overrun),
                "scope": "sqlite_same_host_shared_file"}
