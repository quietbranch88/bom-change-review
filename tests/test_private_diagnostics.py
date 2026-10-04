"""Private archive regression contracts. Only synthetic responses/credentials."""

import io
import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error

import openrouter_smoke as smoke
import private_diagnostics as private
from test_openrouter_smoke import FakeAPI, SENTINEL


UNKNOWN = 'New vendor validation error: fields.input_max needs a number or null.'


def captured(raw):
    error = urllib.error.HTTPError("https://openrouter.ai/", 400, "", {}, io.BytesIO(raw))
    with patch.object(smoke.urllib.request, "build_opener") as builder:
        builder.return_value.open.side_effect = error
        try:
            smoke.api_request("POST", "/chat/completions", SENTINEL, {})
        except smoke.Blocked as blocked:
            return blocked
    raise AssertionError("Expected HTTP failure")


@unittest.skipUnless(os.name == "nt", "Real Windows DPAPI required")
class PrivateArchiveTests(unittest.TestCase):
    def test_unknown_http_error_is_not_irretrievably_discarded(self):
        failure = captured(json.dumps({"error": {"message": UNKNOWN}}).encode())
        self.assertIsNotNone(getattr(failure, "private_archive", None))
        self.assertNotIn(UNKNOWN, json.dumps(failure.diagnostic))
        self.assertEqual(str(failure), "http_400")
        self.assertEqual(private.unseal(failure.private_archive),
                         json.dumps({"error": {"message": UNKNOWN}}).encode())

    def test_binary_and_non_json_errors_are_recoverable(self):
        for raw in (b'\xffvendor-error', b'<html>vendor validation failure</html>', b''):
            failure = captured(raw)
            self.assertEqual(private.unseal(failure.private_archive), raw)
            self.assertEqual(failure.diagnostic['body_status'], 'unavailable')

    def test_secrets_removed_before_encrypting_other_private_text(self):
        raw = (SENTINEL + '\nsk-or-v1-synthetic\nAuthorization: secret\nBearer secret\n'
               + UNKNOWN + '\nsynthetic-person@example.invalid').encode()
        archive = private.seal(raw, SENTINEL)
        plain = private.unseal(archive)
        self.assertNotIn(SENTINEL.encode(), plain)
        self.assertNotIn(b'secret', plain)
        self.assertNotIn(b'sk-or-', plain)
        self.assertIn(UNKNOWN.encode(), plain)
        self.assertIn(b'synthetic-person@example.invalid', plain)
        self.assertNotIn(UNKNOWN.encode(), base64.b64decode(archive['ciphertext']))

    def test_body_limit_and_oversize(self):
        self.assertEqual(private.unseal(captured(b'x' * 8192).private_archive), b'x' * 8192)
        failure = captured(b'x' * 8193)
        self.assertIsNone(failure.private_archive)
        self.assertEqual(failure.diagnostic['body_status'], 'too_large')
        with self.assertRaises(ValueError):
            private.seal(b'x' * 8193)

    def test_crypto_failure_has_no_plaintext_fallback(self):
        with patch.object(private, '_dpapi', side_effect=OSError('synthetic crypto failure')):
            failure = captured(UNKNOWN.encode())
        self.assertIsNone(failure.private_archive)
        self.assertEqual(str(failure), 'http_400')
        api = FakeAPI()
        api.failure = failure
        with tempfile.TemporaryDirectory(prefix='bom-crypto-failure-') as directory:
            result = smoke.run_once(SENTINEL, directory, api)
            self.assertEqual(result['private_archive_status'], 'unavailable')
            self.assertEqual(result['reservation_usd'], '0.10')
            self.assertFalse(Path(directory, 'error.private.json').exists())
            with self.assertRaisesRegex(smoke.Blocked, 'already_attempted'):
                smoke.run_once(SENTINEL, directory, api)
            self.assertEqual(api.posts, 1)

    def test_storage_failure_preserves_status_and_repeat_lock(self):
        api = FakeAPI()
        api.failure = captured(UNKNOWN.encode())
        original = smoke.write_new

        def failed_write(path, value):
            if Path(path).name == 'error.private.json':
                raise OSError('synthetic disk failure')
            return original(path, value)

        with tempfile.TemporaryDirectory(prefix='bom-disk-failure-') as directory:
            with patch.object(smoke, 'write_new', side_effect=failed_write):
                result = smoke.run_once(SENTINEL, directory, api)
            self.assertEqual(result['status'], 'http_400')
            self.assertEqual(result['private_archive_status'], 'save_failed')
            self.assertEqual(result['reservation_usd'], '0.10')
            self.assertFalse(result['review_created'])
            self.assertNotIn(UNKNOWN, json.dumps(result))
            with self.assertRaisesRegex(smoke.Blocked, 'already_attempted'):
                smoke.run_once(SENTINEL, directory, api)
            self.assertEqual(api.posts, 1)

    def test_invalid_envelopes_and_corrupt_ciphertext_rejected(self):
        for value in (None, {}, {'format': private.FORMAT, 'ciphertext': '!'},
                      {'format': private.FORMAT, 'ciphertext': 'A' * 32769},
                      {'format': private.FORMAT, 'ciphertext': base64.b64encode(b'invalid').decode()}):
            with self.subTest(value_type=type(value).__name__), self.assertRaises((ValueError, OSError)):
                private.unseal(value)

    def test_get_errors_do_not_archive(self):
        error = urllib.error.HTTPError('https://openrouter.ai/', 400, '', {}, io.BytesIO(UNKNOWN.encode()))
        with patch.object(smoke.urllib.request, 'build_opener') as builder:
            builder.return_value.open.side_effect = error
            with self.assertRaises(smoke.Blocked) as raised:
                smoke.api_request('GET', '/key', SENTINEL)
        self.assertIsNone(raised.exception.private_archive)

    def test_non_windows_has_no_plaintext_fallback(self):
        with patch.object(private.os, 'name', 'posix'), self.assertRaises(OSError):
            private.seal(UNKNOWN.encode())

    def test_loopback_to_disk_to_separate_process_decryption(self):
        payload = json.dumps({'error': {'message': UNKNOWN, 'synthetic_credential': SENTINEL}}).encode()

        class Handler(BaseHTTPRequestHandler):
            posts = 0

            def do_POST(self):
                type(self).posts += 1
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(400)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        original_request = smoke.urllib.request.Request
        api = FakeAPI()

        def local_request(url, **kwargs):
            self.assertEqual(url, 'https://openrouter.ai/api/v1/chat/completions')
            return original_request(f'http://127.0.0.1:{server.server_port}/test', **kwargs)

        def transport(method, path, key=None, body=None):
            return api(method, path, key, body) if method == 'GET' else smoke.api_request(method, path, key, body)

        try:
            with tempfile.TemporaryDirectory(prefix='bom-private-integration-') as directory:
                with patch.object(smoke.urllib.request, 'Request', side_effect=local_request):
                    result = smoke.run_once(SENTINEL, directory, transport)
                self.assertEqual(result['private_archive_status'], 'saved')
                self.assertEqual(result['status'], 'http_400')
                self.assertEqual(result['cost_status'], 'pending_reconciliation')
                self.assertFalse(result['review_created'])
                archive = Path(directory, 'error.private.json')
                for path in Path(directory).glob('*.json'):
                    text = path.read_text(encoding='utf-8')
                    self.assertNotIn(UNKNOWN, text)
                    self.assertNotIn(SENTINEL, text)
                command = [sys.executable, str(Path(private.__file__).resolve()), str(archive)]
                default = subprocess.run(command, capture_output=True, text=True, check=False)
                self.assertEqual(default.returncode, 0)
                self.assertEqual(json.loads(default.stdout),
                                 {'decryption_verified': True, 'private_content_shown': False})
                revealed = subprocess.run(command + ['--reveal-private'], capture_output=True, text=True, check=False)
                self.assertEqual(revealed.returncode, 0)
                self.assertEqual(json.loads(revealed.stdout)['private_error_text'],
                                 payload.replace(SENTINEL.encode(), b'[REDACTED]').decode())
                self.assertNotIn(SENTINEL, revealed.stdout)
                with self.assertRaisesRegex(smoke.Blocked, 'already_attempted'):
                    smoke.run_once(SENTINEL, directory, transport)
                self.assertEqual(Handler.posts, 1)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
