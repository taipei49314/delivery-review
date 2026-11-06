"""Controlled SPEC-001 HTTP boundaries; no test contacts a real network.

Fake openers supply authored raw bytes and headers. Deadline tests launch only a
fixed first-party worker or an explicitly controlled sleeping Python process.
They do not execute reviewed PR code or qualify a live GitHub workflow.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.providers import github_http
from delivery_review.providers.github_http import GitHubTransport

PATH = "/repos/fixture-owner/fixture-repo"
URL = "https://api.github.com" + PATH
TOKEN = "ghp_synthetic_test_secret"
OPTIONS = {"api_version": "2026-03-10", "max_bytes": 1024, "timeout_seconds": 5}


class Response(io.BytesIO):
    def __init__(self, body=b"{}\r\n", *, status=200, headers=None, url=URL):
        super().__init__(body)
        self.status = status
        self.headers = {"Content-Type": "application/json"} if headers is None else headers
        self.url = url
        self.read_sizes = []

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_sizes.append(size)
        return super().read(size)


def worker_message(body=b"{}", **options):
    return github_http._encode_result(github_http._result(body=body, status=200, complete=True, **options))


class GitHubHTTPTests(unittest.TestCase):
    def perform(self, response=None, *, token=None, options=None, error=None):
        response = Response() if response is None else response
        opener = Mock()
        if error is not None:
            opener.open.side_effect = error
        else:
            opener.open.return_value = response
        with patch.object(github_http.urllib.request, "build_opener", return_value=opener) as factory:
            result = github_http._perform_get(PATH, token=token, **(OPTIONS if options is None else options))
        return result, opener, factory

    def worker(self, output, *, token=None, environment=None):
        process = Mock()
        process.communicate.return_value = (output, None)
        process.poll.return_value = 0
        process.returncode = 0
        with patch.dict(os.environ, {} if environment is None else environment), \
             patch.object(github_http.subprocess, "Popen", return_value=process) as factory:
            result = GitHubTransport(token=token).get(PATH, **OPTIONS)
        return result, process, factory

    def test_get_is_fixed_https_and_preserves_original_bytes(self):
        raw = '{ "note": "原始 bytes" }\r\n'.encode("utf-8")
        response = Response(raw)
        result, opener, factory = self.perform(response)
        self.assertEqual(raw, result["body"])
        self.assertTrue(result["complete"])
        self.assertIsNone(result["error"])
        self.assertEqual(200, result["status_code"])
        request = opener.open.call_args.args[0]
        self.assertEqual(URL, request.full_url)
        self.assertEqual("GET", request.get_method())
        self.assertIsNone(request.get_header("Authorization"))
        self.assertEqual("2026-03-10", request.get_header("X-github-api-version"))
        self.assertEqual("identity", request.get_header("Accept-encoding"))
        self.assertEqual([OPTIONS["max_bytes"] + 1], response.read_sizes)
        self.assertTrue(response.closed)
        proxy = next(handler for handler in factory.call_args.args if isinstance(handler, urllib.request.ProxyHandler))
        self.assertEqual({}, proxy.proxies)

    def test_token_is_only_in_worker_stdin_and_http_authorization(self):
        result, process, factory = self.worker(worker_message(), token=TOKEN,
                                              environment={"GH_TOKEN": TOKEN, "GITHUB_TOKEN": "another-secret",
                                                           "HTTPS_PROXY": "http://secret@proxy.invalid", "SYSTEMROOT": "C:\\Windows"})
        self.assertIsNone(result["error"])
        command = factory.call_args.args[0]
        self.assertEqual([sys.executable, "-I", "-B"], command[:3])
        self.assertTrue(Path(command[3]).is_absolute())
        self.assertNotIn(TOKEN, repr(command))
        settings = factory.call_args.kwargs
        self.assertIs(settings["stderr"], subprocess.DEVNULL)
        self.assertNotIn("GH_TOKEN", settings["env"])
        self.assertNotIn("GITHUB_TOKEN", settings["env"])
        self.assertNotIn("HTTPS_PROXY", settings["env"])
        self.assertEqual("C:\\Windows", settings["env"]["SYSTEMROOT"])
        request = json.loads(process.communicate.call_args.args[0])
        self.assertEqual(TOKEN, request["token"])
        self.assertLessEqual(process.communicate.call_args.kwargs["timeout"], OPTIONS["timeout_seconds"])
        self.assertNotIn(TOKEN, repr(result))
        _, opener, _ = self.perform(token=TOKEN)
        self.assertEqual("Bearer " + TOKEN, opener.open.call_args.args[0].get_header("Authorization"))

    def test_unsafe_routes_and_invalid_budgets_never_start_worker(self):
        paths = ("https://api.github.com" + PATH, "https://evil.example" + PATH,
                 "//api.github.com" + PATH, PATH + "?access_token=secret", PATH + "#fragment",
                 "/repos/owner/../secret", "/user", PATH + "\n")
        with patch.object(github_http.subprocess, "Popen") as factory:
            for path in paths:
                with self.subTest(path=path):
                    result = GitHubTransport().get(path, **OPTIONS)
                    self.assertEqual({"code": "transport_error"}, result["error"])
            for options in ({**OPTIONS, "max_bytes": 0}, {**OPTIONS, "max_bytes": True},
                            {**OPTIONS, "max_bytes": 2097153}, {**OPTIONS, "timeout_seconds": 31},
                            {**OPTIONS, "timeout_seconds": 0}, {**OPTIONS, "api_version": "latest"}):
                with self.subTest(options=options):
                    self.assertEqual({"code": "transport_error"}, GitHubTransport().get(PATH, **options)["error"])
        factory.assert_not_called()

    def test_invalid_tokens_are_refused_without_disclosing_the_value(self):
        for token in ("", "secret\r\nInjected: yes", "secret space", "x" * 4097, True):
            with self.subTest(kind=type(token).__name__), self.assertRaisesRegex(ValueError, "^invalid token format$"):
                GitHubTransport(token=token)

    def test_redirect_handler_never_follows_and_redirect_body_is_not_retained(self):
        secret_url = "https://evil.example/?token=secret"
        error = urllib.error.HTTPError(URL, 302, "secret message", {"Location": secret_url}, io.BytesIO(secret_url.encode()))
        result, opener, factory = self.perform(error=error)
        self.assertEqual({"code": "redirect_refused"}, result["error"])
        self.assertIsNone(result["body"])
        self.assertFalse(result["complete"])
        self.assertEqual({}, result["response_headers"])
        self.assertNotIn("secret", repr(result))
        opener.open.assert_called_once()
        handler = next(handler for handler in factory.call_args.args if isinstance(handler, github_http._NoRedirect))
        self.assertIsNone(handler.redirect_request(None, None, 302, "", {}, secret_url))

    def test_unexpected_final_response_url_is_refused(self):
        result, _, _ = self.perform(Response(url="https://evil.example/?token=secret"))
        self.assertEqual({"code": "redirect_refused"}, result["error"])
        self.assertIsNone(result["body"])
        self.assertNotIn("secret", repr(result))

    def test_only_allowlisted_headers_are_retained(self):
        response = Response(headers={"Content-Type": "application/json", "ETag": '"opaque"',
                                     "Set-Cookie": "session=secret", "Authorization": "Bearer secret",
                                     "X-Debug": "private environment"})
        result, _, _ = self.perform(response)
        self.assertEqual({"content-type": "application/json", "etag": '"opaque"'}, result["response_headers"])
        self.assertNotIn("secret", repr(result))
        self.assertNotIn("environment", repr(result))

    def test_unsafe_link_values_and_oversized_headers_are_refused_without_echo(self):
        unsafe = (
            '<https://evil.example/?access_token=secret>; rel="next"',
            '<https://api.github.com/repos/fixture-owner/fixture-repo?access_token=secret>; rel="next"',
            '<https://secret@api.github.com/repos/fixture-owner/fixture-repo>; rel="next"',
            "x" * 8193,
        )
        for value in unsafe:
            response = Response(headers={"Link": value})
            with self.subTest(length=len(value)):
                result, _, _ = self.perform(response)
                self.assertEqual({"code": "unsafe_response_headers"}, result["error"])
                self.assertEqual({}, result["response_headers"])
                self.assertIsNone(result["body"])
                self.assertNotIn("secret", repr(result))
                self.assertEqual([], response.read_sizes)

    def test_safe_pagination_header_is_preserved_for_collector_scope_checks(self):
        value = ('<https://api.github.com/repos/fixture-owner/fixture-repo/actions/runs/1/attempts/1/jobs'
                 '?page=2&per_page=100>; rel="next"')
        result, _, _ = self.perform(Response(headers={"Link": value}))
        self.assertEqual(value, result["response_headers"]["link"])
        self.assertIsNone(result["error"])

    def test_known_token_echo_refuses_complete_response_without_rewriting_bytes(self):
        for response in (Response(b'{"echo":"' + TOKEN.encode() + b'"}'),
                         Response(headers={"ETag": TOKEN}), Response(headers={"Set-Cookie": TOKEN})):
            result, _, _ = self.perform(response, token=TOKEN)
            self.assertEqual({"code": "sensitive_response_refused"}, result["error"])
            self.assertEqual({}, result["response_headers"])
            self.assertIsNone(result["body"])
            self.assertFalse(result["complete"])
            self.assertNotIn(TOKEN, repr(result))

    def test_truncation_never_retains_a_known_token_prefix(self):
        result, _, _ = self.perform(Response(b"xxx" + TOKEN.encode()), token=TOKEN,
                                     options={**OPTIONS, "max_bytes": 7})
        self.assertEqual({"code": "sensitive_response_refused"}, result["error"])
        self.assertIsNone(result["body"])

    def test_exact_limit_is_complete_and_one_extra_byte_yields_original_prefix(self):
        for raw, complete in ((b"abcd", True), (b"abcde", False), (b"abcdefghi", False)):
            response = Response(raw)
            with self.subTest(length=len(raw)):
                result, _, _ = self.perform(response, options={**OPTIONS, "max_bytes": 4})
                self.assertEqual(raw[:4], result["body"])
                self.assertEqual(complete, result["complete"])
                self.assertEqual(None if complete else {"code": "response_limit"}, result["error"])
                self.assertEqual([5], response.read_sizes)

    def test_http_failures_and_rate_limit_evidence_remain_distinct(self):
        cases = ((401, {}, "authentication_unavailable"), (403, {}, "resource_unavailable"),
                 (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"),
                 (403, {"Retry-After": "60"}, "rate_limited"), (404, {}, "resource_unavailable"),
                 (429, {}, "rate_limited"), (500, {}, "http_error"), (204, {}, "http_error"))
        for status, headers, expected in cases:
            with self.subTest(status=status, headers=headers):
                raw = b'{"message":"authored HTTP control"}\r\n'
                result, _, _ = self.perform(Response(raw, status=status, headers=headers))
                self.assertEqual(status, result["status_code"])
                self.assertEqual({"code": expected}, result["error"])
                self.assertEqual(raw, result["body"])
                self.assertTrue(result["complete"])

    def test_http_error_exception_bodies_have_same_byte_bound(self):
        error = urllib.error.HTTPError(URL, 404, "do not expose", {}, io.BytesIO(b"abcdef"))
        result, _, _ = self.perform(error=error, options={**OPTIONS, "max_bytes": 4})
        self.assertEqual(404, result["status_code"])
        self.assertEqual(b"abcd", result["body"])
        self.assertEqual({"code": "response_limit"}, result["error"])
        self.assertFalse(result["complete"])

    def test_network_errors_do_not_expose_exception_text_or_credential_urls(self):
        errors = ((urllib.error.URLError("https://secret@proxy.invalid/"), "transport_error"),
                  (socket.timeout("secret"), "request_timeout"),
                  (urllib.error.URLError(TimeoutError("secret")), "request_timeout"),
                  (OSError("secret"), "transport_error"))
        for error, expected in errors:
            with self.subTest(error=type(error).__name__):
                result, _, _ = self.perform(error=error)
                self.assertEqual({"code": expected}, result["error"])
                self.assertIsNone(result["body"])
                self.assertNotIn("secret", repr(result))

    def test_worker_protocol_rejects_malformed_oversized_and_sensitive_output(self):
        invalid = (b"not-json-secret", b"x" * 100000, b'{"body_b64":"%%%"}',
                   github_http._encode_result(github_http._result(status=200, complete=False)))
        for raw in invalid:
            with self.subTest(length=len(raw)):
                result, _, _ = self.worker(raw)
                self.assertEqual({"code": "transport_error"}, result["error"])
                self.assertNotIn("secret", repr(result))
        result, _, _ = self.worker(worker_message(TOKEN.encode()), token=TOKEN)
        self.assertEqual({"code": "sensitive_response_refused"}, result["error"])
        self.assertIsNone(result["body"])

    def test_whole_worker_deadline_kills_and_reaps_blocked_process(self):
        actual_popen = subprocess.Popen
        children = []

        def sleeping_worker(command, **settings):
            process = actual_popen([sys.executable, "-I", "-c", "import time; time.sleep(20)"], **settings)
            children.append(process)
            return process

        started = time.monotonic()
        with patch.object(github_http.subprocess, "Popen", side_effect=sleeping_worker):
            result = GitHubTransport().get(PATH, **{**OPTIONS, "timeout_seconds": 1})
        self.assertEqual({"code": "request_timeout"}, result["error"])
        self.assertIsNotNone(children[0].poll())
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsNone(result["body"])

    def test_fixed_isolated_worker_rejects_invalid_input_without_network(self):
        request = {"path": "https://evil.example/?token=secret", "api_version": "2026-03-10",
                   "max_bytes": 100, "timeout_seconds": 1, "token": None}
        process = subprocess.run([sys.executable, "-I", "-B", str(Path(github_http.__file__).resolve()), "--worker"],
                                 input=json.dumps(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(0, process.returncode)
        self.assertEqual(b"", process.stderr)
        self.assertEqual("transport_error", json.loads(process.stdout)["error"]["code"])
        self.assertNotIn(b"secret", process.stdout)


if __name__ == "__main__":
    unittest.main()
