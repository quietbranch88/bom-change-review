"""Optional official-SDK stdio server. Only the three read tools are registered."""

import json
import logging
import os
import sys

import anyio
from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from evidence_graph_reader import Neo4jSnapshotReader
from evidence_queries import EvidenceQueries, ID_PATTERN, NOTICE, TOOLS, envelope


INPUT_SCHEMA = {"type": "object", "properties": {
    "snapshot_id": {"type": "string", "pattern": "^" + ID_PATTERN + "$", "minLength": 69, "maxLength": 69}},
    "required": ["snapshot_id"], "additionalProperties": False}
OUTPUT_SCHEMA = {"type": "object", "properties": {
    "schema_version": {"const": "evidence-tools-v1"},
    "status": {"enum": ["historical_snapshot", "not_found", "error"]},
    "data": {"type": ["object", "null"]}, "error": {"type": ["string", "null"]},
    "engineering_approval": {"const": False}, "notice": {"const": NOTICE}},
    "required": ["schema_version", "status", "data", "error", "engineering_approval", "notice"],
    "additionalProperties": False}
MAX_RESULT_BYTES = 256 * 1024


def create_server(queries):
    limiter = anyio.CapacityLimiter(1)
    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool(
            name=name, description=description, input_schema=INPUT_SCHEMA, output_schema=OUTPUT_SCHEMA,
            annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=False))
            for name, description in TOOLS.items()])

    async def call_tool(ctx, params):
        # Serialize DB access; each query has a 20s timeout, no retries. The parent
        # client owns the overall deadline. No exception/input text enters results.
        try:
            result = await anyio.to_thread.run_sync(queries.execute, params.name, params.arguments, limiter=limiter)
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
                result = envelope("error", error="response_limit")
        except Exception:
            result = envelope("error", error="internal_error")
        encoded = json.dumps(result, ensure_ascii=False, allow_nan=False)
        return types.CallToolResult(content=[types.TextContent(type="text", text=encoded)],
                                    structured_content=result, is_error=result["status"] == "error")

    return Server("bom-evidence-readonly", version="0.1.0", instructions=NOTICE,
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
