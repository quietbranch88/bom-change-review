"""One authorized E01 smoke round. Default is read-only preflight, never inference."""

import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

import local_review
import offline_eval
import private_diagnostics


ROOT = Path(__file__).resolve().parent
ROUND_DIR = ROOT / "output" / "openrouter-e01-20260925"
MODEL = "mistralai/ministral-3b-2512"
PROVIDER = "mistral/zdr"
ENDPOINT_PATH = f"/models/{MODEL}/endpoints"
KEY_NAME = "BOM_REVIEW_OPENROUTER_API_KEY"
BUDGET = Decimal("0.10")
PRICE_PER_TOKEN_CAP = Decimal("0.0000001")
MAX_TOKENS = 2048
MAX_CONTEXT = 131072
TIMEOUT = 45
MAX_RESPONSE_BYTES = 1000000
MAX_ERROR_BYTES = 8192
MAX_ERROR_MESSAGE_CHARS = 500


class Blocked(ValueError):
    """Fixed error code plus an optional allowlisted diagnostic, never raw text."""

    def __init__(self, code, *, diagnostic=None, private_archive=None):
        super().__init__(code)
        self.diagnostic = diagnostic
        self.private_archive = private_archive


def error_diagnostic(error, key, private_capture=None):
    """Fail closed: arbitrary provider text is withheld, not regex-sanitized prose."""
    safe = {"http_status": error.code, "message": "[WITHHELD]",
            "body_status": "unavailable", "message_status": "withheld"}
    try:
        try:
            raw = error.read(MAX_ERROR_BYTES + 1)
        finally:
            error.close()
        if len(raw) > MAX_ERROR_BYTES:
            safe["body_status"] = "too_large"
            return safe
        if private_capture is not None:
            try:
                private_capture.append(private_diagnostics.seal(raw, key))
            except (OSError, ValueError):
                pass  # No plaintext fallback; caller records archive unavailable.
        envelope = offline_eval.parse_json(raw.decode("utf-8"))
        if not isinstance(envelope, dict) or not isinstance(envelope.get("error"), dict):
            return safe
        detail = envelope["error"]
    except (OSError, ValueError, RecursionError, HTTPException):
        return safe

    def redacted(value):
        if not isinstance(value, str) or len(value) > MAX_ERROR_MESSAGE_CHARS:
            return "[WITHHELD]"
        if key:
            value = value.replace(key, "[REDACTED]")
        value = re.sub(r"sk-or-[A-Za-z0-9_-]+", "[REDACTED]", value)
        return re.sub(r"(?i)(?:authorization\s*:|bearer\s+)\s*[^\r\n]*", "[REDACTED]", value)

    safe["body_status"] = "parsed"
    fixed_messages = {"Provider returned error", "Invalid API key", "Rate limit exceeded",
                      "Bad Request", "Invalid JSON schema", "Insufficient credits"}

    def recognized_message(value):
        message = redacted(value)
        keywords = r"(uniqueItems|additionalProperties|anyOf|oneOf|allOf|enum|type|const|required|items|format)"
        match = re.fullmatch(r"Unsupported schema keyword: " + keywords, message)
        if not match:
            match = re.fullmatch(r"Received unsupported keyword `" + keywords + r"` in schema\.", message)
        if match:
            return "Unsupported schema keyword: " + match.group(1)
        return message if message in fixed_messages else None

    message = recognized_message(detail.get("message"))
    if message:
        safe["message"] = message
        safe["message_status"] = "recognized"
    metadata = detail.get("metadata")
    if isinstance(metadata, dict):
        error_type = redacted(metadata.get("error_type"))
        if error_type in {"invalid_request", "invalid_request_error", "rate_limit_exceeded",
                          "context_length_exceeded", "authentication_error", "server"}:
            safe["error_type"] = error_type
        if redacted(metadata.get("provider_name")) == "Mistral":
            safe["provider"] = "Mistral"
        # One bounded nested envelope only; never copy or recursively traverse raw data.
        vendor = metadata.get("raw")
        if isinstance(vendor, str) and len(vendor) <= MAX_ERROR_BYTES:
            try:
                vendor = offline_eval.parse_json(vendor)
            except (ValueError, RecursionError):
                vendor = None
            if isinstance(vendor, dict):
                provider_message = recognized_message(vendor.get("message"))
                if provider_message:
                    safe["provider_message"] = provider_message
    return safe


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def load_key():
    key = os.environ.get(KEY_NAME)
    if not key and os.name == "nt":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as registry:
                key = winreg.QueryValueEx(registry, KEY_NAME)[0]
        except OSError:
            key = None
    if not isinstance(key, str) or not re.fullmatch(r"sk-or-v1-[A-Za-z0-9_-]+", key):
        raise Blocked("dedicated_key_missing_or_invalid")
    return key


