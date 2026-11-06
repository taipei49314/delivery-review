"""Approved job selection and independently qualified fixed runtime cells.

Names select an API job; they never prove a runtime. Matrix credit additionally
requires the binder's authenticated producer and a reviewed qualification receipt.
No PR matrix expression or workflow code is evaluated here.
"""
from copy import deepcopy
import hashlib
import re

from .contracts import canonical_bytes, loads_strict, require


RUNTIME = {"python": "3.11.16", "implementation": "cpython", "platform": "linux", "architecture": "x86_64"}


def _key(selector):
    return canonical_bytes({key: selector[key] for key in ("workflow_id", "job_name", "matrix")})


def validate_mapping(profile):
    """Validate the complete approved map, without claiming any cell executed."""
    requirements = [row for row in profile["policy"]["requirements"]
                    if row["kind"] in {"test_report", "ci_job"}]
    matrix_requirements = [row for row in requirements if "matrix" in row["selector"]]
    require(all({"workflow_id", "job_name", "app_id"} <= row["selector"].keys() for row in requirements),
            "unsupported_job_selector")
    if "job_matrix" not in profile:
        require(not matrix_requirements, "matrix_mapping_missing")
        return
    mapping = profile["job_matrix"]
    require(profile["profile_version"] == 2 and type(mapping) is dict
            and set(mapping) == {"mapping_version", "cells"}
            and type(mapping["mapping_version"]) is int and mapping["mapping_version"] == 1,
            "invalid_matrix_mapping")
    cells = mapping["cells"]
    require(type(cells) is list and 1 <= len(cells) <= 100, "invalid_matrix_cells")
    workflows = {row["id"]: row for row in profile["github"]["workflows"]}
    mapped, physical = {}, set()
    for cell in cells:
        require(type(cell) is dict and set(cell) == {"workflow_id", "job_name", "matrix", "platform_job_name"},
                "invalid_matrix_cell")
        require(type(cell["workflow_id"]) is int and cell["workflow_id"] in workflows,
                "matrix_workflow_mismatch")
        require(type(cell["job_name"]) is str and 1 <= len(cell["job_name"]) <= 256,
                "invalid_matrix_job_name")
        matrix = cell["matrix"]
        # This declaration may request a missing/unqualified version. Only the
        # qualified runtime below can establish one; spelling is not evidence.
        require(type(matrix) is dict and set(matrix) == {"python"}
                and type(matrix["python"]) is str and re.fullmatch(r"[1-9][0-9]?\.[0-9]{1,2}", matrix["python"]),
                "unsupported_matrix_axes")
        require(type(cell["platform_job_name"]) is str
                and cell["platform_job_name"] in workflows[cell["workflow_id"]]["job_names"],
                "matrix_platform_job_mismatch")
        key, platform = _key(cell), (cell["workflow_id"], cell["platform_job_name"])
        require(key not in mapped, "duplicate_matrix_cell")
        require(platform not in physical, "matrix_platform_job_reused")
        mapped[key] = cell
        physical.add(platform)
    expected = {_key(row["selector"]) for row in matrix_requirements}
    require(set(mapped) == expected, "matrix_mapping_inventory_mismatch")
    test_cells = {_key(row["selector"]) for row in matrix_requirements if row["kind"] == "test_report"}
    require(expected <= test_cells, "matrix_test_proof_requirement_missing")
    for row in requirements:
        selector = row["selector"]
        if "matrix" not in selector:
            require((selector["workflow_id"], selector["job_name"]) not in physical,
                    "matrix_platform_job_reused")


def platform_job_name(profile, requirement):
    """Resolve one exact API name; never infer matrix values from the name."""
    selector = requirement["selector"]
    if "matrix" not in selector:
        return selector["job_name"]
    require(type(profile) is dict and profile.get("profile_version") == 2, "matrix_profile_required")
    validate_mapping(profile)
    cells = [cell for cell in profile["job_matrix"]["cells"] if _key(cell) == _key(selector)]
    require(len(cells) == 1, "matrix_mapping_ambiguous")
    return cells[0]["platform_job_name"]


def qualified_matrix(requirement, qualification, source_mode):
    """Return measured axes only after the caller authenticated this producer.

The release-owned qualification receipt is separately digest-pinned in the
registry. Historical receipts without runtime proof cannot gain matrix credit.
Actual GitHub cryptography is the binder's additional mandatory preceding gate.
"""
    selector = requirement["selector"]
    if "matrix" not in selector:
        return {}
    # Local import avoids a module cycle with the binder's profile validator.
    from .test_evidence import _qualification
    _qualification(qualification, selector, source_mode)
    raw = qualification.qualification_receipt_bytes
    require(hashlib.sha256(raw).hexdigest() == qualification.qualification_receipt_sha256,
            "producer_qualification_digest_mismatch")
    receipt = loads_strict(raw)
    runtime = receipt.get("runtime")
    require(type(runtime) is dict and canonical_bytes(runtime) == canonical_bytes(RUNTIME),
            "matrix_runtime_unconfirmed")
    measured = {"python": ".".join(runtime["python"].split(".")[:2])}
    require(canonical_bytes(selector["matrix"]) == canonical_bytes(measured), "matrix_runtime_mismatch")
    return deepcopy(measured)
