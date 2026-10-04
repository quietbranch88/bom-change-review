"""Deterministic guards with fake HTTP responses, not live model-quality evidence."""

import copy
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import local_review
import offline_eval
import openrouter_smoke as smoke


ROOT = Path(__file__).resolve().parents[1]
SENTINEL = "unit-test-credential-never-real"


def key_metadata():
    return {"limit": 0.1, "limit_remaining": 0.1, "limit_reset": None,
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "is_management_key": False, "include_byok_in_limit": False}


def endpoint_metadata():
    return {"id": "mistralai/ministral-3b-2512", "endpoints": [{
        "tag": "mistral/zdr", "model_id": "mistralai/ministral-3b-2512", "status": 0,
        "context_length": 131072, "max_completion_tokens": 104857,
        "supported_parameters": ["max_tokens", "temperature", "response_format", "structured_outputs"],
        "pricing": {"prompt": "0.0000001", "completion": "0.0000001", "input_cache_read": "0.00000001"},
    }]}


def output_draft():
    # Hand-authored teaching fixture, not reference answers loaded by the caller.
    result = json.loads((ROOT / "examples/local-review/draft.simulated.json").read_text(encoding="utf-8"))
    result["fields"]["input_max"]["value"] = 30
    return result


def completion():
    return {"id": "unit-test-generation", "model": "mistralai/ministral-3b-2512", "provider": "Mistral",
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": json.dumps(output_draft())}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 400, "total_tokens": 1400, "cost": 0.00014},
            "user_id": "must-not-be-saved"}


class FakeAPI:
    def __init__(self):
        self.endpoint = endpoint_metadata()
        self.key = key_metadata()
        self.response = completion()
        self.posts = 0
        self.calls = []
        self.failure = None
        self.before_post = None

    def __call__(self, method, path, key=None, body=None):
        self.calls.append((method, path))
        if method == "GET":
            return {"data": copy.deepcopy(self.key if path == "/key" else self.endpoint)}
        self.posts += 1
        if self.before_post:
            self.before_post()
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.response)


