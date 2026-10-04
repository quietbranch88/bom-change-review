"""No-spend controller simulation. No provider SDK, database IO or model prose."""

import asyncio
from dataclasses import dataclass
import re
from typing import Protocol

from evidence_queries import ID_PATTERN, TOOLS


@dataclass(frozen=True)
class Evidence:
    tool: str
    snapshot_id: str
    origin: str
    assessment_status: str
    citations: tuple[str, ...]
    gap_codes: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()


class Planner(Protocol):
    kind: str

    async def next(self, question: str, snapshot_id: str, evidence: tuple[Evidence, ...]) -> dict: ...


class ReadTools(Protocol):
    async def call(self, name: str, arguments: dict) -> Evidence: ...


class StopRun(Exception):
    """Fixed controller/adapter reason, no raw input."""


SAFE_REASONS = {"invalid_planner_response", "tool_not_allowed", "invalid_tool_arguments", "tool_limit",
                "tool_result_mismatch", "inconsistent_evidence", "insufficient_evidence", "invalid_citations",
                "planner_limit", "evidence_unavailable", "invalid_tool_result", "tool_transport_failed"}


async def run(question, snapshot_id, planner: Planner, tools: ReadTools, *, timeout_seconds=90):
    counts = {"planner_calls": 0, "tool_calls": 0}
    trace = []
    seen = {}

    def result(status, reason=None, answer=None):
        return {"mode": "scripted_planner_simulation", "status": status, "reason": reason,
                "answer": answer, "counts": dict(counts), "trace": trace,
                "paid_model_calls": 0, "engineering_approval": False}

    if (planner.kind != "scripted" or not isinstance(question, str) or not question.strip()
            or len(question) > 4000 or not isinstance(snapshot_id, str)
            or re.fullmatch(ID_PATTERN, snapshot_id) is None):
        return result("stopped", "invalid_run_input_or_mode")
    if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 90):
        return result("stopped", "invalid_deadline")
    try:
        async with asyncio.timeout(timeout_seconds):
            while counts["planner_calls"] < 4:
                counts["planner_calls"] += 1
                message = await planner.next(question, snapshot_id, tuple(seen.values()))
                if not isinstance(message, dict):
                    raise StopRun("invalid_planner_response")
                if message.get("action") == "tool":
                    if set(message) != {"action", "name", "arguments"}:
                        raise StopRun("invalid_planner_response")
                    name, arguments = message["name"], message["arguments"]
                    if not isinstance(name, str) or name not in TOOLS:
                        raise StopRun("tool_not_allowed")
                    if (not isinstance(arguments, dict) or set(arguments) != {"snapshot_id"}
                            or arguments["snapshot_id"] != snapshot_id):
                        raise StopRun("invalid_tool_arguments")
                    if counts["tool_calls"] >= 3:
                        raise StopRun("tool_limit")
                    counts["tool_calls"] += 1
                    trace.append({"event": "tool_attempt", "name": name})
                    evidence = await tools.call(name, arguments)
                    if (not isinstance(evidence, Evidence) or evidence.tool != name
                            or evidence.snapshot_id != snapshot_id):
                        raise StopRun("tool_result_mismatch")
                    if seen and any(e.origin != evidence.origin for e in seen.values()):
                        raise StopRun("inconsistent_evidence")
                    seen[name] = evidence
                elif message.get("action") == "finish":
                    if set(message) != {"action", "citations"}:
                        raise StopRun("invalid_planner_response")
                    if not {"get_assessment_evidence", "get_case_gaps"}.issubset(seen):
                        raise StopRun("insufficient_evidence")
                    decision, gaps = seen["get_assessment_evidence"], seen["get_case_gaps"]
                    if decision.assessment_status != gaps.assessment_status:
                        raise StopRun("inconsistent_evidence")
                    refs = message["citations"]
                    allowed = {ref for item in seen.values() for ref in item.citations}
                    required = set(decision.citations) | set(gaps.citations)
                    if (not isinstance(refs, list) or not refs or len(refs) > 64
                            or any(not isinstance(ref, str) for ref in refs)
                            or len(refs) != len(set(refs)) or not set(refs).issubset(allowed)
                            or not required.issubset(refs)):
                        raise StopRun("invalid_citations")
                    answer = {"snapshot_id": snapshot_id, "origin": decision.origin,
                              "result": decision.assessment_status,
                              "scope": "bias_recommended_voltage_range_only",
                              "gap_codes": list(gaps.gap_codes), "next_actions": list(gaps.next_actions),
                              "citations": refs, "overall": "needs_engineering_review",
                              "historical_only": True, "source_facts_review": "pending",
                              "automatic_transition_allowed": False}
                    trace.append({"event": "finished"})
                    return result("completed", answer=answer)
                else:
                    raise StopRun("invalid_planner_response")
            raise StopRun("planner_limit")
    except TimeoutError:
        return result("stopped", "deadline_exceeded")
    except StopRun as error:
        return result("stopped", str(error) if str(error) in SAFE_REASONS else "execution_failed")
    except Exception:
        return result("stopped", "execution_failed")
