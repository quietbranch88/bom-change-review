"""Reproducible offline interview demo. Every review/application receipt is simulated."""

import argparse
import copy
import json
from pathlib import Path

import local_review as review
import range_assessment as assessment
import supplemental_intake as intake


ROOT = Path(__file__).resolve().parent
DRAFT = ROOT / "examples/interview-demo/requirements-draft.json"
FACTS = ROOT / "examples/ti-1222180/candidate-facts-v1.json"
SCENARIOS = ROOT / "examples/interview-demo/scenarios.json"
VERSION = "interview-demo-v2"
LEGACY_VERSION = "interview-demo-v1"
REVIEWER = "SIM-reviewer-not-an-engineer"
POLICY = "simulated_not_human_approval"


def validate_scenarios(data):
    intake.exact_keys(data, ("origin", "notice", "scenarios"))
    if data["origin"] != "synthetic_fixture":
        raise ValueError("synthetic_only")
    review.require_text(data["notice"])
    rows = data["scenarios"]
    if not isinstance(rows, list) or len(rows) != 3:
        raise ValueError("three_demo_scenarios_required")
    if [row["id"] for row in rows] != ["internal", "tied", "unknown"]:
        raise ValueError("invalid_scenario_identity")
    for row in rows:
        intake.exact_keys(row, ("id", "source_id", "text", "application"))
        if row["source_id"] != "SIM-" + row["id"].upper():
            raise ValueError("synthetic_source_required")
        review.require_text(row["text"])
        app = row["application"]
        intake.exact_keys(app, ("input_maps_to_bias", "vcc_supply", "junction_temperature", "reviewer", "reference"))
        if app["reviewer"] != REVIEWER or not app["reference"].startswith(row["source_id"] + ":"):
            raise ValueError("synthetic_reference_required")


def load_inputs():
    draft, facts, scenarios = (intake.read_json(path) for path in (DRAFT, FACTS, SCENARIOS))
    validate_scenarios(scenarios)
    assessment.validate_facts(facts)
    if facts["human_fact_review"] != "pending" or facts["engineering_suitability"] != "not_evaluated":
        raise ValueError("pending_source_draft_required")
    return draft, facts, scenarios


def source_bindings(draft, facts, scenarios):
    return {name: review.fingerprint(value) for name, value in
            (("draft", draft), ("facts", facts), ("scenarios", scenarios))}


def make_stages(requirements, facts, scenarios):
    """Use the real comparator; fixtures contain inputs, never expected outcomes."""
    validate_scenarios(scenarios)
    stages = [{"id": "initial", "reply_source_id": None, "review_stage": "missing_evidence",
               "context": None, "assessment": assessment.evaluate(requirements, facts)}]
    for row in scenarios["scenarios"]:
        context = {"schema_version": 1, "origin": "synthetic_fixture",
                   "requirements_sha256": review.fingerprint(requirements),
                   "facts_sha256": review.fingerprint(facts), "candidate_model": facts["candidate"]["model"],
                   "fact_review": None, "application": copy.deepcopy(row["application"])}
        for phase in ("received", "reviewed"):
            if phase == "reviewed":
                context["fact_review"] = {"status": "accepted", "reviewer": REVIEWER,
                                          "reference": row["source_id"] + ": simulated fact review, not human approval"}
            stages.append({"id": row["id"] + "/" + phase, "reply_source_id": row["source_id"],
                           "review_stage": "pending_fact_review" if phase == "received" else "simulated_review",
                           "context": copy.deepcopy(context),
                           "assessment": assessment.evaluate(requirements, facts, context)})
    return stages


def create_bundle(simulate_review=False):
    if simulate_review is not True:
        raise ValueError("explicit_simulation_required")
    draft, facts, scenarios = load_inputs()
    requirements = review.create_review("E01", draft, "demo")
    # The graph also requires reviewed original identity; all three decisions are simulated.
    for field in ("subject_parts", "input_min", "input_max"):
        requirements = review.decide(requirements, field, "accept", REVIEWER,
                                     "Simulated source-text check for interview demo only.")
    payload = {"version": VERSION, "origin": "synthetic_fixture", "review_policy": POLICY,
               "created_at": review.now(), "rule_version": assessment.RULE_VERSION,
               "source_bindings": source_bindings(draft, facts, scenarios),
               "requirements": requirements, "facts": facts, "scenarios": scenarios,
               "stages": make_stages(requirements, facts, scenarios)}
    return {**payload, "bundle_sha256": review.fingerprint(payload)}


