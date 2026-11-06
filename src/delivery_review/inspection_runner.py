"""Complete live inspection and offline reconstruction from original evidence.

Only the separate approved producer runs tests. This process fetches static Git
objects, runs pinned static readers, parses artifacts and verifies signatures.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
import hashlib
import os
from pathlib import Path
import re
import tempfile
from types import MappingProxyType
import zlib

from .adapters.github_attestation import AttestationExecution, GH_PINS, TRUSTED_ROOT_PINS, verify_github_attestation
from .composition import compose_report
from .contracts import MAX_JSON_BYTES, canonical_bytes, loads_strict, require, validate_report
from .engine_runner import EngineExecution, EngineRunLimits, run_engine
from .evidence.capture import parse_capture
from .evidence.inspection import InspectionWriter, MAX_FILE, MAX_FILES, MAX_TOTAL, verify_inspection
from .evidence.store import _check_paths, _read, _write
from .inspection_profile import load_inspection_profile
from .producer_registry import qualified_producer
from .providers.github_collect import collect_observations, utc_now
from .providers.github_observations import map_capture
from .providers.github_routes import API_VERSION, parse_pr_url, sha
from .providers.github_source import acquire_source
from .reporting import render_report_html, render_report_markdown
from .source_snapshot import export_snapshot, restore_snapshot
from .test_evidence import (EventSourceFact, RawApiFact, TestEvidenceInputs, bind_test_evidence,
                           capture_payload, selected_job)


@dataclass(frozen=True)
class InspectionTools:
    git_executable: Path
    python_executable: Path
    engine_wheels: dict[str, Path]
    gh_executable: Path
    trusted_root_bytes: bytes


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _test_key(identity):
    return _sha(identity.encode("utf-8"))


def _keep(writer, raw, name, data):
    require(type(data) is bytes and len(data) <= MAX_FILE, "inspection_byte_limit")
    _check_paths(["raw/" + name])
    require(name not in raw or raw[name] == data, "inspection_raw_collision")
    if name not in raw:
        reserved_bytes, reserved_files = writer.reserved_bytes, writer.reserved_files
        if name.startswith("github/"):
            reserved_bytes = max(0, reserved_bytes - len(data))
            reserved_files = max(0, reserved_files - 1)
        require(sum(map(len, raw.values())) + len(data) + reserved_bytes <= MAX_TOTAL
            and len(raw) + 1 + reserved_files <= MAX_FILES - 3, "inspection_total_limit")
        writer.reserved_bytes, writer.reserved_files = reserved_bytes, reserved_files
    path = writer.root / "raw" / name
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists():
        require(_read(path, MAX_FILE) == data, "inspection_capture_changed")
    else:
        _write(path, data)
    raw[name] = data


def _failed_process_evidence(writer, raw, directory, prefix, kind):
    """Retain fixed controller diagnostics even when no success object exists."""
    directory = Path(directory)
    marker = directory / "acquisition-error.json"
    if kind == "source" and marker.is_file():
        data = _read(marker, MAX_JSON_BYTES)
        document = loads_strict(data)
        _keep(writer, raw, prefix + "/acquisition-error.json", data)
        for step in document.get("processes", []):
            for row in step.get("streams", {}).values():
                if "path" in row:
                    _check_paths(["raw/" + row["path"]])
                    _keep(writer, raw, prefix + "/acquisition/" + row["path"], _read(directory / row["path"], MAX_FILE, row))
    elif kind == "engine":
        for phase in ("runtime", "git", "venv"):
            for stream in ("stdout", "stderr", "coverage"):
                name = "setup/" + phase + "." + stream
                path = directory / name
                if path.is_file():
                    _keep(writer, raw, prefix + "/" + name, _read(path, MAX_FILE))


def _source_evidence(writer, raw, acquisition, prefix):
    _keep(writer, raw, prefix + "/acquisition.json", acquisition.evidence_bytes)
    document = loads_strict(acquisition.evidence_bytes)
    for step in document["processes"]:
        for row in step["streams"].values():
            if "path" in row:
                _keep(writer, raw, prefix + "/acquisition/" + row["path"],
                    _read(acquisition.directory / row["path"], MAX_FILE, row))
    for row in document["quarantine_files"]:
        _keep(writer, raw, prefix + "/acquisition/" + row["path"],
              _read(acquisition.directory / row["path"], MAX_FILE, row))


def _saved_fact(raw, prefix):
    if prefix + "/request.json" not in raw:
        return None
    request = loads_strict(raw[prefix + "/request.json"])
    require(type(request) is dict and set(request) == {"path", "status_code", "observed_at", "complete", "response_headers", "error"},
            "invalid_saved_api_fact")
    return RawApiFact(request["path"], request["status_code"], raw.get(prefix + "/body.bin", b""),
                      request["observed_at"], request["complete"], request["response_headers"])


def _attestation_from_saved(raw, prefix):
    if prefix + "/verification.json" not in raw:
        return None
    manifest_bytes = raw[prefix + "/verification.json"]
    manifest = loads_strict(manifest_bytes)
    require(type(manifest.get("files")) is dict and len(manifest["files"]) <= 20, "invalid_saved_attestation")
    files = {}
    for name in manifest["files"]:
        require(type(name) is str and re.fullmatch(r"[a-z_]{1,40}", name), "invalid_saved_attestation_stream")
        files[name] = raw[prefix + "/" + name + ".bin"]
    # The saved native process flags are retention only. Every replay executes
    # official cryptographic verification again before binding any claims.
    return AttestationExecution(Path.cwd(), manifest_bytes, MappingProxyType(files), b"{}")


def _test_inputs(profile_bytes, capture, snapshot, raw, tools, runtime, *, writer=None, replay=False):
    from .evidence.artifacts import parse_artifact_capture, map_artifact_capture
    profile = load_inspection_profile(profile_bytes)
    inputs, audit = {}, {}
    event = EventSourceFact(raw["event-source/commit.bin"]) if "event-source/commit.bin" in raw else None
    gh_digest = _sha(_read(tools.gh_executable, 128 * 1024 * 1024))
    require(gh_digest in GH_PINS.values(), "unapproved_attestation_executable")
    trust = raw["trusted-root.jsonl"]
    require(_sha(trust) in TRUSTED_ROOT_PINS, "unapproved_attestation_root")
    for requirement in profile["policy"]["requirements"]:
        if requirement["kind"] != "test_report":
            continue
        key, identity = _test_key(requirement["id"]), requirement["id"]
        prefix = "artifacts/" + key
        artifact, metadata, bundles, complete = None, None, (), False
        if prefix + "/capture.json" in raw:
            artifact_capture = parse_artifact_capture(raw[prefix + "/capture.json"],
                {name[len(prefix) + 1:]: data for name, data in raw.items() if name.startswith(prefix + "/raw/")})
            observed = map_artifact_capture(artifact_capture)
            artifact, bundles, complete = observed["archive_bytes"], observed["bundles"], observed["attestations_complete"]
            meta = observed["metadata"]
            if meta is not None:
                metadata = RawApiFact(meta["request_path"], meta["status_code"], meta["body_bytes"],
                                      meta["observed_at"], meta["complete"], meta["response_headers"])
        producer = _saved_fact(raw, "producer-repositories/" + key)
        qualification = qualified_producer(requirement["selector"])
        if replay:
            saved_receipt = raw.get("tests/" + key + "/qualification.json")
            # Replaying old evidence cannot add a qualification that only became
            # available later. Current release approval is still required for
            # the exact original receipt; revocation stays an explicit gap.
            if qualification is None or saved_receipt != qualification.qualification_receipt_bytes:
                qualification = None
        values = TestEvidenceInputs(archive_bytes=artifact, artifact_metadata=metadata, producer_repository=producer,
            event_source=event, qualification=qualification)
        preflight = bind_test_evidence(profile_bytes=profile_bytes, capture=capture, snapshot=snapshot,
            requirement_id=identity, **{field.name: getattr(values, field.name) for field in fields(values)})
        expected = preflight.diagnostics["attestation_expected"]
        if expected is not None and complete and len(bundles) == 1:
            try:
                # Bound fresh replay audit before launching a verifier. It keeps
                # one archive, bundle, root, bounded streams and small metadata.
                if replay:
                    remaining = MAX_TOTAL - sum(map(len, raw.values())) - sum(map(len, audit.values()))
                    require(len(artifact) + len(bundles[0]) + len(trust) + 6 * MAX_JSON_BYTES <= remaining,
                            "inspection_audit_limit")
                result = verify_github_attestation(artifact_bytes=artifact, bundle_bytes=bundles[0], trusted_root_bytes=trust,
                    gh_executable=tools.gh_executable, gh_sha256=gh_digest, expected=expected,
                    destination=runtime / ("verification-" + key))
            except (ValueError, OSError):
                # One unavailable verifier must not erase other requirements'
                # independently captured test or known-failure evidence.
                audit["audit/" + key + "/controller-error.json"] = canonical_bytes({"status": "error",
                    "reason": "attestation_controller_unavailable", "requirement_id": identity}) + b"\n"
                inputs[identity] = values
                continue
            if replay:
                prefix = "tests/" + key + "/attestation"
                retained = _attestation_from_saved(raw, prefix)
                # Fresh verification can confirm or reject original proof. It
                # cannot insert proof missing from the original observation.
                if retained is not None:
                    values = replace(values, attestation=result, attestation_retention=retained)
                audit["audit/" + key + "/verification.json"] = result.manifest_bytes
                audit.update({"audit/" + key + "/" + name + ".bin": data for name, data in result.raw_files.items()})
            else:
                values = replace(values, attestation=result)
        inputs[identity] = values
    return inputs, audit


def _publish(writer, composition, raw, *, collector, audit=None, derived_from=None):
    # Composition outputs are derived again from original streams. A collision
    # can only be the same bytes, never a silent rewrite of original evidence.
    for name, data in composition.raw_files.items():
        _keep(writer, raw, name, data)
    report = dict(composition.report)
    # Contract-v1 reports have 1,000 direct references. Large Git object sets
    # remain fully indexed in source/inventory.json and in the outer envelope;
    # do not flatten thousands of objects into that frozen report field.
    references = set(composition.raw_files) | {"execution-plan.json", "trusted-root.jsonl"}
    references |= {name for name in raw if name in {"source/acquisition.json", "event-source/acquisition.json"}
                   or name.startswith("artifacts/") and name.endswith("/capture.json")}
    report["raw_inventory"] = [{"path": name, "size_bytes": len(raw[name]), "sha256": _sha(raw[name])}
                               for name in sorted(references)]
    policy = load_inspection_profile(raw["inspection-profile.json"])["policy"]
    validate_report(report, policy)
    files = {"raw/" + name: data for name, data in raw.items()}
    files.update({"raw/" + name: data for name, data in (audit or {}).items()})
    files.update({"report.json": canonical_bytes(report) + b"\n",
        "report.md": render_report_markdown(report, policy).encode("utf-8"),
        "report.html": render_report_html(report, policy).encode("utf-8")})
    writer.seal(files=files, collector=collector, observed_at=report["observed_at"], report_id=report["report_id"], derived_from=derived_from)
    return report


def inspect_pr(pr_url, profile_bytes, destination, *, tools: InspectionTools, collector, token=None,
               platform_transport=None, artifact_transport=None, attestation_transport=None, clock=utc_now):
    """Capture all initial evidence, run static readers, then recheck freshness."""
    from .evidence.artifacts import ArtifactLimits, collect_artifacts
    from .providers.github_http import GitHubTransport
    from .providers.github_artifact_http import GitHubArtifactTransport
    from .providers.github_attestation_http import GitHubAttestationTransport
    profile = load_inspection_profile(profile_bytes)
    parse_pr_url(pr_url, profile["github"])
    platform_transport = platform_transport or GitHubTransport(token=token)
    artifact_transport = artifact_transport or GitHubArtifactTransport(token=token)
    attestation_transport = attestation_transport or GitHubAttestationTransport(token=token)
    writer = InspectionWriter(destination)
    # The observation collector writes its own original bodies. Reserve its
    # complete approved remaining allowance before other stages can consume it.
    writer.reserved_bytes = profile["github"]["limits"]["max_total_bytes"] + 2 * MAX_JSON_BYTES
    writer.reserved_files = profile["github"]["limits"]["max_requests"] + 2
    raw, executions, stages = {}, {}, {}
    _keep(writer, raw, "inspection-profile.json", profile_bytes)
    _keep(writer, raw, "trusted-root.jsonl", tools.trusted_root_bytes)
    with tempfile.TemporaryDirectory(prefix="delivery-review-inspect-") as temporary:
        runtime = Path(temporary)
        def stage(initial):
            _keep(writer, raw, "github/profile.json", initial.profile_bytes)
            for path, data in initial.bodies.items():
                _keep(writer, raw, "github/" + path, data)
            observations = map_capture(initial)
            subject = observations["subject"]
            require(subject is not None, "inspection_subject_unconfirmed")
            try:
                acquisition = acquire_source(runtime / "source", repository=profile["github"]["repository"],
                    head_sha=subject["head_sha"], base_tip_sha=subject["base_tip_sha"], git_executable=tools.git_executable, token=token)
            except (ValueError, OSError):
                _failed_process_evidence(writer, raw, runtime / "source", "source", "source")
                raise
            snapshot = acquisition.snapshot
            stages["snapshot"] = snapshot
            _source_evidence(writer, raw, acquisition, "source")
            _keep(writer, raw, "source/inventory.json", snapshot.inventory_bytes)
            for oid, data in export_snapshot(snapshot).items():
                _keep(writer, raw, "source/objects/" + oid + ".zlib", data)
            _, pr = capture_payload(initial, "pull_request")
            merge = pr.get("merge_commit_sha")
            if sha(merge):
                try:
                    event = acquire_source(runtime / "event-source", repository=profile["github"]["repository"],
                        head_sha=merge, base_tip_sha=subject["base_tip_sha"], git_executable=tools.git_executable, token=token)
                    _source_evidence(writer, raw, event, "event-source")
                    content = export_snapshot(event.snapshot)[merge]
                    _keep(writer, raw, "event-source/commit.bin", zlib.decompress(content))
                except (ValueError, OSError):
                    _failed_process_evidence(writer, raw, runtime / "event-source", "event-source", "source")
                    stages["errors"].append("event_source_unavailable")
            names = {row["selector"]["engine"] for row in profile["policy"]["requirements"] if row["kind"] in {"scanner", "ledger"}}
            for name in sorted(names):
                try:
                    execution = run_engine(snapshot, engine_name=name, wheel_path=tools.engine_wheels[name],
                        python_executable=tools.python_executable, git_executable=tools.git_executable,
                        destination=runtime / ("engine-" + name),
                        native_policy=dict(profile["engine_options"][name], evaluation_date=initial.manifest["observed_at"][:10]),
                        limits=EngineRunLimits(timeout_seconds=profile["policy"]["limits"]["scanner_seconds"]))
                    executions[name] = execution
                    _keep(writer, raw, "engines/" + name + "/execution.json", execution.manifest_bytes)
                    for key, data in execution.raw_files.items():
                        _keep(writer, raw, "engines/" + name + "/" + key + ".bin", data)
                    for setup in execution.manifest["setup"]:
                        for row in setup["streams"].values():
                            _keep(writer, raw, "engines/" + name + "/" + row["path"], _read(execution.directory / row["path"], MAX_FILE, row))
                    row = execution.manifest["installation"]
                    _keep(writer, raw, "engines/" + name + "/installation.json", _read(execution.directory / row["path"], MAX_FILE, row))
                except (ValueError, OSError):
                    _failed_process_evidence(writer, raw, runtime / ("engine-" + name), "engines/" + name, "engine")
                    stages["errors"].append("engine_" + name + "_unavailable")
            for requirement in profile["policy"]["requirements"]:
                if requirement["kind"] != "test_report":
                    continue
                key = _test_key(requirement["id"])
                producer = profile["producers"][requirement["id"]]
                path = "/repos/" + producer["repository_full_name"]
                response = platform_transport.get(path, api_version=API_VERSION, max_bytes=MAX_JSON_BYTES, timeout_seconds=30)
                prefix = "producer-repositories/" + key
                _keep(writer, raw, prefix + "/request.json", canonical_bytes({"path": path, "status_code": response["status_code"],
                    "observed_at": clock(), "response_headers": response["response_headers"], "complete": response["complete"],
                    "error": response["error"]}) + b"\n")
                if response["body"] is not None:
                    _keep(writer, raw, prefix + "/body.bin", response["body"])
                workflow, job, outcome, _ = selected_job(observations, requirement, initial)
                if outcome is not None:
                    continue
                prefix = "artifacts/" + key
                remaining = MAX_TOTAL - sum(map(len, raw.values())) - writer.reserved_bytes - MAX_JSON_BYTES
                remaining_files = MAX_FILES - len(raw) - writer.reserved_files - 4
                if remaining < 1 or remaining_files < 1:
                    stages["errors"].append("artifact_capture_limit")
                    continue
                (writer.root / "raw/artifacts").mkdir(mode=0o700, exist_ok=True)
                acquired = collect_artifacts(repository=profile["github"]["repository"], run_id=workflow["selection"]["run_id"],
                    run_attempt=workflow["selection"]["attempt"], check_run_id=job["check"]["id"],
                    destination=writer.root / "raw" / prefix, transport=artifact_transport, attestation_transport=attestation_transport,
                    collector=collector, clock=clock, limits=ArtifactLimits(
                        max_archive_bytes=profile["policy"]["limits"]["artifact_bytes"],
                        max_total_bytes=min(100 * 1024 * 1024, remaining), max_requests=min(200, remaining_files)))
                _keep(writer, raw, prefix + "/capture.json", acquired.manifest_bytes)
                for name, data in acquired.raw_files.items():
                    _keep(writer, raw, prefix + "/" + name, data)
            stages["test_inputs"], stages["audit"] = _test_inputs(profile_bytes, initial, snapshot, raw, tools, runtime)
        stages["errors"] = []
        capture = collect_observations(pr_url, canonical_bytes(profile["github"]) + b"\n", writer.root / "raw/github",
            transport=platform_transport, collector=collector, clock=clock, before_recheck=stage)
        _keep(writer, raw, "github/capture.json", capture.manifest_bytes)
        _keep(writer, raw, "github/profile.json", capture.profile_bytes)
        for path, data in capture.bodies.items():
            _keep(writer, raw, "github/" + path, data)
        writer.reserved_bytes, writer.reserved_files = 0, 0
        if "snapshot" not in stages:
            _write(writer.root / "inspection-error.json", canonical_bytes({"status": "error", "reason": "source_unavailable",
                "raw_capture_retained": True, "ready_for_review": False}) + b"\n")
            raise ValueError("inspection_source_unavailable")
        _keep(writer, raw, "execution-plan.json", canonical_bytes({"engine_names": sorted(executions),
            "stage_errors": sorted(stages["errors"]), "pr_url": pr_url}) + b"\n")
        report_id = "inspection-" + _sha(capture.manifest_bytes)[:40]
        composition = compose_report(profile_bytes=profile_bytes, capture=capture, snapshot=stages["snapshot"],
            engine_executions=executions, test_inputs=stages.get("test_inputs", {}), report_id=report_id, mode="live")
        return _publish(writer, composition, raw, collector=collector, audit=stages.get("audit"))


def replay_inspection(source, destination, *, tools: InspectionTools):
    """No GitHub calls or static-engine execution; reparse bytes and reverify crypto."""
    source, destination = Path(os.path.abspath(source)), Path(os.path.abspath(destination))
    require(not destination.is_relative_to(source), "output_must_be_outside_inspection")
    saved = verify_inspection(source)
    writer = InspectionWriter(destination)
    raw = {name[4:]: data for name, data in saved.files.items() if name.startswith("raw/") and not name.startswith("raw/audit/")}
    profile_bytes = raw["inspection-profile.json"]
    profile = load_inspection_profile(profile_bytes)
    capture = parse_capture(raw["github/capture.json"], raw["github/profile.json"],
        {name[len("github/"):]: data for name, data in raw.items() if name.startswith("github/raw/")})
    plan = loads_strict(raw["execution-plan.json"])
    require(type(plan) is dict and set(plan) == {"engine_names", "stage_errors", "pr_url"}
        and type(plan["engine_names"]) is list and len(plan["engine_names"]) == len(set(plan["engine_names"]))
        and set(plan["engine_names"]) <= {"checkwash", "pinwash", "ledgerwash"}, "invalid_inspection_execution_plan")
    parse_pr_url(plan["pr_url"], profile["github"])
    with tempfile.TemporaryDirectory(prefix="delivery-review-replay-") as temporary:
        runtime = Path(temporary)
        objects = {name[len("source/objects/"):-len(".zlib")]: data for name, data in raw.items()
                   if name.startswith("source/objects/") and name.endswith(".zlib")}
        snapshot = restore_snapshot(runtime / "source", inventory_bytes=raw["source/inventory.json"],
                                    loose_objects=objects, git_executable=tools.git_executable)
        executions = {}
        for name in plan["engine_names"]:
            prefix = "engines/" + name
            manifest_bytes = raw[prefix + "/execution.json"]
            manifest = loads_strict(manifest_bytes)
            require(type(manifest["streams"]) is dict and set(manifest["streams"]) <= {"stdout", "stderr", "coverage"},
                    "invalid_saved_engine_streams")
            streams = {key: raw[prefix + "/" + key + ".bin"] for key in manifest["streams"]}
            executions[name] = EngineExecution(runtime, manifest_bytes, MappingProxyType(streams), b"{}")
        inputs, audit = _test_inputs(profile_bytes, capture, snapshot, raw, tools, runtime, replay=True)
        composition = compose_report(profile_bytes=profile_bytes, capture=capture, snapshot=snapshot,
            engine_executions=executions, test_inputs=inputs, report_id=saved.manifest["report_id"], mode="offline_replay")
        return _publish(writer, composition, raw, collector=saved.manifest["collector"], audit=audit,
                        derived_from=_sha(saved.manifest_bytes))
