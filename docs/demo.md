# Five-minute demo

Python 3.11+ is required. The default path needs no network, Docker, API key, or model budget. Use a new output directory each time.

```sh
python system_demo.py --simulate-review --out output/demo-01
```

First inspect `lifecycle.stages`:

| Stage | Expected state |
| --- | --- |
| initial | unknown; two gaps |
| internal received | unknown; fact review missing |
| internal reviewed | matches_requirement; this conditional voltage check matches |
| tied received | unknown; fact review missing |
| tied reviewed | violates_requirement; zero gaps does not mean pass |
| unknown received | unknown; fact review and voltage-node mapping missing |
| unknown reviewed | unknown; voltage-node mapping still unresolved |

All review receipts and wiring declarations are simulated. The overall result still requires engineering review.

Next inspect `sync`:

1. `initial.sync_state` is synced: the old snapshot has been read back and verified.
2. `lost_ack.sync_state` is failed, but `commit_observed_after_failed_ack` is true. This deliberately injects a lost acknowledgment after the commit.
3. `recovered.sync_state` is synced after reopening the real SQLite ledger, explicitly retrying, and verifying readback.
4. `old_snapshot_unchanged` is true: the old unknown result and gaps are not overwritten.

Finally inspect `concurrency`: ten synthetic identities submit concurrently, with two active tasks, four waiting, and four busy responses. Each result includes queue_ms and elapsed_ms. Unauthorized cases are denied; disallowed tools produce tool_not_allowed; fabricated citations produce invalid_citations. Active permits and waiters return to zero afterward.

The complete report is saved to `output/demo-01/report.json`, alongside the source bundle and SQLite ledger. Existing directories are not overwritten.

## Real Neo4j and MCP

This path uses an isolated test container and synthetic data. Docker and uv are required. The harness stops only its own container; it does not delete containers or volumes.

```sh
uv sync --extra mcp --frozen
docker pull neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e
uv run --frozen --extra mcp python scripts/verify_neo4j.py --run-isolated --mcp --system-demo --readiness-seconds 420
```

The harness exercises real DB, stdio MCP, and SQLite recovery tests, then runs the public CLI with real Neo4j storage. Neo4j Community 5.26.29 is pinned; the container is limited to 2 CPU and 2 GiB, with 512 MiB heap and 256 MiB page cache. The random test password enters only child-process environment and local Docker configuration, not public reports.

420 seconds is a readiness deadline, not a startup-speed promise. A resource-heavy host previously exceeded the default 150-second deadline; see the [observed verification results](verification.md). New containers disable Neo4j usage reporting, but this is not a claim that all network traffic is isolated.

Real mode replaces only the sync graph-storage boundary. Concurrent-task delays remain controlled fixtures and the planner remains scripted. A separate suite supplies real MCP client/server/query evidence.

## Check that guards detect defects

```sh
python -m unittest discover -s tests -v
uv run --frozen --extra mcp python scripts/verify_demo_guards.py
```

The second command needs the optional MCP extra. It removes six guards in temporary copies: readback, case permission, queue limit, MCP output validation, unknown tools and recovery-wait validation. It requires relevant assertion failures, restores the code, and requires passes. Skips do not count as verification. Tests and expected values remain unchanged; the original checkout is not mutated. See the [MCP contract](mcp-contracts.md) for schemas, safe errors and diagnostic timing boundaries.
