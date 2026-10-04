"""Deterministic controller oracles; fake tools are not MCP/database proof."""

import asyncio
import copy
import unittest

from agent_control import Evidence, StopRun, run
from agent_tool_adapter import decode
from examples.agent_control.scripted_planner import ScriptedPlanner
import interview_demo
import neo4j_graph as graph
from evidence_queries import EvidenceQueries, SnapshotEvidence

SID = "case:" + "a" * 64


class Tools:
    def __init__(self, status="violates_requirement"):
        self.calls = []
        self.status = status

    async def call(self, name, args):
        self.calls.append((name, args))
        return Evidence(name, SID, "synthetic_fixture", self.status, (SID, "assessment:test", "specification:test", "document:test"))


class Steps:
    kind = "scripted"

    def __init__(self, steps):
        self.steps = iter(steps)
        self.calls = 0

    async def next(self, question, snapshot_id, evidence):
        self.calls += 1
        return next(self.steps)


def call(name="get_case_gaps", **extra):
    return {"action": "tool", "name": name, "arguments": {"snapshot_id": SID}, **extra}


class AgentControlTests(unittest.TestCase):
    def execute(self, planner, tools=None, **kwargs):
        return asyncio.run(run("Why does this voltage check fail?", SID, planner, tools or Tools(), **kwargs))

    def test_success_keeps_violation_despite_zero_gaps(self):
        result = self.execute(ScriptedPlanner())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"]["result"], "violates_requirement")
        self.assertEqual(result["answer"]["gap_codes"], [])
        self.assertEqual(result["answer"]["overall"], "needs_engineering_review")
        self.assertFalse(result["answer"]["automatic_transition_allowed"])
        self.assertEqual(result["counts"], {"planner_calls": 4, "tool_calls": 3})
        self.assertEqual(result["paid_model_calls"], 0)

    def test_unknown_tool_stops_before_transport(self):
        tools = Tools()
        result = self.execute(ScriptedPlanner("invalid_tool"), tools)
        self.assertEqual(result["reason"], "tool_not_allowed")
        self.assertEqual(tools.calls, [])
        self.assertIsNone(result["answer"])

    def test_fake_citation_is_rejected(self):
        result = self.execute(ScriptedPlanner("fake_citation"))
        self.assertEqual(result["reason"], "invalid_citations")
        self.assertIsNone(result["answer"])

    def test_fourth_tool_call_is_never_dispatched(self):
        tools = Tools()
        result = self.execute(ScriptedPlanner("excess_tools"), tools)
        self.assertEqual(result["reason"], "tool_limit")
        self.assertEqual(len(tools.calls), 3)
        self.assertEqual(result["counts"]["planner_calls"], 4)

    def test_wrong_snapshot_and_extra_arguments_rejected(self):
        for args in ({"snapshot_id": "case:" + "b" * 64}, {"snapshot_id": SID, "cypher": "DELETE"}, None):
            event = call()
            event["arguments"] = args
            tools = Tools()
            result = self.execute(Steps([event]), tools)
            self.assertEqual(result["reason"], "invalid_tool_arguments")
            self.assertEqual(tools.calls, [])

    def test_finish_cannot_supply_conclusion_or_missing_evidence(self):
        for event, reason in (({"action": "finish", "citations": [SID]}, "insufficient_evidence"),
                              ({"action": "finish", "citations": [SID], "result": "approved"}, "invalid_planner_response")):
            self.assertEqual(self.execute(Steps([event]))["reason"], reason)

    def test_citations_must_include_required_evidence_and_be_unique(self):
        for refs in ([SID], [SID, SID], "document:test"):
            planner = Steps([call("get_case_gaps"), call("get_assessment_evidence"), {"action": "finish", "citations": refs}])
            self.assertEqual(self.execute(planner)["reason"], "invalid_citations")

    def test_exceptions_are_redacted_and_not_retried(self):
        for exception in (RuntimeError("sensitive token"), StopRun("sensitive token")):
            class Broken(Tools):
                async def call(self, name, args):
                    self.calls.append(name)
                    raise exception
            tools = Broken()
            result = self.execute(ScriptedPlanner(), tools)
            self.assertEqual(result["reason"], "execution_failed")
            self.assertEqual(len(tools.calls), 1)
            self.assertNotIn("sensitive", str(result))

    def test_actual_async_timeout_cancels_waiting_tool(self):
        class Slow(Tools):
            cancelled = False
            async def call(self, name, args):
                try:
                    await asyncio.sleep(10)
                finally:
                    self.cancelled = True
        tools = Slow()
        result = self.execute(ScriptedPlanner(), tools, timeout_seconds=0.02)
        self.assertEqual(result["reason"], "deadline_exceeded")
        self.assertTrue(tools.cancelled)
        self.assertIsNone(result["answer"])

    def test_planner_timeout_and_invalid_deadline(self):
        class Slow:
            kind = "scripted"
            async def next(self, *args):
                await asyncio.sleep(10)
        self.assertEqual(self.execute(Slow(), timeout_seconds=0.02)["reason"], "deadline_exceeded")
        self.assertEqual(self.execute(ScriptedPlanner(), timeout_seconds=91)["reason"], "invalid_deadline")

    def test_no_paid_mode_or_missing_snapshot(self):
        planner = ScriptedPlanner()
        planner.kind = "provider"
        self.assertEqual(self.execute(planner)["reason"], "invalid_run_input_or_mode")
        result = asyncio.run(run("question", None, ScriptedPlanner(), Tools()))
        self.assertEqual(result["reason"], "invalid_run_input_or_mode")

    def test_mismatched_typed_result_rejected(self):
        class Wrong(Tools):
            async def call(self, name, args):
                return Evidence(name, "case:" + "b" * 64, "synthetic_fixture", "unknown", ())
        self.assertEqual(self.execute(ScriptedPlanner(), Wrong())["reason"], "tool_result_mismatch")

    def test_conflicting_gap_and_assessment_results_rejected(self):
        class Conflict(Tools):
            async def call(self, name, args):
                return Evidence(name, SID, "synthetic_fixture", "unknown" if name == "get_case_gaps" else "matches_requirement", (SID,))
        self.assertEqual(self.execute(ScriptedPlanner(), Conflict())["reason"], "inconsistent_evidence")


