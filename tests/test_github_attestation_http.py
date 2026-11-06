"""Authored byte/network controls. These are not signed-product acceptance."""
from copy import deepcopy
from email.message import Message
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from delivery_review.providers import github_attestation_http as transport
from delivery_review.providers.github_evidence_routes import attestation_path, evidence_headers, next_evidence_page
from delivery_review.contracts import ContractError

REPO = "fixture-owner/fixture-repo"
DIGEST = "a" * 64
PREDICATE = "https://github.com/taipei49314/delivery-review/attestation/pytest/v1"
TOKEN = "fictional-github-credential"
PATH = attestation_path(REPO, DIGEST, PREDICATE, 10)
API = "https://api.github.com" + PATH
STORAGE = "https://tmaproduction.blob.core.windows.net/attestations/1/fixture.json?sig=abcdefghijklmnop1234567890"
BUNDLE = b'{"original":"authored unsigned bundle control"}\r\n'


class Response:
    def __init__(self, url, code=200, body=BUNDLE, headers=()):
        self.url, self.code, self.body, self.closed = url, code, body, False
        self.headers = Message()
        for name, value in headers:
            self.headers[name] = value
        self.reads = []
    def getcode(self): return self.code
    def geturl(self): return self.url
    def read(self, size):
        self.reads.append(size)
        return self.body[:size]
    def close(self): self.closed = True


def listing(entries=None, headers=()):
    return Response(API, body=json.dumps({"attestations": entries if entries is not None else
        [{"repository_id": 9001, "bundle_url": STORAGE, "initiator": "user"}]}).encode(), headers=headers)


