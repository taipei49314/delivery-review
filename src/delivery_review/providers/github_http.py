"""Bounded, read-only GitHub HTTP acquisition for SPEC-001.

The first-party worker has no reviewed-PR code or dependencies. Its parent owns
the whole-request deadline, including DNS/connect and slow response streams.
Only completed bounded worker messages return to the collector; a killed worker
does not supply a partial HTTP observation. Bodies remain original bytes.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

# The isolated worker is launched by absolute filename, without PYTHONPATH or a
# reviewed checkout on its import path. Add only this first-party package root.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from delivery_review.providers.github_routes import (
        ACQUISITION_ERRORS, ALLOWED_RESPONSE_HEADERS, API_VERSION,
        validate_api_path, validate_response_headers,
    )
else:
    from .github_routes import (
        ACQUISITION_ERRORS, ALLOWED_RESPONSE_HEADERS, API_VERSION,
        validate_api_path, validate_response_headers,
    )


_HOST = "https://api.github.com"
_MAX_BODY = 2 * 1024 * 1024
_MAX_WORKER_INPUT = 16384
_TOKEN = re.compile(r"[A-Za-z0-9_.-]{1,4096}")
_RESULT_KEYS = {"status_code", "response_headers", "body", "complete", "error"}


def _result(code=None, *, status=None, headers=None, body=None, complete=False):
    return {"status_code": status, "response_headers": {} if headers is None else headers,
            "body": body, "complete": complete,
            "error": None if code is None else {"code": code}}


def _valid_token(token) -> bool:
    return token is None or (type(token) is str and _TOKEN.fullmatch(token) is not None)


def _validate_request(path, api_version, max_bytes, timeout_seconds, token) -> None:
    if (api_version != API_VERSION or type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BODY
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 30 or not _valid_token(token)):
        raise ValueError("invalid transport inputs")
    validate_api_path(path)


def _contains_token(token, *, headers=(), body=None, incomplete=False) -> bool:
    if token is None:
        return False
    if any(token in value for value in headers if isinstance(value, str)):
        return True
    if body is not None:
        encoded = token.encode("ascii")
        if encoded in body:
            return True
        # Do not retain a truncated token prefix at the response-limit boundary.
        if incomplete and any(body.endswith(encoded[:n]) for n in range(1, min(len(encoded), len(body)) + 1)):
            return True
    return False


def _response_headers(headers, token, *, validator=validate_response_headers):
    items = list(headers.items())
    if _contains_token(token, headers=[value for _, value in items]):
        return {}, "sensitive_response_refused"
    retained = {}
    for name, value in items:
        if not isinstance(name, str) or not isinstance(value, str):
            return {}, "unsafe_response_headers"
        name = name.lower()
        if name not in ALLOWED_RESPONSE_HEADERS:
            continue
        if name in retained:
            return {}, "unsafe_response_headers"
        retained[name] = value
    try:
        return validator(retained), None
    except (ValueError, TypeError):
        # In particular, never retain a credential-bearing or alternate-host
        # Link value as either metadata or an interpolated exception message.
        return {}, "unsafe_response_headers"


def _http_error(status, headers):
    if 300 <= status < 400:
        return "redirect_refused"
    if status == 401:
        return "authentication_unavailable"
    if status == 429 or (status == 403 and (headers.get("x-ratelimit-remaining", "").strip() == "0" or "retry-after" in headers)):
        return "rate_limited"
    if status in {403, 404}:
        return "resource_unavailable"
    return None if status == 200 else "http_error"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _perform_get(path, *, api_version, max_bytes, timeout_seconds, token=None):
    """One worker-side request; test with a controlled opener, never live PRs."""
    status, headers, response = None, {}, None
    try:
        _validate_request(path, api_version, max_bytes, timeout_seconds, token)
        request_headers = {"Accept": "application/vnd.github+json", "Accept-Encoding": "identity",
                           "X-GitHub-Api-Version": api_version, "User-Agent": "delivery-review-observations/1"}
        if token is not None:
            request_headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(_HOST + path, headers=request_headers, method="GET")
        # Explicitly disable environment/system proxy discovery. Redirects never
        # become a second request, even to the same GitHub host.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as exc:
            response = exc
        status = response.getcode()
        if type(status) is not int or not 100 <= status <= 599:
            return _result("transport_error")
        if response.geturl() != request.full_url:
            return _result("redirect_refused", status=status)
        headers, header_error = _response_headers(response.headers, token)
        if header_error is not None:
            return _result(header_error, status=status)
        code = _http_error(status, headers)
        if code == "redirect_refused":
            # Redirect bodies can repeat signed Location URLs. Refusal retains
            # neither Location nor its body and never follows the destination.
            return _result(code, status=status, headers=headers)
        raw = response.read(max_bytes + 1)
        if type(raw) is not bytes:
            return _result("transport_error", status=status, headers=headers)
        complete = len(raw) <= max_bytes
        if _contains_token(token, headers=headers.values(), body=raw, incomplete=not complete):
            return _result("sensitive_response_refused", status=status)
        return _result(code if complete else "response_limit", status=status, headers=headers,
                       body=raw[:max_bytes], complete=complete)
    except (TimeoutError, socket.timeout):
        return _result("request_timeout", status=status, headers=headers)
    except urllib.error.URLError as exc:
        code = "request_timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "transport_error"
        return _result(code, status=status, headers=headers)
    except Exception:
        # Never expose network exceptions, URLs, raw transport stderr or secrets.
        return _result("transport_error", status=status, headers=headers)
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass


def _worker_environment(token):
    # Preserve only runtime/system locations and locale. In particular, no
    # GH_TOKEN/GITHUB_TOKEN, proxy credentials, PYTHONPATH or other app secrets.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "PATHEXT", "COMSPEC",
               "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    return {name: value for name, value in os.environ.items()
            if name.upper() in allowed and (token is None or token not in value)}


def _encode_result(result):
    encoded = dict(result)
    body = encoded.pop("body")
    encoded["body_b64"] = None if body is None else base64.b64encode(body).decode("ascii")
    return json.dumps(encoded, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def _decode_result(raw, max_bytes, token, *, validator=validate_response_headers):
    if type(raw) is not bytes or len(raw) > ((max_bytes + 2) // 3) * 4 + 65536:
        raise ValueError("invalid worker response")
    result = json.loads(raw)
    if type(result) is not dict or set(result) != (_RESULT_KEYS - {"body"}) | {"body_b64"}:
        raise ValueError("invalid worker response")
    body = result.pop("body_b64")
    if body is not None:
        if type(body) is not str:
            raise ValueError("invalid worker response")
        body = base64.b64decode(body, validate=True)
        if len(body) > max_bytes:
            raise ValueError("invalid worker response")
    result["body"] = body
    status, complete, error = result["status_code"], result["complete"], result["error"]
    if (status is not None and (type(status) is not int or not 100 <= status <= 599)) or type(complete) is not bool:
        raise ValueError("invalid worker response")
    result["response_headers"] = validator(result["response_headers"])
    if error is not None and (type(error) is not dict or set(error) != {"code"}
                              or error["code"] not in ACQUISITION_ERRORS):
        raise ValueError("invalid worker response")
    if (complete and body is None) or (not complete and error is None) or (error is None and status != 200):
        raise ValueError("invalid worker response")
    if _contains_token(token, headers=result["response_headers"].values(), body=body, incomplete=not complete):
        return _result("sensitive_response_refused", status=status)
    return result


class GitHubTransport:
    """Read only the fixed SPEC-001 API routes within one parent-owned deadline."""

    def __init__(self, *, token: str | None = None):
        if not _valid_token(token):
            raise ValueError("invalid token format")
        self._token = token

    def get(self, path: str, *, api_version: str, max_bytes: int, timeout_seconds: int) -> dict:
        try:
            _validate_request(path, api_version, max_bytes, timeout_seconds, self._token)
        except (ValueError, TypeError):
            return _result("transport_error")
        payload = json.dumps({"path": path, "api_version": api_version, "max_bytes": max_bytes,
                              "timeout_seconds": timeout_seconds, "token": self._token},
                             ensure_ascii=True, separators=(",", ":")).encode("ascii")
        process = None
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--worker"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                cwd=Path(__file__).resolve().parent, env=_worker_environment(self._token),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            remaining = timeout_seconds - (time.monotonic() - started)
            if remaining <= 0:
                raise subprocess.TimeoutExpired("github-http-worker", timeout_seconds)
            output, _ = process.communicate(payload, timeout=remaining)
            if process.returncode != 0:
                return _result("transport_error")
            return _decode_result(output, max_bytes, self._token)
        except subprocess.TimeoutExpired:
            return _result("request_timeout")
        except Exception:
            return _result("transport_error")
        finally:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.communicate()
                    except Exception:
                        pass


def _worker_main():
    try:
        raw = sys.stdin.buffer.read(_MAX_WORKER_INPUT + 1)
        if len(raw) > _MAX_WORKER_INPUT:
            raise ValueError("invalid worker input")
        request = json.loads(raw)
        if type(request) is not dict or set(request) != {"path", "api_version", "max_bytes", "timeout_seconds", "token"}:
            raise ValueError("invalid worker input")
        result = _perform_get(**request)
    except Exception:
        result = _result("transport_error")
    sys.stdout.buffer.write(_encode_result(result))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(2)
    raise SystemExit(_worker_main())
