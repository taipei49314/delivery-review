"""Create-once bundles of raw bytes and saved normalized observations.

The manifest is a completion marker, not a signature. A filesystem owner can
rewrite both data and hashes. Callers must keep the bundle tree exclusively
owned during IO; this module is not a sandbox against concurrent local writers.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from functools import wraps
import hashlib
import os
from pathlib import Path
import re
import stat
from types import MappingProxyType

from ..contracts import (
    ContractError, MAX_JSON_BYTES, canonical_bytes, loads_strict, validate_report,
)


class BundleError(ContractError):
    """A bundle is incomplete, unsafe, inconsistent or outside local limits."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise BundleError(message)


def _boundary(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except BundleError:
            raise
        except (ContractError, OSError) as exc:
            raise BundleError(str(exc)) from exc
    return wrapped


@dataclass(frozen=True)
class BundleLimits:
    """Local budgets; never obtained from an untrusted bundle's metadata.

File count/total include policy and report, but exclude the bounded manifest.
Artifact extraction and its separate policy limits belong to a later stage.
"""

    max_files: int = 100
    max_file_bytes: int = 25 * 1024 * 1024
    max_total_bytes: int = 100 * 1024 * 1024
    max_manifest_bytes: int = MAX_JSON_BYTES

    def __post_init__(self):
        for value in (self.max_files, self.max_file_bytes,
                      self.max_total_bytes, self.max_manifest_bytes):
            _need(type(value) is int and value > 0, "bundle limits must be positive integers")


DEFAULT_LIMITS = BundleLimits()
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_RESERVED = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)
}
_CONTROL_FILES = {"policy.json", "report.json"}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _row(path: str, raw: bytes) -> dict:
    return {"path": path, "size_bytes": len(raw), "sha256": _sha(raw)}


def _check_paths(paths: list[str]) -> None:
    """Reject platform aliases as well as lexical escapes, on every platform."""
    nodes = {}
    seen = set()
    for path in paths:
        _need(type(path) is str and 0 < len(path) <= 512, "invalid bundle path")
        _need(path not in seen, "duplicate bundle path")
        seen.add(path)
        _need(path in _CONTROL_FILES or path.startswith("raw/"), "raw paths must start with raw/")
        parts = path.split("/")
        _need(len(parts) <= 16, "bundle path depth limit exceeded")
        for i, part in enumerate(parts):
            _need(_SEGMENT.fullmatch(part) is not None and not part.endswith("."),
                  "unsafe or non-portable bundle path")
            _need(part.split(".")[0].lower() not in _RESERVED, "reserved bundle path")
            prefix = "/".join(parts[:i + 1])
            node = (prefix, "file" if i == len(parts) - 1 else "directory")
            key = prefix.casefold()
            _need(key not in nodes or nodes[key] == node, "bundle path collision")
            nodes[key] = node


def _limits(limits: BundleLimits) -> None:
    _need(isinstance(limits, BundleLimits), "expected BundleLimits")


def _validate_rows(rows, limits: BundleLimits) -> dict[str, dict]:
    _need(type(rows) is list and 2 <= len(rows) <= limits.max_files,
          "bundle file count limit exceeded or missing control files")
    total = 0
    for item in rows:
        _need(type(item) is dict and set(item) == {"path", "sha256", "size_bytes"},
              "invalid bundle file entry")
        size = item["size_bytes"]
        _need(type(size) is int and 0 <= size <= limits.max_file_bytes,
              "bundle file size limit exceeded or invalid size")
        _need(type(item["sha256"]) is str and _DIGEST.fullmatch(item["sha256"]) is not None,
              "invalid bundle digest")
        total += size
    _need(total <= limits.max_total_bytes, "bundle total byte limit exceeded")
    _check_paths([item["path"] for item in rows])
    indexed = {item["path"]: item for item in rows}
    _need(_CONTROL_FILES <= indexed.keys(), "missing policy or report")
    return indexed


def _validate_manifest(manifest, limits: BundleLimits) -> dict[str, dict]:
    _need(type(manifest) is dict and set(manifest) == {
        "kind", "bundle_version", "replay_basis", "observed_at", "files", "derived_from",
    }, "invalid bundle manifest fields")
    _need(manifest["kind"] == "delivery-review-evidence-bundle", "unsupported bundle kind")
    _need(type(manifest["bundle_version"]) is int and manifest["bundle_version"] == 1,
          "unsupported bundle version")
    _need(manifest["replay_basis"] == "saved_normalized_report", "unsupported replay basis")
    derivation = manifest["derived_from"]
    if derivation is not None:
        _need(type(derivation) is dict and set(derivation) == {"manifest_sha256", "report_sha256"},
              "invalid replay derivation")
        _need(all(type(v) is str and _DIGEST.fullmatch(v) is not None for v in derivation.values()),
              "invalid replay source digest")
    return _validate_rows(manifest["files"], limits)


