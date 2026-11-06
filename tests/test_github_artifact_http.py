"""Credential-separated artifact transport controls; no live network."""
from email.message import Message
import json
from pathlib import Path
import sys
import subprocess
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.providers.github_artifact_http import (
    GitHubArtifactTransport, _decode, _encode, _perform_get, _storage_url,
)

API = "https://api.github.com/repos/fixture-owner/fixture-repo/actions/artifacts/123/zip"
STORAGE = "https://productionresults123.blob.core.windows.net/artifact/path?sig=abcdefghijklmnopqrstuvwx1234567890"
TOKEN = "fictional-github-credential"


class Response:
    def __init__(self, url, code=200, body=b"PK authored archive bytes", headers=()):
        self.url, self.code, self.body = url, code, body
        self.headers = Message()
        for name, value in headers:
            self.headers[name] = value
        self.closed = False
        self.reads = []

    def getcode(self):
        return self.code

    def geturl(self):
        return self.url

    def read(self, size):
        self.reads.append(size)
        return self.body[:size]

    def close(self):
        self.closed = True


class Opener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class ArtifactTransportTests(unittest.TestCase):
    def run_get(self, responses, **kwargs):
        opener = Opener(responses)
        arguments = dict(operation="archive", repository="fixture-owner/fixture-repo", identity=123,
                         max_bytes=1024, timeout_seconds=5, token=TOKEN)
        arguments.update(kwargs)
        with patch("delivery_review.providers.github_artifact_http.urllib.request.build_opener", return_value=opener):
            result = _perform_get(**arguments)
        return result, opener

    def test_follow_one_qualified_redirect_without_forwarding_api_credentials(self):
        first = Response(API, 302, b"signed Location must never be read", [("Location", STORAGE), ("X-GitHub-Request-Id", "fixture")])
        second = Response(STORAGE)
        result, opener = self.run_get([first, second])
        self.assertIsNone(result["response"]["error"])
        self.assertEqual(second.body, result["response"]["body"])
        self.assertEqual("Bearer " + TOKEN, opener.requests[0].get_header("Authorization"))
        self.assertIsNone(opener.requests[1].get_header("Authorization"))
        self.assertIsNone(opener.requests[1].get_header("X-github-api-version"))
        self.assertEqual([], first.reads)
        self.assertTrue(first.closed and second.closed)
        encoded = _encode(result)
        self.assertNotIn(STORAGE.encode(), encoded)
        self.assertNotIn(b"sig=", encoded)
        self.assertEqual(result, _decode(encoded, 1024, TOKEN))

    def test_unapproved_storage_credential_userinfo_ports_and_duplicates_are_refused(self):
        locations = ["http://productionresults123.blob.core.windows.net/x", "https://api.github.com/next",
                     "https://productionresults123.blob.core.windows.net.attacker.invalid/x",
                     "https://user:pass@productionresults123.blob.core.windows.net/x",
                     "https://productionresults123.blob.core.windows.net:444/x", STORAGE + "#fragment",
                     "https://127.0.0.1/x", STORAGE + "\nsecret"]
        for location in locations:
            with self.subTest(location=location):
                result, opener = self.run_get([Response(API, 302, headers=[("Location", location)])])
                self.assertEqual("redirect_refused", result["response"]["error"]["code"])
                self.assertEqual(1, len(opener.requests))
        result, opener = self.run_get([Response(API, 302, headers=[("Location", STORAGE), ("Location", STORAGE)])])
        self.assertEqual("redirect_refused", result["response"]["error"]["code"])

    def test_storage_redirect_is_not_followed_and_its_body_is_omitted(self):
        second = Response(STORAGE, 302, b"secret locator", [("Location", "https://example.invalid")])
        result, opener = self.run_get([Response(API, 302, headers=[("Location", STORAGE)]), second])
        self.assertEqual("redirect_refused", result["response"]["error"]["code"])
        self.assertIsNone(result["response"]["body"])
        self.assertEqual([], second.reads)
        self.assertEqual(2, len(opener.requests))

    def test_native_http_error_and_bounded_body_prefix_remain_distinct(self):
        result, _ = self.run_get([Response(API, 403, b'{"message":"forbidden"}')])
        self.assertEqual(403, result["response"]["status_code"])
        self.assertEqual(b'{"message":"forbidden"}', result["response"]["body"])
        result, _ = self.run_get([Response(API, 302, headers=[("Location", STORAGE)]), Response(STORAGE, body=b"Z" * 2000)])
        self.assertEqual("response_limit", result["response"]["error"]["code"])
        self.assertEqual(b"Z" * 1024, result["response"]["body"])
        self.assertFalse(result["response"]["complete"])

    def test_api_token_and_signed_locator_echoes_are_never_retained(self):
        for body in (TOKEN.encode(), STORAGE.encode(), b"abcdefghijklmnopqrstuvwx1234567890"):
            result, _ = self.run_get([Response(API, 302, headers=[("Location", STORAGE)]), Response(STORAGE, body=body)])
            self.assertEqual("sensitive_response_refused", result["response"]["error"]["code"])
            self.assertIsNone(result["response"]["body"])
        first = Response(API, 302, headers=[("Location", STORAGE), ("ETag", STORAGE)])
        result, opener = self.run_get([first])
        self.assertEqual("sensitive_response_refused", result["response"]["error"]["code"])
        self.assertIsNone(result["api_redirect"])
        self.assertEqual(1, len(opener.requests))

    def test_metadata_queries_are_fixed_gets_and_cannot_redirect(self):
        url = "https://api.github.com/repos/fixture-owner/fixture-repo/actions/runs/123/artifacts?page=2&per_page=100"
        result, opener = self.run_get([Response(url, body=b'{"artifacts":[]}')], operation="run_artifacts", page=2, per_page=100)
        self.assertEqual(url, opener.requests[0].full_url)
        self.assertEqual("GET", opener.requests[0].get_method())
        self.assertIsNone(result["api_redirect"])
        self.assertEqual(b'{"artifacts":[]}', result["response"]["body"])
        result, opener = self.run_get([Response(url, 302, headers=[("Location", STORAGE)])], operation="run_artifacts", page=2, per_page=100)
        self.assertEqual("redirect_refused", result["response"]["error"]["code"])
        self.assertEqual(1, len(opener.requests))

    def test_storage_error_headers_cannot_retain_known_signed_locator(self):
        for status in (200, 302, 403):
            second = Response(STORAGE, status, headers=[("ETag", STORAGE)])
            result, opener = self.run_get([Response(API, 302, headers=[("Location", STORAGE)]), second])
            self.assertEqual("sensitive_response_refused", result["response"]["error"]["code"])
            self.assertEqual({}, result["response"]["response_headers"])
            self.assertEqual([], second.reads)
            self.assertNotIn(STORAGE.encode(), _encode(result))
            self.assertEqual(2, len(opener.requests))

    def test_invalid_inputs_never_open_a_connection(self):
        for changes in ({"identity": True}, {"repository": "../repo"}, {"repository": "owner/repo?token=x"},
                        {"operation": "delete"}, {"max_bytes": True}, {"max_bytes": 26 * 1024 * 1024},
                        {"timeout_seconds": 61}, {"page": 1}, {"per_page": 100}):
            result, opener = self.run_get([], **changes)
            self.assertEqual("transport_error", result["response"]["error"]["code"])
            self.assertEqual([], opener.requests)

    def test_timeout_is_sanitized_and_redirect_evidence_remains(self):
        result, _ = self.run_get([Response(API, 302, headers=[("Location", STORAGE)]), TimeoutError("secret URL")])
        self.assertEqual("request_timeout", result["response"]["error"]["code"])
        self.assertEqual(302, result["api_redirect"]["status_code"])
        self.assertNotIn(b"secret URL", _encode(result))

    def test_worker_metadata_cannot_smuggle_unexpected_fields_or_credentials(self):
        for raw in (b"not-json", b'{"response":{},"api_redirect":null}',
                    b'{"response":{},"api_redirect":{"Location":"secret"}}'):
            with self.subTest(raw=raw), self.assertRaises((ValueError, KeyError)):
                _decode(raw, 1024, TOKEN)
        with patch("delivery_review.providers.github_artifact_http.subprocess.Popen") as spawn:
            result = GitHubArtifactTransport(token=TOKEN).get("archive", "../repo", 1)
        spawn.assert_not_called()
        self.assertEqual("transport_error", result["response"]["error"]["code"])

    def test_listing_pagination_uses_artifact_route_validation(self):
        url = "https://api.github.com/repos/fixture-owner/fixture-repo/actions/runs/123/artifacts?page=1&per_page=100"
        link = '<' + url.replace("page=1&", "page=2&") + '>; rel="next"'
        result, _ = self.run_get([Response(url, body=b'{"artifacts":[]}', headers=[("Link", link)])],
                                  operation="run_artifacts", page=1, per_page=100)
        self.assertIsNone(result["response"]["error"])
        self.assertEqual(link, _decode(_encode(result), 1024, TOKEN)["response"]["response_headers"]["link"])

    def test_whole_request_deadline_reaps_blocked_worker(self):
        spawn, children = subprocess.Popen, []
        def sleeper(argv, **settings):
            process = spawn([sys.executable, "-I", "-c", "import time; time.sleep(20)"], **settings)
            children.append(process)
            return process
        started = time.monotonic()
        with patch("delivery_review.providers.github_artifact_http.subprocess.Popen", side_effect=sleeper):
            result = GitHubArtifactTransport().get("archive", "fixture-owner/fixture-repo", 123, timeout_seconds=1)
        self.assertEqual("request_timeout", result["response"]["error"]["code"])
        self.assertIsNotNone(children[0].poll())
        self.assertLess(time.monotonic() - started, 5)


if __name__ == "__main__":
    unittest.main()
