"""Bounded artifact metadata/download GETs with credential-separated redirects.

Signed storage locators are transient credentials: neither the Location header
nor redirect body is retained. Final archive bytes and safe API headers remain
original observations. This transport makes no artifact/job binding claim.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from delivery_review.providers.github_http import (
    _NoRedirect, _contains_token, _decode_result, _encode_result, _http_error,
    _response_headers, _result, _valid_token, _worker_environment,
)
from delivery_review.providers.github_routes import API_VERSION
from delivery_review.providers.github_evidence_routes import artifact_path, evidence_headers

MAX_ARCHIVE_BYTES = 25 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024


def _path(operation, repository, identity, page, per_page):
    return artifact_path(operation, repository, identity, page, per_page)


def _inputs(operation, repository, identity, page, per_page, max_bytes, timeout_seconds, token):
    path = _path(operation, repository, identity, page, per_page)
    maximum = MAX_ARCHIVE_BYTES if operation == "archive" else MAX_METADATA_BYTES
    if (not _valid_token(token) or type(max_bytes) is not int or not 1 <= max_bytes <= maximum or
            type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 60):
        raise ValueError("invalid_artifact_limits")
    return path


def _storage_url(value):
    # GitHub Actions v4's qualified Azure storage route. Unknown storage/CDN
    # domains need an explicit extension and qualification, never a broad suffix.
    if type(value) is not str or not 1 <= len(value) <= 8192 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("invalid_storage_locator")
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or
            parsed.port not in {None, 443} or parsed.fragment or not parsed.path.startswith("/") or
            not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", parsed.hostname or "")):
        raise ValueError("unqualified_storage_locator")
    return value


def _signed_secret_echo(location, headers, raw, *, incomplete=False):
    values = [location]
    for key, value in urllib.parse.parse_qsl(urllib.parse.urlsplit(location).query):
        if key.lower() in {"sig", "token", "jwt"} and len(value) >= 16:
            values.extend((value, urllib.parse.quote(value, safe="")))
    return any(_contains_token(value, headers=headers.values(), body=raw, incomplete=incomplete)
               for value in values if value.isascii())


def _envelope(response, redirect=None):
    return {"response": response, "api_redirect": redirect}


def _perform_get(*, operation, repository, identity, page=None, per_page=None,
                 max_bytes, timeout_seconds, token=None):
    response, redirect, status, headers = None, None, None, {}
    try:
        path = _inputs(operation, repository, identity, page, per_page, max_bytes, timeout_seconds, token)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        request_headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity",
                           "X-GitHub-Api-Version": API_VERSION, "User-Agent": "delivery-review-artifacts/1"}
        if token:
            request_headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request("https://api.github.com" + path, headers=request_headers, method="GET")
        def open_response(req):
            try:
                return opener.open(req, timeout=timeout_seconds)
            except urllib.error.HTTPError as exc:
                return exc
        response = open_response(request)
        status = response.getcode()
        if type(status) is not int or not 100 <= status <= 599 or response.geturl() != request.full_url:
            return _envelope(_result("redirect_refused"))
        headers, error = _response_headers(response.headers, token, validator=evidence_headers)
        if error:
            return _envelope(_result(error, status=status))
        location = None
        if status == 302 and operation == "archive":
            try:
                locations = response.headers.get_all("Location")
                if len(locations or []) != 1:
                    raise ValueError("ambiguous_storage_locator")
                location = _storage_url(locations[0])
                if token and token in location:
                    raise ValueError("sensitive_storage_locator")
            except (ValueError, TypeError):
                return _envelope(_result("redirect_refused", status=status, headers=headers))
            if _signed_secret_echo(location, headers, b""):
                return _envelope(_result("sensitive_response_refused", status=status))
            redirect = {"status_code": status, "response_headers": headers,
                        "body_retention": "omitted_ephemeral_storage_credentials"}
            response.close()
            request = urllib.request.Request(location, headers={"Accept-Encoding": "identity",
                "User-Agent": "delivery-review-artifacts/1"}, method="GET")
            response = open_response(request)
            status = response.getcode()
            if type(status) is not int or not 100 <= status <= 599 or response.geturl() != location:
                return _envelope(_result("redirect_refused"), redirect)
            headers, error = _response_headers(response.headers, token, validator=evidence_headers)
            if error:
                return _envelope(_result(error, status=status), redirect)
            if _signed_secret_echo(location, headers, b""):
                return _envelope(_result("sensitive_response_refused", status=status), redirect)
        code = _http_error(status, headers)
        if code == "redirect_refused":
            return _envelope(_result(code, status=status, headers=headers), redirect)
        raw = response.read(max_bytes + 1)
        if type(raw) is not bytes:
            return _envelope(_result("transport_error", status=status), redirect)
        complete = len(raw) <= max_bytes
        if (_contains_token(token, headers=headers.values(), body=raw, incomplete=not complete) or
                location and _signed_secret_echo(location, headers, raw, incomplete=not complete)):
            return _envelope(_result("sensitive_response_refused", status=status), redirect)
        return _envelope(_result(code if complete else "response_limit", status=status,
            headers=headers, body=raw[:max_bytes], complete=complete), redirect)
    except (TimeoutError, socket.timeout):
        return _envelope(_result("request_timeout"), redirect)
    except urllib.error.URLError as exc:
        code = "request_timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "transport_error"
        return _envelope(_result(code), redirect)
    except Exception:
        return _envelope(_result("transport_error"), redirect)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _encode(envelope):
    return json.dumps({"response": json.loads(_encode_result(envelope["response"])),
                       "api_redirect": envelope["api_redirect"]}, separators=(",", ":")).encode("ascii")


def _decode(raw, max_bytes, token):
    if type(raw) is not bytes or len(raw) > ((max_bytes + 2) // 3) * 4 + 131072:
        raise ValueError("invalid_artifact_worker_response")
    value = json.loads(raw)
    if type(value) is not dict or set(value) != {"response", "api_redirect"}:
        raise ValueError("invalid_artifact_worker_response")
    response = _decode_result(json.dumps(value["response"]).encode(), max_bytes, token, validator=evidence_headers)
    redirect = value["api_redirect"]
    if redirect is not None:
        if (type(redirect) is not dict or set(redirect) != {"status_code", "response_headers", "body_retention"} or
                type(redirect["status_code"]) is not int or redirect["status_code"] != 302 or
                redirect["body_retention"] != "omitted_ephemeral_storage_credentials"):
            raise ValueError("invalid_artifact_worker_redirect")
        redirect["response_headers"] = evidence_headers(redirect["response_headers"])
        if _contains_token(token, headers=redirect["response_headers"].values()):
            return _envelope(_result("sensitive_response_refused"))
    return _envelope(response, redirect)


class GitHubArtifactTransport:
    def __init__(self, *, token=None):
        if not _valid_token(token):
            raise ValueError("invalid_token_format")
        self._token = token

    def get(self, operation, repository, identity, *, page=None, per_page=None,
            max_bytes=MAX_METADATA_BYTES, timeout_seconds=30):
        request = dict(operation=operation, repository=repository, identity=identity, page=page,
                       per_page=per_page, max_bytes=max_bytes, timeout_seconds=timeout_seconds, token=self._token)
        process = None
        started = time.monotonic()
        try:
            _inputs(**request)
            payload = json.dumps(request, separators=(",", ":")).encode("ascii")
            process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parent, env=_worker_environment(self._token),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired("artifact-worker", timeout_seconds)
            raw, _ = process.communicate(payload, timeout=remaining)
            if process.returncode != 0:
                return _envelope(_result("transport_error"))
            return _decode(raw, max_bytes, self._token)
        except subprocess.TimeoutExpired:
            return _envelope(_result("request_timeout"))
        except Exception:
            return _envelope(_result("transport_error"))
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
        if type(request) is not dict or set(request) != {"operation", "repository", "identity", "page",
                "per_page", "max_bytes", "timeout_seconds", "token"}:
            raise ValueError("invalid_worker_input")
        result = _perform_get(**request)
    except Exception:
        result = _envelope(_result("transport_error"))
    sys.stdout.buffer.write(_encode(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main() if sys.argv[1:] == ["--worker"] else 2)
