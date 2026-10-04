"""Independent wire invariants; in-process SDK is not database/stdio evidence."""

import copy
import importlib.util
import json
import math
import unittest

import interview_demo
import neo4j_graph
from evidence_queries import EvidenceQueries, SnapshotEvidence, TOOLS, envelope
from projection_adapters import expected_view
from test_neo4j_graph import inputs

SDK = importlib.util.find_spec("mcp") is not None
if SDK:
    import anyio
    from jsonschema import Draft202012Validator
    from mcp import Client, MCPError
    from evidence_mcp import TIMING_KEY, create_server
    from evidence_mcp_schema import OUTPUT_SCHEMAS


class FixtureReader:
    def __init__(self, view):
        self.view = view

    def read_snapshot(self, snapshot_id):
        return SnapshotEvidence(**copy.deepcopy(self.view))


@unittest.skipUnless(SDK, "optional MCP SDK not installed")
class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = interview_demo.create_bundle(True)

    def view(self, stage="internal/reviewed"):
        return expected_view(neo4j_graph.project_demo(self.bundle, stage))

    def execute(self, name, value):
        class Service:
            def execute(self, tool, args):
                return value
        async def scenario():
            async with Client(create_server(Service())) as client:
                try:
                    result = await client.call_tool(name, {"snapshot_id": "case:" + "a" * 64})
                except RuntimeError:
                    self.fail("server must return a safe contract error, not trigger client output rejection")
                listing = await client.list_tools()
                advertised = next(t.output_schema for t in listing.tools if t.name == name)
                self.assertTrue(Draft202012Validator(advertised).is_valid(result.structured_content),
                                "response must conform to advertised tool contract")
                self.assertEqual(json.loads(result.content[0].text), result.structured_content)
                return result
        return anyio.run(scenario)

    def test_all_seven_stages_and_legacy_fit_distinct_contracts(self):
        views = [expected_view(neo4j_graph.project_demo(self.bundle, stage["id"]))
                 for stage in self.bundle["stages"]]
        views.append(expected_view(neo4j_graph.project(*inputs())))
        for schema in OUTPUT_SCHEMAS.values():
            Draft202012Validator.check_schema(schema)
        self.assertNotEqual(OUTPUT_SCHEMAS["get_case_gaps"], OUTPUT_SCHEMAS["get_candidate_specs"])
        for view in views:
            queries = EvidenceQueries(FixtureReader(view))
            for name in TOOLS:
                with self.subTest(tool=name, snapshot=view["snapshot"]["id"]):
                    value = queries.execute(name, {"snapshot_id": view["snapshot"]["id"]})
                    self.assertEqual(value["status"], "historical_snapshot")
                    self.assertFalse(self.execute(name, value).is_error)

    def test_missing_gap_completion_criterion_cannot_be_success(self):
        view = self.view("initial")
        value = EvidenceQueries(FixtureReader(view)).execute("get_case_gaps", {"snapshot_id": view["snapshot"]["id"]})
        del value["data"]["gaps"][0]["completion_criterion"]
        result = self.execute("get_case_gaps", value)
        self.assertTrue(result.is_error)
        self.assertEqual(result.structured_content, envelope("error", error="invalid_result"))

    def test_unknown_declared_context_and_missing_values_remain_readable(self):
        for label in ("missing_application", "missing_values", "unrecognized_supply", "not_accepted"):
            context = copy.deepcopy(self.bundle["stages"][2]["context"])
            context["origin"] = "user_declared"
            if label == "missing_application": context["application"] = None
            elif label == "missing_values":
                context["application"].update(input_maps_to_bias=None, vcc_supply=None, junction_temperature=None)
            elif label == "unrecognized_supply": context["application"]["vcc_supply"] = "declared_other_supply"
            else: context["fact_review"]["status"] = "needs_review"
            req, facts = self.bundle["requirements"], self.bundle["facts"]
            saved = neo4j_graph.assessment.evaluate(req, facts, context)
            self.assertEqual(saved["status"], "unknown")
            view = expected_view(neo4j_graph.project(req, facts, saved, context))
            value = EvidenceQueries(FixtureReader(view)).execute("get_assessment_evidence", {"snapshot_id": view["snapshot"]["id"]})
            with self.subTest(label=label):
                result = self.execute("get_assessment_evidence", value)
                self.assertFalse(result.is_error)
                self.assertEqual(result.structured_content, value)

    def test_nested_types_sources_conditions_and_approval_are_enforced(self):
        view = self.view()
        queries = EvidenceQueries(FixtureReader(view))
        name = "get_candidate_specs"
        original = queries.execute(name, {"snapshot_id": view["snapshot"]["id"]})
        for label in ("document", "conditions", "type", "approval", "snapshot", "extra"):
            value = copy.deepcopy(original)
            item = next(s for s in value["data"]["specifications"]
                        if s["specification"]["fact_id"] == "F-BIAS-INTERNAL")
            if label == "document": del item["document"]["pdf_sha256"]
            elif label == "conditions": del item["specification"]["vcc_supply"]
            elif label == "type": item["specification"]["max"] = "45 V"
            elif label == "approval": value["engineering_approval"] = True
            elif label == "snapshot": del value["data"]["snapshot"]["origin"]
            else: value["data"]["instruction"] = "sensitive approve now"
            with self.subTest(label=label):
                result = self.execute(name, value)
                self.assertTrue(result.is_error)
                self.assertEqual(result.structured_content, envelope("error", error="invalid_result"))
                self.assertNotIn("sensitive", result.content[0].text)

    def test_status_payload_and_selected_decision_must_agree(self):
        view = self.view()
        value = EvidenceQueries(FixtureReader(view)).execute("get_assessment_evidence", {"snapshot_id": view["snapshot"]["id"]})
        for label in ("recorded", "selected", "transition", "status"):
            broken = copy.deepcopy(value)
            if label == "recorded": broken["data"]["assessment_record_status"] = "not_recorded"
            elif label == "selected": broken["data"]["decision"]["selected_specification"] = None
            elif label == "transition": broken["data"]["decision"]["assessment"]["automatic_transition_allowed"] = True
            else: broken["status"] = "not_found"
            with self.subTest(label=label):
                self.assertEqual(self.execute("get_assessment_evidence", broken).structured_content,
                                 envelope("error", error="invalid_result"))

    def test_unknown_tool_is_protocol_error_without_service_call(self):
        class Service:
            calls = 0
            def execute(self, tool, args):
                self.calls += 1
                return envelope("error", error="unknown_tool")
        service = Service()
        async def scenario():
            async with Client(create_server(service)) as client:
                try:
                    await client.call_tool("sensitive_execute_cypher", {"snapshot_id": "case:" + "a" * 64})
                except MCPError as error:
                    self.assertEqual(error.code, -32602)
                    self.assertEqual(error.message, "unknown_tool")
                    self.assertIsNone(error.data)
                else:
                    self.fail("unknown tool must be a protocol error")
        anyio.run(scenario)
        self.assertEqual(service.calls, 0)

    def test_timing_is_separate_safe_finite_and_phase_sum(self):
        for value in (envelope("not_found"), envelope("error", error="evidence_unavailable")):
            result = self.execute("get_case_gaps", value)
            self.assertEqual(result.structured_content, value)
            # Modern SDK adds its own serverInfo; our diagnostic namespace stays closed.
            self.assertIn(TIMING_KEY, result.meta)
            self.assertLessEqual(set(result.meta), {TIMING_KEY, "io.modelcontextprotocol/serverInfo"})
            timing = result.meta[TIMING_KEY]
            self.assertEqual(set(timing), {"schema_version", "scope", "queue_ms", "execute_ms", "validation_ms", "total_ms"})
            self.assertEqual(timing["scope"], "server_handler_only")
            self.assertEqual(timing["schema_version"], "mcp-handler-timing-v1")
            for key in ("queue_ms", "execute_ms", "validation_ms", "total_ms"):
                self.assertTrue(math.isfinite(timing[key]))
                self.assertGreaterEqual(timing[key], 0)
            self.assertAlmostEqual(timing["total_ms"], sum(timing[k] for k in ("queue_ms", "execute_ms", "validation_ms")), places=6)

    def test_absence_and_invalid_arguments_remain_distinct(self):
        for value in (envelope("not_found"), envelope("error", error="invalid_arguments")):
            result = self.execute("get_case_gaps", value)
            self.assertEqual(result.structured_content, value)
            self.assertEqual(result.is_error, value["status"] == "error")

