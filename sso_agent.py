"""Authenticated optional review workflow; transport and model remain adapters."""
import anyio
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agent_control import run, StopRun
from agent_tool_adapter import MCPReadTools

QUESTION = "What evidence is missing, what can be done now, and what completes each missing item?"


class SSOReview:
    def __init__(self, provider, grants, planner_factory, mode="fixture"):
        self.provider, self.grants, self.planner_factory = provider, grants, planner_factory
        self.mode = mode

    async def execute(self, access, subject, snapshot_id, session_active):
        async def check():
            token = await self.provider.verify_token(access)
            if (not session_active() or token is None or token.subject != subject
                    or not self.grants.allowed(token, snapshot_id)):
                raise StopRun("evidence_unavailable")

        await check()  # No planner creation, key lookup, reservation or model request yet.
        planner = await self.planner_factory(check)
        async with httpx2.AsyncClient(headers={"Authorization": "Bearer " + access},
                                      timeout=25, trust_env=False, follow_redirects=False) as http:
            async with Client(streamable_http_client(self.provider.resource, http_client=http),
                              read_timeout_seconds=25, cache=None) as client:
                adapter = MCPReadTools(client)

                class Guarded:
                    async def call(self, name, arguments):
                        await check()
                        value = await adapter.call(name, arguments)
                        await check()
                        return value

                result = await run(QUESTION, snapshot_id, planner, Guarded())
        await check()  # Withhold if logout or revocation occurred while the model waited.
        return result


def review_factory(provider, grants, *, mode, budget_path, allow_paid=False):
    if mode not in {"fixture", "live"} or (mode == "live" and allow_paid is not True):
        raise StopRun("model_transport_disabled")

    async def planner(check):
        from live_model import TrialBudget, OpenRouterTransport, LiveProviderPlanner
        budget = TrialBudget(budget_path, "0.02", account="sso-trial")
        if mode == "fixture":
            from examples.agent_control.provider_fixture import FixtureProvider
            from openrouter_planner import ProviderPlanner

            class CheckedFixture(ProviderPlanner):
                async def request(self, function, parameters, context):
                    await check()
                    return await super().request(function, parameters, context)
            return CheckedFixture(FixtureProvider(), budget)
        from openrouter_smoke import load_key
        transport = OpenRouterTransport(load_key(), enabled=True)
        await transport.preflight()  # Read-only catalog/key calls, not inference.
        await check()
        return LiveProviderPlanner(transport, budget, check)

    return SSOReview(provider, grants, planner, mode)