class PreflightTests(unittest.TestCase):
    def test_exact_request_contract_and_no_reference_read(self):
        original = offline_eval.read_json

        def only_inputs(path):
            self.assertEqual(Path(path).name, "inputs.json")
            return original(path)

        with patch.object(offline_eval, "read_json", side_effect=only_inputs):
            body = smoke.build_request()
        self.assertEqual(body["model"], "mistralai/ministral-3b-2512")
        self.assertEqual(body["max_tokens"], 2048)
        self.assertEqual(body["temperature"], 0)
        self.assertEqual(body["provider"]["only"], ["mistral/zdr"])
        self.assertFalse(body["provider"]["allow_fallbacks"])
        self.assertTrue(body["provider"]["require_parameters"])
        self.assertTrue(body["provider"]["zdr"])
        self.assertEqual(body["provider"]["data_collection"], "deny")
        self.assertEqual(body["provider"]["max_price"], {"prompt": 0.1, "completion": 0.1})
        self.assertEqual(body["plugins"], [])
        self.assertNotIn("tools", body)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])

    def test_fresh_plan_has_conservative_bound_and_no_post(self):
        api = FakeAPI()
        plan = smoke.preflight(SENTINEL, smoke.build_request(), api)
        # Independent arithmetic: (131072 + 2048) * 1e-7 * 1.10.
        self.assertEqual(smoke.money(plan["conservative_token_cost_ceiling_usd"]), smoke.Decimal("0.0146432"))
        self.assertEqual(api.posts, 0)
        self.assertNotIn(SENTINEL, json.dumps(plan))

    def test_unlimited_reset_exhausted_expired_or_management_key_blocks(self):
        variants = [("limit", None), ("limit", 1), ("limit", True), ("limit_reset", "daily"),
                    ("limit_remaining", 0), ("limit_remaining", 0.001), ("limit_remaining", 1),
                    ("expires_at", "2000-01-01T00:00:00+00:00"), ("is_management_key", True)]
        for name, value in variants:
            api = FakeAPI()
            api.key[name] = value
            with self.subTest(name=name, value=value), self.assertRaises(smoke.Blocked):
                smoke.preflight(SENTINEL, smoke.build_request(), api)
            self.assertEqual(api.posts, 0)

    def test_price_nonfinite_addon_and_capability_guards(self):
        for kind in ("expensive", "nan", "negative", "addon", "cache", "tag", "context", "completion", "parameters", "status"):
            api = FakeAPI()
            endpoint = api.endpoint["endpoints"][0]
            if kind == "expensive":
                endpoint["pricing"]["prompt"] = "0.00001"
            elif kind == "nan":
                endpoint["pricing"]["completion"] = "NaN"
            elif kind == "negative":
                endpoint["pricing"]["prompt"] = "-1"
            elif kind == "addon":
                endpoint["pricing"]["request"] = "0.01"
            elif kind == "cache":
                endpoint["pricing"]["input_cache_read"] = "0.001"
            elif kind == "tag":
                endpoint["tag"] = "mistral"
            elif kind == "context":
                endpoint["context_length"] = 100000000
            elif kind == "completion":
                endpoint["max_completion_tokens"] = 100
            elif kind == "parameters":
                endpoint["supported_parameters"].remove("structured_outputs")
            else:
                endpoint["status"] = 1
            with self.subTest(kind=kind), self.assertRaises(smoke.Blocked):
                smoke.preflight(SENTINEL, smoke.build_request(), api)

    def test_money_rejects_bool_none_nonfinite(self):
        for value in (True, None, float("inf"), "NaN", -1, "bad"):
            with self.subTest(value=value), self.assertRaises(smoke.Blocked):
                smoke.money(value)
        self.assertEqual(smoke.money(0), 0)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-smoke-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.api = FakeAPI()

    def run_round(self):
        return smoke.run_once(SENTINEL, self.root, self.api)

    def test_one_post_persists_pending_review_and_real_cli_can_read_it(self):
        def inspect_reservation():
            claim = offline_eval.read_json(self.root / "claim.json")
            self.assertEqual(claim["reserved_usd"], "0.10")
            self.assertNotIn(SENTINEL, json.dumps(claim))

        self.api.before_post = inspect_reservation
        result = self.run_round()
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(result["status"], "pending_review")
        self.assertEqual(result["cost_usd"], "0.00014")
        self.assertEqual(result["cost_status"], "reported_by_provider")
        stored = offline_eval.read_json(self.root / "review-0.json")
        self.assertEqual(stored["events"], [])
        self.assertEqual(stored["draft_origin_declared"], "model_output")
        self.assertIsNone(stored["draft"]["fields"]["continuous_current"]["value"])
        output = subprocess.run([sys.executable, str(ROOT / "local_review.py"), "show", "--review", str(self.root / "review-0.json")], capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(output.returncode, 0, output.stderr)
        report = json.loads(output.stdout)
        self.assertEqual(report["extraction_status"], "pending_review")
        self.assertEqual(report["engineering_suitability"], "not_evaluated")
        self.assertNotIn("must-not-be-saved", (self.root / "response.json").read_text(encoding="utf-8"))

    def test_repeat_or_corrupt_claim_blocks_before_any_network(self):
        (self.root / "claim.json").write_text("partial crashed claim", encoding="utf-8")
        with self.assertRaisesRegex(smoke.Blocked, "already_attempted"):
            self.run_round()
        self.assertEqual(self.api.calls, [])

    def test_success_cannot_be_resubmitted(self):
        self.run_round()
        with self.assertRaises(smoke.Blocked):
            self.run_round()
        self.assertEqual(self.api.posts, 1)

    def test_exclusive_claim_prevents_racing_invocation(self):
        original_preflight = smoke.preflight

        def racing_preflight(*args):
            preview = original_preflight(*args)
            (self.root / "claim.json").write_text("other invocation claimed", encoding="utf-8")
            return preview

        with patch.object(smoke, "preflight", side_effect=racing_preflight), self.assertRaises(FileExistsError):
            self.run_round()
        self.assertEqual(self.api.posts, 0)
        self.assertEqual((self.root / "claim.json").read_text(), "other invocation claimed")

    def test_timeout_no_retry_keeps_unknown_cost_and_reservation(self):
        self.api.failure = smoke.Blocked("transport_or_response_error")
        result = self.run_round()
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(result["cost_status"], "pending_reconciliation")
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["reservation_usd"], "0.10")
        self.assertFalse((self.root / "review-0.json").exists())
        with self.assertRaises(smoke.Blocked):
            self.run_round()
        self.assertEqual(self.api.posts, 1)

    def test_missing_cost_still_creates_pending_review_but_not_zero_cost(self):
        del self.api.response["usage"]["cost"]
        result = self.run_round()
        self.assertTrue(result["review_created"])
        self.assertIsNone(result["cost_usd"])
        self.assertEqual(result["cost_status"], "pending_reconciliation")
        self.assertEqual(result["reservation_usd"], "0.10")

    def test_invalid_output_refusal_truncation_wrong_route_no_review(self):
        for kind in ("json", "number", "unit", "refusal", "length", "tool", "model", "provider", "error"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="bom-smoke-invalid-") as temp:
                api = FakeAPI()
                message = api.response["choices"][0]["message"]
                if kind == "json":
                    message["content"] = "not JSON"
                elif kind in ("number", "unit"):
                    answer = output_draft()
                    answer["fields"]["input_max"]["value" if kind == "number" else "unit"] = True if kind == "number" else "mV"
                    message["content"] = json.dumps(answer)
                elif kind == "refusal":
                    message["refusal"] = "Refused"
                elif kind == "length":
                    api.response["choices"][0]["finish_reason"] = "length"
                elif kind == "tool":
                    message["tool_calls"] = [{"name": "do_not_execute"}]
                elif kind in ("model", "provider"):
                    api.response[kind] = "unexpected"
                else:
                    api.response["error"] = {"message": SENTINEL}
                result = smoke.run_once(SENTINEL, temp, api)
                self.assertEqual(api.posts, 1)
                self.assertFalse(result["review_created"])
                self.assertFalse(Path(temp, "review-0.json").exists())
                self.assertEqual(result["cost_usd"], "0.00014")
                self.assertNotIn(SENTINEL, Path(temp, "response.json").read_text(encoding="utf-8"))

    def test_source_changed_during_call_preserves_response_not_review(self):
        inputs = offline_eval.read_json(offline_eval.DATA_DIR / "inputs.json")
        data_dir = self.root / "data"
        data_dir.mkdir()
        path = data_dir / "inputs.json"
        path.write_text(json.dumps(inputs), encoding="utf-8")

        def change_source():
            inputs["cases"][0]["blocks"][1]["text"] += " New request revision."
            path.write_text(json.dumps(inputs), encoding="utf-8")

        self.api.before_post = change_source
        result = smoke.run_once(SENTINEL, self.root / "run", self.api, data_dir)
        self.assertEqual(result["status"], "source_changed_during_inference")
        self.assertTrue((self.root / "run/response.json").exists())
        self.assertFalse((self.root / "run/review-0.json").exists())

    def test_local_write_failure_does_not_cause_second_post(self):
        original_write = smoke.write_new

        def fail_response(path, value):
            if Path(path).name == "response.json":
                raise OSError("unit test disk failure")
            return original_write(path, value)

        with patch.object(smoke, "write_new", side_effect=fail_response):
            result = self.run_round()
        self.assertEqual(result["status"], "response_or_local_processing_failed")
        self.assertEqual(result["cost_status"], "pending_reconciliation")
        with self.assertRaises(smoke.Blocked):
            self.run_round()
        self.assertEqual(self.api.posts, 1)

    def test_budget_blocked_does_not_claim_or_post(self):
        self.api.key["limit_remaining"] = 0
        with self.assertRaises(smoke.Blocked):
            self.run_round()
        self.assertEqual(self.api.posts, 0)
        self.assertFalse((self.root / "claim.json").exists())


