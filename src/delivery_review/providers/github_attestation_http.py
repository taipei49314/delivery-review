"""Bounded attestation discovery; signed storage locators never leave the worker.

Original signed bundle bytes are retained. Discovery JSON is deliberately not
retained because it contains temporary bearer URLs: its exact digest, size and
omission reason are recorded instead. A discovery result authenticates nothing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from delivery_review.contracts import ContractError, loads_strict
from delivery_review.providers.github_artifact_http import _signed_secret_echo, _storage_url
from delivery_review.providers.github_evidence_routes import attestation_path, evidence_headers, next_evidence_page
from delivery_review.providers.github_http import (
    _NoRedirect, _contains_token, _decode_result, _encode_result, _http_error,
    _response_headers, _result, _valid_token, _worker_environment,
)
from delivery_review.providers.github_routes import ACQUISITION_ERRORS, API_VERSION, positive

MAX_BODY = 2 * 1024 * 1024
MAX_TOTAL = 25 * 1024 * 1024
_ERRORS = ACQUISITION_ERRORS | {"attestation_discovery_invalid", "attestation_bundle_limit", "pagination_incomplete"}
_RETENTION = "omitted_ephemeral_storage_credentials"


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _inputs(repository, digest, predicate_type, per_page, cursor, max_discovery_bytes,
            max_bundle_bytes, max_total_bytes, timeout_seconds, token):
    path = attestation_path(repository, digest, predicate_type, per_page, cursor)
    if (not _valid_token(token) or any(type(x) is not int or not 1 <= x <= MAX_BODY
            for x in (max_discovery_bytes, max_bundle_bytes)) or
            type(max_total_bytes) is not int or not 1 <= max_total_bytes <= MAX_TOTAL or
            type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60):
        raise ValueError("invalid_attestation_transport_limits")
    return path


def _observation(code=None, *, status=None, headers=None, raw=None, complete=False):
    return {"status_code": status, "response_headers": headers or {}, "body_retention": _RETENTION,
            "body_sha256": None if raw is None else _sha(raw),
            "body_size_bytes": None if raw is None else len(raw), "body_complete": complete,
            "error": None if code is None else {"code": code}}


def _empty(code, path=None):
    return {"request_path": path, "discovery": _observation(code), "bundles": [], "next_cursor": None}


def _perform_get(*, repository, digest, predicate_type, per_page=10, cursor=None,
                 max_discovery_bytes=MAX_BODY, max_bundle_bytes=MAX_BODY,
                 max_total_bytes=MAX_TOTAL, timeout_seconds=30, token=None):
    result = _empty("transport_error")
    response = None
    try:
        path = _inputs(repository, digest, predicate_type, per_page, cursor, max_discovery_bytes,
                       max_bundle_bytes, max_total_bytes, timeout_seconds, token)
        result["request_path"] = path
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

        def open_response(url, authorized=False):
            headers = {"Accept-Encoding": "identity", "User-Agent": "delivery-review-attestations/1"}
            if authorized:
                headers.update(Accept="application/vnd.github+json", **{"X-GitHub-Api-Version": API_VERSION})
                if token:
                    headers["Authorization"] = "Bearer " + token
            request = urllib.request.Request(url, headers=headers, method="GET")
            try:
                return opener.open(request, timeout=timeout_seconds)
            except urllib.error.HTTPError as exc:
                return exc

        url = "https://api.github.com" + path
        response = open_response(url, True)
        status = response.getcode()
        if type(status) is not int or not 100 <= status <= 599 or response.geturl() != url:
            return _empty("redirect_refused", path)
        headers, error = _response_headers(response.headers, token, validator=evidence_headers)
        if error:
            result["discovery"] = _observation(error, status=status)
            return result
        code = _http_error(status, headers)
        if code == "redirect_refused":
            result["discovery"] = _observation(code, status=status, headers=headers)
            return result
        ceiling = min(max_discovery_bytes, max_total_bytes)
        raw = response.read(ceiling + 1)
        response.close()
        response = None
        if type(raw) is not bytes:
            return _empty("transport_error", path)
        complete = len(raw) <= ceiling
        if _contains_token(token, headers=headers.values(), body=raw, incomplete=not complete):
            return _empty("sensitive_response_refused", path)
        result["discovery"] = _observation(code if complete else "response_limit", status=status,
                                           headers=headers, raw=raw[:ceiling], complete=complete)
        if code or not complete:
            return result
        try:
            document = loads_strict(raw)
            entries = document.get("attestations") if type(document) is dict else None
            if type(entries) is not list or len(entries) > per_page:
                raise ValueError("invalid_attestation_listing")
            locators, identities = [], []
            for entry in entries:
                if type(entry) is not dict or not positive(entry.get("repository_id")):
                    raise ValueError("invalid_attestation_entry")
                location = _storage_url(entry.get("bundle_url"))
                if location in locators or token and token in location:
                    raise ValueError("invalid_attestation_locator")
                locators.append(location)
                identities.append(entry["repository_id"])
            if any(_signed_secret_echo(location, headers, b"") for location in locators):
                return _empty("sensitive_response_refused", path)
        except (ContractError, ValueError, TypeError):
            result["discovery"]["error"] = {"code": "attestation_discovery_invalid"}
            # An invalid discovery document may prevent enumerating every signed
            # locator. Do not retain header values that could echo an unparsed
            # credential; the original body digest/status remain recorded.
            result["discovery"]["response_headers"] = {}
            return result
        try:
            result["next_cursor"] = next_evidence_page(headers, path)
        except (ContractError, ValueError, TypeError):
            result["discovery"]["error"] = {"code": "pagination_incomplete"}
            return result
        remaining = max_total_bytes - len(raw)
        for index, (location, repository_id) in enumerate(zip(locators, identities), 1):
            entry = {"ordinal": index, "repository_id": repository_id, "locator_sha256": _sha(location.encode("ascii")),
                     "response": _result("capture_limit")}
            result["bundles"].append(entry)
            if remaining <= 0:
                result["discovery"]["error"] = {"code": "attestation_bundle_limit"}
                break
            try:
                response = open_response(location)
                bundle_status = response.getcode()
                if (type(bundle_status) is not int or not 100 <= bundle_status <= 599 or
                        response.geturl() != location):
                    entry["response"] = _result("redirect_refused")
                    continue
                bundle_headers, error = _response_headers(response.headers, token, validator=evidence_headers)
                if error:
                    entry["response"] = _result(error, status=bundle_status)
                    continue
                if any(_signed_secret_echo(loc, bundle_headers, b"") for loc in locators):
                    entry["response"] = _result("sensitive_response_refused", status=bundle_status)
                    continue
                code = _http_error(bundle_status, bundle_headers)
                if code == "redirect_refused":
                    entry["response"] = _result(code, status=bundle_status, headers=bundle_headers)
                    continue
                limit = min(remaining, max_bundle_bytes)
                bundle_raw = response.read(limit + 1)
                if type(bundle_raw) is not bytes:
                    entry["response"] = _result("transport_error", status=bundle_status)
                    continue
                bundle_complete = len(bundle_raw) <= limit
                # A refused secret echo still consumed this operation's byte
                # budget; omitting unsafe evidence must not permit more reads.
                remaining -= min(len(bundle_raw), limit)
                if (_contains_token(token, headers=bundle_headers.values(), body=bundle_raw, incomplete=not bundle_complete) or
                        any(_signed_secret_echo(loc, bundle_headers, bundle_raw, incomplete=not bundle_complete) for loc in locators)):
                    entry["response"] = _result("sensitive_response_refused", status=bundle_status)
                    continue
                entry["response"] = _result(code if bundle_complete else "response_limit", status=bundle_status,
                    headers=bundle_headers, body=bundle_raw[:limit], complete=bundle_complete)
            except (TimeoutError, socket.timeout):
                entry["response"] = _result("request_timeout")
            except urllib.error.URLError as exc:
                entry["response"] = _result("request_timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "transport_error")
            finally:
                if response is not None:
                    response.close()
                    response = None
        return result
    except (TimeoutError, socket.timeout):
        result["discovery"]["error"] = {"code": "request_timeout"}
        return result
    except urllib.error.URLError as exc:
        result["discovery"]["error"] = {"code": "request_timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "transport_error"}
        return result
    except Exception:
        result["discovery"]["error"] = {"code": "transport_error"}
        return result
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _encode(value):
    result = {**value, "bundles": [{**entry, "response": json.loads(_encode_result(entry["response"]))}
                                   for entry in value["bundles"]]}
    return json.dumps(result, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _decode(raw, *, path, per_page, max_bundle_bytes, max_total_bytes, token, max_discovery_bytes=MAX_BODY):
    if type(raw) is not bytes or len(raw) > ((max_total_bytes + 2) // 3) * 4 + 1024 * 1024:
        raise ValueError("invalid_attestation_worker_response")
    value = json.loads(raw)
    if type(value) is not dict or set(value) != {"request_path", "discovery", "bundles", "next_cursor"}:
        raise ValueError("invalid_attestation_worker_response")
    if value["request_path"] not in {None, path}:
        raise ValueError("invalid_attestation_worker_path")
    discovery = value["discovery"]
    fields = {"status_code", "response_headers", "body_retention", "body_sha256", "body_size_bytes", "body_complete", "error"}
    if type(discovery) is not dict or set(discovery) != fields or discovery["body_retention"] != _RETENTION:
        raise ValueError("invalid_attestation_worker_discovery")
    status, size, digest, error = (discovery[key] for key in ("status_code", "body_size_bytes", "body_sha256", "error"))
    if (status is not None and (type(status) is not int or not 100 <= status <= 599) or
            type(discovery["body_complete"]) is not bool or
            error is not None and (type(error) is not dict or set(error) != {"code"} or error["code"] not in _ERRORS)):
        raise ValueError("invalid_attestation_worker_discovery")
    if size is None:
        if digest is not None or discovery["body_complete"] or error is None:
            raise ValueError("invalid_attestation_worker_digest")
    elif type(size) is not int or not 0 <= size <= min(max_discovery_bytes, max_total_bytes) or type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("invalid_attestation_worker_digest")
    if error is None and (status != 200 or not discovery["body_complete"]):
        raise ValueError("invalid_attestation_worker_completion")
    discovery["response_headers"] = evidence_headers(discovery["response_headers"])
    if _contains_token(token, headers=discovery["response_headers"].values()):
        return _empty("sensitive_response_refused", path)
    if (error is None or value["next_cursor"] is not None) and value["next_cursor"] != next_evidence_page(discovery["response_headers"], path):
        raise ValueError("invalid_attestation_worker_cursor")
    bundles = value["bundles"]
    if type(bundles) is not list or len(bundles) > per_page:
        raise ValueError("invalid_attestation_worker_bundles")
    total, locators = size or 0, set()
    for index, entry in enumerate(bundles, 1):
        if (type(entry) is not dict or set(entry) != {"ordinal", "repository_id", "locator_sha256", "response"} or
                type(entry["ordinal"]) is not int or entry["ordinal"] != index or not positive(entry["repository_id"]) or
                type(entry["locator_sha256"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", entry["locator_sha256"]) or
                entry["locator_sha256"] in locators):
            raise ValueError("invalid_attestation_worker_entry")
        locators.add(entry["locator_sha256"])
        entry["response"] = _decode_result(json.dumps(entry["response"]).encode(), max_bundle_bytes, token,
                                            validator=evidence_headers)
        total += len(entry["response"]["body"] or b"")
    if total > max_total_bytes:
        raise ValueError("invalid_attestation_worker_budget")
    return value


class GitHubAttestationTransport:
    def __init__(self, *, token=None):
        if not _valid_token(token):
            raise ValueError("invalid_token_format")
        self._token = token

    def get(self, repository, digest, predicate_type, *, per_page=10, cursor=None,
            max_discovery_bytes=MAX_BODY, max_bundle_bytes=MAX_BODY, max_total_bytes=MAX_TOTAL, timeout_seconds=30):
        request = dict(repository=repository, digest=digest, predicate_type=predicate_type, per_page=per_page,
            cursor=cursor, max_discovery_bytes=max_discovery_bytes, max_bundle_bytes=max_bundle_bytes,
            max_total_bytes=max_total_bytes, timeout_seconds=timeout_seconds, token=self._token)
        process, path = None, None
        started = time.monotonic()
        try:
            path = _inputs(**request)
            process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parent, env=_worker_environment(self._token),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired("attestation-worker", timeout_seconds)
            raw, _ = process.communicate(json.dumps(request, separators=(",", ":")).encode("ascii"), timeout=remaining)
            if process.returncode != 0:
                return _empty("transport_error", path)
            return _decode(raw, path=path, per_page=per_page, max_bundle_bytes=max_bundle_bytes,
                           max_total_bytes=max_total_bytes, token=self._token,
                           max_discovery_bytes=max_discovery_bytes)
        except subprocess.TimeoutExpired:
            return _empty("request_timeout", path)
        except Exception:
            return _empty("transport_error", path)
        finally:
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate()


def _worker_main():
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("invalid_worker_input")
        request = json.loads(raw)
        if type(request) is not dict or set(request) != {"repository", "digest", "predicate_type", "per_page", "cursor",
                "max_discovery_bytes", "max_bundle_bytes", "max_total_bytes", "timeout_seconds", "token"}:
            raise ValueError("invalid_worker_input")
        result = _perform_get(**request)
    except Exception:
        result = _empty("transport_error")
    sys.stdout.buffer.write(_encode(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main() if sys.argv[1:] == ["--worker"] else 2)
