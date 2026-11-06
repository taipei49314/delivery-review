"""Load only release-bundled producer qualification receipts, never PR claims."""
import hashlib
import re

from .contracts import canonical_bytes, loads_strict, require
from .resources import definition_bytes
from .test_evidence import ProducerQualification, _qualification


def qualified_producer(selector):
    registry = loads_strict(definition_bytes("formats", "producer-qualifications.json"))
    require(type(registry) is dict and set(registry) == {"kind", "registry_version", "producers"}
        and registry["kind"] == "delivery-review-qualified-producers" and type(registry["registry_version"]) is int
        and registry["registry_version"] == 1 and type(registry["producers"]) is list, "invalid_producer_registry")
    candidates, identities = [], set()
    for row in registry["producers"]:
        require(type(row) is dict and set(row) == {"receipt", "sha256"}, "invalid_producer_registry_entry")
        require(type(row["sha256"]) is str and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]), "invalid_qualification_digest")
        require(type(row["receipt"]) is str and row["receipt"] == "producer-qualification-" + row["sha256"] + ".json",
            "invalid_qualification_resource")
        raw = definition_bytes("formats", row["receipt"])
        require(hashlib.sha256(raw).hexdigest() == row["sha256"], "producer_qualification_digest_mismatch")
        receipt = loads_strict(raw)
        require(type(receipt) is dict and type(receipt.get("producer")) is dict
            and set(receipt["producer"]) == {"repository_id", "path", "sha"}, "invalid_qualification_identity")
        identity = receipt["producer"]
        require(type(identity["repository_id"]) is int and 1 <= identity["repository_id"] <= 2**53 - 1
            and type(identity["path"]) is str and re.fullmatch(r"\.github/workflows/[A-Za-z0-9_][A-Za-z0-9_.-]*\.ya?ml", identity["path"])
            and type(identity["sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", identity["sha"]), "invalid_qualification_identity")
        modes = receipt.get("source_modes")
        require(type(modes) is list and 1 <= len(modes) <= 2 and all(type(mode) is str for mode in modes)
            and len(set(modes)) == len(modes) and set(modes) <= {"pr_head", "github_merge"}, "invalid_qualification_source_modes")
        key = canonical_bytes(identity)
        require(key not in identities, "duplicate_producer_qualification")
        identities.add(key)
        value = ProducerQualification(**identity, status=receipt["result"], producer_profile=receipt["producer_profile"],
            qualification_receipt_sha256=row["sha256"], qualification_receipt_bytes=raw,
            test_path=receipt["test_path"], source_modes=tuple(receipt["source_modes"]))
        expected = {"producer_repository_id": value.repository_id, "producer_path": value.path, "producer_sha": value.sha}
        for mode in value.source_modes:
            _qualification(value, expected, mode)
        if canonical_bytes({key: selector[key] for key in expected}) == canonical_bytes(expected):
            candidates.append(value)
    require(len(candidates) <= 1, "ambiguous_producer_qualification")
    return candidates[0] if candidates else None
