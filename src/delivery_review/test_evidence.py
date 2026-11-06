"""Bind original test evidence to independently selected GitHub execution facts.

This is a verifier: it parses data and never runs reviewed project code. An
AttestationExecution must come from this process's official offline verification,
including on replay; a stored observation or a JSON success flag is not proof.
ProducerQualification is supplied only by the separately approved registry.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from types import MappingProxyType

from .adapters.artifact_zip import ArtifactZipContents, read_artifact_zip
from .adapters import github_attestation as attest
from .adapters.pytest_junit import JUnitError, parse_pytest_junit
from .contracts import ContractError, canonical_bytes, loads_strict, require, validate_receipt
from .evidence.capture import VerifiedCapture, parse_capture
from .inspection_profile import load_inspection_profile
from .providers.github_observations import map_capture
from .providers.github_routes import next_page, positive, validate_response_headers
from .source_snapshot import SourceSnapshot, verify_snapshot, _commit


@dataclass(frozen=True)
class RawApiFact:
    request_path: str
    status_code: int | None
    body_bytes: bytes
    observed_at: str
    complete: bool
    headers: Mapping[str, str]


@dataclass(frozen=True)
class EventSourceFact:
    """Canonical Git commit bytes and an optional original merge-ref API fact."""
    commit_bytes: bytes
    reference: RawApiFact | None = None


@dataclass(frozen=True)
class ProducerQualification:
    repository_id: int
    path: str
    sha: str
    status: str
    producer_profile: str
    qualification_receipt_sha256: str
    qualification_receipt_bytes: bytes
    test_path: str = "tests"
    source_modes: tuple[str, ...] = ("pr_head",)


@dataclass(frozen=True)
class TestEvidenceInputs:
    artifact: ArtifactZipContents | None = None
    archive_bytes: bytes | None = None
    artifact_metadata: RawApiFact | None = None
    producer_repository: RawApiFact | None = None
    attestation: attest.AttestationExecution | None = None
    attestation_retention: attest.AttestationExecution | None = None
    event_source: EventSourceFact | None = None
    qualification: ProducerQualification | None = None
    event_source_basis: str = "legacy_pr_field_v1"


@dataclass(frozen=True)
class TestEvidenceResult:
    evidence: dict
    obligation: dict
    raw_files: Mapping[str, bytes]
    diagnostics: dict


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _strict_equal(left, right):
    return canonical_bytes(left) == canonical_bytes(right)


def derived_id(prefix, identity):
    value = prefix + identity
    return value if len(value) <= 128 else prefix + _sha(identity.encode())


def evidence_record(requirement, repository_id):
    return {"id": derived_id("e-", requirement["id"]), "kind": requirement["kind"],
        "repository_id": repository_id, "subject_sha": None, "workflow_id": None,
        "run_id": None, "attempt": None, "job_id": None, "app_id": None,
        "artifact_id": None, "artifact_sha256": None, "job_name": None, "matrix": {},
        "test_counts": None, "conclusion": None, "binding_level": "reported",
        "producer_proof": None, "status": "missing", "raw_sha256": None,
        "source_url": None, "messages": []}


def obligation_record(requirement, evidence, outcome, reasons=(), effect="clear"):
    return {key: requirement[key] for key in ("id", "kind", "required", "minimum_binding")} | {
        "outcome": outcome, "effect": effect, "evidence_ids": [evidence["id"]],
        "reason_codes": sorted(set(reasons))}


def validated_context(profile_bytes, capture, snapshot):
    profile = load_inspection_profile(profile_bytes)
    require(type(capture) is VerifiedCapture, "inspection_capture_required")
    capture = parse_capture(capture.manifest_bytes, capture.profile_bytes, capture.bodies)
    require(_strict_equal(profile["github"], capture.profile), "inspection_capture_profile_mismatch")
    require(type(snapshot) is SourceSnapshot, "inspection_source_required")
    verify_snapshot(snapshot)
    observations = map_capture(capture)
    subject = observations["subject"]
    require(subject is not None, "inspection_subject_unconfirmed")
    require(snapshot.repository == subject["full_name"] and snapshot.head_sha == subject["head_sha"]
            and snapshot.base_tip_sha == subject["base_tip_sha"], "inspection_source_mismatch")
    return profile, capture, observations


def _job_inventory_complete(capture, workflow_id, run_id, attempt):
    pages = [row for row in capture.manifest["records"] if row["phase"] == "initial" and row["role"] == "jobs"
             and (row["workflow_id"], row["run_id"], row["attempt"]) == (workflow_id, run_id, attempt)]
    if not pages:
        return False
    numbers = {row["page"] for row in pages}
    if numbers != set(range(1, max(numbers) + 1)):
        return False
    totals, ids = set(), []
    try:
        for page in pages:
            if page["status_code"] != 200 or page["error"] is not None or page["body"] is None or not page["body"]["complete"]:
                return False
            payload = loads_strict(capture.bodies[page["body"]["path"]])
            if (type(payload) is not dict or type(payload.get("jobs")) is not list
                    or type(payload.get("total_count")) is not int or payload["total_count"] < 0
                    or len(payload["jobs"]) > capture.profile["limits"]["per_page"]):
                return False
            totals.add(payload["total_count"])
            for row in payload["jobs"]:
                if type(row) is not dict or not positive(row.get("id")):
                    return False
                ids.append(row["id"])
            following = next_page(page["response_headers"], page["request"]["path"], max_pages=capture.profile["limits"]["max_pages"])
            if following != (page["page"] + 1 if page["page"] < max(numbers) else None):
                return False
        return len(totals) == 1 and len(set(ids)) == len(ids) == next(iter(totals))
    except (ContractError, ValueError, TypeError, KeyError):
        return False


def selected_job(observations, requirement, capture):
    """Never replace a selected untrusted/pending run with an older success."""
    selector = requirement["selector"]
    workflow = next((row for row in observations["workflows"] if row["workflow_id"] == selector["workflow_id"]), None)
    if workflow is None or workflow["selection"].get("status") != "selected":
        return workflow, None, "missing", ["required_workflow_unavailable"]
    if workflow["selection"].get("attempt") is None:
        return workflow, None, "untrusted", ["selected_attempt_unconfirmed"]
    if not _job_inventory_complete(capture, workflow["workflow_id"], workflow["selection"]["run_id"], workflow["selection"]["attempt"]):
        return workflow, None, "untrusted", ["job_pagination_unconfirmed"]
    candidates = [row for row in workflow["jobs"] if row["name"] == selector["job_name"]]
    match = next((row for row in workflow["expected_jobs"] if row["name"] == selector["job_name"]), None)
    if len(candidates) != 1 or match is None:
        return workflow, None, "missing" if not candidates else "untrusted", ["required_job_missing" if not candidates else "required_job_ambiguous"]
    job = candidates[0]
    if (workflow["association"] != "matched" or job["association"] != "matched" or match["status"] != "matched"
            or job.get("check") is None or job["check"].get("app_id") != selector["app_id"]):
        return workflow, job, "untrusted", ["platform_execution_unconfirmed"]
    check = job["check"]
    if (job["native_state"] != "observed" or check["native_state"] != "observed"
            or (job["status"] == "completed") != (check["status"] == "completed")
            or job["conclusion"] != check["conclusion"]):
        return workflow, job, "untrusted", ["platform_execution_state_conflict"]
    return workflow, job, None, []


def capture_payload(capture, role, *, phase="initial", **identity):
    records = [row for row in capture.manifest["records"] if row["role"] == role and row["phase"] == phase
               and all(row[key] == value for key, value in identity.items())]
    require(len(records) == 1, "independent_api_fact_ambiguous")
    record = records[0]
    require(record["status_code"] == 200 and record["error"] is None and record["body"] is not None
            and record["body"]["complete"], "independent_api_fact_unavailable")
    return record, loads_strict(capture.bodies[record["body"]["path"]])


def _api(fact, expected_path):
    require(type(fact) is RawApiFact and type(fact.body_bytes) is bytes, "api_fact_missing")
    require(fact.request_path == expected_path, "api_fact_route_mismatch")
    require(type(fact.complete) is bool and fact.complete and type(fact.status_code) is int
            and fact.status_code == 200, "api_fact_unavailable")
    require(type(fact.observed_at) is str and datetime.strptime(fact.observed_at, "%Y-%m-%dT%H:%M:%SZ").strftime(
        "%Y-%m-%dT%H:%M:%SZ") == fact.observed_at, "api_fact_time_invalid")
    validate_response_headers(dict(fact.headers))
    return loads_strict(fact.body_bytes)


def merge_reference_identity(reference, repository, pr_number):
    """Validate the original fixed-route GitHub merge ref before any Git fetch."""
    require(type(repository) is str and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
            and all(part not in {".", ".."} for part in repository.split("/")) and positive(pr_number),
            "event_merge_reference_scope_invalid")
    path = f"/repos/{repository}/git/ref/pull/{pr_number}/merge"
    value = _api(reference, path)
    require(type(value) is dict and value.get("ref") == f"refs/pull/{pr_number}/merge"
            and type(value.get("object")) is dict, "event_merge_reference_invalid")
    obj = value["object"]
    sha = obj.get("sha")
    require(obj.get("type") == "commit" and type(sha) is str and re.fullmatch(r"[0-9a-f]{40}", sha)
            and obj.get("url") == f"https://api.github.com/repos/{repository}/git/commits/{sha}",
            "event_merge_reference_invalid")
    return sha


def _event_source_basis(value):
    require(type(value) is str and value in {"legacy_pr_field_v1", "github_merge_reference_v1"},
            "event_source_basis_unsupported")


def event_source_identity(event_source, snapshot, pr, *, event_source_basis="legacy_pr_field_v1"):
    _event_source_basis(event_source_basis)
    if event_source_basis == "github_merge_reference_v1":
        require(type(event_source) is EventSourceFact and type(event_source.reference) is RawApiFact,
                "event_merge_reference_missing")
    elif type(event_source) is EventSourceFact:
        require(event_source.reference is None, "event_source_basis_reference_conflict")
    if event_source is None:
        return snapshot.head_sha, []
    require(type(event_source) is EventSourceFact and type(event_source.commit_bytes) is bytes
            and 0 < len(event_source.commit_bytes) <= 2 * 1024 * 1024, "event_source_invalid")
    header, payload = event_source.commit_bytes.split(b"\0", 1)
    require(header == b"commit " + str(len(payload)).encode(), "event_source_object_invalid")
    sha = hashlib.sha1(event_source.commit_bytes).hexdigest()
    _, parents = _commit(payload)
    if event_source.reference is not None:
        require(type(pr) is dict, "event_merge_reference_scope_invalid")
        reference_sha = merge_reference_identity(event_source.reference, snapshot.repository, pr.get("number"))
        require(sha == reference_sha, "event_merge_reference_sha_mismatch")
        require(list(parents) == [snapshot.base_tip_sha, snapshot.head_sha], "event_merge_parent_mismatch")
        return sha, list(parents)
    # Only historical callers without a separate merge reference may use the
    # deprecated PR field or the original head-commit shortcut.
    if sha == snapshot.head_sha:
        return sha, []
    require(sha == pr.get("merge_commit_sha") and list(parents) == [snapshot.base_tip_sha, snapshot.head_sha],
            "event_merge_parent_mismatch")
    return sha, list(parents)


def _qualification(value, selector, source_mode):
    require(type(value) is ProducerQualification and value.status == "qualified"
            and value.producer_profile == attest.PRODUCER_PROFILE and value.test_path == "tests"
            and type(value.source_modes) is tuple and 0 < len(value.source_modes) <= 2
            and len(set(value.source_modes)) == len(value.source_modes)
            and set(value.source_modes) <= {"pr_head", "github_merge"} and source_mode in value.source_modes,
            "producer_not_qualified")
    require(type(value.qualification_receipt_bytes) is bytes
            and _sha(value.qualification_receipt_bytes) == value.qualification_receipt_sha256,
            "producer_qualification_digest_mismatch")
    expected = {"repository_id": selector["producer_repository_id"], "path": selector["producer_path"],
                "sha": selector["producer_sha"]}
    require(_strict_equal({key: getattr(value, key) for key in expected}, expected), "producer_qualification_identity_mismatch")
    receipt = loads_strict(value.qualification_receipt_bytes)
    require(type(receipt) is dict and receipt.get("kind") == "delivery-review-producer-qualification"
            and type(receipt.get("qualification_version")) is int and receipt["qualification_version"] == 1
            and receipt.get("result") == "qualified" and _strict_equal(receipt.get("producer"), expected)
            and receipt.get("producer_profile") == attest.PRODUCER_PROFILE and receipt.get("test_path") == "tests"
            and _strict_equal(receipt.get("source_modes"), list(value.source_modes)), "producer_qualification_receipt_mismatch")


def _attestation_inventory(execution):
    require(type(execution) is attest.AttestationExecution, "attestation_missing")
    manifest, raw = execution.manifest, execution.raw_files
    require(manifest.get("kind") == "delivery-review-attestation-execution"
            and type(manifest.get("execution_version")) is int and manifest["execution_version"] == 1,
            "attestation_execution_invalid")
    tool = manifest.get("tool", {})
    require(type(tool) is dict and set(tool) == {"name", "version", "platform", "architecture", "sha256"}
            and type(tool["sha256"]) is str and tool.get("name") == "gh" and tool.get("version") == attest.GH_VERSION and
            attest.GH_PINS.get((tool.get("platform"), tool.get("architecture"))) == tool.get("sha256"),
            "attestation_tool_unapproved")
    require(set(raw) == {"artifact", "bundle", "trusted_root", "expected", "stdout", "stderr", "version_stdout", "version_stderr"}
            and set(manifest["files"]) == set(raw), "attestation_inventory_mismatch")
    for name, data in raw.items():
        require(type(data) is bytes and manifest["files"][name]["sha256"] == _sha(data)
                and manifest["files"][name]["size_bytes"] == len(data), "attestation_raw_digest_mismatch")
    return manifest, raw


def _retained_attestation(execution, retention):
    if retention is None:
        return execution
    require(type(execution) is attest.AttestationExecution, "fresh_attestation_execution_required")
    _, original = _attestation_inventory(retention)
    require(all(original[key] == execution.raw_files.get(key) for key in ("artifact", "bundle", "trusted_root", "expected")),
            "attestation_retention_inputs_mismatch")
    # A previous process result is historical data only. The caller saves this
    # replay's fresh execution audit outside the deterministic report inventory.
    return retention


def _attestation_execution(execution, expected, archive):
    """Reparse native verifier bytes; never accept saved normalized success."""
    manifest, raw = _attestation_inventory(execution)
    require(raw["artifact"] == archive and _sha(raw["trusted_root"]) in attest.TRUSTED_ROOT_PINS,
            "attestation_inputs_mismatch")
    require(not manifest.get("controller_integrity_errors") and manifest["version_process"]["termination"] == "exited"
            and type(manifest["version_process"]["exit_code"]) is int and manifest["version_process"]["exit_code"] == 0
            and raw["version_stdout"].startswith(("gh version " + attest.GH_VERSION + " ").encode()),
            "attestation_controller_failed")
    process = manifest["process"]
    require(process["termination"] == "exited" and type(process["exit_code"]) is int and process["exit_code"] == 0,
            "attestation_verification_failed")
    common = {key: expected[key] for key in attest._COMMON - {"subject_name", "predicate_type"}}
    common.update(predicate_type=attest.PREDICATE_TYPE, subject_name="https://github.com/" + expected["repository_full_name"]
        + "/actions/runs/" + str(expected["run_id"]) + "/artifacts/" + str(expected["artifact_id"]))
    predicate = {key: expected[key] for key in ("repository_id", "run_id", "run_attempt", "job_id", "check_run_id", "artifact_id",
                                             "artifact_sha256", "receipt_sha256", "junit_sha256")}
    predicate.update(predicate_version=1, producer_profile=attest.PRODUCER_PROFILE)
    require(_strict_equal(loads_strict(raw["expected"]), {"certificate_and_subject": common, "predicate": predicate}),
            "attestation_expectations_mismatch")
    # Paths are controller-owned; the fixed flags must remain exact. Merely
    # having an exit-zero blob in an invented verifier manifest is insufficient.
    argv = process["argv"]
    require(type(argv) is list and len(argv) == 25 and argv[1:3] == ["attestation", "verify"]
            and argv[4] == "--bundle" and argv[6] == "--custom-trusted-root"
            and argv[8:] == ["--repo", common["repository_full_name"], "--hostname", "github.com",
                "--cert-identity", attest._identity(common), "--signer-digest", common["producer_sha"],
                "--cert-oidc-issuer", attest.ISSUER, "--deny-self-hosted-runners", "--predicate-type", attest.PREDICATE_TYPE,
                "--digest-alg", "sha256", "--format", "json"], "attestation_invocation_mismatch")
    states, reasons, native = attest._interpret(raw["stdout"], common, _sha(archive), predicate)
    claim, error = attest._bundle_claim(raw["bundle"])
    require(not reasons and error is None and all(value == "matched" for value in states.values()),
            "attestation_claims_unconfirmed")
    encode = lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    require(encode(claim) == encode(native[0]["verificationResult"]["statement"]), "attestation_statement_mismatch")


def bind_test_evidence(*, profile_bytes, capture, snapshot, requirement_id,
        artifact=None, archive_bytes=None, artifact_metadata=None, producer_repository=None, attestation=None,
        attestation_retention=None, event_source=None, qualification=None, event_source_basis="legacy_pr_field_v1"):
    """Derive one test obligation; missing/untrusted observations stay explicit.

    ``diagnostics.attestation_expected`` is available before cryptographic
    execution when sufficient raw claims exist. Run the official verifier with
    that expectation, then call again with its actual AttestationExecution.
    """
    _event_source_basis(event_source_basis)
    profile, capture, observations = validated_context(profile_bytes, capture, snapshot)
    require(artifact is None or archive_bytes is None, "ambiguous_artifact_input")
    requirements = [row for row in profile["policy"]["requirements"] if row["id"] == requirement_id and row["kind"] == "test_report"]
    require(len(requirements) == 1, "unknown_test_requirement")
    requirement, policy = requirements[0], profile["policy"]
    selector, producer = requirement["selector"], profile["producers"][requirement_id]
    evidence = evidence_record(requirement, policy["repository_id"])
    files, diagnostics, problems = {}, {"attestation_expected": None}, []
    prefix = "tests/" + _sha(requirement_id.encode()) + "/"
    def retain(name, raw):
        if type(raw) is bytes:
            files[prefix + name] = raw
    for name, fact in (("artifact-metadata.json", artifact_metadata), ("producer-repository.json", producer_repository),
            ("event-merge-reference.json", event_source.reference if type(event_source) is EventSourceFact else None)):
        if type(fact) is RawApiFact:
            retain(name, fact.body_bytes)
            retain(name + ".request.json", canonical_bytes({"path": fact.request_path, "status_code": fact.status_code,
                "complete": fact.complete, "observed_at": fact.observed_at, "headers": dict(fact.headers)}) + b"\n")
    if type(event_source) is EventSourceFact:
        retain("event-source.git", event_source.commit_bytes)
    if event_source_basis == "github_merge_reference_v1" and not (
            type(event_source) is EventSourceFact and type(event_source.reference) is RawApiFact):
        problems.append(("untrusted", "event_merge_reference_missing"))
    if type(qualification) is ProducerQualification:
        retain("qualification.json", qualification.qualification_receipt_bytes)
    retained_attestation = _retained_attestation(attestation, attestation_retention)
    if type(retained_attestation) is attest.AttestationExecution:
        retain("attestation/verification.json", retained_attestation.manifest_bytes)
        for name, raw in retained_attestation.raw_files.items():
            retain("attestation/" + name + ".bin", raw)
    workflow, job, initial_outcome, reasons = selected_job(observations, requirement, capture)
    problems.extend((initial_outcome, reason) for reason in reasons)
    if workflow is not None:
        evidence.update(workflow_id=workflow["workflow_id"], run_id=workflow["selection"].get("run_id"),
                        attempt=workflow["selection"].get("attempt"))
    if job is not None:
        evidence.update(job_id=job["id"], job_name=job["name"], app_id=(job.get("check") or {}).get("app_id"),
                        conclusion=job["conclusion"])
        diagnostics["platform_job"] = {key: job[key] for key in ("status", "conclusion", "native_state", "association")}
        if initial_outcome is None and job["status"] != "completed":
            problems.append(("pending", "required_job_pending"))
            evidence.update(status="pending", conclusion="pending")
        elif initial_outcome is None and job["conclusion"] in {"skipped", "neutral", "cancelled", "timed_out", "action_required"}:
            problems.append(("missing", "required_job_" + job["conclusion"]))
    effect = "block" if initial_outcome is None and job["native_state"] == "observed" and job["conclusion"] == "failure" else "clear"
    if artifact is None and archive_bytes is None:
        problems.append(("pending" if evidence["status"] == "pending" else "missing", "artifact_missing"))
    else:
        try:
            if archive_bytes is None:
                require(type(artifact) is ArtifactZipContents, "artifact_type_invalid")
                archive_bytes = artifact.archive_bytes
            require(type(archive_bytes) is bytes and len(archive_bytes) <= 25 * 1024 * 1024, "artifact_input_limit")
            retain("artifact.zip", archive_bytes)
            artifact = read_artifact_zip(archive_bytes, policy_limits=policy["limits"])
            receipt_raw, junit_raw = artifact.files["receipt.json"], artifact.files["junit.xml"]
            retain("receipt.json", receipt_raw)
            retain("junit.xml", junit_raw)
            receipt = loads_strict(receipt_raw)
            validate_receipt(receipt)
            junit = parse_pytest_junit(junit_raw)
            diagnostics["junit"] = junit
            counts = {"passed": junit["case_counts"]["passed"], "failed": junit["case_counts"]["failures"],
                      "errors": junit["case_counts"]["errors"], "skipped": junit["case_counts"]["skipped"]}
            evidence.update(status="observed", raw_sha256=_sha(receipt_raw), artifact_sha256=artifact.archive_sha256,
                            test_counts=counts, subject_sha=receipt["source"]["checked_out_sha"])
            require(junit["valid"], "junit_counts_invalid")
            require(_strict_equal(receipt["execution"]["tests"], counts), "receipt_counts_mismatch")
            require(receipt["execution"]["junit"] == {"path": "junit.xml", "sha256": _sha(junit_raw), "size_bytes": len(junit_raw)},
                    "receipt_junit_digest_mismatch")
            command = ["/work/venv/bin/python", "-I", "-m", "pytest", "-c", "/trusted/pytest.ini",
                       "--rootdir=/work/source", "--junitxml=/work/output/junit.xml", "--strict-markers", "tests"]
            require(receipt["execution"]["command"] == command, "producer_command_unqualified")
            require(_strict_equal({key: receipt[key] for key in ("repository_id", "head_repository_id", "pr_number")},
                                 {key: observations["subject"][key] for key in ("repository_id", "head_repository_id", "pr_number")}),
                    "receipt_subject_identity_mismatch")
            _, pr = capture_payload(capture, "pull_request")
            cert_sha, parents = event_source_identity(event_source, snapshot, pr, event_source_basis=event_source_basis)
            source = receipt["source"]
            expected_source = {"mode": source["mode"], "head_sha": snapshot.head_sha, "base_tip_sha": snapshot.base_tip_sha,
                "checked_out_sha": snapshot.head_sha if source["mode"] == "pr_head" else cert_sha,
                "merge_parents": [] if source["mode"] == "pr_head" else parents}
            require(source["mode"] in policy["allowed_source_modes"] and _strict_equal(source, expected_source)
                    and (source["mode"] != "github_merge" or parents == [snapshot.base_tip_sha, snapshot.head_sha]),
                    "receipt_source_mismatch")
            require(initial_outcome is None, "platform_execution_unconfirmed")
            expected_producer = {"repository_id": selector["producer_repository_id"], "workflow_path": selector["producer_path"],
                "workflow_sha": selector["producer_sha"], "run_id": evidence["run_id"], "attempt": evidence["attempt"], "job_id": job["id"]}
            require(_strict_equal(receipt["producer"], expected_producer), "receipt_execution_mismatch")
            repository = _api(producer_repository, "/repos/" + producer["repository_full_name"])
            require(type(repository) is dict and type(repository.get("id")) is int
                    and repository["id"] == selector["producer_repository_id"] and repository.get("full_name") == producer["repository_full_name"],
                    "producer_repository_mismatch")
            attempt_record, attempt = capture_payload(capture, "attempt", workflow_id=selector["workflow_id"],
                run_id=evidence["run_id"], attempt=evidence["attempt"])
            reference_path = producer["repository_full_name"] + "/" + selector["producer_path"]
            references = attempt.get("referenced_workflows")
            require(type(references) is list, "referenced_producer_missing")
            matched = [row for row in references if type(row) is dict and type(row.get("path")) is str
                       and row["path"].split("@", 1)[0] == reference_path]
            require(len(matched) == 1 and matched[0].get("sha") == selector["producer_sha"]
                    and matched[0]["path"] == reference_path + "@" + selector["producer_sha"], "referenced_producer_mismatch")
            meta = loads_strict(artifact_metadata.body_bytes) if type(artifact_metadata) is RawApiFact else {}
            artifact_id = meta.get("id") if type(meta) is dict else None
            require(type(artifact_id) is int and 1 <= artifact_id <= 2**53 - 1, "artifact_identity_missing")
            meta = _api(artifact_metadata, "/repos/" + snapshot.repository + "/actions/artifacts/" + str(artifact_id))
            evidence["artifact_id"] = artifact_id
            require(meta.get("digest") == "sha256:" + artifact.archive_sha256
                    and type(meta.get("size_in_bytes")) is int and meta["size_in_bytes"] == artifact.archive_size_bytes,
                    "artifact_transport_digest_mismatch")
            require(meta.get("expired") is False and meta.get("name") == f"delivery-review-{evidence['run_id']}-{evidence['attempt']}-{job['check']['id']}",
                    "artifact_locator_mismatch")
            run_meta = meta.get("workflow_run")
            require(type(run_meta) is dict and type(run_meta.get("id")) is int and run_meta["id"] == evidence["run_id"]
                    and run_meta.get("head_sha") == snapshot.head_sha and type(run_meta.get("repository_id")) is int
                    and run_meta["repository_id"] == policy["repository_id"] and type(run_meta.get("head_repository_id")) is int
                    and run_meta["head_repository_id"] == observations["subject"]["head_repository_id"], "artifact_run_mismatch")
            # Transport/run consistency does not identify the producing job.
            # Do not label a job-bearing test record platform_bound until the
            # signed assertion and separately qualified producer establish it.
            diagnostics["artifact_transport"] = "matched"
            expected = {"repository_id": policy["repository_id"], "repository_full_name": snapshot.repository,
                "certificate_source_sha": cert_sha, "certificate_event": "pull_request", "run_id": evidence["run_id"],
                "run_attempt": evidence["attempt"], "job_id": job["id"], "check_run_id": job["check"]["id"],
                "artifact_id": artifact_id, "artifact_sha256": artifact.archive_sha256,
                "receipt_sha256": _sha(receipt_raw), "junit_sha256": _sha(junit_raw),
                "producer_repository_full_name": producer["repository_full_name"], "producer_path": selector["producer_path"],
                "producer_sha": selector["producer_sha"]}
            diagnostics["attestation_expected"] = expected
            _attestation_execution(attestation, expected, artifact.archive_bytes)
            if attestation_retention is not None:
                # Fresh cryptography is necessary but must not retroactively
                # replace a failed original verification with successful proof.
                # The historical execution is only a negative gate; it cannot
                # authorize anything without the fresh verification above.
                _attestation_execution(attestation_retention, expected, artifact.archive_bytes)
            _qualification(qualification, selector, source["mode"])
            evidence.update(binding_level="workflow_bound", producer_proof={"method": "github_api_referenced_workflow",
                "repository_id": selector["producer_repository_id"], "path": selector["producer_path"], "sha": selector["producer_sha"],
                "api_response_sha256": attempt_record["body"]["sha256"]},
                source_url=f"https://github.com/{snapshot.repository}/actions/runs/{evidence['run_id']}/artifacts/{artifact_id}")
            execution = receipt["execution"]
            require(execution["setup_exit"] == 0 and execution["collection_exit"] is None
                    and type(execution["test_exit"]) is int and execution["test_exit"] in {0, 1, 2, 3, 4, 5},
                    "producer_execution_incomplete")
            native = execution["test_exit"]
            require((native != 0 or not counts["failed"] and not counts["errors"])
                    and (native != 1 or counts["failed"] + counts["errors"] > 0)
                    and (native != 5 or sum(counts.values()) == 0), "pytest_exit_counts_mismatch")
            if counts["failed"] or counts["errors"]:
                evidence["conclusion"] = "failure"
                problems.append(("failed", "observed_test_failure"))
                effect = "block"
            elif native in {1, 2, 3, 4}:
                problems.append(("error", "pytest_error_without_junit_error"))
                effect = "block"
            elif sum(counts.values()) == 0:
                evidence["conclusion"] = "neutral"
                problems.append(("missing", "zero_tests"))
            elif counts["passed"] == 0:
                evidence["conclusion"] = "skipped"
                problems.append(("missing", "all_tests_skipped"))
            elif counts["passed"] < selector["min_tests"]:
                problems.append(("missing", "minimum_tests_unmet"))
            elif counts["skipped"] and not selector["allow_skips"]:
                problems.append(("missing", "partial_skips_disallowed"))
            else:
                evidence["conclusion"] = "success"
        except (ContractError, ValueError, TypeError, KeyError, IndexError) as exc:
            # Do not copy raw parser/schema messages; they may embed PR bytes.
            code = str(exc) if re.fullmatch(r"[a-z][a-z0-9_]{0,100}", str(exc)) else "test_evidence_invalid"
            category = "untrusted" if code in {"attestation_missing", "attestation_verification_failed", "attestation_claims_unconfirmed",
                "producer_not_qualified", "referenced_producer_missing", "platform_execution_unconfirmed", "api_fact_missing",
                "event_merge_reference_missing"} else "error"
            problems.append((category, code))
    priority = {"error": 8, "mismatch": 7, "untrusted": 6, "unavailable": 5, "unsupported": 4, "missing": 3, "pending": 2, "failed": 1}
    outcome = max((item[0] for item in problems), key=priority.get, default="satisfied")
    if outcome == "satisfied" and evidence["binding_level"] != "workflow_bound":
        outcome = "untrusted"
        problems.append((outcome, "test_binding_unconfirmed"))
    reasons = sorted({item[1] for item in problems})
    evidence["messages"] = reasons
    if outcome == "error":
        evidence["status"] = "error"
    diagnostics["reason_codes"] = reasons
    return TestEvidenceResult(evidence, obligation_record(requirement, evidence, outcome, reasons, effect),
                              MappingProxyType(files), diagnostics)
