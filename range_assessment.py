"""Read-only, condition-bound BIAS voltage comparison; never whole-circuit approval."""

import argparse
from decimal import Decimal
import json
from pathlib import Path

import local_review as review
import supplemental_intake as intake


RULE_VERSION = "bias-recommended-range-v1"


def number(value):
    if type(value) not in (int, float):
        raise ValueError("numeric_value_required")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("finite_value_required")
    return result


def interval(value, unit):
    intake.exact_keys(value, ("min", "max", "unit"))
    low, high = number(value["min"]), number(value["max"])
    if value["unit"] != unit or low > high:
        raise ValueError("invalid_interval")
    return low, high


def covers(outer, inner):
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def bindings(requirements, facts, context):
    return {"requirements": review.fingerprint(requirements), "facts": review.fingerprint(facts),
            "context": None if context is None else review.fingerprint(context)}


def validate_facts(data):
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("unsupported_facts_version")
    review.require_text(data["candidate"]["model"])
    source = data["source"]
    for name in ("source_id", "url", "document_id", "revision", "pdf_sha256"):
        review.require_text(source[name])
    if not isinstance(source["applicable_models_in_header"], list):
        raise ValueError("model_list_required")
    if not isinstance(data["facts"], list):
        raise ValueError("fact_list_required")
    ids = set()
    for fact in data["facts"]:
        review.require_text(fact["fact_id"])
        if fact["fact_id"] in ids:
            raise ValueError("duplicate_fact_id")
        ids.add(fact["fact_id"])
        for name in ("property", "specification_class"):
            review.require_text(fact[name])
        interval(fact["range"], "V")
        locator = fact["locator"]
        if type(locator["page"]) is not int or locator["page"] < 1:
            raise ValueError("page_required")
        for name in ("section", "row", "footnote"):
            review.require_text(locator[name])
        if fact["specification_class"] == "recommended_operating" and fact["property"] == "bias_pin_voltage":
            intake.exact_keys(fact["conditions"], ("vcc_supply", "junction_temperature"))
            review.require_text(fact["conditions"]["vcc_supply"])
            interval(fact["conditions"]["junction_temperature"], "degC")


