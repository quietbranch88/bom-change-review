"""Independent known-reference contract and privacy oracles; no paid requests."""
import asyncio
import json
import unittest

from agent_control import Evidence, run
from examples.agent_control.provider_fixture import FixtureProvider
from model_budget import Budget
from openrouter_planner import ProviderPlanner

SID = "case:" + "a" * 64
REQUIRED = sorted([SID, "assessment:test", "document:test", "specification:test", "gap:test"])


class Tools:
    async def call(self, name, args):
        refs = (SID, "gap:test") if name == "get_case_gaps" else (
            SID, "assessment:test", "document:test", "specification:test")
        return Evidence(name, SID, "synthetic_fixture", "matches_requirement", refs)


class Steps:
    kind = "scripted"

    def __init__(self, refs):
        self.steps = iter([
            {"action": "tool", "name": "get_case_gaps", "arguments": {"snapshot_id": SID}},
            {"action": "tool", "name": "get_assessment_evidence", "arguments": {"snapshot_id": SID}},
            {"action": "finish", "citations": refs}])

    async def next(self, *args):
        return next(self.steps)


class CitationContractTests(unittest.TestCase):
    def execute(self, refs):
        return asyncio.run(run("Explain missing evidence", SID, Steps(refs), Tools()))

    def test_missing_references_have_numeric_diagnostic_not_a_repaired_answer(self):
        result = self.execute([SID])
        self.assertEqual(result["reason"], "invalid_citations")
        self.assertIsNone(result["answer"])
        self.assertEqual(result.get("citation_validation"), {
            "shape_valid": True, "provided_count": 1, "required_count": 5, "allowed_count": 5,
            "missing_required_count": 4, "unknown_count": 0, "duplicate_count": 0})

    def test_duplicate_and_unknown_counts_never_include_untrusted_reference_text(self):
        private_text = "privacy-sentinel-not-for-output"
        result = self.execute(REQUIRED + [SID, private_text])
        self.assertEqual(result["reason"], "invalid_citations")
        self.assertIsNone(result["answer"])
        self.assertEqual(result.get("citation_validation"), {
            "shape_valid": True, "provided_count": 7, "required_count": 5, "allowed_count": 5,
            "missing_required_count": 0, "unknown_count": 1, "duplicate_count": 1})
        self.assertNotIn(private_text, json.dumps(result))
        self.assertNotIn(SID, json.dumps(result.get("citation_validation")))

    def test_non_string_and_oversized_shape_are_redacted_and_still_rejected(self):
        for refs in ([{"secret": "privacy-sentinel"}], "privacy-sentinel", [SID] * 65):
            result = self.execute(refs)
            self.assertEqual(result["reason"], "invalid_citations")
            self.assertIsNone(result["answer"])
            diagnostic = result.get("citation_validation", {})
            self.assertIs(diagnostic.get("shape_valid"), False)
            self.assertIsNone(diagnostic.get("missing_required_count"))
            self.assertNotIn("privacy-sentinel", json.dumps(result))

    def test_valid_reference_answer_still_uses_evidence_not_model_conclusion(self):
        result = self.execute(REQUIRED)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"]["result"], "matches_requirement")
        self.assertEqual(result["answer"]["overall"], "needs_engineering_review")
        self.assertIs(result["engineering_approval"], False)

    def test_finish_payload_defines_exact_required_and_allowed_reference_contract(self):
        provider = FixtureProvider()
        planner = ProviderPlanner(provider, Budget("0.02"))
        result = asyncio.run(run("Explain missing evidence", SID, planner, Tools()))
        self.assertEqual(result["status"], "completed")
        request = provider.requests[1]
        context = json.loads(request["messages"][1]["content"])
        self.assertEqual(context.get("required_tools"), ["get_assessment_evidence", "get_case_gaps"])
        self.assertEqual(context.get("available_tools"), ["get_assessment_evidence", "get_candidate_specs", "get_case_gaps"])
        schema = request["tools"][0]["function"]["parameters"]["properties"]["evidence_tools"]
        self.assertEqual(schema["items"].get("enum"), context["available_tools"])
        self.assertNotIn("document:test", request["messages"][1]["content"])
        self.assertIn("exactly once", request["messages"][0]["content"])
        self.assertEqual(len(provider.requests), 2)

    def test_guidance_does_not_accept_a_provider_that_omits_required_references(self):
        class Incomplete(FixtureProvider):
            async def complete(inner, request):
                response = await super().complete(request)
                if len(inner.requests) == 2:
                    response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = json.dumps({"evidence_tools": ["get_case_gaps"]})
                return response
        provider = Incomplete()
        result = asyncio.run(run("Explain", SID, ProviderPlanner(provider, Budget("0.02")), Tools()))
        self.assertEqual(result["reason"], "invalid_citations")
        self.assertIsNone(result["answer"])
        self.assertEqual(len(provider.requests), 2)
