# Evidence-based BOM Change Review

A component review demo with Neo4j, read-only MCP, durable projection recovery, and bounded concurrent tasks.

Starting from a [public TI replacement question](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/1222180/lm5155-replace-lt8301ess), this project organizes conditional LM5155 specifications, assessment evidence, gaps, and next actions. The original component is LT8301ESS. The first implementation checks only the recommended BIAS operating-voltage range; it does not approve a component replacement.

> Decisions need evidence and rules. Unknowns need reasons; closing gaps needs actions and acceptance criteria.

![System architecture and implementation boundaries](docs/architecture.svg)

[Architecture](docs/architecture.md) · [Five-minute demo](docs/demo.md) · [MCP contracts](docs/mcp-contracts.md) · [Verification and limitations](docs/verification.md)

The [non-paid model-interface demo](docs/model-interface.md) additionally exercises two provider-shaped responses, pre-dispatch reservations and conservative handling of unknown charges. It uses only a local fixture, not an OpenRouter API request.

The optional [shared SQLite budget](docs/shared-budget.md) persists that synthetic accounting across cooperating processes on the same host. It does not add real billing, login or multi-host coordination.

The separate [local login demo](docs/local-auth.md) adds password verification, SQLite sessions and case/tenant authorization for synthetic accounts. It is not remote MCP OAuth or enterprise SSO.

## Run the full story

Python 3.11+ is sufficient. No API key, Docker, or network is needed. Use a new output directory for each run.

```sh
python system_demo.py --simulate-review --out output/demo-01
```

The demo shows:

1. Immutable case versions moving from unknown to a conditionally matching, violating, or still-incomplete assessment.
2. A real SQLite sync ledger and an injected failure after the graph commit but before acknowledgment.
3. A failed job reopened and explicitly retried, with complete readback verification and the old snapshot preserved.
4. Ten synthetic identities submitting concurrently: two active tasks, four waiting, and four busy responses.
5. Rejection of unauthorized cases, disallowed tools, and fabricated citations, followed by permit cleanup.

Results, the original bundle, and the ledger are saved under output/demo-01. The default graph is a fixture and the planner is scripted. These concurrency numbers are not production model-performance measurements.

## Real Neo4j and MCP

```sh
uv sync --extra mcp --frozen
docker pull neo4j@sha256:89d577f2e49606de76441eca8cf7a0fe88e594cbaac4d2a3d86c6e59676e2b1e
uv run --frozen --extra mcp python scripts/verify_neo4j.py --run-isolated --mcp --system-demo --readiness-seconds 420
```

Docker and uv are required. The harness writes synthetic data only to its newly created isolated container and stops its own container afterward. It checks real Neo4j, stdio MCP, and SQLite sync failure/recovery. See the [verification record](docs/verification.md) for versions, observed failures, and environmental limitations.

## Implemented and pending

| Capability | State |
| --- | --- |
| Conditional specifications and version-bound evidence | Implemented; source facts remain pending review |
| Immutable review history and three assessment outcomes | Implemented; demo review and wiring are synthetic |
| Neo4j projection and exact evidence paths | Implemented; separate real-engine suite |
| Three read-only MCP tools | Implemented; official SDK 2.2.0, local stdio |
| Durable sync jobs and explicit retry | Implemented; SQLite plus Neo4j adapter |
| Admission, queueing, cancellation, and deadlines | Implemented; single event loop and synthetic permission fixtures |
| Real LLM planning and model evaluation | Pending; planner is currently scripted |
| Pre-dispatch reservation and settlement | Implemented and tested with synthetic quotes/costs; live pricing and billing enforcement pending |
| Persistent shared budget on one host | Implemented; real SQLite/process tests with synthetic money |
| Local CLI login and case authorization | Implemented for synthetic accounts; real SQLite session and authorization tests |
| HTTP MCP OAuth and multi-tenant login | Not implemented |
| Global task and queue limits across workers | Not implemented; separate from same-host shared budget accounting |
| EDA simulation, hardware tests, or replacement approval | Out of scope |

Historical extraction smoke modules do not mean the current Agent is connected to a model; the public demo does not call them. Any paid path requires renewed verification of the model, provider, pricing, and authorized trial budget.

## Tests and guard checks

```sh
python -m unittest discover -s tests -v
uv run --frozen --extra mcp python scripts/verify_demo_guards.py
```

The core uses the Python standard library; MCP is an optional extra. Windows-only private-diagnostic tests and external-service tests are skipped where unavailable. Guard checks need the MCP extra. They remove six guards in temporary copies, including output validation, unknown-tool rejection and recovery-wait validation, require the original tests to fail, then restore the code and require a pass. A skipped test is not a pass. See [executed results](docs/verification.md).

## Code entrypoints

- [system_demo.py](system_demo.py): demo entrypoint and adapter composition
- [projection_sync.py](projection_sync.py): sync and readback verification
- [projection_adapters.py](projection_adapters.py): SQLite and Neo4j boundaries
- [demo_service.py](demo_service.py): admission, queueing, and cancellation
- [agent_control.py](agent_control.py): tool, snapshot, and citation controls
- [evidence_mcp.py](evidence_mcp.py): read-only MCP server
- [evaluation](evaluation/README.md): extraction-development material and limits

Public documentation and diagrams use English. Original-language source excerpts, synthetic inputs, and test fixtures are preserved to avoid changing evidence, fingerprints, or evaluation tasks. They are not translated into new authoritative records.

AI assisted implementation and verification; the author supplied requirements and design decisions. Public evidence, synthetic scenarios, model output, and human engineering approval remain distinct. Original source rights belong to their respective owners. This repository does not include full vendor PDFs, API keys, private model responses, or local audit files.
