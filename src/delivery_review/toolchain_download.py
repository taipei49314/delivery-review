"""First-party, credential-free worker for fixed hash-pinned tool downloads.

Only this module's standard-library code runs. The parent owns the full deadline,
including DNS, and never passes a PR URL, token, proxy, or script to this worker.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


MAX_DOWNLOAD_BYTES = 32 * 1024 * 1024
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ASSET_PATH = re.compile(r"/github-production-release-asset/[0-9]+/[0-9a-f-]+")


class DownloadError(ValueError):
    """A deliberately sanitized transport failure."""


def _need(condition):
    if not condition:
        raise DownloadError("tool_download_refused")


def _url(url, *, origin):
    _need(type(url) is str and 0 < len(url) <= 16384 and url.isascii()
          and not any(ord(c) <= 32 or ord(c) == 127 for c in url))
    parsed = urlsplit(url)
    _need(parsed.scheme == "https" and parsed.port is None and not parsed.username
          and not parsed.password and not parsed.fragment)
    if origin:
        _need(not parsed.query and (
            (parsed.netloc == "files.pythonhosted.org" and re.fullmatch(
                r"/packages/[a-f0-9]{2}/[a-f0-9]{2}/[a-f0-9]{60}/[a-z]+-[0-9.]+-py3-none-any\.whl", parsed.path))
            or (parsed.netloc == "github.com" and re.fullmatch(
                r"/cli/cli/releases/download/v[0-9.]+/gh_[0-9.]+_(?:windows_amd64\.zip|linux_amd64\.tar\.gz)", parsed.path))))
    else:
        _need(parsed.netloc == "release-assets.githubusercontent.com" and _ASSET_PATH.fullmatch(parsed.path))
    return parsed


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download(url, destination, *, size_bytes, sha256, timeout_seconds):
    """Stream at most the pinned size; redirects and HTTP error bodies are discarded."""
    origin = _url(url, origin=True)
    _need(type(size_bytes) is int and 0 < size_bytes <= MAX_DOWNLOAD_BYTES)
    _need(type(sha256) is str and _DIGEST.fullmatch(sha256))
    _need(type(timeout_seconds) in {int, float} and 0 < timeout_seconds <= 60)
    deadline = time.monotonic() + timeout_seconds
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    current = url
    for step in range(2):
        response = None
        try:
            remaining = deadline - time.monotonic()
            _need(remaining > 0)
            request = urllib.request.Request(current, headers={"User-Agent": "delivery-review-tools/1",
                "Accept-Encoding": "identity"}, method="GET")
            try:
                response = opener.open(request, timeout=remaining)
            except urllib.error.HTTPError as exc:
                response = exc
            _need(response.geturl() == current)
            status = response.getcode()
            if status in {301, 302, 303, 307, 308}:
                _need(step == 0 and origin.netloc == "github.com")
                locations = response.headers.get_all("Location", [])
                _need(len(locations) == 1)
                _url(locations[0], origin=False)
                current = locations[0]
                continue
            _need(status == 200)
            encoding = response.headers.get_all("Content-Encoding", [])
            _need(not encoding or encoding == ["identity"])
            lengths = response.headers.get_all("Content-Length", [])
            _need(not lengths or (len(lengths) == 1 and lengths[0].isascii()
                and lengths[0].isdigit() and int(lengths[0]) == size_bytes))
            digest, received = hashlib.sha256(), 0
            with Path(destination).open("xb") as output:
                while True:
                    _need(time.monotonic() < deadline)
                    chunk = response.read(min(65536, size_bytes - received + 1))
                    _need(type(chunk) is bytes)
                    if not chunk:
                        break
                    received += len(chunk)
                    _need(received <= size_bytes)
                    digest.update(chunk)
                    output.write(chunk)
            _need(received == size_bytes and digest.hexdigest() == sha256)
            return
        finally:
            if response is not None:
                response.close()
    raise DownloadError("tool_download_refused")


def main():
    try:
        raw = sys.stdin.buffer.read(16385)
        _need(len(raw) <= 16384)
        value = json.loads(raw)
        _need(type(value) is dict and set(value) == {"url", "destination", "size_bytes", "sha256", "timeout_seconds"})
        download(**value)
        return 0
    except Exception:
        # Never emit signed redirect URLs, response bodies, or environment data.
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
