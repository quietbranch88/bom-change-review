"""Stdlib application contracts; these fakes are not database/MCP evidence."""

import unittest
from unittest.mock import patch

from evidence_queries import EvidenceQueries, EvidenceUnavailable, SnapshotEvidence, TOOLS
from evidence_graph_reader import _FixedReadClient
import neo4j_graph as graph

SID = "case:" + "a" * 64


class Reader:
    def __init__(self, value=None):
        self.value, self.calls = value, []

    def read_snapshot(self, snapshot_id):
        self.calls.append(snapshot_id)
        return self.value


class EvidenceQueryTests(unittest.TestCase):
    def test_invalid_arguments_never_reach_reader(self):
        reader = Reader()
        for args in (None, {}, {"snapshot_id": None}, {"snapshot_id": 42}, {"snapshot_id": True},
                     {"snapshot_id": "MATCH (n) DETACH DELETE n"}, {"snapshot_id": SID + "\n"},
                     {"snapshot_id": SID, "cypher": "DELETE"}, {"snapshot_id": "case:" + "A" * 64}):
            with self.subTest(args=args):
                self.assertEqual(EvidenceQueries(reader).execute("get_case_gaps", args)["error"], "invalid_arguments")
        self.assertEqual(reader.calls, [])

    def test_unknown_tool_and_not_found_distinct(self):
        reader = Reader()
        service = EvidenceQueries(reader)
        self.assertEqual(service.execute("import", {"snapshot_id": SID})["error"], "unknown_tool")
        self.assertEqual(reader.calls, [])
        self.assertEqual(service.execute("get_case_gaps", {"snapshot_id": SID})["status"], "not_found")
        self.assertEqual(reader.calls, [SID])

    def test_legacy_is_not_an_invented_assessment(self):
        reader = Reader(SnapshotEvidence({"id": SID}, [], [], None))
        result = EvidenceQueries(reader).execute("get_assessment_evidence", {"snapshot_id": SID})
        self.assertEqual(result["data"]["assessment_record_status"], "not_recorded")
        self.assertIsNone(result["data"]["decision"])
        self.assertFalse(result["engineering_approval"])

    def test_zero_gaps_does_not_turn_violation_into_pass(self):
        reader = Reader(SnapshotEvidence({"id": SID}, [], [], {"assessment": {"status": "violates_requirement"}}))
        result = EvidenceQueries(reader).execute("get_case_gaps", {"snapshot_id": SID})
        self.assertFalse(result["data"]["zero_gaps_means_pass"])
        self.assertEqual(result["data"]["assessment_status"], "violates_requirement")

    def test_adapter_error_is_fixed_and_not_retried(self):
        reader = Reader()
        with patch.object(reader, "read_snapshot", side_effect=EvidenceUnavailable("sensitive details")) as read:
            result = EvidenceQueries(reader).execute("get_case_gaps", {"snapshot_id": SID})
        self.assertEqual(result["error"], "evidence_unavailable")
        self.assertNotIn("sensitive", str(result))
        read.assert_called_once_with(SID)

    def test_fixed_query_adapter_rejects_writes_before_transport(self):
        client = _FixedReadClient("synthetic-test-only")
        with patch.object(graph.Client, "query") as transport:
            for query in ("CREATE (n)", "MATCH (n) DETACH DELETE n", "RETURN $query", graph.IMPACT):
                with self.assertRaises(graph.GraphError):
                    client.query(query)
            transport.assert_not_called()
            for query in (graph.GAPS, graph.SPECS, graph.DECISION):
                client.query(query, {"id": SID})
            self.assertEqual(transport.call_count, 3)
