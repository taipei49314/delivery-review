"""Compose contract-v1 delivery reports from raw, independently bound evidence."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import hashlib
import json
from types import MappingProxyType

from .adapters.engines import PINS, normalize_engine_result
from .contracts import canonical_bytes, canonical_digest, derive_state, exit_for, require, validate_report
from .engine_runner import EngineExecution
from .providers.github_observations import map_capture
from .providers.github_routes import sha
from .test_evidence import (TestEvidenceInputs, bind_test_evidence, capture_payload,
    derived_id, evidence_record, event_source_identity, obligation_record, selected_job, validated_context)


@dataclass(frozen=True)
class CompositionResult:
    report: dict
    raw_files: Mapping[str, bytes]
    engine_observations: Mapping[str, dict]
    test_diagnostics: Mapping[str, dict]


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _engine(execution, name, profile, snapshot, observed_at):
    require(type(execution) is EngineExecution, "engine_execution_required")
    manifest, raw = execution.manifest, execution.raw_files
    require(manifest.get("kind") == "delivery-review-engine-execution"
            and type(manifest.get("execution_version")) is int and manifest["execution_version"] == 1,
            "engine_execution_version_invalid")
    version, digest, _ = PINS[name]
    require(manifest["engine"] == {"name": name, "version": version, "wheel_sha256": digest}, "engine_execution_pin_mismatch")
    subject = {"head_sha": snapshot.head_sha, "base_tip_sha": snapshot.base_tip_sha, "merge_base_sha": snapshot.merge_base_sha}
    require(manifest["subject"] == subject and manifest["source_inventory_sha256"] == _sha(snapshot.inventory_bytes),
            "engine_execution_source_mismatch")
    require(set(manifest["streams"]) == set(raw), "engine_stream_inventory_mismatch")
    for key, value in raw.items():
        require(type(value) is bytes and manifest["streams"][key]["sha256"] == _sha(value)
                and manifest["streams"][key]["size_bytes"] == len(value), "engine_stream_digest_mismatch")
    native_policy = dict(profile["engine_options"][name], evaluation_date=observed_at[:10])
    policy_bytes = canonical_bytes(native_policy)
    require(manifest["native_policy_sha256"] == _sha(policy_bytes), "engine_execution_policy_mismatch")
    observed = normalize_engine_result(engine_name=name, engine_version=version, wheel_sha256=digest,
        subject=subject, process=manifest["process"], raw_files=raw, native_policy=native_policy)
    if name == "ledgerwash":
        require(("--require-complete" in manifest["process"]["argv"]) == native_policy["require_complete"],
                "engine_execution_policy_mismatch")
    integrity = manifest.get("controller_integrity_errors")
    require(type(integrity) is list and all(type(code) is str for code in integrity), "engine_integrity_observation_invalid")
    for key in ("source_integrity", "installation_integrity"):
        require(type(manifest.get(key)) is dict and set(manifest[key]) == {"before", "after"}
                and manifest[key]["before"] == "verified" and manifest[key]["after"] in {"verified", "failed"},
                "engine_integrity_observation_invalid")
        if manifest[key]["after"] != "verified":
            integrity = sorted(set(integrity) | {key + "_failed"})
    observed["controller_integrity_errors"] = integrity
    if integrity:
        observed.update(status="error", verdict="not_judged")
        observed["reason_codes"] = sorted(set(observed["reason_codes"]) | set(integrity))
        observed["engine"].update(status="error", verdict="not_judged")
    # These are freshly recomputed adapter observations, not stored status flags.
    files = {"engines/" + name + "/execution.json": execution.manifest_bytes,
             "engines/" + name + "/native-policy.json": policy_bytes}
    files.update({"engines/" + name + "/" + key + ".bin": value for key, value in raw.items()})
    return observed, files


def _static_obligation(requirement, observed, repository_id, head):
    evidence = evidence_record(requirement, repository_id)
    engine = observed["engine"]
    evidence.update(subject_sha=head, status="error" if observed["status"] == "error" else "observed",
                    raw_sha256=engine["raw_report_sha256"], messages=list(observed["reason_codes"]))
    effect = "block" if observed["known_block"] else "review" if observed["review_required"] else "clear"
    reasons = list(observed["reason_codes"])
    if effect == "block":
        reasons.append("native_engine_block")
    elif effect == "review":
        reasons.append("native_exemption_review")
    if observed["status"] == "error":
        outcome = "error"
    elif observed["status"] == "not_provided":
        outcome = "missing" if requirement["required"] else "not_applicable"
        reasons.append("ledger_not_provided")
        if not requirement["required"]:
            # A strict Ledgerwash invocation may block solely for absence. The
            # optional requirement cannot acquire a block from that absence;
            # its unchanged native verdict still lives in original stdout.
            effect = "clear"
            reasons = [reason for reason in reasons if reason != "native_engine_block"]
    elif observed["status"] == "unsupported":
        outcome = "unsupported"
        reasons.append("ledger_unsupported")
    elif observed["coverage"]["status"] != "sufficient":
        outcome = "missing"
        reasons.append("required_engine_coverage_unconfirmed")
    elif observed["verdict"] == "block":
        outcome = "failed"
    else:
        outcome = "satisfied"
    return evidence, obligation_record(requirement, evidence, outcome, reasons, effect)


def _ci(requirement, capture, observations):
    evidence = evidence_record(requirement, observations["subject"]["repository_id"])
    workflow, job, outcome, reasons = selected_job(observations, requirement, capture)
    if workflow is not None:
        evidence.update(workflow_id=workflow["workflow_id"], run_id=workflow["selection"].get("run_id"),
                        attempt=workflow["selection"].get("attempt"))
    if job is not None:
        check = job.get("check") or {}
        evidence.update(subject_sha=check.get("head_sha"), job_id=job["id"], job_name=job["name"], app_id=check.get("app_id"),
                        conclusion=job["conclusion"], status="observed")
        records = [row for row in capture.manifest["records"] if row["id"] in check.get("raw_refs", [])]
        if len(records) == 1 and records[0]["body"] is not None:
            evidence["raw_sha256"] = records[0]["body"]["sha256"]
    effect = "clear"
    if outcome is None:
        evidence["binding_level"] = "platform_bound"
        if job["native_state"] != "observed":
            outcome, reasons = "untrusted", ["ci_native_state_unconfirmed"]
        elif job["status"] != "completed":
            outcome, reasons = "pending", ["required_job_pending"]
            evidence.update(status="pending", conclusion="pending")
        elif job["conclusion"] == "success":
            outcome = "satisfied"
        elif job["conclusion"] == "failure":
            outcome, reasons, effect = "failed", ["required_job_failed"], "block"
        else:
            outcome, reasons = "missing", ["required_job_" + (job["conclusion"] or "unconfirmed")]
    evidence["messages"] = reasons
    return evidence, obligation_record(requirement, evidence, outcome, reasons, effect)


def _recheck(capture, observations):
    """Do not manufacture missing final fields to represent an incomplete check."""
    if observations["captured_freshness"] == "unknown":
        return None
    try:
        record, pr = capture_payload(capture, "pull_request", phase="final")
        head, base = pr["head"]["sha"], pr["base"]["sha"]
        if not sha(head) or not sha(base) or pr["number"] != observations["subject"]["pr_number"]:
            return None
        runs = []
        for workflow in observations["workflows"]:
            selection = workflow["recheck"]["selection"]
            if selection["status"] == "selected" and type(selection.get("attempt")) is int:
                runs.append({"workflow_id": workflow["workflow_id"], "run_id": selection["run_id"], "attempt": selection["attempt"]})
            elif selection["status"] != "none":
                return None
        return {"observed_at": max(row["observed_at"] for row in capture.manifest["records"] if row["phase"] == "final"),
                "head_sha": head, "base_tip_sha": base, "selected_runs": runs}
    except (ValueError, TypeError, KeyError):
        return None


def compose_report(*, profile_bytes, capture, snapshot, engine_executions, test_inputs,
                   report_id, mode="offline_replay"):
    """Recompute every obligation from original data; caller cannot supply states.

    The live orchestrator must perform the capture's final recheck *after* all
    source, engine and artifact work. Offline mode always yields unknown freshness.
    Invalid controller identity/inventory raises; observed native errors remain
    in a complete report with their independent policy block/review effects.
    """
    require(mode in {"live", "offline_replay"}, "invalid_inspection_mode")
    profile, capture, observations = validated_context(profile_bytes, capture, snapshot)
    require(isinstance(engine_executions, Mapping) and isinstance(test_inputs, Mapping), "invalid_composition_inputs")
    policy = profile["policy"]
    test_ids = {row["id"] for row in policy["requirements"] if row["kind"] == "test_report"}
    needed_engines = {row["selector"]["engine"] for row in policy["requirements"] if row["kind"] in {"scanner", "ledger"}}
    require(set(engine_executions) <= needed_engines and set(test_inputs) <= test_ids, "undeclared_composition_input")
    raw = {"inspection-profile.json": profile_bytes, "github/capture.json": capture.manifest_bytes,
           "github/profile.json": capture.profile_bytes, "source/inventory.json": snapshot.inventory_bytes}
    raw.update({"github/" + path: data for path, data in capture.bodies.items()})
    engine_observations, test_diagnostics, engines, evidence, obligations = {}, {}, [], [], []
    initial = observations["subject"]
    subject = {key: initial[key] for key in ("repository_id", "head_repository_id", "pr_number", "head_sha", "base_tip_sha")}
    subject.update(repository_full_name=initial["full_name"], merge_base_sha=snapshot.merge_base_sha,
                   merge_commit_sha=None, merge_parents=[])
    _, pr = capture_payload(capture, "pull_request")
    if sha(pr.get("merge_commit_sha")):
        subject["merge_commit_sha"] = pr["merge_commit_sha"]
    for name in sorted(engine_executions):
        observed, files = _engine(engine_executions[name], name, profile, snapshot, observations["observed_at"])
        engine_observations[name] = observed
        raw.update(files)
    engine_evidence_ids = {}
    for requirement in policy["requirements"]:
        if requirement["kind"] == "test_report":
            values = test_inputs.get(requirement["id"], TestEvidenceInputs())
            require(type(values) is TestEvidenceInputs, "invalid_test_evidence_inputs")
            result = bind_test_evidence(profile_bytes=profile_bytes, capture=capture, snapshot=snapshot,
                requirement_id=requirement["id"], **{field.name: getattr(values, field.name) for field in fields(values)})
            item, obligation = result.evidence, result.obligation
            raw.update(result.raw_files)
            test_diagnostics[requirement["id"]] = result.diagnostics
            if values.event_source is not None:
                try:
                    event_sha, parents = event_source_identity(values.event_source, snapshot, pr)
                    if parents:
                        subject.update(merge_commit_sha=event_sha, merge_parents=parents)
                except (ValueError, TypeError, KeyError):
                    pass  # The binder already retained the specific error.
        elif requirement["kind"] in {"scanner", "ledger"}:
            name = requirement["selector"]["engine"]
            if name not in engine_observations:
                item = evidence_record(requirement, policy["repository_id"])
                obligation = obligation_record(requirement, item, "missing", ["engine_execution_missing"])
            else:
                item, obligation = _static_obligation(requirement, engine_observations[name], policy["repository_id"], snapshot.head_sha)
                # One engine may serve multiple requirements with identical
                # static selectors; all link to the same actual raw evidence.
                if name in engine_evidence_ids:
                    item["id"] = engine_evidence_ids[name]
                    obligation["evidence_ids"] = [item["id"]]
                else:
                    engine_evidence_ids[name] = item["id"]
                    engine = dict(engine_observations[name]["engine"], evidence_id=item["id"])
                    engines.append(engine)
        elif requirement["kind"] == "ci_job":
            item, obligation = _ci(requirement, capture, observations)
            if item["subject_sha"] == snapshot.head_sha and "pr_head" not in policy["allowed_source_modes"]:
                obligation.update(outcome="unsupported", reason_codes=["ci_source_mode_disallowed"])
        else:
            item = evidence_record(requirement, policy["repository_id"])
            item.update(subject_sha=snapshot.head_sha, status="observed", raw_sha256=_sha(snapshot.inventory_bytes))
            permitted = "pr_head" in policy["allowed_source_modes"]
            obligation = obligation_record(requirement, item, "satisfied" if permitted else "unsupported",
                                           [] if permitted else ["source_mode_disallowed"])
        if not any(row["id"] == item["id"] for row in evidence):
            evidence.append(item)
        obligations.append(obligation)
    # allowed_source_modes constrains confirmed CI checkout, not static scans.
    # A merge-only policy needs actual qualified merge-test source proof; the
    # platform check's head SHA alone cannot stand in for that checkout. Apply
    # this after all requirements so policy ordering cannot affect the result.
    bound_tests = [row for row in evidence if row["kind"] == "test_report" and row["binding_level"] == "workflow_bound"]
    for requirement, obligation in zip(policy["requirements"], obligations):
        if requirement["kind"] not in {"source", "ci_job"}:
            continue
        item = next(row for row in evidence if row["id"] in obligation["evidence_ids"])
        proofs = sorted(bound_tests, key=lambda row: row["id"])
        if requirement["kind"] == "ci_job":
            proofs = [row for row in proofs if all(row[key] == item[key]
                for key in ("workflow_id", "run_id", "attempt", "job_id", "app_id", "job_name"))]
        proofs = [row for row in proofs if (
            row["subject_sha"] == snapshot.head_sha and "pr_head" in policy["allowed_source_modes"] or
            row["subject_sha"] == subject["merge_commit_sha"] and "github_merge" in policy["allowed_source_modes"]
            and subject["merge_parents"] == [snapshot.base_tip_sha, snapshot.head_sha])]
        if not proofs:
            continue
        proof = proofs[0]
        if requirement["kind"] == "source":
            item.update(subject_sha=proof["subject_sha"], raw_sha256=proof["raw_sha256"], status="observed",
                        messages=["Source established by " + proof["id"]])
            obligation.update(outcome="satisfied", reason_codes=[])
        else:
            _, derived = _ci(requirement, capture, observations)
            item.update(subject_sha=proof["subject_sha"], messages=item["messages"] + ["Test checkout established by " + proof["id"]])
            obligation.update(derived)
    selected = [{"workflow_id": row["workflow_id"], "run_id": row["selection"]["run_id"], "attempt": row["selection"]["attempt"]}
                for row in observations["workflows"] if row["selection"].get("status") == "selected"
                and type(row["selection"].get("attempt")) is int]
    recheck = _recheck(capture, observations)
    limitations = ["Human review evidence only; no correctness or automatic-acceptance claim.",
        "The verifier does not execute reviewed project tests or install project dependencies.",
        "Local digest inventories detect byte changes; they are not publisher signatures.",
        "known_checkwash_419_escape", "Pinwash covers the fixed diff, not a complete editing session."]
    if observations["captured_freshness"] == "stale" and recheck is None:
        limitations.append("observed_drift_with_incomplete_recheck")
    report = {"schema_version": 1, "report_id": report_id, "observed_at": observations["observed_at"], "mode": mode,
        "subject": subject, "selected_runs": selected, "recheck": recheck,
        "policy": {"policy_id": policy["policy_id"], "canonical_sha256": canonical_digest(policy)},
        "collector": {"name": "delivery-review", **capture.manifest["collector"]}, "engines": engines,
        "evidence": evidence, "obligations": obligations, "action_items": [], "limitations": limitations,
        "raw_inventory": [{"path": path, "sha256": _sha(data), "size_bytes": len(data)} for path, data in sorted(raw.items())]}
    for obligation in obligations:
        if obligation["outcome"] not in {"satisfied", "not_applicable"} or obligation["effect"] != "clear":
            report["action_items"].append({"id": derived_id("a-", obligation["id"]), "obligation_ids": [obligation["id"]],
                "message": "Review " + obligation["id"] + ": " + ", ".join(obligation["reason_codes"])})
    report["state"] = derive_state(report)
    report["exit_code"] = exit_for(report["state"])
    report["ready_for_review"] = report["exit_code"] == 0
    validate_report(report, policy)
    return CompositionResult(report, MappingProxyType(raw), MappingProxyType(engine_observations), MappingProxyType(test_diagnostics))
