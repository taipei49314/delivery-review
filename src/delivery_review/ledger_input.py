"""Interpret an external ledger declaration against already verified static source.

Unknown formats are never parsed or passed to a dynamic engine adapter. Original
Git objects and their inventory remain the source of the declared file bytes.
"""
from copy import deepcopy
import hashlib
import re

from .contracts import canonical_bytes, require
from .source_snapshot import _name


def validate_declaration(value):
    require(type(value) is dict and set(value) == {"format", "paths"}, "invalid_ledger_input_declaration")
    require(type(value["format"]) is str and re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", value["format"]),
            "invalid_ledger_format")
    paths = value["paths"]
    require(type(paths) is list and len(paths) <= 16 and all(type(path) is str for path in paths),
            "invalid_ledger_input_paths")
    require(len(paths) == len(set(path.casefold() for path in paths)), "duplicate_ledger_input_path")
    for path in paths:
        require(0 < len(path.encode("utf-8")) <= 240, "invalid_ledger_input_path")
        for part in path.split("/"):
            _name(part.encode("utf-8"))
    require(paths == [] if value["format"] == "ec-ledger-v1" else bool(paths), "ledger_input_scope_mismatch")


def observe_input(declaration, snapshot):
    """Called only after verify_snapshot; inventory proves exact static file presence."""
    validate_declaration(declaration)
    inventory = snapshot.inventory
    require(inventory["head_sha"] == snapshot.head_sha, "ledger_input_source_mismatch")
    files = {row["path"]: row for row in inventory["files"]}
    selected = [{"path": path, "present": path in files,
        "source": deepcopy(files[path]) if path in files else None} for path in declaration["paths"]]
    status = "supported" if declaration["format"] == "ec-ledger-v1" else (
        "unsupported" if any(row["present"] for row in selected) else "not_provided")
    return {"kind": "delivery-review-ledger-input", "observation_version": 1,
        "head_sha": snapshot.head_sha, "format": declaration["format"], "status": status,
        "source_inventory_sha256": hashlib.sha256(snapshot.inventory_bytes).hexdigest(), "files": selected}


def apply_declaration(observed, declaration, snapshot):
    """Add input availability without replacing native errors, findings or streams."""
    receipt = observe_input(declaration, snapshot)
    result = deepcopy(observed)
    result["ledger_input"] = receipt
    if result["status"] != "error" and receipt["status"] != "supported":
        status = receipt["status"]
        reason = "ledger_format_unsupported" if status == "unsupported" else "ledger_not_provided"
        coverage = {"status": "unknown" if status == "unsupported" else "not_provided",
            "details": ["Externally declared ledger format: " + declaration["format"]]
                + [("present: " if row["present"] else "missing: ") + row["path"] for row in receipt["files"]]}
        result.update(status=status, verdict="not_judged", coverage=coverage)
        result["engine"].update(status=status, verdict="not_judged", coverage=deepcopy(coverage))
        result["reason_codes"] = sorted(set(result["reason_codes"]) | {reason})
        # A strict EC coverage-only block cannot certify anything about an
        # externally declared unknown format. Real blocking findings still apply.
        native = result["parsed_native"]
        if native.get("finding_verdict") == "pass" and not any(
                row["severity"] in {"high", "critical"} and row["disposition"] != "allowlisted"
                for row in result["engine"]["findings"]):
            result["known_block"] = False
    return result, canonical_bytes(receipt) + b"\n"
