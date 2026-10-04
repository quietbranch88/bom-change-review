"""Simulation-only PCB follow-up lesson; no LLM, external messages or approvals."""

import argparse
import copy
import json
from pathlib import Path

import local_review
import offline_eval


FIELD = "pcb_changes_allowed"
QUESTION = "是否允許修改 PCB？"
ANSWERS = {
    "allow": (True, "可以修改 PCB。"),
    "deny": (False, "不能修改 PCB。"),
    "unknown": (None, "我不知道，要再確認。"),
}


def create(review):
    checked = local_review.report(review)
    if checked["source_stale"] or FIELD not in checked["fields"]:
        raise ValueError("unsupported_or_stale_review")
    return {
        "schema_version": 1,
        "synthetic": True,
        "field": FIELD,
        "created_at": local_review.now(),
        "base_review": copy.deepcopy(review),
        "base_review_sha256": local_review.fingerprint(review),
        "events": [],
    }


def report(record):
    required = {"schema_version", "synthetic", "field", "created_at",
                "base_review", "base_review_sha256", "events"}
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError("invalid_followup_keys")
    if (type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["synthetic"] is not True or record["field"] != FIELD):
        raise ValueError("invalid_followup_metadata")
    base = record["base_review"]
    if local_review.fingerprint(base) != record["base_review_sha256"]:
        raise ValueError("base_review_mismatch")
    base_report = local_review.report(base)
    base_item = base_report["fields"][FIELD]
    value = base_item["item"]["value"]
    known = [] if value is None else [value]
    if value is not None and type(value) is not bool:
        raise ValueError("invalid_boolean")
    previous_time = local_review.timestamp(record["created_at"])
    asked = False
    sources = []
    if not isinstance(record["events"], list):
        raise ValueError("invalid_events")
    for index, event in enumerate(record["events"], 1):
        if not isinstance(event, dict):
            raise ValueError("invalid_event")
        keys = {"sequence", "at", "action"}
        if event.get("action") == "reply":
            keys |= {"source_id", "answer", "text"}
        if set(event) != keys or type(event["sequence"]) is not int or event["sequence"] != index:
            raise ValueError("invalid_event_keys_or_sequence")
        event_time = local_review.timestamp(event["at"])
        if event_time < previous_time:
            raise ValueError("invalid_event_time")
        previous_time = event_time
        if event["action"] == "ask":
            if asked or known:
                raise ValueError("question_not_needed")
            asked = True
        elif event["action"] == "reply":
            if not asked:
                raise ValueError("question_not_recorded")
            source_id = event["source_id"]
            if (not isinstance(source_id, str) or not source_id.startswith("SIM-")
                    or not source_id[4:] or len(source_id) > 80
                    or not all(c.isascii() and (c.isalnum() or c in "-_") for c in source_id)
                    or source_id in sources):
                raise ValueError("invalid_or_duplicate_sim_source")
            answer_value, text = ANSWERS[event["answer"]]
            if event["text"] != text:
                raise ValueError("unsupported_sim_reply")
            sources.append(source_id)
            if answer_value is not None:
                known.append(answer_value)
        else:
            raise ValueError("invalid_action")
    conflict = True in known and False in known
    value = None if conflict or not known else known[0]
    if conflict:
        state = "conflict"
    elif value is not None:
        state = "pending_review" if sources else "existing_value"
    elif asked:
        state = "waiting_for_confirmation"
    else:
        state = "needs_question"
    if base_report["source_stale"]:
        state = "stale"
    return {
        "synthetic": True,
        "case_id": base_report["case_id"],
        "field": FIELD,
        "state": state,
        "value_proposal": value,
        "base_field_status": base_item["status"],
        "base_value_unchanged": base_item["item"]["value"],
        "source_stale": base_report["source_stale"],
        "question_to_ask": QUESTION if state == "needs_question" else None,
        "question_recorded": asked,
        "reply_source_ids": sources,
        "history": copy.deepcopy(record["events"]),
        "engineering_suitability": "not_evaluated",
        "notice": "Simulated replies only; proposals do not change or approve the base review. "
                  "No external question was sent. Conflicts need review; no automatic resolution.",
    }


def advance(record, action, answer=None, source_id=None):
    checked = report(record)
    if checked["source_stale"]:
        raise ValueError("stale_source_start_new_followup")
    if action == "ask":
        if checked["state"] != "needs_question" or answer is not None or source_id is not None:
            raise ValueError("question_not_needed")
    elif action == "reply":
        if not checked["question_recorded"] or answer not in ANSWERS:
            raise ValueError("invalid_reply")
    else:
        raise ValueError("invalid_action")
    result = copy.deepcopy(record)
    event = {"sequence": len(result["events"]) + 1, "at": local_review.now(), "action": action}
    if action == "reply":
        event.update(source_id=source_id, answer=answer, text=ANSWERS[answer][1])
    result["events"].append(event)
    report(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--review", type=Path, required=True)
    init.add_argument("--out", type=Path, required=True)
    for command in ("show", "ask", "reply"):
        child = sub.add_parser(command)
        child.add_argument("--record", type=Path, required=True)
        if command != "show":
            child.add_argument("--out", type=Path, required=True)
        if command == "reply":
            child.add_argument("--answer", choices=tuple(ANSWERS), required=True)
            child.add_argument("--source-id", required=True, help="Distinct SIM- prefixed teaching source ID")
    args = parser.parse_args()
    try:
        if args.command == "init":
            record = create(offline_eval.read_json(args.review))
        else:
            record = offline_eval.read_json(args.record)
            if args.command != "show":
                record = advance(record, args.command, getattr(args, "answer", None),
                                 getattr(args, "source_id", None))
        result = report(record)
        if args.command != "show":
            local_review.write_new(args.out, record)
    except FileExistsError:
        print(json.dumps({"error": "destination_exists_choose_new_snapshot"}))
        return 2
    except OSError:
        print(json.dumps({"error": "local_io_error_no_overwrite_attempted"}))
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_or_stale_followup_no_snapshot_written"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
