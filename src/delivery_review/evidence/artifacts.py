"""Create-once artifact captures and deterministic mapping of original bytes.

Names select candidates only. Archive consistency is not producer authentication;
the caller must verify saved bundles and bind independent platform/source facts.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import time
from types import MappingProxyType

from ..contracts import ContractError, MAX_JSON_BYTES, canonical_bytes, loads_strict
from ..adapters.bundle_encoding import BundleEncodingError, decode_bundle_body
from ..inspection_profile import PREDICATE
from ..providers import github_artifact_http as artifact_http
from ..providers import github_attestation_http as attestation_http
from ..providers.github_evidence_routes import artifact_path, attestation_path, next_evidence_page
from ..providers.github_routes import positive
from .capture import _collector, _timestamp
from .store import _directories, _read, _scan, _write


class ArtifactCaptureError(ContractError):
    """Unsafe, inconsistent or incomplete capture storage; never a trust verdict."""


def _need(condition, reason):
    if not condition:
        raise ArtifactCaptureError(reason)


def _boundary(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except ArtifactCaptureError:
            raise
        except (ContractError, OSError, ValueError, TypeError, KeyError, RecursionError) as exc:
            raise ArtifactCaptureError("artifact_capture_boundary_failed") from exc
    return wrapped


@dataclass(frozen=True)
class ArtifactLimits:
    max_metadata_bytes: int = MAX_JSON_BYTES
    max_archive_bytes: int = 25 * 1024 * 1024
    max_bundle_bytes: int = MAX_JSON_BYTES
    max_total_bytes: int = 100 * 1024 * 1024
    max_requests: int = 200
    max_pages: int = 10
    max_manifest_bytes: int = MAX_JSON_BYTES
    per_page: int = 100
    attestations_per_page: int = 10
    request_timeout_seconds: int = 30
    timeout_seconds: int = 300

    def __post_init__(self):
        caps = {"max_metadata_bytes": MAX_JSON_BYTES, "max_archive_bytes": 25 * 1024 * 1024,
                "max_bundle_bytes": MAX_JSON_BYTES, "max_total_bytes": 100 * 1024 * 1024,
                "max_requests": 200, "max_pages": 10, "max_manifest_bytes": MAX_JSON_BYTES,
                "per_page": 100, "attestations_per_page": 10,
                "request_timeout_seconds": 30, "timeout_seconds": 300}
        _need(all(type(getattr(self, key)) is int and 1 <= getattr(self, key) <= maximum
                  for key, maximum in caps.items()), "invalid_artifact_limits")


@dataclass(frozen=True)
class ArtifactCapture:
    manifest_bytes: bytes
    raw_files: Mapping[str, bytes]

    @property
    def manifest(self):
        return loads_strict(self.manifest_bytes)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _inputs(repository, run_id, run_attempt, check_run_id):
    _need(type(repository) is dict and set(repository) == {"id", "full_name"}
          and positive(repository["id"]), "invalid_artifact_repository")
    artifact_path("run_artifacts", repository["full_name"], run_id, 1, 100)
    _need(positive(run_attempt) and positive(check_run_id), "invalid_artifact_execution")
    return {"repository": dict(repository), "run_id": run_id,
            "run_attempt": run_attempt, "check_run_id": check_run_id}


def _body_path(index, bundle=None):
    return f"raw/r{index:06d}" + (f"-b{bundle:03d}" if bundle is not None else "") + ".body"


def _body_fields(payload, kind):
    if kind == "attestations":
        _need(type(payload) is dict and type(payload.get("bundles")) is list and len(payload["bundles"]) <= 10,
              "invalid_artifact_record_payload")
        _need(all(type(entry) is dict and type(entry.get("response")) is dict for entry in payload["bundles"]),
              "invalid_artifact_record_payload")
        return [(entry["response"], index) for index, entry in enumerate(payload["bundles"], 1)]
    _need(type(payload) is dict and type(payload.get("response")) is dict,
          "invalid_artifact_record_payload")
    return [(payload["response"], None)]


def _decode_payload(payload, kind, request_path, limits):
    for response, bundle in _body_fields(payload, kind):
        raw = response.get("body")
        cap = limits.max_bundle_bytes if bundle is not None else limits.max_archive_bytes if kind == "archive" else limits.max_metadata_bytes
        _need(raw is None or type(raw) is bytes and len(raw) <= cap, "artifact_transport_body_limit")
    if kind == "attestations":
        return attestation_http._decode(attestation_http._encode(payload), path=request_path,
            per_page=limits.attestations_per_page, max_bundle_bytes=limits.max_bundle_bytes,
            max_discovery_bytes=limits.max_metadata_bytes,
            max_total_bytes=min(limits.max_total_bytes, attestation_http.MAX_TOTAL), token=None)
    _need(kind in {"artifacts", "artifact", "archive"}, "invalid_artifact_record_kind")
    cap = limits.max_archive_bytes if kind == "archive" else limits.max_metadata_bytes
    return artifact_http._decode(artifact_http._encode(payload), cap, None)


def _network_count(payload, kind):
    return 1 + (len(payload["bundles"]) if kind == "attestations"
                else int(payload["api_redirect"] is not None))


def _captured_size(payload, kind):
    size = sum(len(value["body"] or b"") for value, _ in _body_fields(payload, kind))
    if kind == "attestations":
        size += payload["discovery"]["body_size_bytes"] or 0
    return size


class _Stopped(Exception):
    def __init__(self, code):
        self.code = code


def _stop(code):
    raise _Stopped(code)


def _json_response(record):
    response = record["payload"]["response"]
    if response["error"] is not None:
        _stop(response["error"]["code"])
    if response["status_code"] != 200 or not response["complete"] or response["body"] is None:
        _stop("response_incomplete")
    try:
        return loads_strict(response["body"])
    except ContractError:
        _stop("artifact_metadata_invalid")


def _candidate(value, inputs, name):
    if type(value) is not dict:
        _stop("artifact_metadata_invalid")
    run = value.get("workflow_run")
    if (not positive(value.get("id")) or value.get("name") != name or type(value.get("expired")) is not bool
            or type(value.get("size_in_bytes")) is not int or value["size_in_bytes"] < 0
            or type(value.get("digest")) is not str or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["digest"])
            or type(run) is not dict or type(run.get("id")) is not int or run["id"] != inputs["run_id"]
            or type(run.get("repository_id")) is not int or run["repository_id"] != inputs["repository"]["id"]):
        _stop("artifact_identity_mismatch")
    return {key: deepcopy(value[key]) for key in ("id", "name", "expired", "size_in_bytes", "digest", "workflow_run")}


def _drive(request, inputs, limits, *, mapper_version=2):
    """Shared live/replay request plan; every decision comes from original bodies."""
    result = {"status": "error", "reason_codes": [], "artifact_id": None, "metadata": None,
              "archive_bytes": None, "bundles": (), "attestations_complete": False}
    name = f"delivery-review-{inputs['run_id']}-{inputs['run_attempt']}-{inputs['check_run_id']}"
    repository = inputs["repository"]["full_name"]
    try:
        expected_total, identifiers, matches, count = None, set(), [], 0
        page = 1
        while True:
            if page > limits.max_pages:
                _stop("pagination_limit")
            path = artifact_path("run_artifacts", repository, inputs["run_id"], page, limits.per_page)
            record = request("artifacts", path, {"operation": "run_artifacts", "identity": inputs["run_id"],
                                                   "page": page, "per_page": limits.per_page})
            document = _json_response(record)
            if (type(document) is not dict or type(document.get("artifacts")) is not list
                    or type(document.get("total_count")) is not int or document["total_count"] < 0
                    or len(document["artifacts"]) > limits.per_page):
                _stop("artifact_listing_invalid")
            if expected_total is None:
                expected_total = document["total_count"]
            if expected_total != document["total_count"]:
                _stop("artifact_listing_changed")
            for value in document["artifacts"]:
                if (type(value) is not dict or not positive(value.get("id"))
                        or type(value.get("name")) is not str or len(value["name"]) > 256):
                    _stop("artifact_listing_invalid")
                if value["id"] in identifiers:
                    _stop("artifact_duplicate_identity")
                identifiers.add(value["id"])
                count += 1
                if value["name"] == name:
                    matches.append(value)
            if count > expected_total:
                _stop("artifact_listing_count_mismatch")
            try:
                next_page = next_evidence_page(record["payload"]["response"]["response_headers"], path)
            except (ContractError, ValueError, TypeError):
                _stop("pagination_incomplete")
            if next_page is None:
                if count != expected_total:
                    _stop("artifact_listing_count_mismatch")
                break
            page = next_page
        if not matches:
            result["status"] = "missing"
            _stop("artifact_missing")
        if len(matches) != 1:
            result["status"] = "ambiguous"
            _stop("artifact_ambiguous")
        selected = _candidate(matches[0], inputs, name)
        result["artifact_id"] = selected["id"]
        if selected["expired"]:
            result["status"] = "unavailable"
            _stop("artifact_expired")
        if selected["size_in_bytes"] > limits.max_archive_bytes:
            _stop("artifact_archive_limit")
        path = artifact_path("artifact", repository, selected["id"])
        record = request("artifact", path, {"operation": "artifact", "identity": selected["id"]})
        detail = _candidate(_json_response(record), inputs, name)
        if detail != selected:
            _stop("artifact_detail_mismatch")
        response = record["payload"]["response"]
        result["metadata"] = {"request_path": path, "status_code": response["status_code"],
            "response_headers": deepcopy(response["response_headers"]), "observed_at": record["observed_at"],
            "complete": response["complete"], "body_bytes": response["body"],
            "raw_path": _body_path(int(record["id"][1:]))}
        path = artifact_path("archive", repository, selected["id"])
        record = request("archive", path, {"operation": "archive", "identity": selected["id"]})
        response = record["payload"]["response"]
        if response["error"] is not None:
            _stop(response["error"]["code"])
        archive = response["body"]
        if (response["status_code"] != 200 or not response["complete"] or archive is None
                or len(archive) != selected["size_in_bytes"] or "sha256:" + _sha(archive) != selected["digest"]):
            _stop("artifact_archive_mismatch")
        result["archive_bytes"], result["status"] = archive, "selected"
        cursor, seen_cursors, bundles, bundle_hashes, expanded_total = None, set(), [], set(), 0
        for _ in range(limits.max_pages):
            path = attestation_path(repository, _sha(archive), PREDICATE, limits.attestations_per_page, cursor)
            record = request("attestations", path, {"digest": _sha(archive), "predicate_type": PREDICATE,
                "per_page": limits.attestations_per_page, "cursor": cursor})
            payload = record["payload"]
            if payload["discovery"]["error"] is not None:
                _stop(payload["discovery"]["error"]["code"])
            for entry in payload["bundles"]:
                response = entry["response"]
                if response["error"] is not None:
                    _stop(response["error"]["code"])
                if entry["repository_id"] != inputs["repository"]["id"]:
                    _stop("attestation_repository_mismatch")
                body = response["body"]
                if body is None or not response["complete"] or response["status_code"] != 200:
                    _stop("attestation_bundle_incomplete")
                if mapper_version == 2:
                    try:
                        body = decode_bundle_body(body, response["response_headers"].get("content-type"),
                                                  max_output_bytes=limits.max_bundle_bytes)
                    except BundleEncodingError as exc:
                        _stop(str(exc))
                expanded_total += len(body)
                if expanded_total > limits.max_total_bytes:
                    _stop("bundle_expanded_total_limit")
                digest = _sha(body)
                if digest in bundle_hashes:
                    result["status"] = "ambiguous"
                    _stop("attestation_duplicate_bundle")
                bundle_hashes.add(digest)
                bundles.append(body)
                result["bundles"] = tuple(bundles)
            cursor = payload["next_cursor"]
            if cursor is None:
                result["attestations_complete"] = True
                if not bundles:
                    result["reason_codes"].append("attestation_missing")
                return result
            key = canonical_bytes(cursor)
            if key in seen_cursors:
                _stop("attestation_cursor_cycle")
            seen_cursors.add(key)
        _stop("pagination_limit")
    except _Stopped as exc:
        result["reason_codes"].append(exc.code)
        if result["status"] == "selected":
            result["status"] = "error"
        elif result["status"] == "error" and exc.code in {"resource_unavailable", "authentication_unavailable", "rate_limited"}:
            result["status"] = "unavailable"
        return result


def _parse(manifest_bytes, raw_files, limits):
    _need(type(limits) is ArtifactLimits and type(manifest_bytes) is bytes
          and len(manifest_bytes) <= limits.max_manifest_bytes, "artifact_manifest_limit")
    manifest = loads_strict(manifest_bytes)
    _need(type(manifest) is dict and set(manifest) == {"kind", "capture_version", "mapper_version", "inputs",
        "collector", "observed_at", "completed_at", "limits", "termination", "records"}, "invalid_artifact_manifest")
    _need(manifest["kind"] == "delivery-review-artifact-capture"
          and type(manifest["capture_version"]) is int and manifest["capture_version"] == 1
          and type(manifest["mapper_version"]) is int and manifest["mapper_version"] in {1, 2},
          "unsupported_artifact_capture")
    original_inputs = manifest["inputs"]
    _need(type(original_inputs) is dict and set(original_inputs) == {"repository", "run_id", "run_attempt", "check_run_id"},
          "invalid_artifact_capture_inputs")
    _inputs(**original_inputs)
    _collector(manifest["collector"])
    _timestamp(manifest["observed_at"])
    _timestamp(manifest["completed_at"])
    _need(manifest["completed_at"] >= manifest["observed_at"], "artifact_time_reversed")
    _need(type(manifest["limits"]) is dict and set(manifest["limits"]) == {field.name for field in fields(ArtifactLimits)},
          "invalid_artifact_saved_limits")
    saved = ArtifactLimits(**manifest["limits"])
    _need(all(getattr(saved, field.name) <= getattr(limits, field.name) for field in fields(saved)), "artifact_reader_limits")
    termination = manifest["termination"]
    _need(type(termination) is dict and set(termination) == {"status", "reason_codes"}
          and termination["status"] in {"completed", "stopped"}
          and type(termination["reason_codes"]) is list and len(termination["reason_codes"]) <= 10
          and all(type(code) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) for code in termination["reason_codes"])
          and (termination["status"] != "stopped" or termination["reason_codes"]), "invalid_artifact_termination")
    records = manifest["records"]
    _need(type(records) is list and len(records) <= saved.max_requests, "artifact_request_limit")
    _need(isinstance(raw_files, Mapping), "invalid_artifact_files")
    files, restored, total, requests, previous = {}, [], 0, 0, manifest["observed_at"]
    for index, record in enumerate(records, 1):
        _need(type(record) is dict and set(record) == {"id", "kind", "request_path", "observed_at", "payload"}
              and record["id"] == f"r{index:06d}" and type(record["request_path"]) is str,
              "invalid_artifact_record")
        _timestamp(record["observed_at"])
        _need(previous <= record["observed_at"] <= manifest["completed_at"], "artifact_time_reversed")
        previous = record["observed_at"]
        payload = deepcopy(record["payload"])
        for response, bundle in _body_fields(payload, record["kind"]):
            row = response.get("body")
            if row is None:
                continue
            path = _body_path(index, bundle)
            cap = saved.max_bundle_bytes if bundle is not None else saved.max_archive_bytes if record["kind"] == "archive" else saved.max_metadata_bytes
            _need(type(row) is dict and set(row) == {"path", "size_bytes", "sha256"} and row["path"] == path
                  and type(row["size_bytes"]) is int and 0 <= row["size_bytes"] <= cap
                  and type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid_artifact_file")
            raw = raw_files.get(path)
            _need(type(raw) is bytes and len(raw) == row["size_bytes"] and _sha(raw) == row["sha256"], "artifact_file_mismatch")
            files[path], response["body"] = raw, raw
        payload = _decode_payload(payload, record["kind"], record["request_path"], saved)
        total += _captured_size(payload, record["kind"])
        requests += _network_count(payload, record["kind"])
        _need(total <= saved.max_total_bytes and requests <= saved.max_requests, "artifact_capture_budget")
        restored.append({**record, "payload": payload})
    _need(set(raw_files) == set(files), "artifact_files_inventory_mismatch")
    return manifest, restored, saved


@_boundary
def parse_artifact_capture(manifest_bytes: bytes, raw_files: Mapping[str, bytes], *, limits=ArtifactLimits()):
    _parse(manifest_bytes, raw_files, limits)
    return ArtifactCapture(manifest_bytes, MappingProxyType(dict(raw_files)))


@_boundary
def verify_artifact_capture(directory, *, limits=ArtifactLimits()):
    _need(type(limits) is ArtifactLimits, "invalid_artifact_limits")
    root = Path(os.path.abspath(directory))
    _directories(root)
    manifest_bytes = _read(root / "capture.json", limits.max_manifest_bytes)
    manifest = loads_strict(manifest_bytes)
    # Read only generated paths from a bounded record list; no manifest-provided
    # path is passed to the filesystem before the structural parser verifies it.
    _need(type(manifest) is dict and type(manifest.get("records")) is list
          and len(manifest["records"]) <= limits.max_requests, "invalid_artifact_manifest")
    files, total = {}, 0
    for index, record in enumerate(manifest["records"], 1):
        for response, bundle in _body_fields(record["payload"], record["kind"]):
            if response.get("body") is not None:
                path = _body_path(index, bundle)
                cap = limits.max_bundle_bytes if bundle is not None else limits.max_archive_bytes if record["kind"] == "archive" else limits.max_metadata_bytes
                raw = _read(root / path, min(cap, limits.max_total_bytes - total))
                total += len(raw)
                files[path] = raw
    _scan(root, {"capture.json", *files})
    return parse_artifact_capture(manifest_bytes, files, limits=limits)


@_boundary
def map_artifact_capture(capture: ArtifactCapture, *, limits=ArtifactLimits()):
    _need(type(capture) is ArtifactCapture, "invalid_artifact_capture")
    manifest, records, saved = _parse(capture.manifest_bytes, capture.raw_files, limits)
    position = 0
    def request(kind, path, arguments):
        nonlocal position
        if position == len(records):
            terminal = manifest["termination"]
            _stop(terminal["reason_codes"][0] if terminal["status"] == "stopped" else "artifact_record_missing")
        record = records[position]
        position += 1
        if record["kind"] != kind or record["request_path"] != path:
            _stop("artifact_request_scope_mismatch")
        return record
    result = _drive(request, manifest["inputs"], saved, mapper_version=manifest["mapper_version"])
    if position != len(records):
        result["status"] = "error"
        result["reason_codes"].append("artifact_unexpected_records")
        result["attestations_complete"] = False
    if manifest["termination"]["status"] == "stopped" and result["status"] == "selected":
        result["status"] = "error"
        if manifest["termination"]["reason_codes"][0] not in result["reason_codes"]:
            result["reason_codes"].append(manifest["termination"]["reason_codes"][0])
        result["attestations_complete"] = False
    return result


@_boundary
def collect_artifacts(*, repository, run_id, run_attempt, check_run_id, destination, transport,
                      attestation_transport, collector, clock=_now, limits=ArtifactLimits()):
    _need(type(limits) is ArtifactLimits, "invalid_artifact_limits")
    inputs = _inputs(repository, run_id, run_attempt, check_run_id)
    _collector(collector)
    observed = clock()
    _timestamp(observed)
    def document(saved_records, completed, termination):
        return {"kind": "delivery-review-artifact-capture", "capture_version": 1, "mapper_version": 2,
            "inputs": inputs, "collector": deepcopy(collector), "observed_at": observed, "completed_at": completed,
            "limits": asdict(limits), "termination": termination, "records": saved_records}
    def room(saved_records):
        reserved = document(saved_records, observed, {"status": "stopped", "reason_codes": ["a" * 64]})
        return len(canonical_bytes(reserved) + b"\n") <= limits.max_manifest_bytes
    _need(room([]), "artifact_manifest_limit")
    root = Path(os.path.abspath(destination))
    _directories(root.parent)
    root.mkdir(mode=0o700)
    records, files, total, requests = [], {}, 0, 0
    deadline = time.monotonic() + limits.timeout_seconds
    def request(kind, path, arguments):
        nonlocal total, requests
        reservation = 1 + limits.attestations_per_page if kind == "attestations" else 2 if kind == "archive" else 1
        if requests + reservation > limits.max_requests:
            _stop("request_limit")
        remaining = limits.max_total_bytes - total
        if remaining < 1:
            _stop("capture_limit")
        seconds = min(limits.request_timeout_seconds, int(deadline - time.monotonic()))
        if seconds < 1:
            _stop("collection_timeout")
        try:
            if kind == "attestations":
                payload = attestation_transport.get(inputs["repository"]["full_name"],
                    **arguments, max_discovery_bytes=limits.max_metadata_bytes, max_bundle_bytes=limits.max_bundle_bytes,
                    max_total_bytes=min(remaining, attestation_http.MAX_TOTAL), timeout_seconds=seconds)
            else:
                cap = limits.max_archive_bytes if kind == "archive" else limits.max_metadata_bytes
                payload = transport.get(repository=inputs["repository"]["full_name"], **arguments,
                                        max_bytes=min(cap, remaining), timeout_seconds=seconds)
            payload = _decode_payload(payload, kind, path, limits)
        except (ContractError, OSError, ValueError, TypeError, KeyError):
            _stop("artifact_transport_invalid")
        size = _captured_size(payload, kind)
        if size > remaining:
            raise ArtifactCaptureError("artifact_transport_exceeded_budget")
        requests += _network_count(payload, kind)
        total += size
        index, saved_payload = len(records) + 1, deepcopy(payload)
        pending_files = {}
        for response, bundle in _body_fields(saved_payload, kind):
            raw = response["body"]
            if raw is not None:
                body_path = _body_path(index, bundle)
                pending_files[body_path] = raw
                response["body"] = {"path": body_path, "size_bytes": len(raw), "sha256": _sha(raw)}
        observed_at = clock()
        _timestamp(observed_at)
        record = {"id": f"r{index:06d}", "kind": kind, "request_path": path,
                  "observed_at": observed_at, "payload": saved_payload}
        if not room([*records, record]):
            _stop("manifest_limit")
        # Save original response bytes before any JSON, selection or digest
        # mapping. Transport protocol validation never normalizes the bodies.
        if pending_files:
            (root / "raw").mkdir(mode=0o700, exist_ok=True)
        for body_path, raw in pending_files.items():
            _write(root / body_path, raw)
            files[body_path] = raw
        records.append(record)
        if time.monotonic() >= deadline:
            _stop("collection_timeout")
        return {**record, "payload": payload}
    result = _drive(request, inputs, limits)
    if "collection_timeout" not in result["reason_codes"] and time.monotonic() >= deadline:
        result["status"] = "error"
        result["reason_codes"].append("collection_timeout")
        result["attestations_complete"] = False
    completed = clock()
    _timestamp(completed)
    manifest = document(records, completed, {"status": "completed", "reason_codes": []})
    if result["reason_codes"] and result["reason_codes"] != ["attestation_missing"]:
        manifest["termination"] = {"status": "stopped", "reason_codes": result["reason_codes"]}
    raw = canonical_bytes(manifest) + b"\n"
    capture = parse_artifact_capture(raw, files, limits=limits)
    _write(root / "capture.json", raw)
    _scan(root, {"capture.json", *files})
    return capture
