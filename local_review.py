"""Local extraction review snapshots; no model calls or engineering approval."""

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import offline_eval


def fingerprint(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def require_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("required_text")


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset().total_seconds() != 0:
        raise ValueError("timestamp_must_be_utc")
    return result


def source_context(case_id, data_dir=offline_eval.DATA_DIR):
    inputs, case = offline_eval.load_case(case_id, data_dir)
    return {
        "dataset_id": inputs["dataset_id"],
        "retrieved_on": inputs["retrieved_on"],
        "adaptation": inputs["adaptation"],
        "field_definitions": {name: inputs["field_definitions"][name] for name in case["fields"]},
        "case": case,
    }


def validate_draft(context, draft):
    if offline_eval.validate_response(context, context["case"], draft):
        raise ValueError("invalid_draft_structure")


def create_review(case_id, draft, origin, data_dir=offline_eval.DATA_DIR):
    if origin not in ("demo", "manual_draft", "model_output"):
        raise ValueError("invalid_origin")
    context = source_context(case_id, data_dir)
    validate_draft(context, draft)
    return {
        "schema_version": 1,
        "created_at": now(),
        "draft_origin_declared": origin,
        "reviewer_identity": "self_declared_not_authenticated",
        "source": context,
        "source_sha256": fingerprint(context),
        "draft": copy.deepcopy(draft),
        "draft_sha256": fingerprint(draft),
        "events": [],
    }


def replay(record):
    required = {"schema_version", "created_at", "draft_origin_declared", "reviewer_identity",
                "source", "source_sha256", "draft", "draft_sha256", "events"}
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError("invalid_record_keys")
    if type(record["schema_version"]) is not int or record["schema_version"] != 1:
        raise ValueError("unsupported_schema")
    if (record["draft_origin_declared"] not in ("demo", "manual_draft", "model_output")
            or record["reviewer_identity"] != "self_declared_not_authenticated"):
        raise ValueError("invalid_review_metadata")
    context, draft = record["source"], record["draft"]
    if (fingerprint(context) != record["source_sha256"]
            or fingerprint(draft) != record["draft_sha256"]):
        raise ValueError("snapshot_hash_mismatch")
    validate_draft(context, draft)
    current = copy.deepcopy(draft)
    statuses = {field: "pending" for field in current["fields"]}
    previous_time = timestamp(record["created_at"])
    if not isinstance(record["events"], list):
        raise ValueError("invalid_history")
    for index, event in enumerate(record["events"], 1):
        required_event = {"sequence", "at", "reviewer", "reason", "field", "action", "before", "after"}
        if not isinstance(event, dict) or set(event) != required_event:
            raise ValueError("invalid_event_keys")
        if type(event["sequence"]) is not int or event["sequence"] != index:
            raise ValueError("invalid_sequence")
        require_text(event["reviewer"])
        require_text(event["reason"])
        event_time = timestamp(event["at"])
        if event_time < previous_time:
            raise ValueError("history_time_reversed")
        previous_time = event_time
        field, action = event["field"], event["action"]
        if field not in statuses or action not in ("accept", "correct", "needs_input"):
            raise ValueError("invalid_decision")
        if fingerprint(event["before"]) != fingerprint(current["fields"][field]):
            raise ValueError("history_before_mismatch")
        changed = fingerprint(event["before"]) != fingerprint(event["after"])
        if (action == "correct") != changed:
            raise ValueError("replacement_action_mismatch")
        current["fields"][field] = copy.deepcopy(event["after"])
        validate_draft(context, current)
        statuses[field] = {"accept": "accepted", "correct": "corrected", "needs_input": "needs_input"}[action]
    return current, statuses


def is_stale(record, data_dir=offline_eval.DATA_DIR):
    live = source_context(record["draft"]["case_id"], data_dir)
    return fingerprint(live) != record["source_sha256"]


def decide(record, field, action, reviewer, reason, replacement=None, data_dir=offline_eval.DATA_DIR):
    current, statuses = replay(record)
    if is_stale(record, data_dir):
        raise ValueError("stale_source_start_new_review")
    if field not in statuses or action not in ("accept", "correct", "needs_input"):
        raise ValueError("invalid_decision")
    require_text(reviewer)
    require_text(reason)
    if (action == "correct") != (replacement is not None):
        raise ValueError("replacement_required_only_for_correction")
    before = current["fields"][field]
    after = replacement if action == "correct" else before
    result = copy.deepcopy(record)
    result["events"].append({
        "sequence": len(record["events"]) + 1,
        "at": now(), "reviewer": reviewer, "reason": reason,
        "field": field, "action": action,
        "before": copy.deepcopy(before), "after": copy.deepcopy(after),
    })
    replay(result)
    return result


def report(record, data_dir=offline_eval.DATA_DIR):
    current, statuses = replay(record)
    stale = is_stale(record, data_dir)
    if stale:
        state = "stale"
    elif "needs_input" in statuses.values():
        state = "needs_input"
    elif all(value in ("accepted", "corrected") for value in statuses.values()):
        state = "reviewed"
    else:
        state = "pending_review"
    return {
        "case_id": current["case_id"],
        "revision": len(record["events"]),
        "snapshot_sha256": fingerprint(record),
        "draft_origin_declared": record["draft_origin_declared"],
        "reviewer_identity": record["reviewer_identity"],
        "extraction_status": state,
        "source_stale": stale,
        "engineering_suitability": "not_evaluated",
        "unknown_fields": [name for name, item in current["fields"].items() if item["value"] is None],
        "fields": {name: {"status": statuses[name], "item": item} for name, item in current["fields"].items()},
        "history": copy.deepcopy(record["events"]),
        "notice": "Declared review of extraction only; not authenticated approval or component qualification.",
    }


def write_new(path, record):
    # Exclusive create: never overwrite any input or earlier review snapshot.
    content = json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as handle:
        handle.write(content)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--case", required=True)
    init.add_argument("--draft", type=Path, required=True)
    init.add_argument("--origin", choices=("demo", "manual_draft", "model_output"), required=True)
    init.add_argument("--out", type=Path, required=True)
    decision = sub.add_parser("decide")
    decision.add_argument("--review", type=Path, required=True)
    decision.add_argument("--field", required=True)
    decision.add_argument("--action", choices=("accept", "correct", "needs_input"), required=True)
    decision.add_argument("--reviewer", required=True)
    decision.add_argument("--reason", required=True)
    decision.add_argument("--replacement", type=Path, help="JSON field item, not an entire response")
    decision.add_argument("--out", type=Path, required=True)
    show = sub.add_parser("show")
    show.add_argument("--review", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "init":
            record = create_review(args.case, offline_eval.read_json(args.draft), args.origin)
        else:
            record = offline_eval.read_json(args.review)
            if args.command == "decide":
                replacement = offline_eval.read_json(args.replacement) if args.replacement else None
                record = decide(record, args.field, args.action, args.reviewer, args.reason, replacement)
        result = report(record)
        if args.command != "show":
            write_new(args.out, record)
    except FileExistsError:
        print(json.dumps({"error": "destination_exists_choose_new_snapshot"}))
        return 2
    except OSError:
        print(json.dumps({"error": "local_io_error_no_overwrite_attempted"}))
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_or_stale_input_no_snapshot_written"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
