"""Outcome oracles for server-bound references; no model or database requests."""
import asyncio
import json
import unittest

from agent_control import Evidence, StopRun, run
from examples.agent_control.provider_fixture import FixtureProvider
from model_budget import Budget
from openrouter_planner import ProviderPlanner

SID = "case:" + "a" * 64
EXPECTED = sorted([SID, "gap:known", "assessment:known", "document:known", "specification:known"])

class Tools:
    async def call(self, name, args):
        refs = (SID, "gap:known") if name == "get_case_gaps" else (
            SID, "assessment:known", "document:known", "specification:known")
        return Evidence(name, SID, "synthetic_fixture", "matches_requirement", refs)

class Provider(FixtureProvider):
    def __init__(self, selection):
        super().__init__()
        self.selection = selection
    async def complete(self, request):
        response = await super().complete(request)
        if len(self.requests) == 1:
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(
                {"tool_names": ["get_case_gaps", "get_assessment_evidence"]})
        if len(self.requests) == 2:
            response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps(
                {"evidence_tools": self.selection})
        return response

class BundleFinishTests(unittest.TestCase):
    def execute(self, selection):
        provider = Provider(selection)
        result = asyncio.run(run("Explain missing evidence", SID,
            ProviderPlanner(provider, Budget("0.02")), Tools()))
        return result, provider

    def test_two_mandatory_bundles_bind_all_five_exact_references(self):
        result, provider = self.execute(["get_case_gaps", "get_assessment_evidence"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(sorted(result["answer"]["citations"]), EXPECTED)
        self.assertEqual(result["answer"]["result"], "matches_requirement")
        self.assertIs(result["engineering_approval"], False)
        self.assertEqual(len(provider.requests), 2)

    def test_missing_duplicate_unknown_and_unfetched_selection_never_repairs_answer(self):
        for selection in (["get_case_gaps"], ["get_case_gaps", "get_case_gaps"],
                          ["get_case_gaps", "get_assessment_evidence", "approve"],
                          ["get_case_gaps", "get_assessment_evidence", "get_candidate_specs"], [], "all", [1]):
            result, provider = self.execute(selection)
            self.assertEqual(result["reason"], "invalid_citations")
            self.assertIsNone(result["answer"])
            self.assertEqual(len(provider.requests), 2)

    def test_finish_wire_requires_bundles_not_model_generated_identifiers(self):
        _, provider = self.execute(["get_case_gaps", "get_assessment_evidence"])
        request = provider.requests[1]
        schema = request["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["required"], ["evidence_tools"])
        self.assertNotIn("citations", schema["properties"])
        context = json.loads(request["messages"][1]["content"])
        self.assertEqual(context["required_tools"], ["get_assessment_evidence", "get_case_gaps"])
        self.assertNotIn("document:known", request["messages"][1]["content"])

    def test_foreign_snapshot_bundle_is_refused_before_finishing_request(self):
        provider = Provider(["get_case_gaps", "get_assessment_evidence"])
        planner = ProviderPlanner(provider, Budget("0.02"))
        async def exercise():
            await planner.next("Explain", SID, ())
            await planner.next("Explain", SID, ())
            foreign = "case:" + "b" * 64
            evidence = (Evidence("get_case_gaps", SID, "synthetic_fixture", "matches_requirement", (SID,)),
                        Evidence("get_assessment_evidence", foreign, "synthetic_fixture", "matches_requirement", (foreign,)))
            with self.assertRaisesRegex(StopRun, "^tool_result_mismatch$"):
                await planner.next("Explain", SID, evidence)
        asyncio.run(exercise())
        self.assertEqual(len(provider.requests), 1)
