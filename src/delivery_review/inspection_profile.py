"""Bind acquisition/provenance selectors to a frozen contract-v1 policy ID.

Sealing is deterministic identity construction, never policy approval or signing.
The caller must obtain the whole profile from an independently trusted location.
"""
from copy import deepcopy
import re

from .contracts import canonical_bytes, canonical_digest, loads_strict, require, validate_policy
from .observation_profile import load_observation_profile
from .job_matrix import platform_job_name, validate_mapping
from .ledger_input import validate_declaration
from .policy_change import validate_declaration as validate_policy_input
from .resources import definition_bytes

KIND = "delivery-review-inspection-profile"
PREDICATE = "https://github.com/taipei49314/delivery-review/attestation/pytest/v1"
FIELDS = {"kind", "profile_version", "name", "policy", "github", "producers", "engine_options"}


def _identity(profile):
    basis = deepcopy(profile)
    basis["policy"].pop("policy_id", None)
    return "profile-" + canonical_digest(basis)


def _validate(profile, *, check_identity):
    require(type(profile) is dict and FIELDS <= set(profile), "invalid_inspection_profile_fields")
    require(profile["kind"] == KIND and type(profile["profile_version"]) is int and
            profile["profile_version"] in {1, 2}, "unsupported_inspection_profile")
    optional = {"job_matrix", "ledger_input", "policy_input"} if profile["profile_version"] == 2 else set()
    require(FIELDS <= set(profile) and set(profile) <= FIELDS | optional, "invalid_inspection_profile_fields")
    require(type(profile["name"]) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", profile["name"]),
            "invalid_inspection_profile_name")
    policy = profile["policy"]
    validate_policy(policy)
    github = load_observation_profile(canonical_bytes(profile["github"]))
    if "ledger_input" in profile:
        validate_declaration(profile["ledger_input"])
    require(policy["repository_id"] == github["repository"]["id"], "profile_repository_mismatch")
    require(policy["origin"] == {"kind": "external_file", "revision": None},
            "inspection_profile_requires_external_policy")
    pins = {item["name"]: item for item in policy["engine_pins"]}
    locked = loads_strict(definition_bytes("locks", "engine-candidates.json"))["engines"]
    expected = {item["name"]: {"name": item["name"], "version": item["version"],
                              "artifact_sha256": item["artifact"]["sha256"]} for item in locked}
    require(pins == expected, "unsupported_inspection_engine_pins")
    options = profile["engine_options"]
    require(type(options) is dict and set(options) == set(expected), "invalid_engine_options")
    for name, item in options.items():
        fields = {"threshold", "require_complete"} if name == "ledgerwash" else {"threshold"}
        require(type(item) is dict and set(item) == fields and item["threshold"] == "high", "invalid_engine_options")
        if name == "ledgerwash":
            require(type(item["require_complete"]) is bool, "invalid_engine_options")
    requirements = policy["requirements"]
    changes = [row for row in requirements if row["kind"] == "policy_change"]
    require((len(changes) == 1) == ("policy_input" in profile) and len(changes) <= 1,
            "policy_input_requirement_mismatch")
    if "policy_input" in profile:
        validate_policy_input(profile["policy_input"])
    if profile["profile_version"] == 2:
        validate_mapping(profile)
    scanners = {item["selector"].get("engine") for item in requirements
                if item["kind"] == "scanner" and item["required"]}
    require({"checkwash", "pinwash"} <= scanners, "required_static_engines_missing")
    require(any(item["kind"] == "test_report" and item["required"] for item in requirements),
            "required_test_report_missing")
    workflows = {item["id"]: item for item in github["workflows"]}
    used_workflows = set()
    used_jobs = {key: set() for key in workflows}
    producers = profile["producers"]
    tests = {item["id"]: item for item in requirements if item["kind"] == "test_report"}
    require(type(producers) is dict and set(producers) == set(tests), "producer_inventory_mismatch")
    for requirement in requirements:
        selector = requirement["selector"]
        if requirement["kind"] == "policy_change":
            require(profile["profile_version"] == 2 and selector == {} and requirement["minimum_binding"] == "reported",
                    "unsupported_policy_change_selector")
            continue
        if requirement["kind"] == "source":
            require(selector == {} and requirement["minimum_binding"] == "reported", "unsupported_source_selector")
            continue
        if requirement["kind"] in {"scanner", "ledger"}:
            require(set(selector) == {"engine"} and requirement["minimum_binding"] == "reported",
                    "unsupported_static_selector")
            continue
        require(requirement["kind"] in {"test_report", "ci_job"}, "unsupported_requirement_kind")
        required = {"workflow_id", "app_id", "job_name"}
        require(required <= selector.keys() and ("matrix" not in selector or profile["profile_version"] == 2),
                "unsupported_job_selector")
        if "matrix" in selector:
            required.add("matrix")
        observed_name = platform_job_name(profile, requirement)
        workflow = workflows.get(selector["workflow_id"])
        require(workflow is not None and workflow["app_id"] == selector["app_id"] and
                observed_name in workflow["job_names"], "profile_job_selector_mismatch")
        used_workflows.add(workflow["id"])
        used_jobs[workflow["id"]].add(observed_name)
        if requirement["kind"] == "ci_job":
            require(set(selector) == required and requirement["minimum_binding"] == "platform_bound",
                    "unsupported_ci_selector")
            continue
        required |= {"producer_repository_id", "producer_path", "producer_sha", "min_tests", "allow_skips"}
        require(set(selector) == required and requirement["minimum_binding"] == "workflow_bound",
                "unsupported_test_selector")
        producer = producers[requirement["id"]]
        require(type(producer) is dict and set(producer) == {
            "repository_full_name", "artifact_name_rule", "receipt_member", "junit_member", "dialect", "predicate_type"},
            "invalid_producer_selector")
        name = producer["repository_full_name"]
        require(type(name) is str and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", name) and
                all(part not in {".", ".."} for part in name.split("/")), "invalid_producer_repository")
        require(producer["artifact_name_rule"] == "delivery_review_run_attempt_check_v1" and
                producer["receipt_member"] == "receipt.json" and producer["junit_member"] == "junit.xml" and
                producer["dialect"] == "pytest-8.4.2-xunit2" and producer["predicate_type"] == PREDICATE,
                "unsupported_producer_protocol")
    require(used_workflows == set(workflows), "unused_profile_workflow")
    require(all(used_jobs[key] == set(workflow["job_names"]) for key, workflow in workflows.items()),
            "unused_profile_job")
    if check_identity:
        require(policy["policy_id"] == _identity(profile), "inspection_policy_identity_mismatch")


def seal_inspection_profile(profile: dict) -> bytes:
    """Construct an identity for operator review; does not authorize the policy."""
    result = loads_strict(canonical_bytes(profile))
    _validate(result, check_identity=False)
    result["policy"]["policy_id"] = _identity(result)
    return canonical_bytes(result) + b"\n"


def load_inspection_profile(raw: bytes) -> dict:
    require(type(raw) is bytes, "inspection_profile_requires_original_bytes")
    result = loads_strict(raw)
    _validate(result, check_identity=True)
    return result
