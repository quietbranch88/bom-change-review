"""Canned OpenRouter-shaped responses, synthetic money, zero provider requests."""

import json


class FixtureProvider:
    kind = "local_fixture"

    def __init__(self, scenario="success"):
        self.scenario = scenario
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        function = request["tool_choice"]["function"]["name"]
        if self.scenario == "transport_error":
            raise RuntimeError("private provider error")
        if function == "plan_evidence":
            arguments = {"tool_names": ["get_candidate_specs", "get_case_gaps", "get_assessment_evidence"]}
            if self.scenario == "invalid_tool": arguments["tool_names"][0] = "approve"
        else:
            evidence = json.loads(request["messages"][-1]["content"])["evidence"]
            names = [item["tool"] for item in evidence]
            if self.scenario == "fake_citation": names.append("document:invented")
            arguments = {"evidence_tools": names}
        response = {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "fixture-call", "type": "function", "function": {
                "name": function, "arguments": json.dumps(arguments)}}]}}], "usage": {"cost": 0.001}}
        if self.scenario == "unknown_cost": response["usage"] = {}
        elif self.scenario == "overrun": response["usage"]["cost"] = 0.03
        return response
