# Non-paid model interface and reservation guards

This slice implements an OpenRouter-shaped planner adapter and local budget accounting. The only enabled transport is a canned fixture: no HTTP adapter, API key lookup or real model request is included. Fixture labels are trusted local composition markers, not authentication or a network sandbox.

This document and its receipt describe the earlier fixture checkpoint. A later optional [authenticated bounded Agent](bounded-live-agent.md) adds an explicitly enabled HTTP adapter and a two-attempt persistent trial. Its [application trial](live-model-trial-verification.json) and [later in-app-browser trial](browser-live-verification.json) retrieved MCP evidence but stopped on invalid citations. The base fixture planner remains non-paid; these real-provider observations do not retroactively extend this checkpoint or prove a successful final answer.

## Run a demonstrable path

```sh
python -S model_demo.py --simulate-provider
python -S model_demo.py --simulate-provider --budget-usd 0.005
python -S model_demo.py --simulate-provider --scenario unknown_cost
python -S model_demo.py --simulate-provider --scenario fake_citation
python -S scripts/verify_model_guards.py
```

Success exits0; guard demonstrations intentionally exit2. They return fixed reasons and no answer on failure. The default tied-wiring fixture remains violates_requirement, even with zero gaps; overall engineering review is still required.

The adapter uses two provider-shaped responses. First, plan_evidence chooses two or three distinct allowlisted read tools, including assessment and gaps. Cached plan steps feed the existing controller. At the historical checkpoint, finish_evidence supplied citation identifiers. The current wire contract instead selects already-fetched bundles through evidence_tools; the server binds their exact references. Both mandatory bundles must be selected exactly once, and unfetched/unknown/foreign-case evidence is refused. The final answer retains its citations field. The controller independently checks references and renders the assessment; model prose or approval fields are rejected. Each planner binds its cached plan to one question/snapshot, caps provider attempts at two, and never automatically retries. See the [reference-binding repair](bounded-live-agent.md#reference-ownership-after-the-stopped-trials) and its separate [verification receipt](evidence-bundle-verification.json); older receipts do not establish this new wire behavior.

The payload uses forced function selection, non-streaming requests and disabled parallel tool calls; these fields follow the [official tool-calling format](https://openrouter.ai/docs/guides/features/tool-calling). Responses require one completed function call with string arguments. Duplicate JSON keys, truncated/multiple calls, unexpected functions and extra decision fields are rejected. The parser does not authenticate evidence or establish model quality.

## What budget safety means here

| Outcome | Accounting and next action |
| --- | --- |
| Insufficient available funds | Zero provider dispatch; model_budget_exhausted |
| Dispatch admitted | Reserve the caller-supplied maximum charge before awaiting the provider |
| Known cost within reservation | Record spent; release only the unused portion |
| Missing/invalid cost, transport failure, timeout or cancellation | Keep the hold; block further dispatch; reconcile externally before release |
| Reported cost exceeds the reservation | Record observed cost and overrun; block further calls, not pretend the bill was prevented |

All demo amounts are synthetic: USD0.02 budget, USD0.006 quote per attempt, USD0.001 settlement. Two successful fixture responses yield simulated spent0.002, held0 and available0.018. No money is charged.

The default ledger uses Decimal and supports shared reservation accounting within one event loop; it is neither durable nor cross-worker. The optional [SQLite ledger](shared-budget.md) adds persistent accounting across cooperating processes on the same host. Quotes are supplied by trusted composition, not by model output. A future live path needs verified model/provider pricing, conservative token/fee bounds, currency mapping and provider/key limits. The provider's [usage accounting](https://openrouter.ai/docs/cookbook/administration/usage-accounting) reports cost in credits; this simulation does not prove a live USD conversion or a hard billing cap. No token-based estimate is presented as a guaranteed price.

## Verification boundaries

Unit/CLI tests cover accounting, shared-budget competition, cancellation, cost uncertainty, overrun, malformed output, case binding and citation guards. Three temporary-copy mutations check refusal before over-budget dispatch, preservation of unknown exposure and refusal of non-fixture transports.

An opt-in test substitutes only the evidence boundary with actual stdio MCP and Neo4j. To include it in explicit recovery of an already-owned exited test container, use:

```sh
uv run --frozen --extra mcp python scripts/recover_neo4j_verification.py --container <exact-owned-test-container> --report-name recovery-provider-contract.json --readiness-seconds 900 --provider-contract
```

The harness refuses a mismatched image/owner/state/loopback binding or an existing report. This command is for the disposable synthetic environment, not a shared database. Provider output and money remain fixtures even when MCP/Neo4j are real. See [verification](verification.md) for actual executed results and limits.