def inspect_bundle(bundle):
    intake.exact_keys(bundle, ("version", "origin", "review_policy", "created_at", "rule_version",
                               "source_bindings", "requirements", "facts", "scenarios", "stages", "bundle_sha256"))
    if (bundle["version"] not in (VERSION, LEGACY_VERSION) or bundle["origin"] != "synthetic_fixture"
            or bundle["review_policy"] != POLICY):
        raise ValueError("unsupported_bundle")
    review.timestamp(bundle["created_at"])
    payload = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    if review.fingerprint(payload) != bundle["bundle_sha256"]:
        raise ValueError("bundle_changed")
    requirements, facts, scenarios = bundle["requirements"], bundle["facts"], bundle["scenarios"]
    review.replay(requirements)
    validate_scenarios(scenarios)
    assessment.validate_facts(facts)
    reviewed_fields = ["input_min", "input_max"] if bundle["version"] == LEGACY_VERSION else ["subject_parts", "input_min", "input_max"]
    if (requirements["draft_origin_declared"] != "demo"
            or [event["field"] for event in requirements["events"]] != reviewed_fields
            or any(event["reviewer"] != REVIEWER or event["action"] != "accept" for event in requirements["events"])
            or facts["human_fact_review"] != "pending"):
        raise ValueError("simulated_receipts_required")
    if bundle["source_bindings"] != source_bindings(requirements["draft"], facts, scenarios):
        raise ValueError("inconsistent_source_bindings")
    current = load_inputs()
    stale = (bundle["source_bindings"] != source_bindings(*current)
             or bundle["rule_version"] != assessment.RULE_VERSION or review.is_stale(requirements))
    summary = {"demo_version": bundle["version"], "origin": "synthetic_fixture", "review_policy": POLICY,
               "report_currency": "stale" if stale else "current", "overall": "needs_engineering_review",
               "assessment_scope": "bias_recommended_voltage_range_only", "automatic_transition_allowed": False,
               "network_calls": 0, "notice": "Offline synthetic demonstration, not a real engineering decision."}
    if stale:
        return {**summary, "status": "unknown", "reason_codes": ["demo_inputs_or_rule_changed"], "stages": []}
    expected = make_stages(requirements, facts, scenarios)
    if bundle["stages"] != expected:
        raise ValueError("saved_stages_do_not_recompute")
    return {**summary, "stages": [{"id": item["id"], "review_stage": item["review_stage"],
                                  "status": item["assessment"]["status"],
                                  "reason_codes": item["assessment"]["reason_codes"],
                                  "selected_fact_id": item["assessment"]["selected_fact_id"]} for item in expected]}


def save_run(destination, bundle):
    # Validate completely before creating any output. An incomplete file is never a valid bundle.
    result = inspect_bundle(bundle)
    if result["report_currency"] != "current":
        raise ValueError("cannot_save_stale_bundle")
    encoded = json.dumps(bundle, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(encoded) + 1 > intake.MAX_JSON_BYTES:
        raise ValueError("bundle_too_large")
    Path(destination).mkdir(parents=True, exist_ok=False)
    review.write_new(Path(destination) / "bundle.json", bundle)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--out", type=Path, required=True, help="New directory; existing destinations are never overwritten")
    run.add_argument("--simulate-review", action="store_true", required=True,
                     help="Explicitly opt into canned simulated acceptance; never human approval")
    show = commands.add_parser("show")
    show.add_argument("--run", type=Path, required=True, help="Directory containing bundle.json")
    args = parser.parse_args()
    try:
        if args.command == "run":
            result = save_run(args.out, create_bundle(args.simulate_review))
        else:
            result = inspect_bundle(intake.read_json(args.run / "bundle.json"))
    except FileExistsError:
        print(json.dumps({"error": "destination_exists_choose_new_run"}))
        return 2
    except OSError:
        print(json.dumps({"error": "local_io_error_run_may_be_incomplete"}))
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_demo_data_no_result_trusted"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
