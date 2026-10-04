"""A source-backed teaching example, not a circuit replacement approval tool."""

import argparse
import json
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).parent / "data" / "catalog.json"
REQUIREMENTS = ("required", "forbidden", "unspecified")


def check_hiccup(actual: bool | None, requirement: str) -> str:
    """Keep 'not specified' distinct from 'must not have this feature'."""
    if requirement not in REQUIREMENTS:
        raise ValueError("hiccup must be required, forbidden, or unspecified")
    if requirement == "unspecified" or actual is None:
        return "unknown"
    expected = requirement == "required"
    return "pass" if actual == expected else "fail"


def evaluate(
    catalog: dict[str, Any],
    candidate: str,
    original: str | None = None,
    hiccup: str = "unspecified",
) -> dict[str, Any]:
    """Evaluate a product-model candidate under an unchanged-PCB scope."""
    if hiccup not in REQUIREMENTS:
        raise ValueError("hiccup must be required, forbidden, or unspecified")

    part = catalog["models"].get(candidate, {})
    feature = part.get("hiccup", {})
    actual = feature.get("value")
    checks = {}
    missing = []
    reasons = []
    evidence_ids = set()

    def add_check(name, status, reason, sources):
        checks[name] = {"status": status, "reason": reason, "source_ids": sources}
        evidence_ids.update(sources)

    if original is not None:
        # Only a documented pair establishes catalog pin-for-pin classification.
        pair = catalog["candidate_pairs"].get(f"{original}->{candidate}")
        if pair is None:
            add_check("catalog_pin_for_pin", "unknown", "no_pair_evidence", [])
            missing.append("catalog_pin_for_pin_evidence")
        elif pair["pin_for_pin"]:
            add_check("catalog_pin_for_pin", "pass", "documented_pair", pair["source_ids"])
        else:
            add_check("catalog_pin_for_pin", "fail", "different_pinout", pair["source_ids"])
            add_check("unchanged_pcb_footprint", "fail", "different_package_pin_count", pair["source_ids"])
            reasons.extend(["different_pinout", "different_package_pin_count"])

        original_feature = catalog["models"].get(original, {}).get("hiccup", {})
        old_value = original_feature.get("value")
        if old_value is not None and actual is not None and old_value != actual:
            reasons.append("hiccup_behavior_changed")
            evidence_ids.update(original_feature["source_ids"])
            evidence_ids.update(feature["source_ids"])

    feature_status = check_hiccup(actual, hiccup)
    if hiccup == "unspecified":
        add_check("design_behavior_acceptance", "unknown", "application_requirements_missing", [])
        missing.extend(["hiccup_requirement", "schematic_and_design_conditions"])
        reasons.append("application_requirements_missing")
    else:
        name = "required_integrated_hiccup" if hiccup == "required" else "forbidden_integrated_hiccup"
        if feature_status == "pass":
            reason = "feature_matches_explicit_requirement"
        elif feature_status == "fail":
            reason = "feature_violates_explicit_requirement"
        else:
            reason = "feature_evidence_missing"
        add_check(name, feature_status, reason, feature.get("source_ids", []))
        if feature_status == "unknown":
            missing.append("candidate_hiccup_evidence")
        if feature_status == "fail":
            reasons.append("required_feature_disabled" if hiccup == "required" else "forbidden_feature_enabled")

    # A local feature match cannot establish circuit-wide interchangeability.
    overall = "needs_review"
    if any(check["status"] == "fail" for check in checks.values()):
        overall = "blocked_for_requested_scope"

    return {
        "original_model": original,
        "candidate_model": candidate,
        "identity_scope": "product_model_not_orderable_mpn",
        "assessment_scope": "unchanged_pcb_and_explicit_hiccup_requirement_only",
        "requirements_origin": "user_supplied_or_synthetic_not_manufacturer_requirement",
        "hiccup_requirement": hiccup,
        "observed_candidate_hiccup": actual,
        "checks": checks,
        "overall": overall,
        "reason_codes": reasons,
        "missing_fields": missing,
        "unchecked": ["other_electrical_requirements", "layout", "hardware_validation"],
        "next_action": "review_pcb_and_pin_mapping" if "different_pinout" in reasons else "review_design_requirements",
        "evidence": [{"id": key, **catalog["sources"][key]} for key in sorted(evidence_ids)],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", help="Exact original product model (optional)")
    parser.add_argument("--candidate", default="LM51551", help="Exact candidate product model")
    parser.add_argument("--hiccup", choices=REQUIREMENTS, default="unspecified")
    args = parser.parse_args()
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    report = evaluate(catalog, args.candidate, args.original, args.hiccup)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
