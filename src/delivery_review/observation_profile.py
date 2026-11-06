"""Validate the separate SPEC-001 profile; never a delivery readiness policy."""
from __future__ import annotations

from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry

from .contracts import ContractError, _offline_only, load_document, loads_strict, require

ROOT = Path(__file__).resolve().parents[2]
_PROFILE_VALIDATOR = Draft202012Validator(
    load_document(ROOT / "formats/github-observation-profile-v1.schema.json"),
    registry=Registry(retrieve=_offline_only),
)


def load_observation_profile(raw: bytes) -> dict:
    """Parse original operator-supplied bytes with strict local validation.

    No profile is inferred from jobs or from a reviewed PR. Schema identity and
    selection version are explicit; unsupported versions cannot become defaults.
    """
    require(type(raw) is bytes, "observation profile must be original bytes")
    profile = loads_strict(raw)
    errors = sorted(_PROFILE_VALIDATOR.iter_errors(profile), key=lambda error: str(list(error.absolute_path)))
    if errors:
        raise ContractError("invalid observation profile: " + errors[0].message)
    workflows = profile["workflows"]
    require(len({workflow["id"] for workflow in workflows}) == len(workflows),
            "duplicate observation workflow identity")
    require(len({workflow["path"] for workflow in workflows}) == len(workflows),
            "duplicate observation workflow path")
    return profile
