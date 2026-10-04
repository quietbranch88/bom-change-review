"""Substituted provider plus real local storage; not live model-quality evidence."""

import copy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_review
import openrouter_smoke as smoke
import pcb_followup
import private_diagnostics
import supplemental_intake as intake
import supplemental_model as model
from test_local_review import new_review
from test_openrouter_smoke import endpoint_metadata, key_metadata, SENTINEL


class FakeProvider:
    def __init__(self, record):
        self.posts = 0
        self.calls = []
        self.endpoint = endpoint_metadata()
        # Historical-price fixture, NOT a claim about the live endpoint price.
        self.key = key_metadata()
        self.before_post = None
        self.before_key = None
        self.failure = None
        self.key_error = False
        self.draft = {
            "source_id": record["source"]["source_id"], "source_sha256": record["source_sha256"],
            "permission": {"status": "allowed", "quotes": ["可以改 PCB"]},
            "constraints": [{"quote": "接頭位置不能動", "interpretation": "接頭位置固定"}],
            "open_questions": [{"quote": "費用要先給主管確認", "interpretation": "費用待確認"}],
        }
        self.response = {
            "id": "synthetic-generation", "model": "mistralai/ministral-3b-2512", "provider": "Mistral",
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(self.draft, ensure_ascii=False)}}],
            "usage": {"cost": 0.0001, "prompt_tokens": 1000, "completion_tokens": 100,
                      "total_tokens": 1100, "is_byok": False},
        }

    def __call__(self, method, path, key=None, body=None):
        self.calls.append((method, path))
        if method == "GET":
            if path == "/key":
                if self.key_error:
                    raise smoke.Blocked("http_401")
                if self.before_key:
                    self.before_key()
                return {"data": copy.deepcopy(self.key)}
            return {"data": copy.deepcopy(self.endpoint)}
        self.posts += 1
        if self.before_post:
            self.before_post()
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.response)


class SupplementalModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-supp-model-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        waiting = pcb_followup.advance(pcb_followup.create(new_review()), "ask")
        self.record = intake.capture(waiting, "SIM-1", "可以改 PCB，但接頭位置不能動；費用要先給主管確認。")
        self.source = self.root / "source.json"
        local_review.write_new(self.source, self.record)
        self.original = self.source.read_bytes()
        self.api = FakeProvider(self.record)

    def run_round(self, directory="round"):
        return model.run_once(SENTINEL, source_path=self.source, run_dir=self.root / directory,
                              transport=self.api, authorized_input=local_review.fingerprint(self.record))

    def test_request_is_source_only_strict_pinned_and_offline(self):
        with patch.object(smoke, "load_key", side_effect=AssertionError("no keys offline")):
            plan = model.plan(self.record)
        request = plan["request"]
        self.assertEqual(request["model"], "mistralai/ministral-3b-2512")
        self.assertEqual(request["provider"]["only"], ["mistral/zdr"])
        self.assertFalse(request["provider"]["allow_fallbacks"])
        self.assertTrue(request["provider"]["require_parameters"])
        self.assertTrue(request["provider"]["zdr"])
        self.assertEqual(request["provider"]["data_collection"], "deny")
        self.assertEqual(request["plugins"], [])
        self.assertNotIn("tools", request)
        self.assertEqual(request["max_tokens"], 2048)
        self.assertFalse(plan["paid_request_sent"])
        self.assertEqual(plan["budget_usd"], "0.02")
        payload = json.loads(request["messages"][1]["content"])
        self.assertEqual(payload, {"source": {"source_id": "SIM-1", "text": self.record["source"]["text"]},
                                   "source_sha256": self.record["source_sha256"]})
        self.assertNotIn("LT8301ESS", json.dumps(request))
        self.assertNotIn("uniqueItems", json.dumps(request))
        schema = request["response_format"]["json_schema"]
        self.assertTrue(schema["strict"])
        self.assertEqual(set(schema["schema"]["required"]),
                         {"source_id", "source_sha256", "permission", "constraints", "open_questions"})

    def test_success_creates_model_pending_without_updating_base(self):
        def claim_before_post():
            claim = intake.read_json(self.root / "round/claim.json")
            self.assertEqual(claim["input"], self.record)
            self.assertEqual(claim["reserved_usd"], "0.02")
            self.assertEqual(claim["authorization"], "supplement_once_20260927_USD0.02_no_retry")
        self.api.before_post = claim_before_post
        result = self.run_round()
        self.assertEqual(result["status"], "pending_review")
        self.assertTrue(result["review_created"])
        self.assertEqual(result["cost_status"], "reported_by_provider")
        self.assertEqual(result["cost_usd"], "0.0001")
        self.assertEqual(self.api.posts, 1)
        pending = intake.read_json(self.root / "round/pending.json")
        self.assertEqual(pending["draft"], self.api.draft)
        self.assertEqual(pending["draft_origin"], "model_output")
        self.assertEqual(pending["followup"], self.record["followup"])
        self.assertEqual(self.source.read_bytes(), self.original)
        process = subprocess.run(
            [sys.executable, str(model.ROOT / "supplemental_intake.py"), "show", "--record",
             str(self.root / "round/pending.json")], capture_output=True, text=True,
            encoding="utf-8", timeout=10, check=False)
        self.assertEqual(process.returncode, 0, process.stderr)
        readback = json.loads(process.stdout)
        self.assertEqual(readback["state"], "pending_review")
        self.assertEqual(readback["draft_origin"], "model_output")
        self.assertEqual(readback["semantic_review"], "unverified")
        self.assertEqual(readback["followup_state_unchanged"], "waiting_for_confirmation")
        self.assertFalse(readback["automatic_transition_allowed"])

    def test_demo_origin_remains_compatible_and_no_approval_origin(self):
        self.assertEqual(intake.attach(self.record, self.api.draft)["draft_origin"], "demo_not_model_output")
        with self.assertRaises(ValueError):
            intake.attach(self.record, self.api.draft, origin="human_approved")

    def test_second_run_is_blocked_before_any_network(self):
        self.run_round()
        before = list(self.api.calls)
        with self.assertRaisesRegex(smoke.Blocked, "round_already_attempted"):
            self.run_round()
        self.assertEqual(self.api.calls, before)
        self.assertEqual(self.api.posts, 1)

    def test_historical_price_passes_new_budget_but_not_old_budget(self):
        preview = model.preflight(SENTINEL, model.build_request(self.record), self.api)
        self.assertEqual(Decimal(preview["conservative_token_cost_ceiling_usd"]), Decimal("0.0146432"))
        self.assertEqual(preview["approved_round_budget_usd"], "0.02")
        self.assertEqual(Decimal(preview["project_budget_usd"]), Decimal("0.10"))
        with patch.object(model, "BUDGET", Decimal("0.01")):
            with self.assertRaisesRegex(smoke.Blocked, "supplement_budget_ceiling_exceeded"):
                self.run_round()
        self.assertEqual(self.api.posts, 0)
        self.assertFalse((self.root / "round/claim.json").exists())

    def test_round_budget_boundary(self):
        for ceiling, blocked in (("0.02", False), ("0.0200001", True)):
            # Isolate this round's policy from the separately tested project preflight.
            preview = {"conservative_token_cost_ceiling_usd": ceiling, "approved_budget_usd": "0.10"}
            with self.subTest(ceiling=ceiling), patch.object(smoke, "preflight", return_value=preview):
                if blocked:
                    with self.assertRaisesRegex(smoke.Blocked, "supplement_budget_ceiling_exceeded"):
                        model.preflight(SENTINEL, {})
                else:
                    self.assertEqual(model.preflight(SENTINEL, {})["approved_round_budget_usd"], "0.02")
        self.assertEqual(model.accounting({"usage": {"cost": "0.02"}})["cost_status"], "reported_by_provider")
        self.assertEqual(model.accounting({"usage": {"cost": "0.0200001"}})["cost_status"], "budget_violation")

    def test_unauthenticated_and_expired_key_never_post(self):
        self.api.key_error = True
        with self.assertRaisesRegex(smoke.Blocked, "http_401"):
            self.run_round()
        self.api.key_error = False
        self.api.key["expires_at"] = "2000-01-01T00:00:00+00:00"
        with self.assertRaisesRegex(smoke.Blocked, "key_budget_or_expiry"):
            self.run_round()
        self.assertEqual(self.api.posts, 0)
        self.assertFalse((self.root / "round/claim.json").exists())

    def test_pinned_input_and_existing_artifact_guards(self):
        with self.assertRaisesRegex(smoke.Blocked, "source_outside_authorized"):
            model.run_once(SENTINEL, source_path=self.source, run_dir=self.root / "round",
                           transport=self.api, authorized_input="not-the-authorized-input")
        self.assertEqual(self.api.calls, [])
        (self.root / "round").mkdir()
        local_review.write_new(self.root / "round/response.json", {"synthetic": True})
        with self.assertRaisesRegex(smoke.Blocked, "round_has_existing_artifacts"):
            self.run_round()
        self.assertEqual(self.api.calls, [])

    def test_source_change_before_post_is_blocked(self):
        def replace_source():
            self.source.write_text("null", encoding="utf-8")
        self.api.before_key = replace_source
        with self.assertRaisesRegex(smoke.Blocked, "source_changed_before_submission"):
            self.run_round()
        self.assertEqual(self.api.posts, 0)

    def test_source_change_during_post_never_promotes(self):
        self.api.before_post = lambda: self.source.write_text("null", encoding="utf-8")
        result = self.run_round()
        self.assertEqual(result["status"], "source_changed_during_inference")
        self.assertFalse(result["review_created"])
        self.assertFalse((self.root / "round/pending.json").exists())

    def test_invalid_model_outputs_never_create_pending(self):
        baseline = copy.deepcopy(self.api.response)
        for index, mode in enumerate(("quote", "source", "refusal", "truncated", "model", "provider", "role", "tool", "json")):
            self.api.response = copy.deepcopy(baseline)
            choice = self.api.response["choices"][0]
            if mode in ("quote", "source"):
                draft = copy.deepcopy(self.api.draft)
                if mode == "quote":
                    draft["permission"]["quotes"] = ["不在原文"]
                else:
                    draft["source_sha256"] = "wrong"
                choice["message"]["content"] = json.dumps(draft)
            elif mode == "refusal":
                choice["message"]["refusal"] = "synthetic refusal"
            elif mode == "truncated":
                choice["finish_reason"] = "length"
            elif mode in ("model", "provider"):
                self.api.response[mode] = "unexpected"
            elif mode == "role":
                choice["message"]["role"] = "user"
            elif mode == "tool":
                choice["message"]["tool_calls"] = [{"function": "do-not-run"}]
            else:
                choice["message"]["content"] = "bad json"
            with self.subTest(mode=mode):
                result = self.run_round(f"bad-{index}")
                self.assertFalse(result["review_created"])
                self.assertFalse((self.root / f"bad-{index}/pending.json").exists())
        self.assertEqual(self.api.posts, 9)

    def test_unknown_or_excessive_cost_keeps_reservation_and_no_pending(self):
        for index, cost in enumerate((None, 0.03)):
            self.api.response["usage"]["cost"] = cost
            result = self.run_round(f"cost-{index}")
            self.assertEqual(result["status"], "unreconciled_or_excessive_cost")
            self.assertEqual(result["reservation_usd"], "0.02")
            self.assertFalse(result["review_created"])
            self.assertFalse((self.root / f"cost-{index}/pending.json").exists())

    def test_failed_post_has_no_retry_and_retains_safe_private_diagnostic(self):
        private = private_diagnostics.seal(b"synthetic detail", SENTINEL)
        self.api.failure = smoke.Blocked("http_400", diagnostic={"http_status": 400, "message": "[WITHHELD]"},
                                        private_archive=private)
        result = self.run_round()
        self.assertEqual(result["status"], "http_400")
        self.assertEqual(result["private_archive_status"], "saved")
        self.assertEqual(self.api.posts, 1)
        self.assertTrue((self.root / "round/claim.json").exists())
        self.assertNotIn("synthetic detail", json.dumps(result))
        self.assertFalse((self.root / "round/pending.json").exists())
        with self.assertRaises(smoke.Blocked):
            self.run_round()
        self.assertEqual(self.api.posts, 1)

    def test_partial_local_write_keeps_claim_and_blocks_resubmission(self):
        real_write = smoke.write_new
        def fail_response(path, value):
            if Path(path).name == "response.json":
                raise OSError("synthetic disk failure")
            real_write(path, value)
        with patch.object(smoke, "write_new", side_effect=fail_response):
            result = self.run_round()
        self.assertEqual(result["status"], "response_or_local_processing_failed")
        self.assertEqual(result["cost_status"], "pending_reconciliation")
        self.assertTrue((self.root / "round/claim.json").exists())
        with self.assertRaises(smoke.Blocked):
            self.run_round()
        self.assertEqual(self.api.posts, 1)


if __name__ == "__main__":
    unittest.main()
