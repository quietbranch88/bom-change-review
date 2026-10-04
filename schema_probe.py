"""Fixed five-probe diagnostic. Default plan is offline; explicit run consumes one batch."""

import argparse
import copy
import json
from pathlib import Path

import openrouter_smoke as smoke
import private_diagnostics as private

ROOT = Path(__file__).resolve().parent
BATCH = ROOT / "output/openrouter-schema-probes-20260925"
FIXTURE = ROOT / "examples/schema-probes/probes.json"
FIXTURE_HASH = "dfb7ce6658fac2e64b2adac212f0a95d939e17a89102ba5354fdd67bf15bcf67"
IDS = ("M00", "M01", "M02", "M03", "M04")


def requests():
    packet = smoke.offline_eval.read_json(FIXTURE)
    if smoke.local_review.fingerprint(packet) != FIXTURE_HASH:
        raise smoke.Blocked("probe_fixture_changed")
    result = []
    for variant in packet["variants"]:
        body = smoke.build_request()
        body["messages"] = copy.deepcopy(packet["messages"])
        schema = copy.deepcopy(packet["base_schema"])
        schema["properties"].update(copy.deepcopy(variant["property_overrides"]))
        body["response_format"]["json_schema"].update(name="schema_probe", schema=schema)
        result.append((variant["id"], body))
    if tuple(name for name, _ in result) != IDS:
        raise smoke.Blocked("probe_ids_changed")
    return result


def validate_answer(response):
    if (response.get("provider_error_present") or response.get("model") != smoke.MODEL
            or response.get("provider") != "Mistral" or len(response["choices"]) != 1):
        raise smoke.Blocked("probe_response_identity_or_error")
    choice = response["choices"][0]
    message = choice["message"]
    if choice["finish_reason"] != "stop" or message.get("refusal") or message.get("tool_calls"):
        raise smoke.Blocked("probe_incomplete_or_refused")
    answer = smoke.offline_eval.parse_json(message["content"])
    if (not isinstance(answer, dict) or set(answer) != {"value", "unit", "evidence_ids"}
            or type(answer["value"]) not in (int, float) or answer["value"] != 14
            or answer["unit"] != "V" or answer["evidence_ids"] != ["Q1"]):
        raise smoke.Blocked("probe_answer_mismatch")
    # This exact answer satisfies all five schemas; no engineering review is implied.
    return answer


def run_probe(key, name, body, directory, preview, transport):
    directory.mkdir()
    smoke.write_new(directory / "claim.json", {"id": name, "preflight": preview,
                    "request": body, "created_at": smoke.local_review.now()})
    result = {"id": name, "post_attempts": 1, "started_at": smoke.local_review.now(),
              "status": "unknown", "cost_usd": None, "cost_status": "pending_reconciliation"}
    try:
        response = smoke.response_subset(transport("POST", "/chat/completions", key, body), key)
        smoke.write_new(directory / "response.json", response)
        result.update(smoke.accounting(response))
        answer = validate_answer(response)
        result["answer"] = answer
        result["status"] = "passed"
    except smoke.Blocked as error:
        result["status"] = str(error)
        if error.diagnostic is not None:
            result["diagnostic"] = error.diagnostic
        if str(error).startswith("http_"):
            result["private_archive_status"] = "unavailable"
            if error.private_archive is not None:
                try:
                    smoke.write_new(directory / "error.private.json", error.private_archive)
                    private.unseal(error.private_archive)
                    result["private_archive_status"] = "saved_and_decrypted"
                except (OSError, ValueError):
                    result["private_archive_status"] = "save_or_verify_failed"
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        result["status"] = "probe_local_processing_failed"
    result["finished_at"] = smoke.local_review.now()
    smoke.write_new(directory / "result.json", result)
    return result


def run_batch(key, directory=BATCH, transport=smoke.api_request):
    directory = Path(directory)
    if (directory / "claim.json").exists():
        raise smoke.Blocked("batch_already_claimed_do_not_resubmit")
    trials = requests()
    if private.unseal(private.seal(b"probe-readiness")) != b"probe-readiness":
        raise smoke.Blocked("archive_readiness_failed")
    first = smoke.preflight(key, trials[0][1], transport)
    available = smoke.money(first["key_remaining_usd"])
    reserved = smoke.Decimal(0)
    directory.mkdir(parents=True, exist_ok=True)
    smoke.write_new(directory / "claim.json", {"authorization": "Zoe_yes_max5_20260925",
                    "maximum_posts": 5, "project_total_limit_usd": "0.10", "no_retry": True,
                    "created_at": smoke.local_review.now(), "fixture_sha256": FIXTURE_HASH})
    report = {"results": [], "status": "completed", "maximum_posts": 5,
              "review_created": False, "automatic_retry": False}
    try:
        for name, body in trials:
            preview = first if name == "M00" else smoke.preflight(key, body, transport)
            ceiling = smoke.money(preview["conservative_token_cost_ceiling_usd"])
            if reserved + ceiling > available:
                raise smoke.Blocked("batch_reservation_exceeds_available_budget")
            reserved += ceiling
            result = run_probe(key, name, body, directory / name, preview, transport)
            report["results"].append(result)
            passed = result["status"] == "passed"
            if name == "M00" and not passed:
                report["status"] = "stopped_baseline_failed"
                break
            if result["cost_usd"] is not None and smoke.money(result["cost_usd"]) > ceiling:
                report["status"] = "stopped_cost_above_reservation"
                break
            comparative_error = (result["status"] == "http_400"
                                 and result.get("private_archive_status") == "saved_and_decrypted")
            if not comparative_error and (not passed or result["cost_status"] != "reported_by_provider"):
                report["status"] = "stopped_noncomparative_failure_or_unknown_cost"
                break
    except smoke.Blocked as error:
        report["status"] = str(error)
    except (OSError, ValueError, TypeError, KeyError, IndexError, RecursionError):
        report["status"] = "stopped_local_or_preflight_failure_check_claims"
    report["reserved_token_ceiling_usd"] = str(reserved)
    smoke.write_new(directory / "summary.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run"), default="plan", nargs="?")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            report = {"probe_ids": [name for name, _ in requests()], "paid_request_sent": False}
        else:
            report = run_batch(smoke.load_key())
        print(json.dumps(report, ensure_ascii=True))
        return 0 if args.command == "plan" or (report["status"] == "completed" and all(
            result["status"] == "passed" for result in report["results"])) else 1
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        print('{"error":"probe_blocked_check_existing_claims","automatic_retry":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
