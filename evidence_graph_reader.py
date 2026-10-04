"""Read adapter for the existing graph projection, not a generic Cypher tool."""

import neo4j_graph as graph
from evidence_queries import EvidenceUnavailable, SnapshotEvidence


class _FixedReadClient(graph.Client):
    def query(self, statement, parameters=None):
        if statement not in (graph.GAPS, graph.SPECS, graph.DECISION):
            raise graph.GraphError("read_query_not_allowed")
        return super().query(statement, parameters)


class Neo4jSnapshotReader:
    def __init__(self, password, port=18747):
        self._client = _FixedReadClient(password, port)

    def read_snapshot(self, snapshot_id):
        try:
            result = self._client.show(snapshot_id)
            if result["status"] == "not_found":
                return None
            return SnapshotEvidence(snapshot=result["snapshot"], specifications=result["specifications"],
                                    gaps=result["gaps"], decision=result.get("decision"))
        except (graph.GraphError, KeyError, TypeError, ValueError):
            raise EvidenceUnavailable() from None
