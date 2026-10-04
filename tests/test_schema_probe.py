"""Synthetic probe caller acceptance; no real provider or key."""
import copy
import io
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import schema_probe as probe
from test_openrouter_smoke import FakeAPI, SENTINEL


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="bom-schema-test-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.api = FakeAPI()
        self.api.response["choices"][0]["message"]["content"] = json.dumps(
            {"value": 14, "unit": "V", "evidence_ids": ["Q1"]})

    def test_five_success_and_repeat_no_network(self):
        result = probe.run_batch(SENTINEL, self.path, self.api)
        self.assertEqual([r["status"] for r in result["results"]], ["passed"] * 5)
        self.assertEqual(self.api.posts, 5)
        self.assertEqual(result["reserved_token_ceiling_usd"], "0.073216000")
        self.assertFalse(result["review_created"])
        before = list(self.api.calls)
        with self.assertRaisesRegex(probe.smoke.Blocked, "batch_already_claimed"):
            probe.run_batch(SENTINEL, self.path, self.api)
        self.assertEqual(self.api.calls, before)

    def test_baseline_failure_stops(self):
        self.api.failure = probe.smoke.Blocked("http_400", private_archive=probe.private.seal(b"synthetic"))
        result = probe.run_batch(SENTINEL, self.path, self.api)
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(result["status"], "stopped_baseline_failed")
        self.assertFalse((self.path / "M01").exists())

    def test_later_archived_400_is_comparative_not_retry(self):
        def transport(method, path, key=None, body=None):
            result = self.api(method, path, key, body)
            if method == "POST" and self.api.posts == 2:
                raise probe.smoke.Blocked("http_400", private_archive=probe.private.seal(b"synthetic"))
            return result
        result = probe.run_batch(SENTINEL, self.path, transport)
        self.assertEqual(self.api.posts, 5)
        self.assertEqual([r["id"] for r in result["results"]], list(probe.IDS))
        self.assertEqual(result["results"][1]["status"], "http_400")

    def test_unknown_cost_and_transport_stop(self):
        for kind in ("cost", "transport", "wrong"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                api = copy.deepcopy(self.api)
                if kind == "cost":
                    del api.response["usage"]["cost"]
                elif kind == "transport":
                    api.failure = probe.smoke.Blocked("transport_or_response_error")
                else:
                    api.response["choices"][0]["message"]["content"] = '{"value":15,"unit":"V","evidence_ids":["Q1"]}'
                result = probe.run_batch(SENTINEL, directory, api)
                self.assertEqual(api.posts, 1)
                self.assertNotEqual(result["status"], "completed")

    def test_budget_reservation_stops_before_second_post(self):
        self.api.key["limit_remaining"] = 0.02
        result = probe.run_batch(SENTINEL, self.path, self.api)
        self.assertEqual(self.api.posts, 1)
        self.assertEqual(result["status"], "batch_reservation_exceeds_available_budget")

    def test_crypto_failure_and_corrupt_claim_no_network(self):
        with patch.object(probe.private, "seal", side_effect=OSError("synthetic")), self.assertRaises(OSError):
            probe.run_batch(SENTINEL, self.path, self.api)
        self.assertEqual(self.api.calls, [])
        (self.path / "claim.json").write_text("partial", encoding="utf-8")
        with self.assertRaises(probe.smoke.Blocked):
            probe.run_batch(SENTINEL, self.path, self.api)
        self.assertEqual(self.api.calls, [])

    def test_plan_no_key_or_network(self):
        with patch.object(sys, "argv", ["schema_probe.py"]), patch.object(probe.smoke, "load_key") as key, patch.object(probe, "run_batch") as run, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(probe.main(), 0)
            self.assertFalse(json.loads(output.getvalue())["paid_request_sent"])
            key.assert_not_called()
            run.assert_not_called()

    def test_request_differences_and_answer_validation(self):
        requests = probe.requests()
        for name, body in requests:
            self.assertEqual(body["provider"], probe.smoke.build_request()["provider"])
            self.assertEqual(body["messages"], requests[0][1]["messages"])
            self.assertNotIn("accepted_by", json.dumps(body))
        for answer in ({"value": True, "unit": "V", "evidence_ids": ["Q1"]},
                       {"value": 14, "unit": "V", "evidence_ids": ["Q1", "Q1"]}):
            response = probe.smoke.response_subset(self.api.response, SENTINEL)
            response["choices"][0]["message"]["content"] = json.dumps(answer)
            with self.assertRaises(probe.smoke.Blocked):
                probe.validate_answer(response)

    def test_claim_race_and_result_disk_failure_never_retry(self):
        original = probe.smoke.write_new
        for filename in ("claim.json", "result.json"):
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as directory:
                api = copy.deepcopy(self.api)

                def failing_write(path, value):
                    if Path(path).name == filename:
                        raise FileExistsError("synthetic write collision")
                    original(path, value)

                with patch.object(probe.smoke, "write_new", side_effect=failing_write):
                    if filename == "claim.json":
                        with self.assertRaises(FileExistsError):
                            probe.run_batch(SENTINEL, directory, api)
                        self.assertEqual(api.posts, 0)
                    else:
                        result = probe.run_batch(SENTINEL, directory, api)
                        self.assertEqual(api.posts, 1)
                        self.assertEqual(result["status"], "stopped_local_or_preflight_failure_check_claims")
                        with self.assertRaises(probe.smoke.Blocked):
                            probe.run_batch(SENTINEL, directory, api)

    def test_loopback_http_archive_and_baseline_stop(self):
        class Handler(BaseHTTPRequestHandler):
            posts = 0

            def do_POST(self):
                type(self).posts += 1
                self.rfile.read(int(self.headers["Content-Length"]))
                payload = b'{"error":{"message":"Invalid structured output syntax"}}'
                self.send_response(400)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        original = probe.smoke.urllib.request.Request

        def local_request(url, **kwargs):
            self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
            return original(f"http://127.0.0.1:{server.server_port}/probe", **kwargs)

        def transport(method, path, key=None, body=None):
            return self.api(method, path, key, body) if method == "GET" else probe.smoke.api_request(method, path, key, body)

        try:
            with patch.object(probe.smoke.urllib.request, "Request", side_effect=local_request):
                result = probe.run_batch(SENTINEL, self.path, transport)
            self.assertEqual(result["status"], "stopped_baseline_failed")
            self.assertEqual(result["results"][0]["private_archive_status"], "saved_and_decrypted")
            archive = json.loads((self.path / "M00/error.private.json").read_text())
            self.assertEqual(json.loads(probe.private.unseal(archive))["error"]["message"],
                             "Invalid structured output syntax")
            self.assertEqual(json.loads((self.path / "summary.json").read_text()), result)
            self.assertEqual(Handler.posts, 1)
            self.assertFalse((self.path / "M01").exists())
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