def api_request(method, path, key=None, body=None):
    allowed = {("GET", ENDPOINT_PATH), ("GET", "/key"), ("POST", "/chat/completions")}
    if (method, path) not in allowed:
        raise Blocked("unsupported_api_operation")
    headers = {"Content-Type": "application/json", "X-OpenRouter-Title": "BOM E01 limited smoke"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request("https://openrouter.ai/api/v1" + path, headers=headers, data=data, method=method)
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=TIMEOUT) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise Blocked("response_too_large")
        return offline_eval.parse_json(raw.decode("utf-8"))
    except urllib.error.HTTPError as error:
        private_capture = [] if method == "POST" else None
        diagnostic = error_diagnostic(error, key, private_capture)
        archive = private_capture[0] if private_capture else None
        raise Blocked(f"http_{error.code}", diagnostic=diagnostic, private_archive=archive) from None
    except (OSError, ValueError, UnicodeError):
        raise Blocked("transport_or_response_error") from None


def money(value):
    if isinstance(value, bool) or value is None:
        raise Blocked("invalid_money_metadata")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise Blocked("invalid_money_metadata") from None
    if not number.is_finite() or number < 0:
        raise Blocked("invalid_money_metadata")
    return number


def build_request(data_dir=offline_eval.DATA_DIR):
    packet = offline_eval.build_prompt("E01", data_dir)
    user_packet = {name: packet[name] for name in ("case_id", "field_contract", "blocks")}
    schema = copy.deepcopy(packet["response_schema"])
    # This Mistral endpoint rejects uniqueItems. Keep duplicate rejection locally.
    for field in schema["properties"]["fields"]["properties"].values():
        for name in ("value", "evidence_ids"):
            field["properties"][name].pop("uniqueItems", None)
    return {
        "model": MODEL,
        "messages": [{"role": "system", "content": packet["system"]},
                     {"role": "user", "content": json.dumps(user_packet, ensure_ascii=False)}],
        "max_tokens": MAX_TOKENS, "temperature": 0, "stream": False,
        "plugins": [],
        "provider": {"only": [PROVIDER], "order": [PROVIDER], "allow_fallbacks": False,
                     "require_parameters": True, "data_collection": "deny", "zdr": True,
                     "max_price": {"prompt": 0.10, "completion": 0.10}},
        "response_format": {"type": "json_schema", "json_schema": {
            "name": "hardware_request_e01", "strict": True, "schema": schema,
        }},
    }