def evaluate(requirements, facts, context=None):
    req = review.report(requirements)
    validate_facts(facts)
    bound = bindings(requirements, facts, context)
    result = {"rule_version": RULE_VERSION, "bindings": bound,
              "status": "unknown", "reason_codes": [], "selected_fact_id": None,
              "required_range": None, "fact_range": None, "evidence": None,
              "candidate_model": facts["candidate"]["model"],
              "identity_scope": "product_model_not_orderable_mpn",
              "assessment_scope": "bias_recommended_voltage_range_only",
              "context_origin": None, "reviewer_identity": "self_declared_not_authenticated",
              "overall": "needs_engineering_review", "automatic_transition_allowed": False,
              "not_evaluated": ["output_capability", "qualification", "pin_compatibility", "layout", "hardware_validation"],
              "notice": "Only this voltage range is compared. Receipts are declarations, not authenticated evidence or signatures."}
    reasons = result["reason_codes"]
    if req["source_stale"]:
        reasons.append("requirements_source_stale")
    values = []
    for name in ("input_min", "input_max"):
        field = req["fields"].get(name)
        if field is None or field["status"] not in ("accepted", "corrected") or field["item"]["value"] is None:
            reasons.append("reviewed_" + name + "_required")
            values.append(None)
        else:
            if field["item"]["unit"] != "V":
                raise ValueError("requirements_voltage_unit_required")
            number(field["item"]["value"])
            values.append(field["item"]["value"])
    if None not in values:
        result["required_range"] = dict(zip(("min", "max", "unit"), (*values, "V")))
        interval(result["required_range"], "V")
    if facts["candidate"]["model"] not in facts["source"]["applicable_models_in_header"]:
        reasons.append("candidate_not_in_source_scope")
    if context is None:
        reasons.extend(("fact_review_missing", "application_conditions_missing"))
        return result
    intake.exact_keys(context, ("schema_version", "origin", "requirements_sha256", "facts_sha256",
                                "candidate_model", "fact_review", "application"))
    if (type(context["schema_version"]) is not int or context["schema_version"] != 1
            or context["origin"] not in ("synthetic_fixture", "user_declared")):
        raise ValueError("invalid_context_metadata")
    result["context_origin"] = context["origin"]
    if context["requirements_sha256"] != bound["requirements"] or context["facts_sha256"] != bound["facts"]:
        reasons.append("context_binding_stale")
    if context["candidate_model"] != facts["candidate"]["model"]:
        reasons.append("exact_candidate_model_mismatch")
    receipt = context["fact_review"]
    if receipt is None:
        reasons.append("fact_review_missing")
    else:
        intake.exact_keys(receipt, ("status", "reviewer", "reference"))
        if receipt["status"] != "accepted":
            reasons.append("facts_not_accepted")
        for name in ("reviewer", "reference"):
            review.require_text(receipt[name])
    application = context["application"]
    if application is None:
        reasons.append("application_conditions_missing")
        return result
    intake.exact_keys(application, ("input_maps_to_bias", "vcc_supply", "junction_temperature", "reviewer", "reference"))
    for name in ("reviewer", "reference"):
        review.require_text(application[name])
    mapping = application["input_maps_to_bias"]
    if mapping is not None and type(mapping) is not bool:
        raise ValueError("mapping_boolean_or_unknown_required")
    if mapping is not True:
        reasons.append("direct_input_to_bias_mapping_not_established")
    if application["vcc_supply"] is None:
        reasons.append("vcc_supply_missing")
    else:
        review.require_text(application["vcc_supply"])
    temperature = application["junction_temperature"]
    if temperature is None:
        reasons.append("junction_temperature_missing")
    else:
        interval(temperature, "degC")
    if reasons:
        return result
    selected = [f for f in facts["facts"]
                if f["specification_class"] == "recommended_operating" and f["property"] == "bias_pin_voltage"
                and f["conditions"]["vcc_supply"] == application["vcc_supply"]]
    if len(selected) != 1:
        reasons.append("ambiguous_recommended_facts" if selected else "no_applicable_recommended_fact")
        return result
    fact = selected[0]
    if not covers(interval(fact["conditions"]["junction_temperature"], "degC"), interval(temperature, "degC")):
        reasons.append("junction_temperature_outside_fact_applicability")
        return result
    result.update(selected_fact_id=fact["fact_id"], fact_range=fact["range"],
                  evidence={"source": facts["source"], "locator": fact["locator"], "conditions": fact["conditions"]})
    result["status"] = ("matches_requirement" if covers(interval(fact["range"], "V"), interval(result["required_range"], "V"))
                        else "violates_requirement")
    reasons.append("inclusive_range_covered" if result["status"] == "matches_requirement" else "required_range_not_covered")
    return result


def show(saved, requirements, facts, context=None):
    current = evaluate(requirements, facts, context)
    if saved.get("rule_version") != RULE_VERSION or saved.get("bindings") != current["bindings"]:
        return {"report_currency": "stale", "status": "unknown", "reason_codes": ["saved_inputs_or_rule_changed"],
                "current_bindings": current["bindings"], "automatic_transition_allowed": False}
    # Same file bindings can still become stale when the underlying base source changes.
    if current != saved:
        if "requirements_source_stale" in current["reason_codes"]:
            return {**current, "report_currency": "stale"}
        raise ValueError("saved_assessment_does_not_recompute")
    return {**current, "report_currency": "current"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("assess", "show"))
    parser.add_argument("--requirements", type=Path, required=True)
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--assessment", type=Path)
    args = parser.parse_args()
    try:
        if (args.command == "show" and (args.assessment is None or args.out is not None)
                or args.command == "assess" and args.assessment is not None):
            raise ValueError("invalid_command_arguments")
        req, facts = intake.read_json(args.requirements), intake.read_json(args.facts)
        context = None if args.context is None else intake.read_json(args.context)
        if args.command == "show":
            result = show(intake.read_json(args.assessment), req, facts, context)
        else:
            result = evaluate(req, facts, context)
            if args.out is not None:
                if len((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")) > intake.MAX_JSON_BYTES:
                    raise ValueError("assessment_too_large")
                review.write_new(args.out, result)
    except FileExistsError:
        print(json.dumps({"error": "destination_exists_choose_new_snapshot"}))
        return 2
    except OSError:
        print(json.dumps({"error": "local_io_error_no_overwrite_attempted"}))
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_assessment_input_no_result_written"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
