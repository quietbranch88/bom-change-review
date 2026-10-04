"""Canned planning fixture, not LLM reasoning, training or a model evaluation."""


class ScriptedPlanner:
    kind = "scripted"

    def __init__(self, scenario="success"):
        self.index = 0
        self.scenario = scenario

    async def next(self, question, snapshot_id, evidence):
        index = self.index
        self.index += 1
        names = ("get_candidate_specs", "get_case_gaps", "get_assessment_evidence")
        if index < 3 or self.scenario == "excess_tools":
            name = "approve" if self.scenario == "invalid_tool" else names[index % 3]
            return {"action": "tool", "name": name, "arguments": {"snapshot_id": snapshot_id}}
        refs = list(dict.fromkeys(ref for item in evidence for ref in item.citations))
        if self.scenario == "fake_citation":
            refs.append("document:invented")
        return {"action": "finish", "citations": refs}