def _validate_documents(files: Mapping[str, bytes]) -> dict:
    policy = loads_strict(files["policy.json"])
    report = loads_strict(files["report.json"])
    validate_report(report, policy)
    raw_rows = [_row(path, raw) for path, raw in sorted(files.items()) if path.startswith("raw/")]
    _need(sorted(report["raw_inventory"], key=lambda r: r["path"]) == raw_rows,
          "report raw inventory differs from saved raw files")
    retained = {row["sha256"] for row in raw_rows}
    references = []
    for item in report["evidence"]:
        references.extend((item["raw_sha256"], item["artifact_sha256"]))
        if item["producer_proof"] is not None:
            references.append(item["producer_proof"]["api_response_sha256"])
    references.extend(engine["raw_report_sha256"] for engine in report["engines"])
    _need(all(digest is None or digest in retained for digest in references),
          "report references a raw digest not retained in the bundle")
    return report


def _not_link(info: os.stat_result) -> bool:
    return not (stat.S_ISLNK(info.st_mode) or
                getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _directories(path: Path) -> None:
    # Inspect before resolve(): resolving first would hide a symlink/junction.
    for directory in reversed((path, *path.parents)):
        info = directory.lstat()
        _need(_not_link(info) and stat.S_ISDIR(info.st_mode),
              "bundle directory is not a plain directory (link/reparse point refused)")


def _regular(info: os.stat_result) -> None:
    _need(_not_link(info) and stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
          "bundle file is not a single regular file (links/reparse points refused)")


def _identity(info: os.stat_result) -> tuple:
    # Windows Python 3.12 lstat/fstat disagree on creation vs change time in
    # st_ctime. Compare that field only between calls using the same API.
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _read(path: Path, budget: int, expected: dict | None = None) -> bytes:
    _directories(path.parent)
    before = path.lstat()
    _regular(before)
    _need(before.st_size <= budget, "bundle file byte limit exceeded")
    if expected is not None:
        _need(before.st_size == expected["size_bytes"], "bundle file size mismatch")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        _regular(opened)
        _need(_identity(before) == _identity(opened), "bundle file changed while opening")
        raw = stream.read(budget + 1)
        after = os.fstat(stream.fileno())
    _need(len(raw) <= budget, "bundle file byte limit exceeded")
    final = path.lstat()
    _regular(final)
    _need(_identity(before) == _identity(after) == _identity(final)
          and opened.st_ctime_ns == after.st_ctime_ns and before.st_ctime_ns == final.st_ctime_ns,
          "bundle file changed while reading")
    _need(len(raw) == before.st_size, "bundle file size changed while reading")
    if expected is not None:
        _need(_sha(raw) == expected["sha256"], "bundle file digest mismatch")
    return raw


def _scan(root: Path, files: set[str]) -> None:
    """Enforce the exact tree; do not follow links or walk arbitrary subtrees."""
    directories = {p.rsplit("/", 1)[0] for p in files if "/" in p}
    directories |= {"/".join(p.split("/")[:i]) for p in files for i in range(1, len(p.split("/")))}
    pending = [root]
    seen = set()
    while pending:
        directory = pending.pop()
        _directories(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                relative = (directory / entry.name).relative_to(root).as_posix()
                # Windows DirEntry metadata can report st_nlink=0; lstat obtains
                # the real link count used by the regular-file check below.
                info = (directory / entry.name).lstat()
                _need(_not_link(info), "bundle link/reparse point refused")
                if stat.S_ISDIR(info.st_mode):
                    _need(relative in directories, "unexpected bundle directory")
                    pending.append(directory / entry.name)
                else:
                    _need(relative in files, "unexpected bundle file")
                    _regular(info)
                    seen.add(relative)
    _need(seen == files, "bundle inventory contains missing files")


def _write(path: Path, raw: bytes) -> None:
    _directories(path.parent)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True)
class VerifiedBundle:
    """One bounded, verified in-memory snapshot; bytes are not re-read later."""

    policy_bytes: bytes
    report_bytes: bytes
    manifest_bytes: bytes
    raw_files: Mapping[str, bytes]

    @property
    def policy(self) -> dict:
        return loads_strict(self.policy_bytes)

    @property
    def report(self) -> dict:
        return loads_strict(self.report_bytes)


@_boundary
def create_bundle(destination: str | Path, *, policy_bytes: bytes, report_bytes: bytes,
                  raw_files: Mapping[str, bytes], limits: BundleLimits = DEFAULT_LIMITS) -> Path:
    """Save exact bytes in a new directory; never repair claims or overwrite.

The parent must exist. All raw names start with ``raw/`` and must match the
report's raw_inventory. An interrupted write may leave an unusable directory;
its absent/invalid manifest prevents replay and the path is never reused.
"""
    return _create_bundle(destination, policy_bytes=policy_bytes, report_bytes=report_bytes,
                          raw_files=raw_files, limits=limits, derived_from=None)


def _create_bundle(destination, *, policy_bytes, report_bytes, raw_files, limits, derived_from):
    _limits(limits)
    _need(isinstance(raw_files, Mapping), "raw_files must be a mapping of paths to bytes")
    _need(len(raw_files) + 2 <= limits.max_files, "bundle file count limit exceeded")
    _need(all(type(path) is str and path.startswith("raw/") for path in raw_files),
          "raw paths must start with raw/")
    files = {"policy.json": policy_bytes, "report.json": report_bytes, **raw_files}
    _need(all(type(raw) is bytes for raw in files.values()), "bundle content must be original bytes")
    # Check budgets before hashing or parsing caller-supplied buffers.
    _need(all(len(raw) <= limits.max_file_bytes for raw in files.values()), "bundle file byte limit exceeded")
    _need(sum(map(len, files.values())) <= limits.max_total_bytes, "bundle total byte limit exceeded")
    _check_paths(list(files))
    report = _validate_documents(files)
    manifest = {
        "kind": "delivery-review-evidence-bundle", "bundle_version": 1,
        "replay_basis": "saved_normalized_report", "observed_at": report["observed_at"],
        "files": [_row(path, raw) for path, raw in sorted(files.items())],
        "derived_from": derived_from,
    }
    _validate_manifest(manifest, limits)
    manifest_bytes = canonical_bytes(manifest) + b"\n"
    _need(len(manifest_bytes) <= min(limits.max_manifest_bytes, MAX_JSON_BYTES),
          "bundle manifest byte limit exceeded")
    target = Path(os.path.abspath(destination))
    _directories(target.parent)
    target.mkdir()  # Exclusive, including an existing empty or partial directory.
    for relative, raw in sorted(files.items()):
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _write(path, raw)
    # No completion marker exists until all files have been written successfully.
    _scan(target, set(files))
    _write(target / "manifest.json", manifest_bytes)
    return target


@_boundary
def verify_bundle(source: str | Path, *, limits: BundleLimits = DEFAULT_LIMITS) -> VerifiedBundle:
    """Verify a complete tree, raw references and saved policy/report consistency.

This proves byte consistency only. It neither authenticates the producer nor
maps provider payloads to normalized claims, and never performs network IO.
"""
    _limits(limits)
    root = Path(os.path.abspath(source))
    _directories(root)
    manifest_bytes = _read(root / "manifest.json", min(limits.max_manifest_bytes, MAX_JSON_BYTES))
    manifest = loads_strict(manifest_bytes)
    indexed = _validate_manifest(manifest, limits)
    expected = set(indexed) | {"manifest.json"}
    _scan(root, expected)
    files = {}
    for path, entry in indexed.items():
        budget = min(limits.max_file_bytes, MAX_JSON_BYTES) if path in _CONTROL_FILES else limits.max_file_bytes
        files[path] = _read(root / path, budget, entry)
    report = _validate_documents(files)
    _need(manifest["observed_at"] == report["observed_at"], "bundle observation time differs from report")
    if manifest["derived_from"] is not None:
        _need(report["mode"] == "offline_replay", "derived bundle is not an offline replay")
    _scan(root, expected)
    _need(_read(root / "manifest.json", min(limits.max_manifest_bytes, MAX_JSON_BYTES)) == manifest_bytes,
          "bundle manifest changed while reading")
    return VerifiedBundle(files["policy.json"], files["report.json"], manifest_bytes,
                          MappingProxyType({p: raw for p, raw in files.items() if p.startswith("raw/")}))
