# MCP tool contracts and diagnostics

This is an API contract, not a blog article. The boundary uses the official MCP SDK 2.2.0 over stdio. The core comparator remains standard-library only. This slice makes no paid model request and does not implement HTTP OAuth.

## Three tool-specific outputs

Every tool accepts only `snapshot_id`. Arbitrary Cypher, approval commands, and caller-declared permissions are not accepted. `tools/list` advertises a separate JSON Schema 2020-12 for each tool; the [schema definitions](../evidence_mcp_schema.py) are enforced at the [delivery boundary](../evidence_mcp.py).

| Tool | Historical-snapshot data | Required safeguards |
| --- | --- | --- |
| get_candidate_specs | snapshot and specification entries containing model, specification, document | Preserve ranges, wiring/temperature or AGND conditions, document revision/hash and page; source review remains pending |
| get_case_gaps | snapshot, gaps, assessment_status, zero_gaps_means_pass | Each gap has a reason, next action and completion criterion; zero gaps never means approval |
| get_assessment_evidence | snapshot, assessment_record_status, decision | An absent record has a null decision; unknown has no selected specification; a comparison retains its specification and document |

The envelope remains `evidence-tools-v1` with `engineering_approval=false`. Both evidence-graph-v1 and evidence-graph-context-v2 are supported. Neo4j omits null node properties, while nullable query-map values such as decision, application and fact_review remain null. Declaration status determines whether its corresponding value fields exist.

The fixed v1 wire contract rejects additional fields. Projection additions require an explicit contract update and compatibility tests. Unrecognized wiring and pending review are valid historical states, not application faults. Schema validation checks shape and specified state constraints; it does not authenticate documents, prove cross-object fingerprint consistency, or establish circuit suitability.

## Error classification

The pinned [MCP tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools) distinguishes protocol errors from tool-execution errors.

| Condition | Response | Database access and retry |
| --- | --- | --- |
| Unregistered tool | JSON-RPC -32602; fixed unknown_tool | No application dispatch, DB access or retry |
| Known tool, invalid arguments | isError=true; invalid_arguments | Application validation precedes DB access; no retry |
| Missing snapshot | isError=false; not_found; data=null | Distinct from an operational failure; no retry |
| Unavailable DB or rejected credential | isError=true; evidence_unavailable | No driver/credential details disclosed; no retry |
| Result exceeds 256 KiB | isError=true; response_limit | Never truncate into apparent success; no retry |
| Nested result violates its schema | isError=true; invalid_result | Do not return the malformed source payload; no retry |
| Unexpected execution or serialization exception | isError=true; internal_error | Fixed safe error; no retry |

Only MCP delivery maps unknown tools to protocol errors. Offline EvidenceQueries consumers retain their existing unknown_tool envelope. Known-tool invalid_arguments behavior remains compatible.

## Interpreting timing

Completed tool calls and execution-error responses include `_meta["bom-change-review/timing"]`:

- queue_ms: waiting for this server instance's single read permit.
- execute_ms: worker dispatch and application execution, including DB waits; not pure Cypher time.
- validation_ms: serialization, size checks, schema validation and final content encoding.
- total_ms: the sum of those phases, with scope=server_handler_only.

Diagnostics contain only fixed version/scope fields and milliseconds, not questions, source text, snapshot identifiers or credentials. Evidence JSON contains no timing. The SDK may add its own serverInfo metadata; the [public client](../mcp_read_client.py) exports only this project's timing namespace to diagnostics. Protocol errors and interrupted calls are not guaranteed to include timing.

These values exclude SDK framing, transport, the client, host admission, and planner/model latency. They are not end-to-end p95 or capacity measurements. Each stdio server process serializes its reads but does not bound its waiting callers; other processes have independent permits. The demo's two-active/four-waiting synthetic host path does not globally limit MCP clients.

## Reproduce checks

Install the optional MCP extra as described in the [demo](demo.md), then run:

```sh
uv run --frozen --extra mcp python -m unittest discover -s tests -p test_mcp_contracts.py -v
uv run --frozen --extra mcp python scripts/verify_demo_guards.py
```

Without the SDK, contract tests skip; the mutation harness rejects skipped tests as verification evidence. Use the demo's isolated-container command for actual stdio-to-Neo4j checks. See [verification](verification.md) for the observed environment and outcomes, including retained startup failures.
