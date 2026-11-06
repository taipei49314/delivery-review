"""Create-once full inspection envelopes; raw replay is implemented separately.

An exact local inventory detects changed files. It is neither authentication nor
a readiness result, and the saved report is never the input to raw replay.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from types import MappingProxyType

from ..contracts import MAX_JSON_BYTES, canonical_bytes, loads_strict, require
from .capture import _collector, _timestamp
from .store import _check_paths, _directories, _read, _scan, _write

MAX_TOTAL = 512 * 1024 * 1024
MAX_FILE = 128 * 1024 * 1024
MAX_FILES = 25000
MANDATORY = frozenset({"raw/inspection-profile.json", "raw/github/capture.json", "raw/github/profile.json",
    "raw/source/inventory.json", "raw/execution-plan.json", "report.json", "report.md", "report.html"})


@dataclass(frozen=True)
class InspectionEnvelope:
    manifest_bytes: bytes
    files: Mapping[str, bytes]

    @property
    def manifest(self):
        return loads_strict(self.manifest_bytes)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _manifest(raw):
    require(type(raw) is bytes and len(raw) <= MAX_JSON_BYTES, "inspection_manifest_limit")
    value = loads_strict(raw)
    require(type(value) is dict and set(value) == {"kind", "inspection_version", "replay_basis", "collector",
        "observed_at", "report_id", "derived_from", "files"}, "invalid_inspection_manifest")
    require(value["kind"] == "delivery-review-full-inspection" and type(value["inspection_version"]) is int
        and value["inspection_version"] == 1 and value["replay_basis"] == "raw_evidence_v1", "unsupported_inspection_format")
    _collector(value["collector"])
    _timestamp(value["observed_at"])
    require(type(value["report_id"]) is str and 0 < len(value["report_id"]) <= 128, "invalid_inspection_report_id")
    derived = value["derived_from"]
    require(derived is None or type(derived) is str and len(derived) == 64 and all(c in "0123456789abcdef" for c in derived),
        "invalid_inspection_derivation")
    rows = value["files"]
    require(type(rows) is list and len(MANDATORY) <= len(rows) <= MAX_FILES, "inspection_file_count_limit")
    names, total = [], 0
    for row in rows:
        require(type(row) is dict and set(row) == {"path", "size_bytes", "sha256"}, "invalid_inspection_file")
        name, size, digest = row["path"], row["size_bytes"], row["sha256"]
        require(type(name) is str and (name.startswith("raw/") or name in {"report.json", "report.md", "report.html"}),
            "invalid_inspection_path")
        require(type(size) is int and 0 <= size <= MAX_FILE and type(digest) is str and len(digest) == 64
            and all(c in "0123456789abcdef" for c in digest), "invalid_inspection_file_digest")
        names.append(name)
        total += size
    _check_paths(["raw/" + name for name in names])
    require(names == sorted(names) and MANDATORY <= set(names) and total <= MAX_TOTAL, "invalid_inspection_inventory")
    return value


def parse_inspection(manifest_bytes: bytes, files: Mapping[str, bytes]) -> InspectionEnvelope:
    value = _manifest(manifest_bytes)
    require(isinstance(files, Mapping) and set(files) == {row["path"] for row in value["files"]},
        "inspection_file_inventory_mismatch")
    copied = {}
    for row in value["files"]:
        raw = files[row["path"]]
        require(type(raw) is bytes and len(raw) == row["size_bytes"] and _sha(raw) == row["sha256"],
            "inspection_file_digest_mismatch")
        copied[row["path"]] = raw
    return InspectionEnvelope(manifest_bytes, MappingProxyType(copied))


def write_inspection(destination, *, files, collector, observed_at, report_id, derived_from=None) -> InspectionEnvelope:
    """Exclusive destination; the manifest is written only after every file verifies."""
    return InspectionWriter(destination).seal(files=files, collector=collector, observed_at=observed_at,
                                              report_id=report_id, derived_from=derived_from)


class InspectionWriter:
    """Reserve one new output tree, then seal its captured raw data exactly once."""
    def __init__(self, destination):
        self.root = Path(os.path.abspath(destination))
        _directories(self.root.parent)
        self.root.mkdir(mode=0o700)
        self.closed = False
        self.reserved_bytes = 0
        self.reserved_files = 0

    def seal(self, *, files, collector, observed_at, report_id, derived_from=None):
        require(not self.closed, "inspection_writer_closed")
        self.closed = True
        return _seal(self.root, files=files, collector=collector, observed_at=observed_at,
                     report_id=report_id, derived_from=derived_from)


def _seal(root, *, files, collector, observed_at, report_id, derived_from):
    require(isinstance(files, Mapping) and len(files) <= MAX_FILES, "inspection_file_count_limit")
    require(all(type(raw) is bytes and len(raw) <= MAX_FILE for raw in files.values())
        and sum(map(len, files.values())) <= MAX_TOTAL, "inspection_byte_limit")
    manifest = {"kind": "delivery-review-full-inspection", "inspection_version": 1, "replay_basis": "raw_evidence_v1",
        "collector": collector, "observed_at": observed_at, "report_id": report_id, "derived_from": derived_from,
        "files": [{"path": path, "size_bytes": len(raw), "sha256": _sha(raw)} for path, raw in sorted(files.items())]}
    raw = canonical_bytes(manifest) + b"\n"
    verified = parse_inspection(raw, files)
    for name, content in sorted(verified.files.items()):
        path = root / name
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            require(_read(path, MAX_FILE) == content, "inspection_capture_changed_before_sealing")
        else:
            _write(path, content)
    _scan(root, set(verified.files))
    for row in manifest["files"]:
        _read(root / row["path"], MAX_FILE, row)
    _write(root / "inspection.json", raw)
    return verified


def verify_inspection(source) -> InspectionEnvelope:
    root = Path(os.path.abspath(source))
    _directories(root)
    raw = _read(root / "inspection.json", MAX_JSON_BYTES)
    value = _manifest(raw)
    names = {row["path"] for row in value["files"]} | {"inspection.json"}
    _scan(root, names)
    files = {row["path"]: _read(root / row["path"], MAX_FILE, row) for row in value["files"]}
    envelope = parse_inspection(raw, files)
    _scan(root, names)
    require(_read(root / "inspection.json", MAX_JSON_BYTES) == raw, "inspection_manifest_changed")
    return envelope
