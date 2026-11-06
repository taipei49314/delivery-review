"""Authored cross-document controls, NOT signatures or hosted qualification.

The fake official process envelopes below test semantic binding only. Live
cryptographic verification and producer qualification require separate receipts.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters.artifact_zip import read_artifact_zip
from delivery_review.adapters import github_attestation as attest
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.source_snapshot import materialize_snapshot
from delivery_review.test_evidence import (EventSourceFact, ProducerQualification, RawApiFact,
    TestEvidenceInputs, bind_test_evidence, event_source_identity)
from observation_fixtures import BASE, HEAD, NOW, CaptureFixture
from test_github_attestation import authored_bundle, control as attestation_control
from test_source_snapshot import fixture


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def fact(path, payload):
    return RawApiFact(path, 200, canonical_bytes(payload), NOW, True, {})


class AuthoredInspection:
    """Source bytes are real local Git objects; all GitHub/crypto data authored."""
    def __init__(self, root):
        self.root = root
        self.objects, _, base, head = fixture()
        self.snapshot = materialize_snapshot(root / "source", repository="fixture-owner/fixture-repo",
            head_sha=head, base_tip_sha=base, objects=self.objects.objects, git_executable=shutil.which("git"))
        self.capture_fixture = CaptureFixture(mapper_version=2)
        for row in self.capture_fixture.records:
            data = self.capture_fixture.bodies[row["body"]["path"]].replace(HEAD.encode(), head.encode()).replace(BASE.encode(), base.encode())
            self.capture_fixture.set_payload(row, data)
            row["request"]["path"] = row["request"]["path"].replace(HEAD, head)
        attempt = self.capture_fixture.find("attempt")
        data = self.capture_fixture.payload(attempt)
        data["referenced_workflows"] = [{"path": "fixture-owner/producer/.github/workflows/pytest.yml@" + "d" * 40, "sha": "d" * 40}]
        self.capture_fixture.set_payload(attempt, data)
        self.profile = loads_strict((ROOT / "examples/inspection/profile.json").read_bytes())
        self.profile["github"] = deepcopy(self.capture_fixture.profile)
        self.profile_bytes = seal_inspection_profile(self.profile)
        self.profile = loads_strict(self.profile_bytes)
        self.receipt = loads_strict((ROOT / "examples/receipt.json").read_bytes())
        self.receipt["source"].update(head_sha=head, base_tip_sha=base, checked_out_sha=head)
        self.receipt["recorded_at"] = NOW
        self.receipt["execution"].update(command=["/work/venv/bin/python", "-I", "-m", "pytest", "-c", "/trusted/pytest.ini",
            "--rootdir=/work/source", "--junitxml=/work/output/junit.xml", "--strict-markers", "tests"], collection_exit=None)
        self.junit = self.xml()
        self.qualification_bytes = canonical_bytes({"kind": "delivery-review-producer-qualification", "qualification_version": 1,
            "result": "qualified", "producer": {"repository_id": 9002, "path": ".github/workflows/pytest.yml", "sha": "d" * 40},
            "producer_profile": attest.PRODUCER_PROFILE, "test_path": "tests", "source_modes": ["pr_head"],
            "provenance": "authored synthetic unit control, not hosted qualification"})
        self.qualification = ProducerQualification(9002, ".github/workflows/pytest.yml", "d" * 40,
            "qualified", attest.PRODUCER_PROFILE, digest(self.qualification_bytes), self.qualification_bytes)
        self.trusted_root = b"Authored mocked trust root, never a real verification input."
        self.rebuild()

    def xml(self, *, passed=1, failed=0, errors=0, skipped=0):
        cases = ''.join(f'<testcase classname="fixture" name="test_{kind}_{i}" time="0.000">{child}</testcase>'
            for kind, count, child in (("pass", passed, ""), ("fail", failed, '<failure message="authored">authored</failure>'),
                                      ("error", errors, '<error message="authored">authored</error>'),
                                      ("skip", skipped, '<skipped type="pytest.skip" message="authored">authored</skipped>'))
            for i in range(count))
        return (f'<testsuites name="pytest tests"><testsuite name="delivery-review" tests="{passed+failed+errors+skipped}" '
                f'failures="{failed}" errors="{errors}" skipped="{skipped}" time="0.000" '
                f'timestamp="2026-11-06T00:00:00+00:00" hostname="authored-host">{cases}</testsuite></testsuites>').encode()

    def rebuild(self, *, update_counts=True):
        from delivery_review.adapters.pytest_junit import parse_pytest_junit
        counts = parse_pytest_junit(self.junit)["case_counts"]
        if update_counts:
            self.receipt["execution"]["tests"] = {"passed": counts["passed"], "failed": counts["failures"], "errors": counts["errors"], "skipped": counts["skipped"]}
        self.receipt["execution"]["junit"] = {"path": "junit.xml", "sha256": digest(self.junit), "size_bytes": len(self.junit)}
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("receipt.json", canonical_bytes(self.receipt))
            archive.writestr("junit.xml", self.junit)
        self.artifact = read_artifact_zip(stream.getvalue(), policy_limits=self.profile["policy"]["limits"])
        self.metadata = {"id": 7001, "digest": "sha256:" + self.artifact.archive_sha256,
            "size_in_bytes": len(self.artifact.archive_bytes), "expired": False, "name": "delivery-review-1001-1-4001",
            "workflow_run": {"id": 1001, "head_sha": self.snapshot.head_sha, "repository_id": 9001, "head_repository_id": 9001}}

    def args(self, **changes):
        value = dict(profile_bytes=self.profile_bytes, capture=self.capture_fixture.seal(), snapshot=self.snapshot,
            requirement_id="pytest-linux", artifact=self.artifact,
            artifact_metadata=fact("/repos/fixture-owner/fixture-repo/actions/artifacts/7001", self.metadata),
            producer_repository=fact("/repos/fixture-owner/producer", {"id": 9002, "full_name": "fixture-owner/producer"}),
            qualification=self.qualification)
        value.update(changes)
        return value

    def fake_attestation(self, expected):
        common, predicate, native = attestation_control()
        common = {key: expected[key] for key in attest._COMMON - {"subject_name", "predicate_type"}}
        common.update(subject_name="https://github.com/fixture-owner/fixture-repo/actions/runs/1001/artifacts/7001", predicate_type=attest.PREDICATE_TYPE)
        predicate = {key: expected[key] for key in ("repository_id", "run_id", "run_attempt", "job_id", "check_run_id", "artifact_id", "artifact_sha256", "receipt_sha256", "junit_sha256")}
        predicate.update(predicate_version=1, producer_profile=attest.PRODUCER_PROFILE)
        result = native[0]["verificationResult"]
        cert = result["signature"]["certificate"]
        cert.update(subjectAlternativeName=attest._identity(common), buildSignerURI=attest._identity(common), buildSignerDigest=expected["producer_sha"],
            sourceRepositoryURI="https://github.com/" + expected["repository_full_name"], sourceRepositoryDigest=expected["certificate_source_sha"],
            sourceRepositoryIdentifier=str(expected["repository_id"]),
            runInvocationURI="https://github.com/" + expected["repository_full_name"] + "/actions/runs/1001/attempts/1")
        result["verifiedIdentity"]["subjectAlternativeName"]["subjectAlternativeName"] = attest._identity(common)
        result["statement"].update(subject=[{"name": common["subject_name"], "digest": {"sha256": expected["artifact_sha256"]}}], predicate=predicate)
        argv = ["authored/gh", "attestation", "verify", "artifact", "--bundle", "bundle", "--custom-trusted-root", "root",
            "--repo", common["repository_full_name"], "--hostname", "github.com", "--cert-identity", attest._identity(common),
            "--signer-digest", common["producer_sha"], "--cert-oidc-issuer", attest.ISSUER, "--deny-self-hosted-runners",
            "--predicate-type", attest.PREDICATE_TYPE, "--digest-alg", "sha256", "--format", "json"]
        raw = {"artifact": self.artifact.archive_bytes, "bundle": authored_bundle(result["statement"]), "trusted_root": self.trusted_root,
            "expected": canonical_bytes({"certificate_and_subject": common, "predicate": predicate}),
            "stdout": json.dumps(native).encode(), "stderr": b"", "version_stdout": b"gh version 2.98.0 (authored)\n", "version_stderr": b""}
        manifest = {"kind": "delivery-review-attestation-execution", "execution_version": 1,
            "tool": {"name": "gh", "version": attest.GH_VERSION, "platform": "linux", "architecture": "amd64", "sha256": attest.GH_PINS[("linux", "amd64")]},
            "controller_integrity_errors": [], "version_process": {"exit_code": 0, "termination": "exited"},
            "process": {"argv": argv, "exit_code": 0, "termination": "exited", "duration_ms": 1},
            "files": {key: {"sha256": digest(data), "size_bytes": len(data), "path": key} for key, data in raw.items()}}
        return attest.AttestationExecution(self.root, canonical_bytes(manifest), MappingProxyType(raw), b'{"status":"irrelevant"}')

    def bound(self, **changes):
        args = self.args(**changes)
        first = bind_test_evidence(**args)
        if first.diagnostics["attestation_expected"] is None:
            return first
        args.setdefault("attestation", self.fake_attestation(first.diagnostics["attestation_expected"]))
        with patch.object(attest, "TRUSTED_ROOT_PINS", {digest(self.trusted_root)}):
            return bind_test_evidence(**args)

    def inputs(self):
        args = self.args()
        first = bind_test_evidence(**args)
        args["attestation"] = self.fake_attestation(first.diagnostics["attestation_expected"])
        return TestEvidenceInputs(**{key: value for key, value in args.items() if key not in {"profile_bytes", "capture", "snapshot", "requirement_id"}})


class TestEvidenceBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = AuthoredInspection(Path(self.temp.name))

    def test_authored_chain_binds_only_after_all_independent_inputs(self):
        first = bind_test_evidence(**self.fixture.args())
        self.assertEqual("untrusted", first.obligation["outcome"])
        self.assertEqual("attestation_missing", first.obligation["reason_codes"][0])
        result = self.fixture.bound()
        self.assertEqual("satisfied", result.obligation["outcome"], result.diagnostics)
        self.assertEqual("workflow_bound", result.evidence["binding_level"])
        self.assertEqual(7001, result.evidence["artifact_id"])
        self.assertNotEqual(result.evidence["artifact_sha256"], result.evidence["raw_sha256"])

    def test_no_qualification_cannot_promote_even_matching_authored_claims(self):
        result = self.fixture.bound(qualification=None)
        self.assertEqual("untrusted", result.obligation["outcome"])
        self.assertEqual("reported", result.evidence["binding_level"])
        self.assertIn("producer_not_qualified", result.obligation["reason_codes"])

    def test_qualification_digest_and_mode_are_independent_gates(self):
        for value in (replace(self.fixture.qualification, qualification_receipt_sha256="f" * 64),
                      replace(self.fixture.qualification, source_modes=("github_merge",)),
                      replace(self.fixture.qualification, test_path="tests/subset")):
            result = self.fixture.bound(qualification=value)
            self.assertNotEqual("satisfied", result.obligation["outcome"])

    def test_metadata_id_digest_run_attempt_source_and_locator_cannot_be_receipt_claims(self):
        original = deepcopy(self.fixture.metadata)
        for mutate in (lambda row: row.update(id=7002), lambda row: row.update(digest="sha256:" + "f" * 64),
            lambda row: row["workflow_run"].update(id=999), lambda row: row["workflow_run"].update(head_sha="f" * 40),
            lambda row: row.update(name="delivery-review-1001-2-4001"), lambda row: row.update(expired=True)):
            self.fixture.metadata = deepcopy(original)
            mutate(self.fixture.metadata)
            self.assertNotEqual("satisfied", self.fixture.bound().obligation["outcome"])

    def test_api_route_and_completeness_are_checked(self):
        fact_value = self.fixture.args()["artifact_metadata"]
        for change in ({"request_path": "/unrelated"}, {"status_code": 404}, {"complete": False}):
            result = self.fixture.bound(artifact_metadata=replace(fact_value, **change))
            self.assertNotEqual("satisfied", result.obligation["outcome"])

    def test_receipt_and_junit_counts_and_fixed_test_path_are_not_trusted(self):
        original = deepcopy(self.fixture.receipt)
        for mutate in (lambda row: row["execution"]["command"].__setitem__(-1, "tests/subset"),
            lambda row: row["execution"]["tests"].update(passed=200), lambda row: row["producer"].update(attempt=2),
            lambda row: row["source"].update(checked_out_sha="f" * 40)):
            self.fixture.receipt = deepcopy(original)
            mutate(self.fixture.receipt)
            self.fixture.rebuild(update_counts=False)
            self.assertNotEqual("satisfied", self.fixture.bound().obligation["outcome"])

    def test_failure_is_complete_observation_and_zero_allskip_remain_missing(self):
        for values, native, outcome in (({"passed": 0, "failed": 1}, 1, "failed"),
            ({"passed": 0}, 5, "missing"), ({"passed": 0, "skipped": 1}, 0, "missing"),
            ({"passed": 1, "skipped": 1}, 0, "missing")):
            self.fixture.junit = self.fixture.xml(**values)
            self.fixture.receipt["execution"]["test_exit"] = native
            self.fixture.rebuild()
            result = self.fixture.bound()
            self.assertEqual(outcome, result.obligation["outcome"], result.diagnostics)
            self.assertEqual("block" if outcome == "failed" else "clear", result.obligation["effect"])

    def test_partial_skips_follow_bound_policy(self):
        self.fixture.profile["policy"]["requirements"][0]["selector"]["allow_skips"] = True
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        self.fixture.junit = self.fixture.xml(passed=1, skipped=1)
        self.fixture.rebuild()
        self.assertEqual("satisfied", self.fixture.bound().obligation["outcome"])

    def test_missing_or_retargeted_reusable_workflow_is_untrusted(self):
        row = self.fixture.capture_fixture.find("attempt")
        original = self.fixture.capture_fixture.payload(row)
        for references in ([], [{"path": "fixture-owner/producer/.github/workflows/pytest.yml@main", "sha": "d" * 40}],
                           original["referenced_workflows"] * 2):
            self.fixture.capture_fixture.set_payload(row, dict(original, referenced_workflows=references))
            self.assertNotEqual("satisfied", self.fixture.bound().obligation["outcome"])

    def test_saved_success_observation_cannot_override_native_verifier_failure(self):
        args = self.fixture.args()
        expected = bind_test_evidence(**args).diagnostics["attestation_expected"]
        execution = self.fixture.fake_attestation(expected)
        manifest = execution.manifest
        manifest["process"]["exit_code"] = 1
        execution = replace(execution, manifest_bytes=canonical_bytes(manifest), observation_bytes=b'{"status":"verified_claims"}')
        result = self.fixture.bound(attestation=execution)
        self.assertEqual("untrusted", result.obligation["outcome"])
        self.assertIn("attestation_verification_failed", result.obligation["reason_codes"])

    def test_signed_attempt_wrong_or_raw_bytes_changed_cannot_pass(self):
        expected = bind_test_evidence(**self.fixture.args()).diagnostics["attestation_expected"]
        for mode in ("hash", "claim"):
            execution = self.fixture.fake_attestation(expected)
            raw = dict(execution.raw_files)
            native = json.loads(raw["stdout"])
            native[0]["verificationResult"]["statement"]["predicate"]["run_attempt"] = 2
            raw["stdout"] = json.dumps(native).encode()
            manifest = execution.manifest
            if mode == "claim":
                manifest["files"]["stdout"].update(sha256=digest(raw["stdout"]), size_bytes=len(raw["stdout"]))
            execution = replace(execution, raw_files=raw, manifest_bytes=canonical_bytes(manifest))
            self.assertNotEqual("satisfied", self.fixture.bound(attestation=execution).obligation["outcome"])

    def test_event_merge_parents_are_hashed_and_not_equated_with_tested_head(self):
        value = self.fixture.objects
        merge = value.commit(value.files({"README.txt": b"authored merge"}), [self.fixture.snapshot.base_tip_sha, self.fixture.snapshot.head_sha])
        event = EventSourceFact(value.objects[merge])
        for phase in ("initial", "final"):
            row = self.fixture.capture_fixture.find("pull_request", phase)
            document = self.fixture.capture_fixture.payload(row)
            document["merge_commit_sha"] = merge
            self.fixture.capture_fixture.set_payload(row, document)
        result = self.fixture.bound(event_source=event)
        self.assertEqual("satisfied", result.obligation["outcome"], result.diagnostics)
        self.assertEqual(merge, result.diagnostics["attestation_expected"]["certificate_source_sha"])
        self.assertEqual(self.fixture.snapshot.head_sha, result.evidence["subject_sha"])
        bad = value.commit(value.files({"README.txt": b"wrong merge"}), [self.fixture.snapshot.head_sha, self.fixture.snapshot.base_tip_sha])
        result = self.fixture.bound(event_source=EventSourceFact(value.objects[bad]))
        self.assertIn("event_merge_parent_mismatch", result.obligation["reason_codes"])

    def test_mutated_source_and_unapproved_profile_are_rejected_before_composition(self):
        profile = deepcopy(self.fixture.profile)
        profile["policy"]["requirements"][0]["selector"]["allow_skips"] = True
        with self.assertRaises(ContractError):
            bind_test_evidence(**self.fixture.args(profile_bytes=canonical_bytes(profile)))
        (self.fixture.snapshot.root / "README.txt").write_bytes(b"changed")
        with self.assertRaises(ContractError):
            bind_test_evidence(**self.fixture.args())

    def test_partial_job_inventory_cannot_prove_unique_required_match(self):
        row = self.fixture.capture_fixture.find("jobs")
        document = self.fixture.capture_fixture.payload(row)
        document["total_count"] = 2
        self.fixture.capture_fixture.set_payload(row, document)
        result = self.fixture.bound()
        self.assertEqual("untrusted", result.obligation["outcome"])
        self.assertIn("job_pagination_unconfirmed", result.obligation["reason_codes"])

    def test_pending_and_non_execution_keep_native_reasons(self):
        for state, conclusion, expected in (("in_progress", None, "pending"), ("completed", "skipped", "missing"),
            ("completed", "cancelled", "missing"), ("completed", "timed_out", "missing")):
            for role in ("jobs", "check_run"):
                row = self.fixture.capture_fixture.find(role)
                document = self.fixture.capture_fixture.payload(row)
                target = document["jobs"][0] if role == "jobs" else document
                target.update(status=state, conclusion=conclusion)
                self.fixture.capture_fixture.set_payload(row, document)
            result = self.fixture.bound(artifact=None)
            self.assertEqual(expected, result.obligation["outcome"])
            self.assertIn("required_job_" + ("pending" if conclusion is None else conclusion), result.obligation["reason_codes"])

    def test_job_and_check_conclusion_conflict_cannot_be_success(self):
        row = self.fixture.capture_fixture.find("check_run")
        document = self.fixture.capture_fixture.payload(row)
        document["conclusion"] = "failure"
        self.fixture.capture_fixture.set_payload(row, document)
        result = self.fixture.bound()
        self.assertEqual("untrusted", result.obligation["outcome"])
        self.assertIn("platform_execution_state_conflict", result.obligation["reason_codes"])

    def test_preflight_initial_capture_without_final_check_can_derive_expectations(self):
        self.fixture.capture_fixture.records = [row for row in self.fixture.capture_fixture.records if row["phase"] == "initial"]
        self.fixture.capture_fixture.termination = {"status": "stopped", "reason_codes": ["collection_in_progress"]}
        result = bind_test_evidence(**self.fixture.args())
        self.assertIsNotNone(result.diagnostics["attestation_expected"])
        self.assertEqual(["attestation_missing"], result.obligation["reason_codes"])

    def test_original_archive_input_parses_here_and_malformed_archive_is_error(self):
        result = self.fixture.bound(artifact=None, archive_bytes=self.fixture.artifact.archive_bytes)
        self.assertEqual("satisfied", result.obligation["outcome"])
        bad = b"Authored invalid archive, not a missing transport result."
        result = self.fixture.bound(artifact=None, archive_bytes=bad)
        self.assertEqual("error", result.obligation["outcome"])
        self.assertIn(bad, result.raw_files.values())
        with self.assertRaisesRegex(ContractError, "ambiguous_artifact_input"):
            bind_test_evidence(**self.fixture.args(archive_bytes=self.fixture.artifact.archive_bytes))

    def test_replay_retains_original_audit_but_requires_fresh_actual_verification(self):
        expected = bind_test_evidence(**self.fixture.args()).diagnostics["attestation_expected"]
        original = self.fixture.fake_attestation(expected)
        manifest = original.manifest
        manifest["process"]["duration_ms"] = 99
        fresh = replace(original, manifest_bytes=canonical_bytes(manifest))
        first = self.fixture.bound(attestation=original)
        replay = self.fixture.bound(attestation=fresh, attestation_retention=original)
        self.assertEqual(first.raw_files, replay.raw_files)
        manifest["process"]["exit_code"] = 1
        failed = replace(fresh, manifest_bytes=canonical_bytes(manifest))
        result = self.fixture.bound(attestation=failed, attestation_retention=original)
        self.assertEqual("untrusted", result.obligation["outcome"])
        with self.assertRaisesRegex(ContractError, "fresh_attestation_execution_required"):
            bind_test_evidence(**self.fixture.args(attestation_retention=original))
        changed = dict(fresh.raw_files, bundle=b"different bundle")
        with self.assertRaisesRegex(ContractError, "attestation_retention_inputs_mismatch"):
            self.fixture.bound(attestation=replace(fresh, raw_files=changed), attestation_retention=original)


if __name__ == "__main__":
    unittest.main()