class TransportTests(unittest.TestCase):
    def test_only_fixed_api_origin_and_no_redirects(self):
        self.assertIsNone(smoke.NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid"))
        with self.assertRaises(smoke.Blocked):
            smoke.api_request("GET", "https://elsewhere.invalid", SENTINEL)

    def test_transport_headers_body_and_timeout(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self, _):
                return b'{"data":{}}'

        with patch.object(smoke.urllib.request, "build_opener") as builder:
            builder.return_value.open.return_value = Response()
            smoke.api_request("POST", "/chat/completions", SENTINEL, {"model": smoke.MODEL})
            args, kwargs = builder.return_value.open.call_args
            request = args[0]
            self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer " + SENTINEL)
            self.assertNotIn(SENTINEL, request.data.decode())
            self.assertEqual(kwargs["timeout"], 45)
            self.assertEqual(builder.return_value.open.call_count, 1)

    def test_http_errors_do_not_expose_body_or_headers_and_no_retry(self):
        error = urllib.error.HTTPError("https://openrouter.ai/api/v1/key", 401, SENTINEL, {"Authorization": SENTINEL}, io.BytesIO(SENTINEL.encode()))
        with patch.object(smoke.urllib.request, "build_opener") as builder:
            builder.return_value.open.side_effect = error
            with self.assertRaisesRegex(smoke.Blocked, "^http_401$"):
                smoke.api_request("GET", "/key", SENTINEL)
            self.assertEqual(builder.return_value.open.call_count, 1)

    def test_scrubbed_response_excludes_unknown_account_data(self):
        raw = completion()
        raw["choices"][0]["message"]["content"] = SENTINEL
        safe = smoke.response_subset(raw, SENTINEL)
        self.assertNotIn(SENTINEL, json.dumps(safe))
        self.assertNotIn("user_id", safe)

    def test_default_cli_is_plan_and_never_run(self):
        with patch.object(sys, "argv", ["openrouter_smoke.py"]), patch.object(smoke, "load_key", return_value=SENTINEL), patch.object(smoke, "preflight", return_value={}), patch.object(smoke, "run_once") as run, patch("sys.stdout", new_callable=io.StringIO) as stdout:
            self.assertEqual(smoke.main(), 0)
            self.assertFalse(json.loads(stdout.getvalue())["paid_request_sent"])
            run.assert_not_called()

    def test_usage_above_budget_is_not_marked_normal(self):
        self.assertEqual(smoke.accounting({"usage": {"cost": 0.2}})["cost_status"], "budget_violation")
        self.assertIsNone(smoke.accounting({"usage": {"cost": True}})["cost_usd"])


if __name__ == "__main__":
    unittest.main()
