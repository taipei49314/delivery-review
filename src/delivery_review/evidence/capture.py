"""Bounded create-once SPEC-001 captures, separate from report-v1 bundles.

Bodies remain opaque, including malformed JSON and explicitly incomplete prefixes.
Integrity checks neither interpret GitHub payloads nor authenticate their claims.
Keep the directory exclusively owned during IO; local digests are not signatures.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
from types import MappingProxyType

from ..contracts import ContractError, MAX_JSON_BYTES, canonical_bytes, loads_strict
from ..observation_profile import load_observation_profile
from ..providers.github_routes import (
    ACQUISITION_ERRORS, validate_record_route, validate_response_headers,
)
from .store import _directories, _read, _scan, _write


class CaptureError(ContractError):
    """Invalid, incomplete, unsafe or over-budget capture storage."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise CaptureError(message)


def _boundary(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except CaptureError:
            raise
        except ContractError as exc:
            # Avoid echoing an untrusted profile, URL, header or response body.
            raise CaptureError("capture validation failed") from exc
        except OSError as exc:
            raise CaptureError("capture filesystem operation failed") from exc
    return wrapped


@dataclass(frozen=True)
class CaptureLimits:
    """Independent reader/writer limits; values cannot exceed SPEC-001 ceilings.

The body budget excludes the separately bounded profile and capture manifest.
Profile limits may further lower response bytes, total bytes, requests and pages.
"""

    max_response_bytes: int = MAX_JSON_BYTES
    max_total_bytes: int = 100 * 1024 * 1024
    max_requests: int = 200
    max_pages: int = 10
    max_manifest_bytes: int = MAX_JSON_BYTES
    max_profile_bytes: int = MAX_JSON_BYTES

    def __post_init__(self):
        ceilings = {"max_response_bytes": MAX_JSON_BYTES,
                    "max_total_bytes": 100 * 1024 * 1024,
                    "max_requests": 200, "max_pages": 10,
                    "max_manifest_bytes": MAX_JSON_BYTES,
                    "max_profile_bytes": MAX_JSON_BYTES}
        for name, maximum in ceilings.items():
            value = getattr(self, name)
            _need(type(value) is int and 1 <= value <= maximum,
                  "capture limits must be positive integers within hard ceilings")


DEFAULT_LIMITS = CaptureLimits()
_MANIFEST_FIELDS = {"kind", "capture_version", "mapper_version", "observed_at",
                    "collector", "profile", "termination", "records"}
_RECORD_FIELDS = {"id", "phase", "role", "workflow_id", "run_id", "attempt",
                  "job_id", "check_run_id", "page", "request", "observed_at",
                  "status_code", "response_headers", "body", "error"}
_METADATA_FIELDS = _RECORD_FIELDS - {"id", "body"}
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SHA = re.compile(r"[0-9a-f]{40}")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
# Reserve the bounded collector's entire known diagnostic vocabulary before
# accepting a record. A response must not consume the completion marker's room.
# Arbitrary caller-supplied reasons remain subject to the ordinary seal limit.
_TERMINATION_RESERVE = sorted(ACQUISITION_ERRORS | {
    "collection_incomplete", "subject_unavailable", "pagination_incomplete", "unsupported_fork",
})


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _check_manifest_room(manifest: dict, limits: CaptureLimits) -> None:
    reserved = {**manifest, "termination": {"status": "stopped", "reason_codes": _TERMINATION_RESERVE}}
    _need(len(canonical_bytes(reserved) + b"\n") <= limits.max_manifest_bytes,
          "capture manifest byte limit exceeded")


def _timestamp(value) -> None:
    _need(type(value) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is not None,
          "invalid capture timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CaptureError("invalid capture timestamp") from exc


def _limits(value) -> CaptureLimits:
    _need(type(value) is CaptureLimits, "expected CaptureLimits")
    return value


def _budget(limits: CaptureLimits, profile: dict, name: str) -> int:
    return min(getattr(limits, name), profile["limits"][name])


def _profile(raw: bytes, limits: CaptureLimits) -> dict:
    _need(type(raw) is bytes and len(raw) <= limits.max_profile_bytes,
          "capture profile byte limit exceeded or invalid bytes")
    try:
        return load_observation_profile(raw)
    except ContractError as exc:
        raise CaptureError("invalid observation profile") from exc


def _collector(value) -> None:
    _need(type(value) is dict and set(value) == {"version", "source_sha"}, "invalid capture collector")
    _need(type(value["version"]) is str and 0 < len(value["version"]) <= 64
          and not any(ord(c) < 32 or ord(c) == 127 for c in value["version"]), "invalid collector version")
    _need(type(value["source_sha"]) is str and _SHA.fullmatch(value["source_sha"]) is not None,
          "invalid collector source identity")


def _termination(value) -> None:
    _need(type(value) is dict and set(value) == {"status", "reason_codes"}, "invalid capture termination")
    _need(type(value["status"]) is str and value["status"] in {"completed", "stopped"},
          "invalid capture termination status")
    reasons = value["reason_codes"]
    _need(type(reasons) is list and len(reasons) <= 200
          and all(type(code) is str and _CODE.fullmatch(code) is not None for code in reasons),
          "invalid capture termination reasons")
    _need(len(reasons) == len(set(reasons)), "duplicate capture termination reason")
    _need(value["status"] != "stopped" or bool(reasons), "stopped capture requires a reason")


def _file_entry(value, *, path: str, limit: int, complete: bool = False) -> None:
    fields = {"path", "size_bytes", "sha256"} | ({"complete"} if complete else set())
    _need(type(value) is dict and set(value) == fields, "invalid capture file entry")
    _need(value["path"] == path, "capture file path does not match its record")
    _need(type(value["size_bytes"]) is int and 0 <= value["size_bytes"] <= limit,
          "capture file byte limit exceeded or invalid size")
    _need(type(value["sha256"]) is str and _DIGEST.fullmatch(value["sha256"]) is not None,
          "invalid capture file digest")
    if complete:
        _need(type(value["complete"]) is bool, "invalid body completion flag")


def _headers(value) -> None:
    try:
        validate_response_headers(value)
    except ContractError as exc:
        raise CaptureError("unsafe or oversized capture response headers") from exc


def _logical_key(record: dict) -> tuple:
    return tuple(record[name] for name in ("phase", "role", "workflow_id", "run_id", "attempt",
                                          "job_id", "check_run_id", "page"))


def _record(record, index: int, profile: dict, limits: CaptureLimits) -> None:
    _need(type(record) is dict and set(record) == _RECORD_FIELDS, "invalid capture record fields")
    _need(record["id"] == f"r{index:06d}", "nonmonotonic capture record identity")
    _timestamp(record["observed_at"])
    _need(type(record["role"]) is str and type(record["phase"]) is str, "invalid capture role or phase")
    try:
        validate_record_route(record, profile)
    except (ContractError, ValueError) as exc:
        raise CaptureError("invalid capture request route or metadata") from exc
    if record["page"] is not None:
        _need(type(record["page"]) is int and 1 <= record["page"] <= _budget(limits, profile, "max_pages"),
              "capture page limit exceeded")
    _headers(record["response_headers"])
    status, error, body = record["status_code"], record["error"], record["body"]
    _need(status is None or (type(status) is int and 100 <= status <= 599), "invalid capture HTTP status")
    if error is not None:
        _need(type(error) is dict and set(error) == {"code"}
              and type(error["code"]) is str and error["code"] in ACQUISITION_ERRORS,
              "invalid capture acquisition error")
    if status is None:
        _need(error is not None and body is None and not record["response_headers"],
              "response data without an HTTP response")
    elif status != 200:
        _need(error is not None, "non-success HTTP response requires an acquisition error")
    if body is None:
        _need(error is not None, "missing response body requires an acquisition error")
    else:
        _file_entry(body, path=f"raw/r{index:06d}.body",
                    limit=_budget(limits, profile, "max_response_bytes"), complete=True)
        _need(body["complete"] or error is not None, "incomplete body requires an acquisition error")


def _manifest(raw: bytes, limits: CaptureLimits) -> dict:
    _need(type(raw) is bytes and len(raw) <= limits.max_manifest_bytes,
          "capture manifest byte limit exceeded or invalid bytes")
    try:
        manifest = loads_strict(raw)
    except ContractError as exc:
        raise CaptureError("invalid capture manifest JSON") from exc
    _need(type(manifest) is dict and set(manifest) == _MANIFEST_FIELDS, "invalid capture manifest fields")
    _need(manifest["kind"] == "delivery-review-github-observation-capture", "unsupported capture kind")
    for field in ("capture_version", "mapper_version"):
        _need(type(manifest[field]) is int and manifest[field] == 1, "unsupported capture or mapper version")
    _timestamp(manifest["observed_at"])
    _collector(manifest["collector"])
    _termination(manifest["termination"])
    _file_entry(manifest["profile"], path="profile.json", limit=limits.max_profile_bytes)
    _need(type(manifest["records"]) is list and len(manifest["records"]) <= limits.max_requests,
          "capture request count limit exceeded or invalid records")
    return manifest


def _entries(manifest: dict, profile: dict, limits: CaptureLimits) -> dict[str, dict]:
    records = manifest["records"]
    _need(len(records) <= _budget(limits, profile, "max_requests"), "capture request count limit exceeded")
    entries, seen, total = {}, set(), 0
    previous = manifest["observed_at"]
    for index, record in enumerate(records, 1):
        _record(record, index, profile, limits)
        _need(record["observed_at"] >= previous, "capture observation time moved backwards")
        previous = record["observed_at"]
        key = _logical_key(record)
        _need(key not in seen, "duplicate logical capture request")
        seen.add(key)
        if record["body"] is not None:
            row = record["body"]
            entries[row["path"]] = row
            total += row["size_bytes"]
    _need(total <= _budget(limits, profile, "max_total_bytes"), "capture total body byte limit exceeded")
    return entries


@dataclass(frozen=True)
class VerifiedCapture:
    """Verified immutable byte snapshot, without interpreting response payloads."""

    manifest_bytes: bytes
    profile_bytes: bytes
    bodies: Mapping[str, bytes]

    @property
    def manifest(self) -> dict:
        return loads_strict(self.manifest_bytes)

    @property
    def profile(self) -> dict:
        return loads_strict(self.profile_bytes)


@_boundary
def parse_capture(manifest_bytes: bytes, profile_bytes: bytes, bodies: Mapping[str, bytes], *,
                  limits: CaptureLimits = DEFAULT_LIMITS) -> VerifiedCapture:
    """Verify captured bytes and metadata without reading a saved normalized result."""
    _limits(limits)
    manifest = _manifest(manifest_bytes, limits)
    profile = _profile(profile_bytes, limits)
    profile_row = manifest["profile"]
    _need(len(profile_bytes) == profile_row["size_bytes"] and _sha(profile_bytes) == profile_row["sha256"],
          "capture profile size or digest mismatch")
    entries = _entries(manifest, profile, limits)
    _need(isinstance(bodies, Mapping) and len(bodies) == len(entries), "capture body inventory differs")
    _need(all(type(path) is str for path in bodies) and set(bodies) == set(entries),
          "capture body inventory differs")
    snapshot = {}
    for path, row in entries.items():
        raw = bodies[path]
        _need(type(raw) is bytes and len(raw) == row["size_bytes"], "capture body size mismatch")
        _need(_sha(raw) == row["sha256"], "capture body digest mismatch")
        snapshot[path] = raw
    return VerifiedCapture(manifest_bytes, profile_bytes, MappingProxyType(snapshot))


@_boundary
def verify_capture(source: str | Path, *, limits: CaptureLimits = DEFAULT_LIMITS) -> VerifiedCapture:
    """Read a bounded exact capture tree; reject links, extras, missing or altered bytes."""
    _limits(limits)
    root = Path(os.path.abspath(source))
    _directories(root)
    manifest_bytes = _read(root / "capture.json", limits.max_manifest_bytes)
    manifest = _manifest(manifest_bytes, limits)
    profile_bytes = _read(root / "profile.json", limits.max_profile_bytes, manifest["profile"])
    profile = _profile(profile_bytes, limits)
    entries = _entries(manifest, profile, limits)
    files = {"capture.json", "profile.json", *entries}
    _scan(root, files)
    bodies = {path: _read(root / path, _budget(limits, profile, "max_response_bytes"), row)
              for path, row in entries.items()}
    verified = parse_capture(manifest_bytes, profile_bytes, bodies, limits=limits)
    _scan(root, files)
    _need(_read(root / "capture.json", limits.max_manifest_bytes) == manifest_bytes,
          "capture manifest changed while reading")
    return verified


class CaptureWriter:
    """Record responses before mapping, then seal once with a final manifest.

Invalid arguments do not create records. An IO failure may leave a partial tree;
that writer is unusable and the directory is not reused. No raw body is parsed.
"""

    @_boundary
    def __init__(self, destination: str | Path, profile_bytes: bytes, *, collector: dict,
                 observed_at: str, limits: CaptureLimits = DEFAULT_LIMITS):
        self._closed = False
        self._failed = False
        self._limits = _limits(limits)
        self._profile = _profile(profile_bytes, limits)
        _collector(collector)
        _timestamp(observed_at)
        self._profile_bytes = profile_bytes
        self._bodies = {}
        self._manifest = {"kind": "delivery-review-github-observation-capture", "capture_version": 1,
                          "mapper_version": 1, "observed_at": observed_at,
                          "collector": loads_strict(canonical_bytes(collector)),
                          "profile": {"path": "profile.json", "size_bytes": len(profile_bytes),
                                      "sha256": _sha(profile_bytes)},
                          "termination": {"status": "completed", "reason_codes": []}, "records": []}
        _check_manifest_room(self._manifest, limits)
        self._destination = Path(os.path.abspath(destination))
        _directories(self._destination.parent)
        self._destination.mkdir()
        try:
            _write(self._destination / "profile.json", profile_bytes)
        except (ContractError, OSError):
            self._failed = True
            raise

    def _active(self) -> None:
        _need(not self._closed and not self._failed, "capture writer is sealed or failed")

    @_boundary
    def record(self, metadata: dict, body: bytes | None, *, complete: bool = True) -> dict:
        self._active()
        _need(type(metadata) is dict and set(metadata) == _METADATA_FIELDS, "invalid capture record metadata")
        _need(type(complete) is bool, "invalid body completion flag")
        _need(body is None or type(body) is bytes, "response body must be original bytes or null")
        if body is not None:
            _need(len(body) <= _budget(self._limits, self._profile, "max_response_bytes"),
                  "capture response byte limit exceeded")
        try:
            record = loads_strict(canonical_bytes(metadata))
        except ContractError as exc:
            raise CaptureError("invalid capture record metadata") from exc
        index = len(self._manifest["records"]) + 1
        record["id"] = f"r{index:06d}"
        record["body"] = (None if body is None else
                          {"path": f"raw/r{index:06d}.body", "size_bytes": len(body),
                           "sha256": _sha(body), "complete": complete})
        prospective = {**self._manifest, "records": [*self._manifest["records"], record]}
        _entries(prospective, self._profile, self._limits)
        _check_manifest_room(prospective, self._limits)
        if body is not None:
            try:
                (self._destination / "raw").mkdir(exist_ok=True)
                _write(self._destination / record["body"]["path"], body)
            except (ContractError, OSError):
                self._failed = True
                raise
            self._bodies[record["body"]["path"]] = body
        self._manifest["records"].append(record)
        return loads_strict(canonical_bytes(record))

    @_boundary
    def seal(self, *, status: str = "completed", reason_codes=()) -> VerifiedCapture:
        self._active()
        _need(type(reason_codes) in {list, tuple}, "invalid capture termination reasons")
        manifest = {**self._manifest, "termination": {"status": status, "reason_codes": list(reason_codes)}}
        manifest_bytes = canonical_bytes(manifest) + b"\n"
        verified = parse_capture(manifest_bytes, self._profile_bytes, self._bodies, limits=self._limits)
        try:
            _scan(self._destination, {"profile.json", *self._bodies})
            _need(_read(self._destination / "profile.json", self._limits.max_profile_bytes) == self._profile_bytes,
                  "capture profile changed before sealing")
            for record in manifest["records"]:
                if record["body"] is not None:
                    row = record["body"]
                    _read(self._destination / row["path"],
                          _budget(self._limits, self._profile, "max_response_bytes"), row)
            _write(self._destination / "capture.json", manifest_bytes)
        except (ContractError, OSError):
            self._failed = True
            raise
        self._closed = True
        return verified
