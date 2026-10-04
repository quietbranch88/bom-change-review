# Shared SQLite budget for local processes

The optional SQLite adapter lets multiple processes on one host share a persistent synthetic budget. It replaces independent in-memory balances only when explicitly selected. Reservations, settlements and unknown outcomes survive process exit; the provider remains a local fixture, so no money is charged and no real OpenRouter model is connected.

## Run the persistent demo

```sh
python -S model_demo.py --simulate-provider --budget-db output/budget-demo/account.sqlite
python -S model_demo.py --simulate-provider --budget-db output/budget-demo/account.sqlite
python -S model_demo.py --simulate-provider --budget-db output/budget-demo/account.sqlite --scenario unknown_cost
python -S model_demo.py --simulate-provider --budget-db output/budget-demo/account.sqlite
```

Use a new directory for a new scenario. Reusing the same file deliberately reuses the account: the first two successful runs report simulated spent 0.002 then 0.004, not two fresh balances. The unknown-cost run exits 2, keeps a 0.006 hold and blocks new reservations; the final run exits 2 with model_budget_blocked and zero provider attempts. Those values assume a fresh file for this sequence. Without --budget-db, the existing in-memory demo is unchanged.

Account initialization never resets an existing limit or balance. A different limit or an unrelated existing database is refused with a fixed error. This entrypoint is for operator-selected local synthetic data, not a deployed/shared database migration or an authenticated account service.

## Transaction and storage design

BudgetPort exposes reserve, unknown, settle and snapshot without importing SQLite into the controller. The entrypoint selects SQLiteBudget; the provider adapter commits reserve before calling the fixture and settles afterward. No database transaction spans the provider await.

budget_accounts stores the limit and sticky blocked/overrun flags. budget_reservations stores a database-issued ticket, its account, quoted maximum, state and optional observed cost. The ledger is the source of truth; spent and held are derived from its records rather than duplicated counters.

A short BEGIN IMMEDIATE transaction checks blocked state and remaining capacity, then inserts the reservation before commit. This prevents two cooperating processes from each spending the same available amount. The design uses SQLite's single-writer transaction behavior; the [official transaction documentation](https://www.sqlite.org/lang_transaction.html) establishes engine semantics, not correctness of this application. Actual process tests establish the narrower application result.

Amounts use exact integer micro-USD for this simulation, at most six fractional digits and a maximum supported value of 1000000 USD per amount. Unsupported precision is rejected, never silently rounded. The default rollback journal is required, synchronous=FULL and foreign_keys=ON are set per connection, and the busy timeout is 100 ms. These synchronous calls can briefly block the event loop; the timeout is not an end-to-end latency promise. The adapter uses no automatic storage retry or in-memory fallback.

## Settlement and failure rules

| Event | Durable outcome |
| --- | --- |
| Successful reservation | Unique ticket and held amount committed before dispatch |
| Insufficient capacity or unavailable storage before dispatch | No provider attempt; fixed stop reason |
| Same ticket and same observed cost again | Idempotent settlement; no second charge in the ledger |
| Same ticket with a different repeated cost | model_settlement_conflict; original settlement preserved |
| Cost within quote | Spent recorded and unused hold released |
| Cost above quote | Observed cost committed once, overrun and blocked recorded, then model_cost_overrun |
| Unknown cost, failed transport or cancellation with successful unknown write | Hold retained and account persistently blocked |
| Process dies after reservation commit | Reserved hold remains; no automatic refund, expiry or resume |
| Storage prevents recording unknown after dispatch | Stop and reconciliation_required; committed hold remains, but shared blocked state is not claimed |

Ticket operations include the account boundary. Account names and database paths are trusted composition inputs, not authenticated tenant identity; file access is not application authorization. A late unknown notification cannot reverse an already-settled ticket. A trusted settlement can reconcile an unknown ticket, but does not automatically clear the sticky account block. No release/resume operator endpoint is included.

## Verification and remaining boundaries

```sh
python -m unittest discover -s tests -p test_sqlite_budget.py -v
python -S scripts/verify_shared_budget_guards.py
```

The tests use the adapter's actual schema and SQLite engine. Spawned processes race for capacity, settle the same ticket, and traverse the controller with a delayed fixture provider. Separate CLI runs reuse durable state. A process exits abruptly after commit; another process reads the hold and is denied an over-capacity reservation. Real SQL aborts and exclusive locks exercise rollback and failure diagnostics. See the [executed verification record](verification.md) and [source receipt](shared-budget-verification.json).

This is same-host, cooperating-process admission accounting on a local file. It does not prove network-filesystem safety, multiple-host coordination, authenticated user isolation, power-loss durability, model quality or a provider-enforced billing cap. This slice did not rerun the real Neo4j/MCP chain; the provider and evidence in its new CLI/process tests are fixtures, while SQLite is real. Historical MCP results remain separate. Live pricing/currency mapping, login, remote MCP authorization and distributed capacity are still pending.
