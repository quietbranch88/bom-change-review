"""Validate existing MCP envelopes and map to the controller's typed read contract."""

import json

from agent_control import Evidence, StopRun
from evidence_queries import NOTICE

STATUSES = {"unknown", "matches_requirement", "violates_requirement", "not_recorded"}
ORIGINS = {"synthetic_fixture", "public_case_ai_adaptation"}


def text(value):
    if not isinstance(value, str) or not value or len(value) > 4000:
        raise ValueError("invalid_text")
    return value


def decode(name, snapshot_id, value):
    """Mapping only; does not recompute or authenticate engineering evidence."""
    try:
        if len(json.dumps(value, allow_nan=False).encode()) > 256 * 1024:
            raise ValueError("oversize")
        if (set(value) != {"schema_version", "status", "data", "error", "engineering_approval", "notice"}
                or value["schema_version"] != "evidence-tools-v1"
                or value["engineering_approval"] is not False or value["notice"] != NOTICE):
            raise ValueError("envelope")
        if value["status"] in ("error", "not_found"):
            raise StopRun("evidence_unavailable")
        if value["status"] != "historical_snapshot" or value["error"] is not None:
            raise ValueError("status")
        data = value["data"]
        snapshot = data["snapshot"]
        if (snapshot["id"] != snapshot_id or snapshot["origin"] not in ORIGINS
                or snapshot["overall"] != "needs_engineering_review"):
            raise ValueError("snapshot")
        refs = [snapshot_id]
        status, codes, actions = "not_recorded", [], []
        if name == "get_assessment_evidence":
            decision = data["decision"]
            if decision is None:
                if data["assessment_record_status"] != "not_recorded":
                    raise ValueError("missing_assessment")
            else:
                assessed = decision["assessment"]
                status = assessed["status"]
                if (data["assessment_record_status"] != "recorded" or status not in STATUSES - {"not_recorded"}
                        or assessed["scope"] != "bias_recommended_voltage_range_only"
                        or assessed["overall"] != "needs_engineering_review"
                        or assessed["automatic_transition_allowed"] is not False
                        or assessed["origin"] != snapshot["origin"]
                        or assessed["requirements_sha256"] != snapshot["requirements_sha256"]
                        or assessed["assessment_sha256"] != snapshot["assessment_sha256"]):
                    raise ValueError("assessment")
                refs.append(text(assessed["id"]))
                spec, document = decision["selected_specification"], decision["selected_document"]
                if status == "unknown":
                    if spec is not None or document is not None or assessed.get("selected_fact_id") is not None:
                        raise ValueError("unknown_selected_fact")
                else:
                    if (spec["fact_id"] != assessed["selected_fact_id"]
                            or spec["facts_sha256"] != assessed["facts_sha256"]
                            or spec["specification_class"] != "recommended_operating"
                            or spec["review_status"] != "pending"):
                        raise ValueError("selected_fact")
                    refs.extend((text(spec["id"]), text(document["id"])))
        elif name == "get_case_gaps":
            status = data["assessment_status"]
            if status not in STATUSES or data["zero_gaps_means_pass"] is not False or not isinstance(data["gaps"], list):
                raise ValueError("gaps")
            for gap in data["gaps"]:
                refs.append(text(gap["id"]))
                codes.append(text(gap["code"]))
                actions.append(text(gap["suggested_action"]) + "；" + text(gap["completion_criterion"]))
        elif name == "get_candidate_specs":
            if not isinstance(data["specifications"], list) or not data["specifications"]:
                raise ValueError("specs")
            for item in data["specifications"]:
                if item["specification"]["review_status"] != "pending":
                    raise ValueError("fact_review")
                refs.extend((text(item["specification"]["id"]), text(item["document"]["id"])))
        else:
            raise ValueError("tool")
        return Evidence(name, snapshot_id, snapshot["origin"], status, tuple(dict.fromkeys(refs)), tuple(codes), tuple(actions))
    except StopRun:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        raise StopRun("invalid_tool_result") from None


class MCPReadTools:
    def __init__(self, client):
        self.client = client

    async def call(self, name, arguments):
        try:
            result = await self.client.call_tool(name, arguments, read_timeout_seconds=65)
            if result.is_error:
                raise StopRun("evidence_unavailable")
            return decode(name, arguments["snapshot_id"], result.structured_content)
        except StopRun:
            raise
        except Exception:
            raise StopRun("tool_transport_failed") from None
