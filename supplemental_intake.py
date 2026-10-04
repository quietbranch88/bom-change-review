"""Synthetic text intake and pending drafts. This module never calls a model or approves."""

import argparse
import copy
import json
from pathlib import Path

import local_review
import offline_eval
import pcb_followup


MAX_TEXT_BYTES = 16 * 1024
MAX_JSON_BYTES = 256 * 1024
PERMISSIONS = {"allowed": True, "prohibited": False, "conditional": None,
               "unknown": None, "question": None}


def read_bounded(path, limit):
    with Path(path).open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("input_too_large")
    return raw.decode("utf-8")


def read_json(path):
    return offline_eval.parse_json(read_bounded(path, MAX_JSON_BYTES))


def exact_keys(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError("invalid_keys")


def capture(followup, source_id, text):
    checked = pcb_followup.report(followup)
    if checked["state"] != "waiting_for_confirmation":
        raise ValueError("current_waiting_followup_required")
    source = {"source_id": source_id, "case_id": checked["case_id"],
              "synthetic": True, "captured_at": local_review.now(), "text": text}
    record = {
        "schema_version": 1, "synthetic": True,
        "followup": copy.deepcopy(followup),
        "followup_sha256": local_review.fingerprint(followup),
        "source": source, "source_sha256": local_review.fingerprint(source),
        "draft": None, "draft_origin": None, "attached_at": None,
    }
    report(record)
    return record


def validate_draft(draft, source, source_hash):
    exact_keys(draft, ("source_id", "source_sha256", "permission", "constraints", "open_questions"))
    if draft["source_id"] != source["source_id"] or draft["source_sha256"] != source_hash:
        raise ValueError("draft_source_mismatch")
    exact_keys(draft["permission"], ("status", "quotes"))
    if draft["permission"]["status"] not in PERMISSIONS:
        raise ValueError("invalid_permission")

    def check_quote(quote):
        local_review.require_text(quote)
        if quote not in source["text"]:
            raise ValueError("quote_not_in_source")

    quotes = draft["permission"]["quotes"]
    if not isinstance(quotes, list) or not quotes:
        raise ValueError("permission_quotes_required")
    for quote in quotes:
        check_quote(quote)
    for name in ("constraints", "open_questions"):
        if not isinstance(draft[name], list):
            raise ValueError("invalid_annotation_list")
        for item in draft[name]:
            exact_keys(item, ("quote", "interpretation"))
            check_quote(item["quote"])
            local_review.require_text(item["interpretation"])


def report(record):
    exact_keys(record, ("schema_version", "synthetic", "followup", "followup_sha256",
                        "source", "source_sha256", "draft", "draft_origin", "attached_at"))
    if (type(record["schema_version"]) is not int or record["schema_version"] != 1
            or record["synthetic"] is not True):
        raise ValueError("invalid_metadata")
    if local_review.fingerprint(record["followup"]) != record["followup_sha256"]:
        raise ValueError("followup_mismatch")
    followup = pcb_followup.report(record["followup"])
    if followup["state"] not in ("waiting_for_confirmation", "stale"):
        raise ValueError("waiting_followup_required")
    source = record["source"]
    exact_keys(source, ("source_id", "case_id", "synthetic", "captured_at", "text"))
    source_id = source["source_id"]
    if (not isinstance(source_id, str) or not source_id.startswith("SIM-")
            or not source_id[4:] or len(source_id) > 80
            or not all(c.isascii() and (c.isalnum() or c in "-_") for c in source_id)
            or source_id in followup["reply_source_ids"]):
        raise ValueError("invalid_sim_source_id")
    if source["synthetic"] is not True or source["case_id"] != followup["case_id"]:
        raise ValueError("source_metadata_mismatch")
    local_review.require_text(source["text"])
    if len(source["text"].encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("source_too_large")
    captured_at = local_review.timestamp(source["captured_at"])
    if local_review.fingerprint(source) != record["source_sha256"]:
        raise ValueError("source_hash_mismatch")
    draft = record["draft"]
    if draft is None:
        if record["draft_origin"] is not None or record["attached_at"] is not None:
            raise ValueError("unexpected_draft_metadata")
        state = "captured"
        value = None
    else:
        if record["draft_origin"] not in ("demo_not_model_output", "model_output"):
            raise ValueError("invalid_draft_origin")
        if local_review.timestamp(record["attached_at"]) < captured_at:
            raise ValueError("invalid_attachment_time")
        validate_draft(draft, source, record["source_sha256"])
        state = "pending_review"
        value = PERMISSIONS[draft["permission"]["status"]]
    if followup["source_stale"]:
        state = "stale"
    return {
        "synthetic": True, "state": state,
        "source": copy.deepcopy(source), "source_sha256": record["source_sha256"],
        "draft": copy.deepcopy(draft), "draft_origin": record["draft_origin"],
        "value_proposal": value, "followup_state_unchanged": followup["state"],
        "semantic_review": "unverified", "constraint_completeness": "unverified",
        "automatic_transition_allowed": False, "engineering_suitability": "not_evaluated",
        "notice": "Full source retained. Quote existence is not semantic correctness or completeness. "
                  "Draft origin is declared, not authenticated. No human approval or follow-up update.",
    }


def attach(record, draft, *, origin="demo_not_model_output"):
    checked = report(record)
    if checked["state"] == "stale" or record["draft"] is not None:
        raise ValueError("fresh_captured_source_required")
    validate_draft(draft, record["source"], record["source_sha256"])
    result = copy.deepcopy(record)
    result.update(draft=copy.deepcopy(draft), draft_origin=origin,
                  attached_at=local_review.now())
    report(result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    intake = sub.add_parser("capture")
    intake.add_argument("--followup", type=Path, required=True)
    intake.add_argument("--text-file", type=Path, required=True)
    intake.add_argument("--source-id", required=True)
    intake.add_argument("--synthetic", action="store_true", required=True)
    intake.add_argument("--out", type=Path, required=True)
    add = sub.add_parser("attach-demo")
    add.add_argument("--record", type=Path, required=True)
    add.add_argument("--draft-file", type=Path, required=True)
    add.add_argument("--out", type=Path, required=True)
    show = sub.add_parser("show")
    show.add_argument("--record", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "capture":
            record = capture(read_json(args.followup), args.source_id,
                             read_bounded(args.text_file, MAX_TEXT_BYTES))
        else:
            record = read_json(args.record)
            if args.command == "attach-demo":
                record = attach(record, read_json(args.draft_file))
        result = report(record)
        if args.command != "show":
            # Check the final encoded size so every saved snapshot is readable again.
            if len((json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")) > MAX_JSON_BYTES:
                raise ValueError("snapshot_too_large")
            local_review.write_new(args.out, record)
    except FileExistsError:
        print(json.dumps({"error": "destination_exists_choose_new_snapshot"}))
        return 2
    except OSError:
        print(json.dumps({"error": "local_io_error_no_overwrite_attempted"}))
        return 2
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "invalid_or_stale_intake_no_snapshot_written"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
