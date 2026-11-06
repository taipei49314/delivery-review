"""Run pinned official offline verification, then compare authenticated claims.

This layer never awards workflow_bound: job/check/App/reusable-workflow API
relations and the qualified producer contract are independently required.
"""
from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import time
from types import MappingProxyType

from ..contracts import ContractError, canonical_bytes, loads_strict
from ..engine_runner import _execute
from ..evidence.store import _directories, _read, _write

GH_VERSION = "2.98.0"
GH_PINS = {
    ("win32", "amd64"): "a1701e3c806a805981554ace7df04812772c6449bdcfd1a77675013d97786401",
    ("linux", "amd64"): "62885b97de6a0cd85e616cdd94bcda908bf5cf1018094385892b05cea3537163",
}
TRUSTED_ROOT_PINS = frozenset({"65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c"})
PREDICATE_TYPE = "https://github.com/taipei49314/delivery-review/attestation/pytest/v1"
PRODUCER_PROFILE = "pytest-isolated-container-v1"
ISSUER = "https://token.actions.githubusercontent.com"
_SHA = re.compile(r"[0-9a-f]{40}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_][A-Za-z0-9_.-]{0,99}")
_COMMON = {"repository_id", "repository_full_name", "certificate_source_sha", "certificate_event", "run_id", "run_attempt",
           "producer_repository_full_name", "producer_path", "producer_sha", "subject_name", "predicate_type"}
_PRODUCT = (_COMMON - {"subject_name", "predicate_type"}) | {"job_id", "check_run_id", "artifact_id", "artifact_sha256", "receipt_sha256", "junit_sha256"}
_CERTIFICATE_FIELDS = {
    "certificateIssuer", "subjectAlternativeName", "issuer", "githubWorkflowTrigger", "githubWorkflowSHA",
    "githubWorkflowName", "githubWorkflowRepository", "githubWorkflowRef", "buildSignerURI", "buildSignerDigest",
    "runnerEnvironment", "sourceRepositoryURI", "sourceRepositoryDigest", "sourceRepositoryRef",
    "sourceRepositoryIdentifier", "sourceRepositoryOwnerURI", "sourceRepositoryOwnerIdentifier",
    "buildConfigURI", "buildConfigDigest", "buildTrigger", "runInvocationURI", "sourceRepositoryVisibilityAtSigning",
}


class AttestationError(ContractError):
    """Sanitized controller/storage failure, separate from retained native errors."""


def _need(condition, code):
    if not condition:
        raise AttestationError(code)