def preflight(key, request, transport=api_request):
    endpoints = transport("GET", ENDPOINT_PATH)["data"]
    if endpoints["id"] != MODEL:
        raise Blocked("unexpected_catalog_model")
    matches = [e for e in endpoints["endpoints"] if e.get("tag") == PROVIDER and e.get("model_id") == MODEL]
    if len(matches) != 1:
        raise Blocked("expected_endpoint_unavailable")
    endpoint = matches[0]
    needed = {"max_tokens", "temperature", "response_format", "structured_outputs"}
    if not needed.issubset(endpoint["supported_parameters"]) or endpoint.get("status") != 0:
        raise Blocked("endpoint_capability_or_status")
    context = endpoint["context_length"]
    completion = endpoint["max_completion_tokens"]
    if type(context) is not int or not 0 < context <= MAX_CONTEXT or type(completion) is not int or completion < MAX_TOKENS:
        raise Blocked("endpoint_token_limits")
    prices = endpoint["pricing"]
    input_price, output_price = money(prices["prompt"]), money(prices["completion"])
    if input_price > PRICE_PER_TOKEN_CAP or output_price > PRICE_PER_TOKEN_CAP:
        raise Blocked("endpoint_price_above_cap")
    # Optional per-request/add-on pricing must be zero for this plain-text smoke.
    for name in ("request", "image", "web_search", "internal_reasoning"):
        if name in prices and money(prices[name]) != 0:
            raise Blocked("unexpected_addon_price")
    if "input_cache_read" in prices and money(prices["input_cache_read"]) > input_price:
        raise Blocked("unexpected_cache_price")
    ceiling = (Decimal(context) * input_price + Decimal(MAX_TOKENS) * output_price) * Decimal("1.10")
    # A conservative full-context ceiling, not a tokenizer-derived expected bill.
    if ceiling > BUDGET or len(json.dumps(request, ensure_ascii=False).encode("utf-8")) > 20000:
        raise Blocked("request_budget_or_size")
    data = transport("GET", "/key", key)["data"]
    limit, remaining = money(data["limit"]), money(data["limit_remaining"])
    expiry = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
    if (limit != BUDGET or data["limit_reset"] is not None or remaining > limit
            or remaining < ceiling or remaining <= 0 or data.get("is_management_key") is not False
            or expiry.tzinfo is None or expiry <= datetime.now(timezone.utc)):
        raise Blocked("key_budget_or_expiry")
    return {
        "checked_at": local_review.now(), "model": MODEL, "provider_tag": PROVIDER,
        "prompt_usd_per_token": str(input_price), "completion_usd_per_token": str(output_price),
        "context_length": context, "max_output_tokens": MAX_TOKENS,
        "conservative_token_cost_ceiling_usd": str(ceiling),
        "approved_budget_usd": str(BUDGET), "key_limit_usd": str(limit),
        "key_remaining_usd": str(remaining), "key_reset": None,
        "key_expires_at": data["expires_at"],
        "key_includes_byok_in_limit": data.get("include_byok_in_limit"),
        "cost_scope": "published_token_price_with_headroom_not_external_BYOK_invoice_audit",
        "request_sha256": local_review.fingerprint(request),
    }


def write_new(path, value):
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with Path(path).open("x", encoding="utf-8") as file:
        file.write(text)
        file.flush()
        os.fsync(file.fileno())


def scrub(value, key):
    if isinstance(value, str):
        return re.sub(r"sk-or-[A-Za-z0-9_-]+", "[REDACTED]", value.replace(key, "[REDACTED]"))
    if isinstance(value, list):
        return [scrub(item, key) for item in value]
    if isinstance(value, dict):
        return {scrub(name, key): scrub(item, key) for name, item in value.items()}
    return value


def response_subset(response, key):
    # Keep provenance/content, not account identifiers or unknown vendor metadata.
    if not isinstance(response, dict):
        raise Blocked("invalid_response_envelope")
    result = {name: response.get(name) for name in ("id", "model", "provider")}
    result["choices"] = [{"finish_reason": c.get("finish_reason"),
                          "message": {name: c.get("message", {}).get(name) for name in ("role", "content", "refusal", "tool_calls")}}
                         for c in response.get("choices", [])]
    usage = response.get("usage") or {}
    result["usage"] = {name: usage.get(name) for name in ("prompt_tokens", "completion_tokens", "total_tokens", "cost", "is_byok")}
    result["provider_error_present"] = bool(response.get("error"))
    return scrub(result, key)


def accounting(response):
    usage = response.get("usage", {})
    try:
        cost = money(usage.get("cost"))
    except Blocked:
        return {"cost_usd": None, "cost_status": "pending_reconciliation", "reservation_usd": str(BUDGET)}
    return {"cost_usd": str(cost), "cost_status": "reported_by_provider" if cost <= BUDGET else "budget_violation",
            "reservation_usd": str(BUDGET) if cost > BUDGET else "0"}


