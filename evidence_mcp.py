"""Optional official-SDK stdio server. Only the three read tools are registered."""

import json
import logging
import os
import sys
from time import perf_counter

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import MCPError
from jsonschema import Draft202012Validator

from evidence_graph_reader import Neo4jSnapshotReader
from evidence_queries import EvidenceQueries, NOTICE, TOOLS, envelope
from evidence_mcp_schema import INPUT_SCHEMA, OUTPUT_SCHEMAS


MAX_RESULT_BYTES = 256 * 1024
TIMING_KEY = "bom-change-review/timing"


def create_server(queries):
    limiter = anyio.CapacityLimiter(1)
    validators = {name: Draft202012Validator(schema) for name, schema in OUTPUT_SCHEMAS.items()}
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(
            name=name, description=description, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMAS[name],
            annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False))
            for name, description in TOOLS.items()])

    async def call_tool(ctx, params):
        if params.name not in TOOLS:
            # Fixed protocol message, not the untrusted name. Never reaches DB.
            raise MCPError(code=types.INVALID_PARAMS, message="unknown_tool")
        started = perf_counter()
        # Serialize DB access; each query has a 20s timeout, no retries. The parent
        # client owns the overall deadline. No exception/input text enters results.
        async with limiter:
            acquired = perf_counter()
            try:
                result = await anyio.to_thread.run_sync(queries.execute, params.name, params.arguments)
            except Exception:
                result = envelope("error", error="internal_error")
        executed = perf_counter()
        try:
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
                result = envelope("error", error="response_limit")
            elif not validators[params.name].is_valid(result):
                result = envelope("error", error="invalid_result")
        except Exception:
            result = envelope("error", error="internal_error")
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        finished = perf_counter()
        timing = {"schema_version": "mcp-handler-timing-v1", "scope": "server_handler_only",
                  "queue_ms": (acquired - started) * 1000, "execute_ms": (executed - acquired) * 1000,
                  "validation_ms": (finished - executed) * 1000, "total_ms": (finished - started) * 1000}
        return types.CallToolResult(content=[types.TextContent(type="text", text=encoded)],
                                    structured_content=result, is_error=result["status"] == "error",
                                    _meta={TIMING_KEY: timing})

    return Server("bom-evidence-readonly", version="0.2.0", instructions=NOTICE,
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def serve():
    reader = Neo4jSnapshotReader(os.environ.get("BOM_NEO4J_PASSWORD"),
                                int(os.environ.get("BOM_NEO4J_PORT", "18747")))
    server = create_server(EvidenceQueries(reader))
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main():
    # SDK diagnostics may include peer input. This local server emits only a fixed
    # startup failure; stdout is exclusively the SDK protocol stream.
    logging.disable(logging.CRITICAL)
    try:
        anyio.run(serve)
    except Exception:
        print("mcp_server_failed", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
