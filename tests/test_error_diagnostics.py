"""Synthetic error contracts; no live provider requests or real credentials."""

import io
import json
import sys
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

import openrouter_smoke as smoke
from test_openrouter_smoke import FakeAPI, SENTINEL


def envelope(message, **metadata):
    return json.dumps({"error": {"code": 400, "message": message, "metadata": metadata}}).encode()


class DiagnosticTests(unittest.TestCase):
    def capture(self, raw, key=SENTINEL):
        stream = io.BytesIO(raw)
        error = urllib.error.HTTPError("https://openrouter.ai/private", 400, SENTINEL,
                                       {"Authorization": SENTINEL}, stream)
        with patch.object(smoke.urllib.request, "build_opener") as builder:
            builder.return_value.open.side_effect = error
            with self.assertRaises(smoke.Blocked) as raised:
                smoke.api_request("POST", "/chat/completions", key, {})
            self.assertEqual(builder.return_value.open.call_count, 1)
        self.assertEqual(str(raised.exception), "http_400")
        return getattr(raised.exception, "diagnostic", None), stream

    def test_supported_error_survives_http_handler(self):
        diagnostic, stream = self.capture(envelope("Unsupported schema keyword: uniqueItems",
                                                  provider_name="Mistral", error_type="invalid_request"))
        self.assertEqual(diagnostic, {"http_status": 400,
                                     "message": "Unsupported schema keyword: uniqueItems",
                                     "body_status": "parsed", "message_status": "recognized",
                                     "provider": "Mistral", "error_type": "invalid_request"})
        self.assertTrue(stream.closed)

    def test_nested_provider_error_has_only_canonical_summary(self):
        vendor = json.dumps({"object": "error", "message": "Received unsupported keyword `uniqueItems` in schema.",
                             "type": "invalid_request_error", "private": SENTINEL})
        diagnostic, _ = self.capture(envelope("Provider returned error", raw=vendor, provider_name="Mistral"))
        self.assertEqual(diagnostic.get("provider_message"), "Unsupported schema keyword: uniqueItems")
        self.assertNotIn(SENTINEL, json.dumps(diagnostic))
        self.assertNotIn("raw", diagnostic)

    def test_nested_unknown_private_or_malformed_body_is_not_exposed(self):
        for raw in (SENTINEL, "{" , [], {"message": SENTINEL},
                    json.dumps({"message": "Client alice@example.invalid secret"}),
                    json.dumps({"message": "Received unsupported keyword `private_field` in schema."}),
                    json.dumps({"metadata": {"raw": json.dumps({"message": "Bad Request"})}})):
            diagnostic, _ = self.capture(envelope("Provider returned error", raw=raw))
            self.assertNotIn("provider_message", diagnostic)
            self.assertNotIn(SENTINEL, json.dumps(diagnostic))

    def test_sensitive_and_arbitrary_messages_are_withheld(self):
        messages = [SENTINEL, "Authorization: Bearer private-token", "sk-or-v1-test-only-fake",
                    "Contact alice@example.invalid; GPS 25.03,121.56", "客戶姓名：測試人",
                    "Unsupported schema keyword: uniqueItems\nAuthorization: private-token",
                    "Unsupported schema keyword: customer_secret_field", "x" * 501]
        for message in messages:
            with self.subTest(message=message):
                diagnostic, _ = self.capture(envelope(message, raw="private document", provider_name=SENTINEL,
                                                       error_type=SENTINEL, provider_code=SENTINEL))
                self.assertEqual(diagnostic["message"], "[WITHHELD]")
                self.assertEqual(diagnostic["message_status"], "withheld")
                self.assertEqual(set(diagnostic), {"http_status", "message", "body_status", "message_status"})
                self.assertNotIn(SENTINEL, json.dumps(diagnostic))
                self.assertLessEqual(len(diagnostic["message"]), 500)

    def test_secret_redaction_precedes_message_classification(self):
        diagnostic, _ = self.capture(envelope("Unsupported schema keyword: uniqueItems"), key="uniqueItems")
        self.assertEqual(diagnostic["message"], "[WITHHELD]")
        self.assertNotIn("uniqueItems", json.dumps(diagnostic))

    def test_bad_shapes_and_bodies_fail_closed(self):
        bodies = [b"<html>private error</html>", b"\xff", b"[]", b"null", b'{"error":[]}',
                  b'{"error":{"message":"a","message":"b"}}', b"[" * 1500 + b"]" * 1500]
        for raw in bodies:
            with self.subTest(raw=raw[:30]):
                diagnostic, stream = self.capture(raw)
                self.assertEqual(diagnostic["body_status"], "unavailable")
                self.assertEqual(diagnostic["message"], "[WITHHELD]")
                self.assertTrue(stream.closed)

    def test_wrong_message_and_metadata_types_fail_closed(self):
        for message in (None, [], {}, 400):
            diagnostic, _ = self.capture(json.dumps({"error": {"message": message, "metadata": []}}).encode())
            self.assertEqual(diagnostic["message"], "[WITHHELD]")
            self.assertNotIn("error_type", diagnostic)

    def test_read_cap_and_oversize(self):
        class TrackingStream(io.BytesIO):
            sizes = []

            def read(self, size=-1):
                self.sizes.append(size)
                return super().read(size)

        stream = TrackingStream(b"x" * 10000)
        error = urllib.error.HTTPError("https://openrouter.ai/", 400, "", {}, stream)
        with patch.object(smoke.urllib.request, "build_opener") as builder:
            builder.return_value.open.side_effect = error
            with self.assertRaises(smoke.Blocked) as raised:
                smoke.api_request("POST", "/chat/completions", SENTINEL, {})
        self.assertEqual(stream.sizes, [8193])
        self.assertTrue(stream.closed)
        self.assertEqual(raised.exception.diagnostic["body_status"], "too_large")

    def test_error_body_read_failure_preserves_http_status(self):
        class BrokenStream(io.BytesIO):
            def read(self, _):
                raise OSError(SENTINEL)

        stream = BrokenStream()
        error = urllib.error.HTTPError("https://openrouter.ai/", 400, "", {}, stream)
        with patch.object(smoke.urllib.request, "build_opener") as builder:
            builder.return_value.open.side_effect = error
            with self.assertRaises(smoke.Blocked) as raised:
                smoke.api_request("GET", "/key", SENTINEL)
        self.assertEqual(str(raised.exception), "http_400")
        self.assertEqual(raised.exception.diagnostic["body_status"], "unavailable")
        self.assertTrue(stream.closed)

    def test_fixed_messages_and_no_key_are_supported(self):
        diagnostic, _ = self.capture(envelope("Provider returned error"), key=None)
        self.assertEqual(diagnostic["message"], "Provider returned error")
        self.assertEqual(diagnostic["message_status"], "recognized")

    def test_incomplete_http_body_does_not_escape_as_exception(self):
        class IncompleteStream(io.BytesIO):
            def read(self, _):
                raise IncompleteRead(SENTINEL.encode(), 100)

        stream = IncompleteStream()
        error = urllib.error.HTTPError("https://openrouter.ai/", 400, "", {}, stream)
        with patch.object(smoke.urllib.request, "build_opener") as builder:
            builder.return_value.open.side_effect = error
            with self.assertRaises(smoke.Blocked) as raised:
                smoke.api_request("POST", "/chat/completions", SENTINEL, {})
        self.assertEqual(str(raised.exception), "http_400")
        self.assertEqual(raised.exception.diagnostic["body_status"], "unavailable")
        self.assertTrue(stream.closed)

    def test_exact_body_limit_is_accepted_but_one_more_byte_is_not(self):
        raw = envelope("Rate limit exceeded")
        at_limit = raw + b" " * (8192 - len(raw))
        diagnostic, _ = self.capture(at_limit)
        self.assertEqual(diagnostic["message"], "Rate limit exceeded")
        diagnostic, _ = self.capture(at_limit + b" ")
        self.assertEqual(diagnostic["body_status"], "too_large")

    def test_preflight_cli_includes_safe_diagnostic_without_post(self):
        diagnostic, _ = self.capture(envelope("Invalid API key"))
        failure = smoke.Blocked("http_400", diagnostic=diagnostic)
        with patch.object(sys, "argv", ["openrouter_smoke.py", "plan"]), \
                patch.object(smoke, "load_key", return_value=SENTINEL), \
                patch.object(smoke, "preflight", side_effect=failure), \
                patch.object(smoke, "run_once") as run, patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(smoke.main(), 2)
        result = json.loads(output.getvalue())
        self.assertEqual(result["diagnostic"]["message"], "Invalid API key")
        self.assertFalse(result["automatic_retry"])
        self.assertNotIn(SENTINEL, output.getvalue())
        run.assert_not_called()

    def test_loopback_http_error_to_saved_result_and_repeat_guard(self):
        class Handler(BaseHTTPRequestHandler):
            posts = 0

            def do_POST(self):
                type(self).posts += 1
                self.rfile.read(int(self.headers["Content-Length"]))
                payload = envelope("Unsupported schema keyword: uniqueItems", raw=SENTINEL,
                                   provider_name="Mistral", error_type="invalid_request")
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        original_request = smoke.urllib.request.Request
        fake = FakeAPI()

        def local_request(url, **kwargs):
            self.assertEqual(url, "https://openrouter.ai/api/v1/chat/completions")
            return original_request(f"http://127.0.0.1:{server.server_port}/test", **kwargs)

        def transport(method, path, key=None, body=None):
            return fake(method, path, key, body) if method == "GET" else smoke.api_request(method, path, key, body)

        try:
            with tempfile.TemporaryDirectory(prefix="bom-error-integration-") as directory, \
                    patch.object(smoke.urllib.request, "Request", side_effect=local_request):
                result = smoke.run_once(SENTINEL, directory, transport)
                self.assertEqual(result["status"], "http_400")
                self.assertEqual(result["diagnostic"]["message"], "Unsupported schema keyword: uniqueItems")
                self.assertEqual(result["reservation_usd"], "0.10")
                self.assertEqual(result["cost_status"], "pending_reconciliation")
                self.assertFalse(result["review_created"])
                stored = json.loads(Path(directory, "result.json").read_text(encoding="utf-8"))
                self.assertEqual(stored, result)
                self.assertNotIn(SENTINEL, json.dumps(stored))
                self.assertFalse(Path(directory, "review-0.json").exists())
                with self.assertRaisesRegex(smoke.Blocked, "already_attempted"):
                    smoke.run_once(SENTINEL, directory, transport)
                self.assertEqual(Handler.posts, 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