def extract(response, source):
    if response.get("provider_error_present"):
        raise Blocked("provider_error")
    if response.get("model") != MODEL or response.get("provider") != "Mistral":
        raise Blocked("unexpected_response_model_or_provider")
    choices = response["choices"]
    if len(choices) != 1:
        raise Blocked("unexpected_choice_count")
    choice = choices[0]
    message = choice["message"]
    if choice["finish_reason"] != "stop" or message.get("refusal") or message.get("tool_calls"):
        raise Blocked("incomplete_or_refused_response")
    draft = offline_eval.parse_json(message["content"])
    local_review.validate_draft(source, draft)
    return draft


def run_once(key, run_dir=ROUND_DIR, transport=api_request, data_dir=offline_eval.DATA_DIR):
    run_dir = Path(run_dir)
    if (run_dir / "claim.json").exists():
        raise Blocked("round_already_attempted_do_not_resubmit")
    request = build_request(data_dir)
    source = local_review.source_context("E01", data_dir)
    preview = preflight(key, request, transport)
    run_dir.mkdir(parents=True, exist_ok=True)
    claim = {"authorization": "E01_once_20260925_USD0.10_no_retry", "reserved_usd": str(BUDGET),
             "state": "reserved_submission_outcome_unknown_until_result", "created_at": local_review.now(),
             "preflight": preview, "request": request, "source": source,
             "source_sha256": local_review.fingerprint(source)}
    # Exclusive claim survives errors and prevents another invocation submitting.
    write_new(run_dir / "claim.json", claim)
    started = time.monotonic()
    result = {"started_at": local_review.now(), "case_id": "E01", "model": MODEL,
              "requested_provider_tag": PROVIDER, "post_attempts": 1,
              "status": "submission_outcome_unknown", "review_created": False,
              "cost_usd": None, "cost_status": "pending_reconciliation", "reservation_usd": str(BUDGET)}
    try:
        raw = transport("POST", "/chat/completions", key, request)
        response = response_subset(raw, key)
        write_new(run_dir / "response.json", response)
        result.update(accounting(response))
        result["actual_provider"] = response.get("provider")
        result["generation_id"] = response.get("id")
        result["usage"] = response["usage"]
        draft = extract(response, source)
        if local_review.fingerprint(local_review.source_context("E01", data_dir)) != claim["source_sha256"]:
            raise Blocked("source_changed_during_inference")
        review = local_review.create_review("E01", draft, "model_output", data_dir)
        write_new(run_dir / "draft.json", draft)
        write_new(run_dir / "review-0.json", review)
        result["review_created"] = True
        result["status"] = "pending_review"
    except Blocked as error:
        result["status"] = str(error)
        if error.diagnostic is not None:
            result["diagnostic"] = error.diagnostic
        if str(error).startswith("http_"):
            result["private_archive_status"] = "unavailable"
            if error.private_archive is not None:
                try:
                    write_new(run_dir / "error.private.json", error.private_archive)
                    result["private_archive_status"] = "saved"
                    result["private_archive_file"] = "error.private.json"
                except (OSError, ValueError):
                    result["private_archive_status"] = "save_failed"
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        result["status"] = "response_or_local_processing_failed"
    result["finished_at"] = local_review.now()
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    write_new(run_dir / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "status"), nargs="?", default="plan")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = offline_eval.read_json(ROUND_DIR / "result.json")
        elif args.command == "run":
            result = run_once(load_key())
        else:
            result = preflight(load_key(), build_request())
            result["paid_request_sent"] = False
            result["round_already_claimed"] = (ROUND_DIR / "claim.json").exists()
    except Blocked as error:
        report = {"error": str(error), "automatic_retry": False}
        if error.diagnostic is not None:
            report["diagnostic"] = error.diagnostic
        print(json.dumps(report))
        return 2
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError, RecursionError):
        print(json.dumps({"error": "local_or_preflight_failure", "automatic_retry": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if args.command != "run" or (result["status"] == "pending_review" and result["cost_status"] == "reported_by_provider") else 1


if __name__ == "__main__":
    raise SystemExit(main())
