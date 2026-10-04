"""Explicit non-paid provider-contract demo. No HTTP transport or credentials."""

import argparse
import asyncio
import json

from agent_control import run
from examples.agent_control.provider_fixture import FixtureProvider
from model_budget import Budget
from openrouter_planner import ProviderPlanner
import interview_demo
import neo4j_graph
from projection_adapters import FixtureProjectionTarget, expected_view
from projection_sync import SyncInput
from system_demo import DelayedTools, FixtureReader
from evidence_queries import EvidenceQueries


async def demonstrate(stage="tied/reviewed", scenario="success", budget_usd="0.02", tools=None, snapshot_id=None):
    if tools is None:
        projection = neo4j_graph.project_demo(interview_demo.create_bundle(True), stage)
        target = FixtureProjectionTarget()
        target.apply(SyncInput(projection["snapshot_id"], projection, expected_view(projection)))
        tools = DelayedTools(EvidenceQueries(FixtureReader(target)), 0)
        snapshot_id = projection["snapshot_id"]
    planner = ProviderPlanner(FixtureProvider(scenario), Budget(budget_usd))
    return await run("What evidence and next actions remain?", snapshot_id, planner, tools)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--simulate-provider", action="store_true", required=True)
    parser.add_argument("--scenario", choices=("success", "invalid_tool", "fake_citation", "unknown_cost",
                                             "overrun", "transport_error"), default="success")
    parser.add_argument("--budget-usd", choices=("0.02", "0.005"), default="0.02",
                        help="Synthetic accounting only, never authorizes an API charge")
    args = parser.parse_args()
    result = asyncio.run(demonstrate(scenario=args.scenario, budget_usd=args.budget_usd))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
