"""Explicit scripted planner -> bounded controller -> real MCP -> real Neo4j demo."""

import argparse
import asyncio
import json

from agent_control import run
from agent_tool_adapter import MCPReadTools
from examples.agent_control.scripted_planner import ScriptedPlanner


async def demonstrate(question, snapshot_id, scenario):
    from mcp import Client
    from mcp_read_client import server_parameters

    try:
        async with asyncio.timeout(90):
            async with Client(server_parameters()) as client:
                return await run(question, snapshot_id, ScriptedPlanner(scenario), MCPReadTools(client))
    except Exception:
        return {"mode": "scripted_planner_simulation", "status": "stopped", "reason": "session_failed",
                "answer": None, "paid_model_calls": 0, "engineering_approval": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-planner", action="store_true", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--question", required=True)
    parser.add_argument("--scenario", choices=("success", "invalid_tool", "fake_citation", "excess_tools"), default="success")
    args = parser.parse_args()
    result = asyncio.run(demonstrate(args.question, args.snapshot_id, args.scenario))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
