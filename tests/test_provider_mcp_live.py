"""Fake provider -> actual controller/stdio/Neo4j; never a real-model test."""

import os
import unittest

import interview_demo
import neo4j_graph as graph

ENABLED = os.environ.get("BOM_NEO4J_LIVE") == "isolated-new-container"
if ENABLED:
    import anyio
    from mcp import Client
    from agent_tool_adapter import MCPReadTools
    from mcp_read_client import server_parameters
    from model_demo import demonstrate


@unittest.skipUnless(ENABLED, "isolated provider-contract/MCP/Neo4j smoke not enabled")
class ProviderMCPLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = graph.Client(os.environ["BOM_NEO4J_PASSWORD"], int(os.environ["BOM_NEO4J_PORT"]))
        cls.bundle = interview_demo.create_bundle(True)
        cls.ids = {}
        for stage in ("internal/reviewed", "tied/reviewed", "unknown/reviewed"):
            projection = graph.project_demo(cls.bundle, stage)
            cls.db.import_projection(projection)
            cls.ids[stage] = projection["snapshot_id"]

    def durable_state(self):
        return graph.review.fingerprint({
            "nodes": self.db.query("MATCH (n) RETURN n.id AS id, labels(n) AS labels, properties(n) AS props ORDER BY id"),
            "edges": self.db.query("MATCH (a)-[r]->(b) RETURN a.id AS a, type(r) AS kind, b.id AS b, properties(r) AS props ORDER BY a, kind, b")})

    def test_two_fixture_responses_over_real_mcp_preserve_program_results(self):
        before = self.durable_state()
        async def scenario():
            with anyio.fail_after(90):
                async with Client(server_parameters()) as client:
                    for stage, status in (("internal/reviewed", "matches_requirement"),
                                          ("tied/reviewed", "violates_requirement"),
                                          ("unknown/reviewed", "unknown")):
                        result = await demonstrate(tools=MCPReadTools(client), snapshot_id=self.ids[stage])
                        self.assertEqual(result["status"], "completed")
                        self.assertEqual(result["answer"]["result"], status)
                        self.assertEqual(result["model_usage"]["provider_attempts"], 2)
                        self.assertEqual(result["model_usage"]["budget"]["spent"], "0.002")
                        self.assertEqual(result["paid_model_calls"], 0)
                        self.assertFalse(result["engineering_approval"])
                        if status == "unknown": self.assertTrue(result["answer"]["next_actions"])
        anyio.run(scenario)
        self.assertEqual(self.durable_state(), before)

    def test_budget_and_bad_citations_stop_without_graph_mutation(self):
        before = self.durable_state()
        async def scenario():
            with anyio.fail_after(90):
                async with Client(server_parameters()) as client:
                    for scenario, limit, reason, tools in (("success", "0.005", "model_budget_exhausted", 0),
                        ("unknown_cost", "0.02", "model_cost_unknown", 0),
                        ("fake_citation", "0.02", "invalid_citations", 3)):
                        result = await demonstrate(scenario=scenario, budget_usd=limit,
                            tools=MCPReadTools(client), snapshot_id=self.ids["tied/reviewed"])
                        self.assertEqual(result["reason"], reason)
                        self.assertIsNone(result["answer"])
                        self.assertEqual(result["counts"]["tool_calls"], tools)
                        self.assertEqual(result["paid_model_calls"], 0)
        anyio.run(scenario)
        self.assertEqual(self.durable_state(), before)