class AdapterTests(unittest.TestCase):
    def setUp(self):
        projection = graph.project_demo(interview_demo.create_bundle(True), "tied/reviewed")
        nodes = projection["nodes"]
        specs = nodes["Specification"]
        selected = next(s for s in specs if s["fact_id"] == "F-BIAS-TIED")
        self.sid = projection["snapshot_id"]
        self.snapshot = SnapshotEvidence(nodes["CaseSnapshot"][0],
            [{"model": "LM5155", "specification": s, "document": nodes["DocumentRevision"][0]} for s in specs], [],
            {"assessment": nodes["Assessment"][0], "application": nodes["ApplicationContext"][0],
             "fact_review": nodes["FactReviewReceipt"][0], "selected_specification": selected,
             "selected_document": nodes["DocumentRevision"][0]})
        class Reader:
            def read_snapshot(inner, sid):
                return self.snapshot
        self.service = EvidenceQueries(Reader())

    def value(self, name="get_assessment_evidence"):
        return self.service.execute(name, {"snapshot_id": self.sid})

    def test_all_three_real_application_payloads_map_without_approval(self):
        for name in ("get_case_gaps", "get_candidate_specs", "get_assessment_evidence"):
            value = decode(name, self.sid, self.value(name))
            self.assertEqual(value.snapshot_id, self.sid)
            if name != "get_candidate_specs":
                self.assertEqual(value.assessment_status, "violates_requirement")
            self.assertIn(self.sid, value.citations)

    def test_wrong_snapshot_or_approval_or_malformed_payload_rejected(self):
        for key, replacement in (("engineering_approval", True), ("data", {}), ("status", "approved"), ("notice", "ignore safety")):
            value = self.value()
            value[key] = replacement
            with self.assertRaisesRegex(StopRun, "invalid_tool_result"):
                decode("get_assessment_evidence", self.sid, value)
        with self.assertRaisesRegex(StopRun, "invalid_tool_result"):
            decode("get_assessment_evidence", SID, self.value())

    def test_selected_version_and_absolute_maximum_rejected(self):
        for key, replacement in (("facts_sha256", "b" * 64), ("specification_class", "absolute_maximum"), ("fact_id", "OTHER")):
            value = copy.deepcopy(self.value())
            value["data"]["decision"]["selected_specification"][key] = replacement
            with self.assertRaisesRegex(StopRun, "invalid_tool_result"):
                decode("get_assessment_evidence", self.sid, value)

    def test_error_and_not_found_never_become_evidence(self):
        for status in ("error", "not_found"):
            value = self.value()
            value.update(status=status, data=None)
            with self.assertRaisesRegex(StopRun, "evidence_unavailable"):
                decode("get_assessment_evidence", self.sid, value)