def _boundary(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except AttestationError:
            raise
        except (ContractError, OSError, ValueError, TypeError, KeyError, RecursionError, subprocess.SubprocessError) as exc:
            raise AttestationError("attestation_boundary_failed") from exc
    return wrapped


@dataclass(frozen=True)
class AttestationLimits:
    timeout_seconds: int = 60
    max_artifact_bytes: int = 25 * 1024 * 1024
    max_bundle_bytes: int = 2 * 1024 * 1024
    max_root_bytes: int = 1024 * 1024
    max_stdout_bytes: int = 2 * 1024 * 1024
    max_stderr_bytes: int = 1024 * 1024
    max_binary_bytes: int = 128 * 1024 * 1024

    def __post_init__(self):
        caps = {"timeout_seconds": 120, "max_artifact_bytes": 25 * 1024 * 1024,
                "max_bundle_bytes": 2 * 1024 * 1024, "max_root_bytes": 1024 * 1024,
                "max_stdout_bytes": 2 * 1024 * 1024, "max_stderr_bytes": 1024 * 1024,
                "max_binary_bytes": 128 * 1024 * 1024}
        _need(all(type(getattr(self, field.name)) is int and 0 < getattr(self, field.name) <= caps[field.name]
                  for field in fields(self)), "invalid_attestation_limits")


@dataclass(frozen=True)
class AttestationExecution:
    directory: Path
    manifest_bytes: bytes
    raw_files: Mapping[str, bytes]
    observation_bytes: bytes

    @property
    def manifest(self):
        return loads_strict(self.manifest_bytes)

    @property
    def observation(self):
        return json.loads(self.observation_bytes)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _id(value):
    return type(value) is int and 1 <= value <= 2**53 - 1


def _text(value, maximum=1024):
    return type(value) is str and 0 < len(value) <= maximum and not any(ord(char) < 32 or ord(char) == 127 for char in value)


def _expected(value, *, product):
    _need(type(value) is dict and set(value) == (_PRODUCT if product else _COMMON), "invalid_attestation_expectations")
    _need(all(_id(value[key]) for key in ("repository_id", "run_id", "run_attempt")), "invalid_attestation_id")
    for key in ("repository_full_name", "producer_repository_full_name"):
        _need(type(value[key]) is str and _REPO.fullmatch(value[key]) and not value[key].endswith("."), "invalid_attestation_repository")
    _need(all(type(value[key]) is str and _SHA.fullmatch(value[key]) for key in ("certificate_source_sha", "producer_sha")), "invalid_attestation_sha")
    path = value["producer_path"]
    _need(type(path) is str and re.fullmatch(r"\.github/workflows/[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml", path)
          and "/../" not in path, "invalid_attestation_producer_path")
    _need(value["certificate_event"] in ({"pull_request"} if product else {"pull_request", "workflow_dispatch", "push"}), "unsupported_attestation_event")
    if product:
        _need(all(_id(value[key]) for key in ("job_id", "check_run_id", "artifact_id")), "invalid_attestation_id")
        _need(all(type(value[key]) is str and _DIGEST.fullmatch(value[key]) for key in ("artifact_sha256", "receipt_sha256", "junit_sha256")), "invalid_attestation_digest")
    else:
        _need(_text(value["subject_name"]) and value["predicate_type"] in {PREDICATE_TYPE, "https://slsa.dev/provenance/v1"}, "unsupported_attestation_statement")
    return deepcopy(value)


def _json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result
    def constant(_):
        raise ValueError("nonfinite")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError("BOM")
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if depth > 32 or count > 100000:
            raise ValueError("JSON limit")
        if type(item) is dict:
            pending.extend((child, depth + 1) for child in item.values())
            pending.extend((key, depth + 1) for key in item)
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            item.encode("utf-8", errors="strict")
        elif type(item) is float and not math.isfinite(item):
            raise ValueError("nonfinite")
    return value


def _identity(expected):
    return "https://github.com/" + expected["producer_repository_full_name"] + "/" + expected["producer_path"] + "@" + expected["producer_sha"]


def _bundle_claim(raw):
    """Reject ambiguity in original signed JSON even if Go collapsed keys.

This post-verification cross-check is unverified data. It can only reject or
cross-check; it never supplies authenticated fields in place of the official
verificationResult.statement.
"""
    try:
        bundle = _json(raw)
        if (type(bundle) is not dict or set(bundle) != {"mediaType", "verificationMaterial", "dsseEnvelope"}
                or bundle["mediaType"] != "application/vnd.dev.sigstore.bundle.v0.3+json"
                or type(bundle["verificationMaterial"]) is not dict):
            return None, "unsupported_bundle_contract"
        envelope = bundle["dsseEnvelope"]
        if (type(envelope) is not dict or set(envelope) != {"payload", "payloadType", "signatures"}
                or envelope["payloadType"] != "application/vnd.in-toto+json" or type(envelope["payload"]) is not str
                or type(envelope["signatures"]) is not list or len(envelope["signatures"]) != 1):
            return None, "unsupported_bundle_contract"
        claim = _json(base64.b64decode(envelope["payload"], validate=True))
        if type(claim) is not dict:
            return None, "bundle_claim_invalid_json"
        return claim, None
    except (ValueError, UnicodeError, RecursionError, binascii.Error):
        return None, "bundle_claim_invalid_json"


def _interpret(raw, expected, artifact_digest, predicate):
    """Semantic parser only. Calling this private helper cannot verify a signature."""
    reasons, native = set(), None
    states = {"certificate": "unconfirmed", "subject": "unconfirmed", "predicate": "unconfirmed"}
    try:
        native = _json(raw)
    except (ValueError, UnicodeError, RecursionError):
        return states, ["verification_output_invalid_json"], None
    if type(native) is not list or len(native) != 1:
        return states, ["verification_record_count"], native
    item = native[0]
    if type(item) is not dict or set(item) != {"attestation", "verificationResult"}:
        return states, ["unsupported_verification_contract"], native
    result = item["verificationResult"]
    if (type(result) is not dict or set(result) != {"mediaType", "signature", "verifiedTimestamps", "verifiedIdentity", "statement"}
            or result["mediaType"] != "application/vnd.dev.sigstore.verificationresult+json;version=0.1"
            or type(result["signature"]) is not dict or set(result["signature"]) != {"certificate"}
            or type(result["verifiedIdentity"]) is not dict):
        return states, ["unsupported_verification_contract"], native
    identity = result["verifiedIdentity"]
    if (set(identity) != {"subjectAlternativeName", "issuer", "runnerEnvironment"}
            or type(identity["issuer"]) is not dict or set(identity["issuer"]) != {"issuer", "regexp"}
            or not all(type(value) is str and len(value) <= 4096 for value in identity["issuer"].values())):
        return states, ["unsupported_verification_contract"], native
    if (identity["subjectAlternativeName"] != {"subjectAlternativeName": _identity(expected)}
            or identity["runnerEnvironment"] != "github-hosted"):
        reasons.add("verification_identity_mismatch")
    # gh 2.98.0's verifiedIdentity.issuer display remains an any-issuer matcher
    # in the observed output even with --cert-oidc-issuer. Independently require
    # the authenticated certificate's exact issuer below; do not trust a hint.
    timestamps = result["verifiedTimestamps"]
    if type(timestamps) is not list or not 1 <= len(timestamps) <= 20:
        reasons.add("verified_timestamp_missing")
    else:
        for row in timestamps:
            try:
                if (type(row) is not dict or set(row) != {"type", "uri", "timestamp"}
                        or row["type"] not in {"Tlog", "TSA"} or not _text(row["uri"]) or not row["uri"].startswith("https://")
                        or not _text(row["timestamp"]) or datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")).utcoffset() is None):
                    reasons.add("verified_timestamp_invalid")
            except (ValueError, TypeError):
                reasons.add("verified_timestamp_invalid")
    cert = result["signature"]["certificate"]
    certificate_expected = {"issuer": ISSUER, "subjectAlternativeName": _identity(expected), "buildSignerURI": _identity(expected),
        "buildSignerDigest": expected["producer_sha"], "runnerEnvironment": "github-hosted",
        "sourceRepositoryURI": "https://github.com/" + expected["repository_full_name"],
        "sourceRepositoryDigest": expected["certificate_source_sha"], "sourceRepositoryIdentifier": str(expected["repository_id"]),
        "buildTrigger": expected["certificate_event"],
        "runInvocationURI": "https://github.com/" + expected["repository_full_name"] + "/actions/runs/" + str(expected["run_id"]) + "/attempts/" + str(expected["run_attempt"])}
    if type(cert) is not dict or not set(cert) <= _CERTIFICATE_FIELDS or not all(_text(value, 4096) for value in cert.values()):
        reasons.add("unsupported_certificate_contract")
    elif any(cert.get(key) != value for key, value in certificate_expected.items()):
        reasons.add("certificate_identity_mismatch")
    else:
        legacy = {"githubWorkflowTrigger": expected["certificate_event"], "githubWorkflowSHA": expected["certificate_source_sha"],
                  "githubWorkflowRepository": expected["repository_full_name"], "buildConfigDigest": expected["certificate_source_sha"]}
        if (any(key in cert and cert[key] != value for key, value in legacy.items())
                or ("githubWorkflowRef" in cert and "sourceRepositoryRef" in cert and cert["githubWorkflowRef"] != cert["sourceRepositoryRef"])):
            reasons.add("certificate_repeated_identity_mismatch")
        else:
            states["certificate"] = "matched"
    statement = result["statement"]
    if (type(statement) is not dict or set(statement) != {"_type", "subject", "predicateType", "predicate"}
            or statement["_type"] != "https://in-toto.io/Statement/v1" or statement["predicateType"] != expected["predicate_type"]):
        reasons.add("unsupported_statement_contract")
    else:
        subjects = statement["subject"]
        if subjects != [{"name": expected["subject_name"], "digest": {"sha256": artifact_digest}}]:
            reasons.add("signed_subject_mismatch")
        else:
            states["subject"] = "matched"
        if predicate is not None:
            actual = statement["predicate"]
            # Canonical strict parsing enforces integer identity, unlike Python's
            # equality where True == 1 and 1.0 == 1.
            if (type(actual) is not dict or set(actual) != set(predicate)
                    or any(type(actual[key]) is not type(value) or actual[key] != value for key, value in predicate.items())):
                reasons.add("signed_predicate_mismatch")
            else:
                states["predicate"] = "matched"
    return states, sorted(reasons), native


def _environment(root):
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    environment.update(PATH=str(root / "bin"), HOME=str(root / "home"), USERPROFILE=str(root / "home"),
        TMPDIR=str(root / "home"), TEMP=str(root / "home"), TMP=str(root / "home"), LC_ALL="C", LANG="C", TZ="UTC",
        GH_CONFIG_DIR=str(root / "config"), XDG_CONFIG_HOME=str(root / "config"), XDG_CACHE_HOME=str(root / "cache"),
        GH_HOST="github.com", GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1", GH_NO_EXTENSION_UPDATE_NOTIFIER="1",
        GH_ACCESSIBLE_COLORS="1", NO_COLOR="1", GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull, GIT_TERMINAL_PROMPT="0", HTTP_PROXY="http://127.0.0.1:1",
        HTTPS_PROXY="http://127.0.0.1:1", ALL_PROXY="http://127.0.0.1:1", NO_PROXY="")
    return environment


def _run(*, artifact_bytes, bundle_bytes, trusted_root_bytes, gh_executable, gh_sha256, expected, destination, limits, predicate):
    _need(type(limits) is AttestationLimits, "invalid_attestation_limits")
    _need(all(type(raw) is bytes and 0 < len(raw) <= cap for raw, cap in (
        (artifact_bytes, limits.max_artifact_bytes), (bundle_bytes, limits.max_bundle_bytes), (trusted_root_bytes, limits.max_root_bytes))), "attestation_input_limit")
    architecture = platform.machine().lower()
    architecture = "amd64" if architecture in {"amd64", "x86_64"} else architecture
    pin = GH_PINS.get((sys.platform, architecture))
    _need(pin is not None and gh_sha256 == pin, "unsupported_attestation_tool_pin")
    _need(_sha(trusted_root_bytes) in TRUSTED_ROOT_PINS, "unapproved_attestation_trusted_root")
    executable = Path(gh_executable)
    _need(executable.is_absolute(), "invalid_attestation_executable")
    binary = _read(executable, limits.max_binary_bytes)
    _need(_sha(binary) == pin, "attestation_tool_digest_mismatch")
    root = Path(os.path.abspath(destination))
    _directories(root.parent)
    root.mkdir(mode=0o700)
    for name in ("bin", "home", "config", "cache", "raw"):
        (root / name).mkdir(mode=0o700)
    owned_binary = root / "bin" / ("gh.exe" if os.name == "nt" else "gh")
    _write(owned_binary, binary)
    owned_binary.chmod(0o700)
    raw_files = {"artifact": artifact_bytes, "bundle": bundle_bytes, "trusted_root": trusted_root_bytes,
                 "expected": canonical_bytes({"certificate_and_subject": expected, "predicate": predicate}) + b"\n"}
    inventory = {}
    def save(name, raw):
        path = {"artifact": "raw/artifact.zip", "bundle": "raw/bundle.json", "trusted_root": "raw/trusted_root.jsonl",
                "expected": "raw/expected.json"}.get(name, "raw/" + name + ".bin")
        _write(root / path, raw)
        inventory[name] = {"path": path, "size_bytes": len(raw), "sha256": _sha(raw)}
    for name, raw in raw_files.items():
        save(name, raw)
    environment = _environment(root)
    started = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    deadline = time.monotonic() + limits.timeout_seconds
    version_process, version_raw = _execute([str(owned_binary), "--version"], cwd=root, env=environment, deadline=deadline, limits=limits)
    for name, raw in version_raw.items():
        raw_files["version_" + name] = raw
        save("version_" + name, raw)
    _need(version_process["termination"] == "exited" and version_process["exit_code"] == 0
          and version_raw["stdout"].startswith(("gh version " + GH_VERSION + " ").encode()), "attestation_tool_version_mismatch")
    argv = [str(owned_binary), "attestation", "verify", str(root / inventory["artifact"]["path"]),
            "--bundle", str(root / inventory["bundle"]["path"]), "--custom-trusted-root", str(root / inventory["trusted_root"]["path"]),
            "--repo", expected["repository_full_name"], "--hostname", "github.com", "--cert-identity", _identity(expected),
            "--signer-digest", expected["producer_sha"], "--cert-oidc-issuer", ISSUER, "--deny-self-hosted-runners",
            "--predicate-type", expected["predicate_type"], "--digest-alg", "sha256", "--format", "json"]
    process, streams = _execute(argv, cwd=root, env=environment, deadline=deadline, limits=limits)
    for name, raw in streams.items():
        raw_files[name] = raw
        save(name, raw)
    integrity_errors = []
    try:
        _read(owned_binary, limits.max_binary_bytes, {"size_bytes": len(binary), "sha256": pin})
        for name in ("artifact", "bundle", "trusted_root", "expected"):
            _read(root / inventory[name]["path"], len(raw_files[name]), inventory[name])
    except (ContractError, OSError):
        integrity_errors.append("attestation_post_integrity_failed")
    native_ok = process["termination"] == "exited" and process["exit_code"] == 0 and not integrity_errors
    states, reasons, parsed = _interpret(streams["stdout"], expected, _sha(artifact_bytes), predicate)
    claim, bundle_error = _bundle_claim(bundle_bytes)
    if bundle_error:
        reasons.append(bundle_error)
    elif (type(parsed) is list and len(parsed) == 1 and type(parsed[0]) is dict
          and type(parsed[0].get("verificationResult")) is dict and "statement" in parsed[0]["verificationResult"]):
        encode = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
        if encode(claim) != encode(parsed[0]["verificationResult"]["statement"]):
            reasons.append("verified_statement_bundle_mismatch")
    if not native_ok:
        reasons.append("official_verification_failed" if process["termination"] == "exited" else "verification_" + process["termination"])
        states = {key: "unconfirmed" for key in states}
    reasons.extend(integrity_errors)
    schema_errors = {"verification_output_invalid_json", "verification_record_count", "unsupported_verification_contract",
                     "verified_timestamp_missing", "verified_timestamp_invalid", "unsupported_certificate_contract",
                     "unsupported_statement_contract", "verification_identity_mismatch", "unsupported_bundle_contract",
                     "bundle_claim_invalid_json", "verified_statement_bundle_mismatch"}
    crypto_confirmed = native_ok and not (set(reasons) & schema_errors)
    if not crypto_confirmed:
        states = {key: "unconfirmed" for key in states}
    observation = {"kind": "delivery-review-github-attestation-observation", "adapter_version": 1,
        "status": "verified_claims" if native_ok and not reasons else "error",
        "cryptographic_verification": "verified" if crypto_confirmed else "unconfirmed", **states,
        "platform_binding": "unconfirmed", "producer_qualification": "unconfirmed", "tested_source_binding": "unconfirmed",
        "reason_codes": sorted(set(reasons)), "controller_integrity_errors": integrity_errors,
        "parsed_native": parsed, "process": process, "expected": deepcopy(expected), "expected_predicate": deepcopy(predicate)}
    observed = (json.dumps(observation, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode()
    _write(root / "observation.json", observed)
    manifest = {"kind": "delivery-review-attestation-execution", "execution_version": 1,
        "tool": {"name": "gh", "version": GH_VERSION, "sha256": pin, "platform": sys.platform, "architecture": architecture},
        "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "offline_mode": "saved_bundle_saved_root_no_credentials_invalid_proxies", "network_isolation": "not_enforced",
        "version_process": version_process, "process": process, "controller_integrity_errors": integrity_errors,
        "files": inventory, "observation": {"path": "observation.json", "size_bytes": len(observed), "sha256": _sha(observed)}}
    manifest_bytes = canonical_bytes(manifest) + b"\n"
    _write(root / "verification.json", manifest_bytes)
    return AttestationExecution(root, manifest_bytes, MappingProxyType(dict(raw_files)), observed)


@_boundary
def verify_attestation_cryptography(*, artifact_bytes: bytes, bundle_bytes: bytes, trusted_root_bytes: bytes,
        gh_executable: str | Path, gh_sha256: str, expected: dict, destination: str | Path,
        limits: AttestationLimits = AttestationLimits()) -> AttestationExecution:
    """Verify bytes/certificate/subject only; never qualify a product predicate."""
    value = _expected(expected, product=False)
    return _run(artifact_bytes=artifact_bytes, bundle_bytes=bundle_bytes, trusted_root_bytes=trusted_root_bytes,
                gh_executable=gh_executable, gh_sha256=gh_sha256, expected=value, destination=destination, limits=limits, predicate=None)


@_boundary
def verify_github_attestation(*, artifact_bytes: bytes, bundle_bytes: bytes, trusted_root_bytes: bytes,
        gh_executable: str | Path, gh_sha256: str, expected: dict, destination: str | Path,
        limits: AttestationLimits = AttestationLimits()) -> AttestationExecution:
    """Verify the strict custom signed claims; independent platform proof remains."""
    value = _expected(expected, product=True)
    common = {key: value[key] for key in _COMMON - {"subject_name", "predicate_type"}}
    common.update(predicate_type=PREDICATE_TYPE, subject_name="https://github.com/" + value["repository_full_name"]
                  + "/actions/runs/" + str(value["run_id"]) + "/artifacts/" + str(value["artifact_id"]))
    predicate = {key: value[key] for key in ("repository_id", "run_id", "run_attempt", "job_id", "check_run_id", "artifact_id",
                                           "artifact_sha256", "receipt_sha256", "junit_sha256")}
    predicate.update(predicate_version=1, producer_profile=PRODUCER_PROFILE)
    # Artifact digest is independently computed; a signed predicate claiming a
    # different ZIP is rejected even when its subject happens to hash correctly.
    _need(type(limits) is AttestationLimits and type(artifact_bytes) is bytes
          and 0 < len(artifact_bytes) <= limits.max_artifact_bytes, "attestation_input_limit")
    if value["artifact_sha256"] != _sha(artifact_bytes):
        raise AttestationError("expected_artifact_digest_mismatch")
    return _run(artifact_bytes=artifact_bytes, bundle_bytes=bundle_bytes, trusted_root_bytes=trusted_root_bytes,
                gh_executable=gh_executable, gh_sha256=gh_sha256, expected=common, destination=destination, limits=limits, predicate=predicate)
