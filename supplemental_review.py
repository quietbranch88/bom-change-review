"""Apply explicit text-review decisions locally; never approve engineering or call a model."""

import argparse
import copy
import json
from pathlib import Path

import local_review
import supplemental_intake as intake


IDENTITY = "self_declared_not_authenticated"


def items(draft):
    result = {"permission": draft["permission"]}
    for name in ("constraints", "open_questions"):
        result.update((f"{name}/{index}", item) for index, item in enumerate(draft[name]))
    return result


def replace_item(draft, target, value):
    if target == "permission":
        draft[target] = copy.deepcopy(value)
    else:
        name, index = target.split("/")
        draft[name][int(index)] = copy.deepcopy(value)


def validate_decisions(pending, decisions):
    intake.exact_keys(decisions, ("pending_sha256", "reviewer", "decision_reference", "items"))
    if decisions["pending_sha256"] != local_review.fingerprint(pending):
        raise ValueError("decision_snapshot_mismatch")
    for name in ("reviewer", "decision_reference"):
        local_review.require_text(decisions[name])
    expected = items(pending["draft"])
    if not isinstance(decisions["items"], list):
        raise ValueError("decision_list_required")
    seen = set()
    corrected = copy.deepcopy(pending["draft"])
    events = []
    for decision in decisions["items"]:
        if not isinstance(decision, dict):
            raise ValueError("invalid_decision")
        action = decision.get("action")
        if action not in ("accept", "correct"):
            raise ValueError("unsupported_decision")
        keys = ("target", "action", "value") if action == "correct" else ("target", "action")
        intake.exact_keys(decision, keys)
        target = decision["target"]
        if not isinstance(target, str) or target not in expected or target in seen:
            raise ValueError("invalid_or_duplicate_target")
        seen.add(target)
        before = expected[target]
        after = before if action == "accept" else decision["value"]
        if action == "correct" and after == before:
            raise ValueError("correction_must_change_item")
        replace_item(corrected, target, after)
        events.append({"target": target, "action": action,
                       "before": copy.deepcopy(before), "after": copy.deepcopy(after)})
    if seen != set(expected):
        raise ValueError("all_existing_items_require_explicit_decision")
    intake.validate_draft(corrected, pending["source"], pending["source_sha256"])
    return events, corrected


def apply(pending, decisions):
    if intake.report(pending)["state"] != "pending_review":
        raise ValueError("current_pending_required")
    events, _ = validate_decisions(pending, decisions)
    record = {
        "schema_version": 1, "synthetic": True,
        "pending": copy.deepcopy(pending), "pending_sha256": local_review.fingerprint(pending),
        "reviewer_identity": IDENTITY, "reviewer": decisions["reviewer"],
        "decision_reference": decisions["decision_reference"],
        "applied_at": local_review.now(), "events": events,
    }
    report(record, pending)
    return record


def report(record, current_pending):
    intake.exact_keys(record, ("schema_version", "synthetic", "pending", "pending_sha256",
                               "reviewer_identity", "reviewer", "decision_reference", "applied_at", "events"))
    if (type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["synthetic"] is not True or record["reviewer_identity"] != IDENTITY):
        raise ValueError("invalid_review_metadata")
    pending = record["pending"]
    old_report = intake.report(pending)
    current_report = intake.report(current_pending)
    if old_report["state"] not in ("pending_review", "stale") or pending["draft"] is None:
        raise ValueError("pending_snapshot_required")
    if local_review.fingerprint(pending) != record["pending_sha256"]:
        raise ValueError("pending_binding_mismatch")
    if local_review.timestamp(record["applied_at"]) < local_review.timestamp(pending["attached_at"]):
        raise ValueError("review_predates_draft")
    if not isinstance(record["events"], list):
        raise ValueError("event_list_required")
    decisions = {"pending_sha256": record["pending_sha256"], "reviewer": record["reviewer"],
                 "decision_reference": record["decision_reference"], "items": []}
    for event in record["events"]:
        intake.exact_keys(event, ("target", "action", "before", "after"))
        decision = {"target": event["target"], "action": event["action"]}
        if event["action"] == "correct":
            decision["value"] = event["after"]
        decisions["items"].append(decision)
    replayed, draft = validate_decisions(pending, decisions)
    if replayed != record["events"]:
        raise ValueError("event_replay_mismatch")
    stale = (old_report["state"] == "stale" or current_report["state"] != "pending_review"
             or local_review.fingerprint(current_pending) != record["pending_sha256"])
    return {
        "synthetic": True, "state": "stale" if stale else "text_reviewed",
        "review_scope": "existing_draft_items_only", "reviewer_identity": IDENTITY,
        "reviewer": record["reviewer"], "decision_reference": record["decision_reference"],
        "source": copy.deepcopy(pending["source"]), "source_sha256": pending["source_sha256"],
        "original_draft": copy.deepcopy(pending["draft"]), "reviewed_draft": draft,
        "history": copy.deepcopy(record["events"]),
        "semantic_review": "stale" if stale else "human_decisions_recorded",
        "constraint_completeness": "unverified",
        "unresolved_questions": copy.deepcopy(draft["open_questions"]),
        "value_proposal": None if stale else intake.PERMISSIONS[draft["permission"]["status"]],
        "followup_state_unchanged": current_report["followup_state_unchanged"],
        "automatic_transition_allowed": False, "engineering_suitability": "not_evaluated",
        "notice": "Text decisions only, not engineering approval. Reviewer identity is self-declared. "
                  "Open questions remain unresolved. Fingerprints are not signatures or a global replay ledger.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--pending", type=Path, required=True)
    apply_parser.add_argument("--decisions", type=Path, required=True)
    apply_parser.add_argument("--out", type=Path, required=True)
    show = sub.add_parser("show")
    show.add_argument("--record", type=Path, required=True)
    show.add_argument("--pending", type=Path, required=True, help="Current pending snapshot for staleness checks")
    args = parser.parse_args()
    try:
        pending = intake.read_json(args.pending)
        if args.command == "apply":
            record = apply(pending, intake.read_json(args.decisions))
        else:
            record = intake.read_json(args.record)
        result = report(record, pending)
        if args.command == "apply":
            if len((json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")) > intake.MAX_JSON_BYTES:
                raise ValueError("snapshot_too_large")
            # Recheck files immediately before writing; no transaction/locking guarantee is claimed.
            if local_review.fingerprint(intake.read_json(args.pending)) != record["pending_sha256"]:
                raise ValueError("pending_changed_before_save")
            if report(record, pending)["state"] != "text_reviewed":
                raise ValueError("source_changed_before_save")
            local_review.write_new(args.out, record)
    except FileExistsError:
        print(json.dumps({"error": "destination_exists_choose_new_snapshot"}))
        return 2
    except OSError:
        print(json.dumps({"error": "local_io_error_no_overwrite_attempted"}))
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_or_stale_review_no_snapshot_written"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