class AttestationTransportTests(unittest.TestCase):
    def get(self, responses, **changes):
        requests, pending = [], list(responses)
        class Opener:
            def open(self, request, timeout):
                requests.append(request)
                value = pending.pop(0)
                if isinstance(value, Exception): raise value
                return value
        kwargs = dict(repository=REPO, digest=DIGEST, predicate_type=PREDICATE, token=TOKEN)
        kwargs.update(changes)
        with patch.object(transport.urllib.request, "build_opener", return_value=Opener()):
            result = transport._perform_get(**kwargs)
        return result, requests

    def test_original_bundle_retained_discovery_explicitly_omitted_credentials_separated(self):
        first, second = listing(), Response(STORAGE)
        value, requests = self.get([first, second])
        self.assertIsNone(value["discovery"]["error"])
        self.assertEqual(hashlib.sha256(first.body).hexdigest(), value["discovery"]["body_sha256"])
        self.assertEqual(len(first.body), value["discovery"]["body_size_bytes"])
        self.assertEqual("omitted_ephemeral_storage_credentials", value["discovery"]["body_retention"])
        self.assertEqual(BUNDLE, value["bundles"][0]["response"]["body"])
        self.assertEqual("Bearer " + TOKEN, requests[0].get_header("Authorization"))
        self.assertIsNone(requests[1].get_header("Authorization"))
        self.assertIsNone(requests[1].get_header("X-github-api-version"))
        raw = transport._encode(value)
        self.assertNotIn(STORAGE.encode(), raw)
        self.assertNotIn(b"sig=", raw)
        self.assertNotIn(TOKEN.encode(), raw)
        self.assertEqual(value, transport._decode(raw, path=PATH, per_page=10, max_bundle_bytes=transport.MAX_BODY,
                                                 max_total_bytes=transport.MAX_TOTAL, token=TOKEN))
        self.assertTrue(first.closed and second.closed)

    def test_cursor_next_and_all_link_scopes_validated_before_bundle_download(self):
        cursor = {"after": "Y3Vyc29yOnYyLXRlc3Q="}
        link = '<https://api.github.com' + attestation_path(REPO, DIGEST, PREDICATE, 10, cursor) + '>; rel="next"'
        value, _ = self.get([listing(headers=[("Link", link)]), Response(STORAGE)])
        self.assertEqual(cursor, value["next_cursor"])
        for wrong in (link.replace(REPO, "other/repo"), link.replace("a" * 64, "b" * 64),
                      link.replace("per_page=10", "per_page=5")):
            value, requests = self.get([listing(headers=[("Link", wrong)])])
            self.assertEqual("pagination_incomplete", value["discovery"]["error"]["code"])
            self.assertEqual(1, len(requests))
        wrong = link.replace("after=", "token=")
        value, requests = self.get([listing(headers=[("Link", wrong)])])
        self.assertEqual("unsafe_response_headers", value["discovery"]["error"]["code"])

    def test_malformed_duplicate_missing_or_oversized_discovery_does_not_download(self):
        for first in (Response(API, body=b"{broken"), Response(API, body=b'{"attestations":[],"attestations":[]}'),
                      listing([{"repository_id": True, "bundle_url": STORAGE}]),
                      listing([{"repository_id": 9001, "bundle": {}}]),
                      listing([{"repository_id": 9001, "bundle_url": STORAGE}] * 2),
                      listing([{"repository_id": 9001, "bundle_url": STORAGE}] * 11)):
            value, requests = self.get([first])
            self.assertEqual("attestation_discovery_invalid", value["discovery"]["error"]["code"])
            self.assertEqual(1, len(requests))
            self.assertTrue(value["discovery"]["body_complete"])
        value, _ = self.get([listing()], max_discovery_bytes=10)
        self.assertEqual("response_limit", value["discovery"]["error"]["code"])
        self.assertEqual(10, value["discovery"]["body_size_bytes"])
        self.assertFalse(value["discovery"]["body_complete"])

    def test_storage_url_ssrf_and_redirects_refused_without_token_forwarding(self):
        for url in ("http://tmaproduction.blob.core.windows.net/x", "https://127.0.0.1/x",
                    "https://api.github.com/x", "https://x.blob.core.windows.net.attacker.invalid/x",
                    "https://user:pass@tmaproduction.blob.core.windows.net/x"):
            value, requests = self.get([listing([{"repository_id": 9001, "bundle_url": url}])])
            self.assertEqual("attestation_discovery_invalid", value["discovery"]["error"]["code"])
            self.assertEqual(1, len(requests))
        response = Response(STORAGE, 302, b"credential URL", [("Location", "https://attacker.invalid/")])
        value, requests = self.get([listing(), response])
        self.assertEqual("redirect_refused", value["bundles"][0]["response"]["error"]["code"])
        self.assertEqual([], response.reads)
        self.assertEqual(2, len(requests))

    def test_known_credentials_and_signed_locator_echoes_never_retained(self):
        for body in (TOKEN.encode(), STORAGE.encode(), b"abcdefghijklmnop1234567890"):
            value, _ = self.get([listing(), Response(STORAGE, body=body)])
            response = value["bundles"][0]["response"]
            self.assertEqual("sensitive_response_refused", response["error"]["code"])
            self.assertIsNone(response["body"])
        value, _ = self.get([listing(headers=[("ETag", STORAGE)])])
        self.assertEqual("sensitive_response_refused", value["discovery"]["error"]["code"])

    def test_invalid_discovery_cannot_retain_unparsed_locator_echo_in_headers(self):
        value, _ = self.get([listing([{"repository_id": True, "bundle_url": STORAGE}], headers=[("ETag", STORAGE)])])
        self.assertEqual("attestation_discovery_invalid", value["discovery"]["error"]["code"])
        self.assertEqual({}, value["discovery"]["response_headers"])
        self.assertNotIn(STORAGE.encode(), transport._encode(value))
        self.assertEqual({}, value["discovery"]["response_headers"])
        value, _ = self.get([Response(API, body=TOKEN.encode())])
        self.assertEqual("sensitive_response_refused", value["discovery"]["error"]["code"])

    def test_bundle_and_total_limits_retain_original_prefixes_and_native_error(self):
        value, _ = self.get([listing(), Response(STORAGE, body=b"X" * 20)], max_bundle_bytes=10)
        response = value["bundles"][0]["response"]
        self.assertEqual(b"X" * 10, response["body"])
        self.assertEqual("response_limit", response["error"]["code"])
        self.assertFalse(response["complete"])
        value, _ = self.get([listing(), Response(STORAGE, 403, b'{"expired":true}')])
        self.assertEqual(403, value["bundles"][0]["response"]["status_code"])
        self.assertEqual(b'{"expired":true}', value["bundles"][0]["response"]["body"])
        first = listing()
        value, requests = self.get([first], max_total_bytes=len(first.body))
        self.assertEqual("attestation_bundle_limit", value["discovery"]["error"]["code"])
        self.assertEqual("capture_limit", value["bundles"][0]["response"]["error"]["code"])
        self.assertEqual(1, len(requests))

    def test_storage_error_headers_refuse_signed_locator_before_any_branch(self):
        for status in (200, 302, 403):
            second = Response(STORAGE, status, headers=[("ETag", STORAGE)])
            value, _ = self.get([listing(), second])
            response = value["bundles"][0]["response"]
            self.assertEqual("sensitive_response_refused", response["error"]["code"])
            self.assertEqual({}, response["response_headers"])
            self.assertEqual([], second.reads)
            self.assertNotIn(STORAGE.encode(), transport._encode(value))

    def test_refused_secret_body_still_consumes_total_download_budget(self):
        other = STORAGE.replace("fixture.json", "other.json")
        first = listing([{"repository_id": 9001, "bundle_url": STORAGE},
                         {"repository_id": 9001, "bundle_url": other}])
        secret = b"abcdefghijklmnop1234567890"
        value, requests = self.get([first, Response(STORAGE, body=secret)],
            max_total_bytes=len(first.body) + len(secret))
        self.assertEqual(2, len(requests))
        self.assertEqual("sensitive_response_refused", value["bundles"][0]["response"]["error"]["code"])
        self.assertEqual("capture_limit", value["bundles"][1]["response"]["error"]["code"])
        self.assertEqual("attestation_bundle_limit", value["discovery"]["error"]["code"])

    def test_parent_rechecks_lowered_discovery_limit(self):
        value, _ = self.get([listing(), Response(STORAGE)])
        with self.assertRaisesRegex(ValueError, "invalid_attestation_worker_digest"):
            transport._decode(transport._encode(value), path=PATH, per_page=10,
                max_bundle_bytes=transport.MAX_BODY, max_total_bytes=transport.MAX_TOTAL,
                max_discovery_bytes=value["discovery"]["body_size_bytes"] - 1, token=TOKEN)

    def test_failure_does_not_erase_already_observed_discovery(self):
        first = listing()
        value, _ = self.get([first, TimeoutError("sensitive URL")])
        self.assertEqual(hashlib.sha256(first.body).hexdigest(), value["discovery"]["body_sha256"])
        self.assertEqual("request_timeout", value["bundles"][0]["response"]["error"]["code"])
        self.assertNotIn(b"sensitive URL", transport._encode(value))

    def test_parent_rejects_protocol_tampering_and_hidden_next_page(self):
        cursor = {"after": "abc="}
        link = '<https://api.github.com' + attestation_path(REPO, DIGEST, PREDICATE, 10, cursor) + '>; rel="next"'
        value, _ = self.get([listing(headers=[("Link", link)]), Response(STORAGE)])
        for modify in (lambda v: v.update(next_cursor=None), lambda v: v.update(request_path="/repos/evil/repo"),
                       lambda v: v["discovery"].update(Location=STORAGE),
                       lambda v: v["bundles"][0].update(repository_id=True),
                       lambda v: v["discovery"].update(body_size_bytes=True)):
            forged = deepcopy(value)
            modify(forged)
            with self.assertRaises((ValueError, ContractError)):
                transport._decode(transport._encode(forged), path=PATH, per_page=10,
                    max_bundle_bytes=transport.MAX_BODY, max_total_bytes=transport.MAX_TOTAL, token=TOKEN)

    def test_whole_request_deadline_kills_and_reaps_worker(self):
        spawn, children = subprocess.Popen, []
        def sleeper(argv, **settings):
            process = spawn([sys.executable, "-I", "-c", "import time; time.sleep(20)"], **settings)
            children.append(process)
            return process
        started = time.monotonic()
        with patch.object(transport.subprocess, "Popen", side_effect=sleeper):
            value = transport.GitHubAttestationTransport().get(REPO, DIGEST, PREDICATE, timeout_seconds=1)
        self.assertEqual("request_timeout", value["discovery"]["error"]["code"])
        self.assertIsNotNone(children[0].poll())
        self.assertLess(time.monotonic() - started, 5)

    def test_invalid_request_never_starts_worker(self):
        for changes in ({"repository": "../repo"}, {"digest": "A" * 64}, {"predicate_type": "unknown"},
                        {"per_page": 11}, {"per_page": True}, {"cursor": {"after": "a", "before": "b"}},
                        {"cursor": {"after": "https://evil.invalid?a=b"}}, {"max_total_bytes": True}):
            kwargs = dict(repository=REPO, digest=DIGEST, predicate_type=PREDICATE)
            kwargs.update(changes)
            with patch.object(transport.subprocess, "Popen") as spawn:
                value = transport.GitHubAttestationTransport().get(**kwargs)
            spawn.assert_not_called()
            self.assertEqual("transport_error", value["discovery"]["error"]["code"])

    def test_artifact_links_scope_duplicates_and_next_sequence(self):
        path = "/repos/fixture-owner/fixture-repo/actions/runs/1/artifacts?page=1&per_page=100"
        link = '<https://api.github.com' + path.replace("page=1&", "page=2&") + '>; rel="next"'
        self.assertEqual(2, next_evidence_page({"link": link}, path))
        for wrong in (link.replace("page=2&", "page=3&"), link.replace("runs/1", "runs/2"), link + "," + link,
                      link.replace("page=2&", "page=2&token=secret&")):
            with self.assertRaises((ValueError, ContractError)):
                next_evidence_page({"link": wrong}, path)
        with self.assertRaises((ValueError, ContractError)):
            evidence_headers({"authorization": "secret"})


if __name__ == "__main__":
    unittest.main()
