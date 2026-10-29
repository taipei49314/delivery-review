"""Offline schema and consistency checks, not a GitHub collector or oracle.

Normalized outcomes are inputs to this M0 checker. It does not establish that
the observations happened, that a producer is authentic, or that code is correct.
"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

ROOT = Path(__file__).resolve().parents[2]
PREFIX = "urn:delivery-review:contract:1:"
MAX_JSON_BYTES = 2 * 1024 * 1024
MAX_DEPTH = 32
MAX_INTEGER = 2**53 - 1
SCHEMAS = ("common", "policy", "receipt", "report", "acceptance-case", "engine-lock")
BINDINGS = {"reported": 0, "platform_bound": 1, "workflow_bound": 2}
EFFECTS = {"clear": 0, "review": 1, "block": 2}
FORMATS = FormatChecker()


@FORMATS.checks("date-time", raises=ValueError)
def _utc_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return True  # The schema's type assertion handles non-strings.
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == value


class ContractError(ValueError):
    """A document is malformed or contradicts the frozen contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _no_float(value: str) -> None:
    raise ContractError(f"non-integer JSON number is unsupported: {value}")


def _check_values(value: Any, depth: int = 0) -> None:
    require(depth <= MAX_DEPTH, "contract JSON depth limit exceeded")
    if type(value) is dict:
        for key, child in value.items():
            require(type(key) is str, "JSON object key must be a string")
            _check_values(key, depth + 1)
            _check_values(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _check_values(child, depth + 1)
    elif type(value) is int:
        require(abs(value) <= MAX_INTEGER, "integer exceeds interoperable contract range")
    elif type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise ContractError("unpaired surrogate in contract JSON") from exc
    else:
        require(value is None or type(value) is bool, "unsupported contract JSON type")


def loads_strict(raw: bytes | str) -> Any:
    try:
        data = raw if isinstance(raw, bytes) else raw.encode("utf-8", errors="strict")
        require(len(data) <= MAX_JSON_BYTES, "contract JSON byte limit exceeded")
        require(not data.startswith(b"\xef\xbb\xbf"), "UTF-8 BOM is unsupported")
        value = json.loads(data.decode("utf-8", errors="strict"), object_pairs_hook=_pairs,
                           parse_float=_no_float, parse_constant=_no_float)
        _check_values(value)
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError(f"invalid contract JSON: {type(exc).__name__}") from exc


def load_document(path: str | Path) -> Any:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    return loads_strict(raw)


def canonical_bytes(value: Any) -> bytes:
    """Project-local canonical JSON; this is NOT RFC 8785 or a signature."""
    _check_values(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _offline_only(uri: str) -> Resource:
    raise NoSuchResource(ref=uri)


@lru_cache(maxsize=1)
def validators() -> dict[str, Draft202012Validator]:
    documents = {name: load_document(ROOT / "schemas" / f"{name}.schema.json") for name in SCHEMAS}
    registry = Registry(retrieve=_offline_only).with_resources(
        (document["$id"], Resource.from_contents(document)) for document in documents.values())
    result = {}
    for name, document in documents.items():
        Draft202012Validator.check_schema(document)
        result[name] = Draft202012Validator(document, registry=registry, format_checker=FORMATS)
    return result


def validate_document(kind: str, document: Any) -> None:
    require(kind in SCHEMAS, "unknown contract schema")
    _check_values(document)
    errors = sorted(validators()[kind].iter_errors(document), key=lambda e: str(list(e.absolute_path)))
    if errors:
        error = errors[0]
        location = "/".join(str(p) for p in error.absolute_path) or "<root>"
        raise ContractError(f"{kind} at {location}: {error.message}")


def _index(rows: list[dict], field: str, label: str) -> dict:
    result = {}
    for row in rows:
        key = row[field]
        require(key not in result, f"duplicate {label}: {key}")
        result[key] = row
    return result


def _runs(rows: list[dict]) -> list[tuple[int, int, int]]:
    _index(rows, "workflow_id", "selected workflow")
    return sorted((r["workflow_id"], r["run_id"], r["attempt"]) for r in rows)


def derive_state(report: dict) -> dict:
    """Reduce already-normalized obligations; no raw-source interpretation."""
    required = [o for o in report["obligations"] if o["required"]]
    if any(o["outcome"] == "error" for o in required):
        evidence = "error"
    elif any(o["outcome"] not in {"satisfied", "failed"} for o in required):
        evidence = "incomplete"
    else:
        evidence = "complete"
    policy = max((o["effect"] for o in report["obligations"]), key=EFFECTS.get, default="clear")
    end = report["recheck"]
    if report["mode"] == "offline_replay" or end is None:
        freshness = "unknown"
    elif (end["head_sha"] != report["subject"]["head_sha"]
          or end["base_tip_sha"] != report["subject"]["base_tip_sha"]
          or _runs(end["selected_runs"]) != _runs(report["selected_runs"])):
        freshness = "stale"
    else:
        freshness = "current"
    return {"evidence": evidence, "freshness": freshness, "policy": policy}


def exit_for(state: dict) -> int:
    if state["evidence"] != "complete" or state["freshness"] != "current":
        return 2
    return 0 if state["policy"] == "clear" else 1


def safe_relative_path(path: str) -> bool:
    p = PurePosixPath(path)
    return bool(path and "\\" not in path and ":" not in path and "\x00" not in path
                and not p.is_absolute() and all(part not in {"", ".", ".."} for part in path.split("/")))


def validate_policy(policy: dict) -> None:
    validate_document("policy", policy)
    requirements = _index(policy["requirements"], "id", "policy requirement")
    require(any(r["required"] for r in requirements.values()), "policy must declare a required obligation")
    pins = _index(policy["engine_pins"], "name", "engine pin")
    require(len(set(policy["allowed_source_modes"])) == len(policy["allowed_source_modes"]), "duplicate source mode")
    if policy["origin"]["kind"] == "approved_git_blob":
        require(policy["origin"]["revision"] is not None, "approved policy needs a pinned revision")
    for requirement in requirements.values():
        selector = requirement["selector"]
        if requirement["kind"] in {"scanner", "ledger"}:
            require("engine" in selector, "scanner/ledger requirement needs an engine selector")
            require((requirement["kind"] == "ledger") == (selector["engine"] == "ledgerwash"),
                    "engine selector does not match requirement kind")
        if "engine" in selector:
            require(selector["engine"] in pins, "requirement references an unpinned engine")
        if requirement["minimum_binding"] == "workflow_bound":
            require(all(k in selector for k in ("workflow_id", "app_id", "producer_repository_id", "producer_path", "producer_sha")),
                    "workflow-bound requirement needs an approved producer selector")


def _source_matches(item: dict, report: dict, policy: dict) -> bool:
    subject = report["subject"]
    source = item["subject_sha"]
    if item["kind"] in {"scanner", "ledger"}:
        return source == subject["head_sha"]
    if source == subject["head_sha"]:
        return "pr_head" in policy["allowed_source_modes"]
    return bool("github_merge" in policy["allowed_source_modes"]
                and source is not None and source == subject["merge_commit_sha"]
                and subject["merge_parents"] == [subject["base_tip_sha"], subject["head_sha"]])


def _validate_support(item: dict, obligation: dict, requirement: dict, report: dict, policy: dict) -> None:
    require(item["status"] == "observed", "a completed obligation needs observed evidence")
    require(item["raw_sha256"] is not None, "a completed obligation needs a raw evidence digest")
    require(item["repository_id"] == report["subject"]["repository_id"], "evidence repository differs")
    require(item["kind"] == obligation["kind"], "evidence kind differs from obligation")
    require(BINDINGS[item["binding_level"]] >= BINDINGS[obligation["minimum_binding"]], "insufficient evidence binding")
    selector = requirement["selector"]
    for field in ("workflow_id", "app_id", "job_name", "matrix"):
        if field in selector:
            require(item[field] == selector[field], f"evidence selector mismatch: {field}")
    if item["kind"] in {"source", "ci_job", "test_report", "scanner", "ledger"}:
        require(_source_matches(item, report, policy), "evidence source is stale or unsupported")
    if item["kind"] in {"ci_job", "test_report"}:
        require(all(item[k] is not None for k in ("workflow_id", "run_id", "attempt", "job_id", "app_id", "job_name")),
                "completed CI evidence lacks platform execution identity")
        if item["kind"] == "test_report":
            require(item["artifact_id"] is not None and item["artifact_sha256"] is not None,
                    "test report lacks artifact identity/digest")
    if item["run_id"] is not None:
        require((item["workflow_id"], item["run_id"], item["attempt"]) in _runs(report["selected_runs"]),
                "evidence belongs to an unselected run or attempt")
    if item["binding_level"] == "workflow_bound":
        proof = item["producer_proof"]
        require(proof is not None, "workflow binding cannot be self-declared")
        for field, proof_field in (("producer_repository_id", "repository_id"), ("producer_path", "path"), ("producer_sha", "sha")):
            require(field in selector and selector[field] == proof[proof_field], "producer is not approved by policy")
        require(all(item[k] is not None for k in ("workflow_id", "run_id", "attempt", "job_id", "app_id")),
                "workflow-bound evidence lacks platform identity")
    if obligation["outcome"] == "satisfied" and item["kind"] in {"ci_job", "test_report"}:
        require(item["conclusion"] == "success", "non-success execution cannot satisfy required success")
    if obligation["outcome"] == "failed" and item["kind"] in {"ci_job", "test_report"}:
        require(item["conclusion"] == "failure", "non-execution cannot be a completed failure")
        if item["kind"] == "test_report":
            counts = item["test_counts"]
            require(counts is not None and counts["failed"] + counts["errors"] > 0,
                    "completed test failure requires observed test/collection errors")
    if item["kind"] == "test_report" and obligation["outcome"] == "satisfied":
        counts = item["test_counts"]
        require(counts is not None, "test evidence lacks test counts")
        require(counts["failed"] == 0 and counts["errors"] == 0, "failed tests cannot satisfy success")
        require(counts["passed"] >= max(1, selector.get("min_tests", 1)), "zero or insufficient executed tests")
        require(selector.get("allow_skips", False) or counts["skipped"] == 0, "skips exceed policy")


def validate_report(report: dict, policy: dict) -> None:
    validate_document("report", report)
    validate_policy(policy)
    require(report["policy"] == {"policy_id": policy["policy_id"], "canonical_sha256": canonical_digest(policy)}, "policy identity/digest differs")
    require(report["subject"]["repository_id"] == policy["repository_id"], "policy repository differs")
    requirements = _index(policy["requirements"], "id", "policy requirement")
    obligations = _index(report["obligations"], "id", "report obligation")
    evidence = _index(report["evidence"], "id", "evidence identity")
    engines = _index(report["engines"], "name", "engine identity")
    _runs(report["selected_runs"])
    require(set(requirements) == set(obligations), "report changed the policy obligation inventory")
    for key, obligation in obligations.items():
        requirement = requirements[key]
        for field in ("kind", "required", "minimum_binding"):
            require(obligation[field] == requirement[field], f"report changed requirement {key}/{field}")
        require(len(obligation["evidence_ids"]) == len(set(obligation["evidence_ids"])), "duplicate evidence reference")
        require(all(e in evidence for e in obligation["evidence_ids"]), "dangling evidence reference")
        require(obligation["outcome"] != "failed" or obligation["effect"] == "block", "observed failure must block")
        if obligation["outcome"] not in {"satisfied", "not_applicable"} or obligation["effect"] != "clear":
            require(bool(obligation["reason_codes"]), "non-clear obligation needs reason codes")
        if obligation["outcome"] in {"satisfied", "failed"}:
            require(bool(obligation["evidence_ids"]), "completed obligation lacks supporting evidence")
            for evidence_id in obligation["evidence_ids"]:
                _validate_support(evidence[evidence_id], obligation, requirement, report, policy)
    pins = _index(policy["engine_pins"], "name", "engine pin")
    for name, engine in engines.items():
        require(name in pins, "unapproved engine")
        require(engine["version"] == pins[name]["version"] and engine["artifact_sha256"] == pins[name]["artifact_sha256"], "engine pin differs")
        if engine["evidence_id"] is not None:
            require(engine["evidence_id"] in evidence, "engine evidence reference is missing")
        if engine["status"] == "completed":
            require(engine["raw_report_sha256"] is not None and engine["exit_code"] is not None, "completed engine lacks native result")
            require((engine["exit_code"], engine["verdict"]) in {(0, "pass"), (1, "block")},
                    "native check/scan exit and verdict contradict completed engine status")
            require(engine["evidence_id"] in evidence, "completed engine lacks raw evidence")
            require(engine["raw_report_sha256"] == evidence[engine["evidence_id"]]["raw_sha256"],
                    "engine raw report digest differs from linked evidence")
        else:
            require(engine["verdict"] == "not_judged", "unfinished engine cannot claim a verdict")
        if engine["verdict"] == "block":
            require(any(o["effect"] == "block" and engine["evidence_id"] in o["evidence_ids"] for o in obligations.values()), "blocking engine finding was suppressed")
        if engine["verdict"] == "held" or any(f["disposition"] == "added_exemption" for f in engine["findings"]):
            require(any(o["effect"] in {"review", "block"} and engine["evidence_id"] in o["evidence_ids"] for o in obligations.values()), "human review hold was suppressed")
    for requirement in requirements.values():
        name = requirement["selector"].get("engine")
        obligation = obligations[requirement["id"]]
        if name and obligation["outcome"] in {"satisfied", "failed"}:
            require(name in engines and engines[name]["status"] == "completed", "scanner obligation lacks a completed pinned engine")
            engine = engines[name]
            require(engine["evidence_id"] in obligation["evidence_ids"], "scanner evidence does not match engine")
            if obligation["outcome"] == "satisfied":
                require(engine["verdict"] in {"pass", "held"}, "non-passing engine cannot satisfy scanner success")
                require(engine["coverage"]["status"] == "sufficient", "scanner coverage is incomplete")
    for action in report["action_items"]:
        require(all(key in obligations for key in action["obligation_ids"]), "action references unknown obligation")
    _index(report["action_items"], "id", "action identity")
    _index(report["raw_inventory"], "path", "raw file path")
    for item in report["raw_inventory"]:
        require(safe_relative_path(item["path"]), "unsafe raw inventory path")
    if report["recheck"] is not None:
        require(report["recheck"]["observed_at"] >= report["observed_at"], "freshness observation predates collection")
    expected_state = derive_state(report)
    require(report["state"] == expected_state, "reported state contradicts normalized obligations/freshness")
    expected_exit = exit_for(expected_state)
    require(report["exit_code"] == expected_exit, "exit code contradicts state")
    require(report["ready_for_review"] is (expected_exit == 0), "ready flag contradicts state")


def validate_engine_lock(lock: dict) -> None:
    validate_document("engine-lock", lock)
    engines = _index(lock["engines"], "name", "locked engine")
    require(set(engines) == {"checkwash", "pinwash", "ledgerwash"}, "candidate engine inventory differs")
    for name, engine in engines.items():
        require(engine["verification"]["metadata"] == {"name": name, "version": engine["version"]},
                "candidate metadata identity differs")
        require(engine["artifact"]["filename"] == f"{name}-{engine['version']}-py3-none-any.whl",
                "candidate wheel filename differs")


def validate_receipt(receipt: dict) -> None:
    validate_document("receipt", receipt)
    source = receipt["source"]
    if source["mode"] == "pr_head":
        require(source["checked_out_sha"] == source["head_sha"], "head receipt has another checkout")
        require(source["merge_parents"] == [], "head receipt has merge parents")
    else:
        require(source["merge_parents"] == [source["base_tip_sha"], source["head_sha"]], "merge receipt has different parents")
        require(source["checked_out_sha"] not in source["merge_parents"], "merge checkout cannot be a parent")
    junit = receipt["execution"]["junit"]
    if junit is not None:
        require(safe_relative_path(junit["path"]), "unsafe JUnit report path")


def validate_case(case: dict) -> None:
    validate_document("acceptance-case", case)
    require(case["case_id"].startswith(case["family_id"] + "-"), "case and family identity differ")
    validate_report(case["report"], case["policy"])
    for field in ("state", "exit_code", "ready_for_review"):
        require(case["expected"][field] == case["report"][field], f"frozen expectation differs: {field}")
    actual_reasons = {reason for o in case["report"]["obligations"] for reason in o["reason_codes"]}
    require(set(case["expected"]["reason_codes"]) == actual_reasons, "expected reasons differ from report")
    if "419" in case["case_id"]:
        require("known_checkwash_419_escape" in case["report"]["limitations"], "known escape disclosure is missing")
