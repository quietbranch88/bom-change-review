"""Optional deterministic read client; not an LLM Agent and never calls a model."""

import argparse
import json
import os
from pathlib import Path
import sys

import anyio
from mcp import Client, StdioServerParameters
from evidence_queries import TOOLS

ROOT = Path(__file__).resolve().parent


def server_parameters():
    # The SDK adds a small OS allowlist. Do not inherit provider keys or dotenv.
    env = {key: os.environ[key] for key in ("BOM_NEO4J_PASSWORD", "BOM_NEO4J_PORT") if key in os.environ}
    env.update(PYTHONIOENCODING="utf-8", OTEL_SDK_DISABLED="true")
    return StdioServerParameters(command=sys.executable, args=[str(ROOT / "evidence_mcp.py")],
                                 cwd=ROOT, env=env)


async def query(snapshot_id, tool):
    with anyio.fail_after(75):
        async with Client(server_parameters()) as client:
            result = await client.call_tool(tool, {"snapshot_id": snapshot_id}, read_timeout_seconds=65)
            return {"protocol_version": client.protocol_version, "is_error": result.is_error,
                    "result": result.structured_content}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tool", choices=tuple(TOOLS))
    parser.add_argument("--snapshot-id", required=True)
    args = parser.parse_args()
    try:
        result = anyio.run(query, args.snapshot_id, args.tool)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2 if result["is_error"] else 0
    except Exception:
        print(json.dumps({"error": "mcp_client_failed", "retry": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
