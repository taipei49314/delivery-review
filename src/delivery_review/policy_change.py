"""Observe a declared policy proposal statically; never apply reviewed policy."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
import time
from types import MappingProxyType
import zlib

from .contracts import BINDINGS, ContractError, canonical_bytes, canonical_digest, loads_strict, require, validate_policy
from .evidence.store import _read
from .source_snapshot import SnapshotLimits, _commit, _name, _tree, verify_snapshot


MAX_POLICY_BYTES = 1024 * 1024


@dataclass(frozen=True)
class PolicyChangeResult:
    observation: dict
    raw_files: object


def validate_declaration(value):
    require(type(value) is dict and set(value) == {"format", "path"}
            and value["format"] == "delivery-review-policy-v1", "invalid_policy_input_declaration")
    path = value["path"]
    require(type(path) is str and 0 < len(path.encode("utf-8")) <= 240
            and len(path.split("/")) <= 32, "invalid_policy_input_path")
    for part in path.split("/"):
        _name(part.encode("utf-8"))


def _differences(before, after, path="", *, maximum=256):
    """Bounded change index; original complete documents are retained separately."""
    result, truncated = [], False
    missing = object()
    def visit(left, right, pointer):
        nonlocal truncated
        if left is not missing and right is not missing and canonical_bytes(left) == canonical_bytes(right):
            return
        if len(result) >= maximum:
            truncated = True
            return
        if type(left) is dict and type(right) is dict:
            for key in sorted(set(left) | set(right)):
                visit(left.get(key, missing), right.get(key, missing), pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
        else:
            result.append({"path": pointer or "/", "change": "added" if left is missing else "removed" if right is missing else "changed",
                "before_sha256": None if left is missing else canonical_digest(left),
                "after_sha256": None if right is missing else canonical_digest(right)})
    visit(before, after, path)
    return result, truncated


def _relaxations(before, after):
    if after is None:
        return ["policy_removed"] if before is not None else []
    if before is None:
        return []
    result = []
    if set(after["allowed_source_modes"]) - set(before["allowed_source_modes"]):
        result.append("source_modes_expanded")
    new = {row["id"]: row for row in after["requirements"]}
    for old in before["requirements"]:
        current = new.get(old["id"])
        if old["required"] and (current is None or not current["required"]):
            result.append("required_obligation_removed_or_optional")
        if current is None:
            continue
        if BINDINGS[current["minimum_binding"]] < BINDINGS[old["minimum_binding"]]:
            result.append("minimum_binding_lowered")
        left, right = old["selector"], current["selector"]
        if "min_tests" in left and ("min_tests" not in right or right["min_tests"] < left["min_tests"]):
            result.append("minimum_tests_lowered")
        if left.get("allow_skips") is False and right.get("allow_skips") is True:
            result.append("test_skips_newly_permitted")
    if any(after["limits"][key] > value for key, value in before["limits"].items()):
        result.append("resource_limits_increased")
    return sorted(set(result))


def observe_policy_change(profile, snapshot):
    """Read the exact merge-base/HEAD path from verified canonical Git objects."""
    require(profile["profile_version"] == 2 and "policy_input" in profile, "policy_input_missing")
    declaration = profile["policy_input"]
    validate_declaration(declaration)
    verify_snapshot(snapshot)
    limits = SnapshotLimits()
    inventory = {row["oid"]: row for row in snapshot.inventory["objects"]}
    cache, total = {}, 0
    deadline = time.monotonic() + limits.timeout_seconds
    raw = {"policy-change/approved-policy.json": canonical_bytes(profile["policy"]) + b"\n"}
    observation = {"kind": "delivery-review-policy-change", "observation_version": 1,
        "declaration": deepcopy(declaration), "comparison_basis": "merge_base_to_head",
        "head_sha": snapshot.head_sha, "merge_base_sha": snapshot.merge_base_sha,
        "source_inventory_sha256": hashlib.sha256(snapshot.inventory_bytes).hexdigest(),
        "approved_policy_sha256": canonical_digest(profile["policy"]), "documents": {},
        "changed": None, "changes": [], "change_details_truncated": False,
        "relaxations": [], "head_matches_approved": None, "outcome": "error", "effect": "clear", "reason_codes": []}

    def object_payload(oid, kind):
        nonlocal total
        require(time.monotonic() < deadline, "policy_source_read_timeout")
        if oid not in cache:
            require(oid in inventory, "policy_source_object_missing")
            row = inventory[oid]
            encoded = _read(snapshot.root / ".git/objects" / oid[:2] / oid[2:], row["loose_size_bytes"],
                {"size_bytes": row["loose_size_bytes"], "sha256": row["loose_sha256"]})
            decoder = zlib.decompressobj()
            original = decoder.decompress(encoded, limits.max_object_bytes + 1)
            require(len(original) <= limits.max_object_bytes and decoder.eof and not decoder.unused_data
                    and not decoder.unconsumed_tail, "policy_source_object_limit")
            total += len(original)
            require(total <= limits.max_total_object_bytes, "policy_source_total_limit")
            require(hashlib.sha1(original, usedforsecurity=False).hexdigest() == oid
                    and hashlib.sha256(original).hexdigest() == row["sha256"]
                    and len(original) == row["size_bytes"], "policy_source_object_mismatch")
            cache[oid] = original
        original = cache[oid]
        header, separator, payload = original.partition(b"\0")
        require(separator and header == kind.encode() + b" " + str(len(payload)).encode(), "policy_source_object_type")
        return payload

    def read_document(label, commit):
        document = {"commit_sha": commit, "path": declaration["path"], "present": False, "oid": None,
                    "object_sha256": None, "sha256": None, "size_bytes": None, "raw_path": None}
        observation["documents"][label] = document
        tree, _ = _commit(object_payload(commit, "commit"))
        parts = declaration["path"].split("/")
        for index, part in enumerate(parts):
            entry = next((row for row in _tree(object_payload(tree, "tree")) if row[1] == part), None)
            if entry is None:
                return None
            mode, _, oid = entry
            if index + 1 < len(parts):
                require(mode == "40000", "policy_input_parent_not_directory")
                tree = oid
                continue
            require(mode in {"100644", "100755"}, "policy_input_not_file")
            payload = object_payload(oid, "blob")
            destination = "policy-change/" + label + ".json"
            raw[destination] = payload
            document.update(present=True, oid=oid, object_sha256=inventory[oid]["sha256"],
                sha256=hashlib.sha256(payload).hexdigest(), size_bytes=len(payload), raw_path=destination)
            return payload

    errors = []
    parsed = {}
    for label, commit in (("base", snapshot.merge_base_sha), ("head", snapshot.head_sha)):
        try:
            payload = read_document(label, commit)
            if payload is None:
                parsed[label] = None
            else:
                require(len(payload) <= MAX_POLICY_BYTES, "policy_document_limit")
                try:
                    parsed[label] = loads_strict(payload)
                    validate_policy(parsed[label])
                except (ContractError, ValueError, TypeError, KeyError):
                    raise ContractError("policy_document_invalid") from None
        except (ContractError, OSError, ValueError, TypeError, KeyError, zlib.error) as exc:
            code = str(exc) if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", str(exc)) else "policy_source_unavailable"
            errors.append(code)
    verify_snapshot(snapshot)
    if errors:
        observation["reason_codes"] = sorted(set(errors))
    else:
        before, after = parsed["base"], parsed["head"]
        if before is None and after is None:
            observation.update(outcome="missing", reason_codes=["policy_proposal_missing"])
        else:
            changed = canonical_bytes(before) != canonical_bytes(after)
            changes, truncated = _differences(before, after)
            relaxations = sorted(set(_relaxations(before, after) + (_relaxations(profile["policy"], after) if changed else [])))
            observation.update(changed=changed, changes=changes, change_details_truncated=truncated,
                relaxations=relaxations, head_matches_approved=after == profile["policy"], outcome="satisfied",
                effect="review" if changed else "clear",
                reason_codes=["unapproved_policy_relaxation" if relaxations else "policy_change_requires_review"] if changed else [])
    raw["policy-change/observation.json"] = canonical_bytes(observation) + b"\n"
    return PolicyChangeResult(observation, MappingProxyType(raw))
