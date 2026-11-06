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
    TestEvidenceInputs, bind_test_evidence, event_source_identity, merge_reference_identity)
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

    def merge_event(self, *, parents=None):
        snapshot = self.fixture.snapshot
        value = self.fixture.objects
        merge = value.commit(value.files({"README.txt": b"authored merge-reference control"}),
            [snapshot.base_tip_sha, snapshot.head_sha] if parents is None else parents)
        payload = {"ref": "refs/pull/7/merge", "node_id": "authored", "url":
            "https://api.github.com/repos/fixture-owner/fixture-repo/git/refs/pull/7/merge",
            "object": {"type": "commit", "sha": merge,
                "url": "https://api.github.com/repos/fixture-owner/fixture-repo/git/commits/" + merge}}
        reference = fact("/repos/fixture-owner/fixture-repo/git/ref/pull/7/merge", payload)
        return EventSourceFact(value.objects[merge], reference), merge, payload

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

    def test_v2_digest_contradiction_blocks_without_reinterpreting_v1(self):
        self.fixture.metadata["digest"] = "sha256:" + "f" * 64
        for version, effect in ((1, "clear"), (2, "block")):
            with self.subTest(profile_version=version):
                self.fixture.profile["profile_version"] = version
                self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
                result = self.fixture.bound()
                self.assertEqual(("error", effect), (result.obligation["outcome"], result.obligation["effect"]))
                self.assertIn("artifact_transport_digest_mismatch", result.obligation["reason_codes"])
                self.assertEqual(version == 2, "artifact_digest_mismatch" in result.obligation["reason_codes"])
                self.assertIn(self.fixture.args()["artifact_metadata"].body_bytes, result.raw_files.values())
                self.assertIn(self.fixture.artifact.archive_bytes, result.raw_files.values())

    def test_v2_generic_absence_or_unconfirmed_app_does_not_become_a_block(self):
        self.fixture.profile["profile_version"] = 2
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        row = self.fixture.capture_fixture.find("check_run")
        original = self.fixture.capture_fixture.payload(row)
        for app in (None, {}, {"id": True}, {"id": "99999"}):
            with self.subTest(app=app):
                document = dict(original, app=app)
                self.fixture.capture_fixture.set_payload(row, document)
                result = self.fixture.bound(artifact=None)
                self.assertEqual(("untrusted", "clear"), (result.obligation["outcome"], result.obligation["effect"]))
                self.assertNotIn("check_app_mismatch", result.obligation["reason_codes"])
        self.fixture.capture_fixture.set_payload(row, original)
        for changes in ({"artifact": None}, {"qualification": None}, {"artifact_metadata": None},
                        {"artifact": None, "archive_bytes": b"authored malformed archive", "artifact_metadata": None}):
            with self.subTest(changes=tuple(changes)):
                result = self.fixture.bound(**changes)
                self.assertNotEqual("satisfied", result.obligation["outcome"])
                self.assertEqual("clear", result.obligation["effect"])

    def test_v2_damaged_zip_keeps_scoped_platform_digest_contradiction_before_parsing(self):
        original = self.fixture.artifact.archive_bytes
        metadata = self.fixture.args()["artifact_metadata"]
        for version in (1, 2):
            self.fixture.profile["profile_version"] = version
            self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
            for damaged in (b"X" + original[1:], original[:-1]):
                with self.subTest(profile_version=version, same_size=len(damaged) == len(original)):
                    result = self.fixture.bound(artifact=None, archive_bytes=damaged)
                    self.assertEqual(("error", "block" if version == 2 else "clear"),
                        (result.obligation["outcome"], result.obligation["effect"]))
                    self.assertEqual(version == 2, "artifact_digest_mismatch" in result.obligation["reason_codes"])
                    self.assertEqual("reported", result.evidence["binding_level"])
                    self.assertIsNone(result.diagnostics["attestation_expected"])
                    self.assertIn(damaged, result.raw_files.values())
                    self.assertIn(metadata.body_bytes, result.raw_files.values())
                    if version == 2:
                        self.assertEqual((7001, digest(damaged)),
                            (result.evidence["artifact_id"], result.evidence["artifact_sha256"]))

    def test_v2_digest_negative_proof_requires_original_complete_scoped_metadata(self):
        self.fixture.profile["profile_version"] = 2
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        original = self.fixture.artifact.archive_bytes
        damaged = b"X" + original[1:]
        metadata = self.fixture.args()["artifact_metadata"]
        cases = [None, replace(metadata, request_path=metadata.request_path + "0"),
                 replace(metadata, status_code=404), replace(metadata, complete=False),
                 replace(metadata, complete=1), replace(metadata, headers={"authorization": "authored unsafe header"}),
                 replace(metadata, body_bytes=b"{}"), replace(metadata, body_bytes=b'{"id":7001,"id":7001}')]
        mutations = (lambda row: row.update(id=True), lambda row: row.update(id="7001"),
            lambda row: row.update(digest="not-an-algorithm-digest"), lambda row: row.update(size_in_bytes=True),
            lambda row: row.update(size_in_bytes=-1), lambda row: row.update(size_in_bytes=1.5),
            lambda row: row.update(name="delivery-review-1001-2-4001"), lambda row: row.update(expired=True),
            lambda row: row["workflow_run"].update(id=999), lambda row: row["workflow_run"].update(id=True),
            lambda row: row["workflow_run"].update(head_sha="f" * 40),
            lambda row: row["workflow_run"].update(repository_id=9002),
            lambda row: row["workflow_run"].update(head_repository_id=9002))
        for mutate in mutations:
            document = deepcopy(self.fixture.metadata)
            mutate(document)
            cases.append(replace(metadata, body_bytes=json.dumps(document).encode()))
        for index, candidate in enumerate(cases):
            with self.subTest(candidate=index):
                result = self.fixture.bound(artifact=None, archive_bytes=damaged, artifact_metadata=candidate)
                self.assertEqual(("error", "clear"), (result.obligation["outcome"], result.obligation["effect"]))
                self.assertNotIn("artifact_digest_mismatch", result.obligation["reason_codes"])
                self.assertIn(damaged, result.raw_files.values())
        # The later metadata check must also validate scope before it can use
        # a contradictory digest for an otherwise parseable archive.
        document = deepcopy(self.fixture.metadata)
        document["workflow_run"]["id"] = 999
        document["digest"] = "sha256:" + "f" * 64
        result = self.fixture.bound(artifact_metadata=fact(metadata.request_path, document))
        self.assertEqual(("error", "clear"), (result.obligation["outcome"], result.obligation["effect"]))
        self.assertIn("artifact_run_mismatch", result.obligation["reason_codes"])
        self.assertNotIn("artifact_digest_mismatch", result.obligation["reason_codes"])

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

    def test_v2_authenticated_zero_and_all_skipped_counts_block_while_v1_remains_clear(self):
        for version in (1, 2):
            self.fixture.profile["profile_version"] = version
            self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
            for skipped, native, reason in ((0, 0, "zero_tests"), (0, 5, "zero_tests"), (3, 0, "all_tests_skipped")):
                with self.subTest(profile_version=version, skipped=skipped, native=native):
                    self.fixture.junit = self.fixture.xml(passed=0, skipped=skipped)
                    self.fixture.receipt["execution"]["test_exit"] = native
                    self.fixture.rebuild()
                    result = self.fixture.bound()
                    self.assertEqual(("missing", "block" if version == 2 else "clear"),
                        (result.obligation["outcome"], result.obligation["effect"]))
                    self.assertIn(reason, result.obligation["reason_codes"])
                    self.assertEqual("workflow_bound", result.evidence["binding_level"])
                    self.assertIn(self.fixture.junit, result.raw_files.values())

    def test_v2_untrusted_zero_or_all_skipped_claims_do_not_gain_count_based_block(self):
        self.fixture.profile["profile_version"] = 2
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        for skipped, native in ((0, 5), (3, 0)):
            self.fixture.junit = self.fixture.xml(passed=0, skipped=skipped)
            self.fixture.receipt["execution"]["test_exit"] = native
            self.fixture.rebuild()
            for result in (bind_test_evidence(**self.fixture.args()), self.fixture.bound(qualification=None)):
                with self.subTest(skipped=skipped, reasons=result.obligation["reason_codes"]):
                    self.assertEqual(("untrusted", "clear"), (result.obligation["outcome"], result.obligation["effect"]))
                    self.assertNotIn("zero_tests", result.obligation["reason_codes"])
                    self.assertNotIn("all_tests_skipped", result.obligation["reason_codes"])

    def test_collection_error_alias_requires_qualified_native_exit_and_specific_junit_phase(self):
        for version in (1, 2):
            self.fixture.profile["profile_version"] = version
            self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
            for native, passed, errors, message, collection in (
                    (2, 0, 1, "collection failure", True),
                    (1, 0, 1, "collection failure", False),
                    (2, 0, 1, "failed on setup", False),
                    (2, 1, 1, "collection failure", False),
                    (3, 0, 1, "collection failure", False),
                    (2, 1, 0, "collection failure", False)):
                with self.subTest(profile_version=version, native=native, passed=passed, errors=errors, message=message):
                    self.fixture.junit = self.fixture.xml(passed=passed, errors=errors).replace(
                        b'<error message="authored">', ('<error message="' + message + '">').encode())
                    self.fixture.receipt["execution"]["test_exit"] = native
                    self.fixture.rebuild()
                    result = self.fixture.bound()
                    self.assertEqual(version == 2 and collection,
                        "pytest_collection_error" in result.obligation["reason_codes"])
                    self.assertEqual("block", result.obligation["effect"])
                    self.assertEqual("failed" if errors else "error", result.obligation["outcome"])
                    self.assertIsNone(self.fixture.receipt["execution"]["collection_exit"])

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

    def test_merge_reference_binds_omitted_pr_field_and_retains_original_bytes(self):
        event, merge, payload = self.merge_event()
        event = replace(event, reference=replace(event.reference,
            body_bytes=b" \n" + json.dumps(payload, indent=2).encode() + b"\n"))
        for phase in ("initial", "final"):
            row = self.fixture.capture_fixture.find("pull_request", phase)
            document = self.fixture.capture_fixture.payload(row)
            document.pop("merge_commit_sha")
            self.fixture.capture_fixture.set_payload(row, document)
        before = dict(self.fixture.capture_fixture.bodies)
        result = self.fixture.bound(event_source=event, event_source_basis="github_merge_reference_v1")
        self.assertEqual("satisfied", result.obligation["outcome"], result.diagnostics)
        self.assertEqual(merge, result.diagnostics["attestation_expected"]["certificate_source_sha"])
        self.assertEqual(self.fixture.snapshot.head_sha, result.evidence["subject_sha"])
        self.assertEqual(before, self.fixture.capture_fixture.bodies)
        retained = next(raw for name, raw in result.raw_files.items() if name.endswith("/event-merge-reference.json"))
        self.assertEqual(event.reference.body_bytes, retained)
        request = next(loads_strict(raw) for name, raw in result.raw_files.items()
            if name.endswith("/event-merge-reference.json.request.json"))
        self.assertEqual(event.reference.request_path, request["path"])
        self.assertEqual(200, request["status_code"])

    def test_new_reference_basis_does_not_read_deprecated_pr_merge_field(self):
        event, merge, unused = self.merge_event()
        for legacy in (None, "f" * 40, merge):
            pr = {"number": 7, "merge_commit_sha": legacy}
            with self.subTest(legacy=legacy):
                self.assertEqual((merge, [self.fixture.snapshot.base_tip_sha, self.fixture.snapshot.head_sha]),
                    event_source_identity(event, self.fixture.snapshot, pr, event_source_basis="github_merge_reference_v1"))
        bad = replace(event, reference=replace(event.reference, request_path=event.reference.request_path + "?fallback=1"))
        with self.assertRaisesRegex(ContractError, "api_fact_route_mismatch"):
            event_source_identity(bad, self.fixture.snapshot, {"number": 7, "merge_commit_sha": merge},
                event_source_basis="github_merge_reference_v1")

    def test_merge_reference_requires_exact_route_status_completeness_and_safe_headers(self):
        event, unused, payload = self.merge_event()
        changes = [
            {"request_path": "/repos/other-owner/fixture-repo/git/ref/pull/7/merge"},
            {"request_path": "/repos/fixture-owner/fixture-repo/git/ref/pull/8/merge"},
            {"request_path": "/repos/fixture-owner/fixture-repo/git/ref/pull/7/head"},
            {"request_path": "/repos/fixture-owner/fixture-repo/git/matching-refs/pull/7/merge"},
            {"request_path": event.reference.request_path + "?page=1"},
            {"status_code": 404}, {"status_code": True}, {"complete": False}, {"complete": 1},
            {"observed_at": "2026-11-06"}, {"headers": {"authorization": "authored unsafe header"}},
        ]
        for change in changes:
            reference = replace(event.reference, **change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                merge_reference_identity(reference, self.fixture.snapshot.repository, 7)
        bad = replace(event, reference=replace(event.reference, status_code=404))
        result = self.fixture.bound(event_source=bad, event_source_basis="github_merge_reference_v1")
        self.assertIsNone(result.diagnostics["attestation_expected"])
        self.assertNotEqual("satisfied", result.obligation["outcome"])
        self.assertIn(bad.reference.body_bytes, result.raw_files.values())
        request = next(loads_strict(raw) for name, raw in result.raw_files.items()
            if name.endswith("/event-merge-reference.json.request.json"))
        self.assertEqual(404, request["status_code"])

    def test_merge_reference_rejects_noncommit_ambiguous_json_and_noncanonical_identity(self):
        event, merge, payload = self.merge_event()
        changes = [({"ref": "refs/pull/8/merge"}, {}), ({"ref": "refs/heads/merge"}, {}),
            ({}, {"type": "tag"}), ({}, {"sha": merge[:12]}), ({}, {"sha": merge.upper()}),
            ({}, {"sha": "g" * 40}), ({}, {"url": "https://api.github.com/repos/other/repo/git/commits/" + merge}),
            ({}, {"url": "https://api.github.com/repos/fixture-owner/fixture-repo/git/commits/" + merge + "?x=1"}),
            ({}, {"url": "https://api.github.com.evil.example/repos/fixture-owner/fixture-repo/git/commits/" + merge})]
        bodies = [b"[]", b'{"ref":"refs/pull/7/merge","ref":"refs/pull/7/merge","object":{}}']
        for top, obj in changes:
            document = deepcopy(payload)
            document.update(top)
            document["object"].update(obj)
            bodies.append(canonical_bytes(document))
        for raw in bodies:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                merge_reference_identity(replace(event.reference, body_bytes=raw), self.fixture.snapshot.repository, 7)
        for repository, number in (("fixture-owner/../escape", 7), ("fixture-owner/fixture-repo", True),
                ("fixture-owner/fixture-repo", 0), ("fixture-owner/fixture-repo", "7")):
            with self.subTest(repository=repository, number=number), self.assertRaises(ContractError):
                merge_reference_identity(event.reference, repository, number)

    def test_merge_reference_requires_original_commit_hash_and_exact_ordered_parents(self):
        event, unused, payload = self.merge_event()
        snapshot = self.fixture.snapshot
        for parents in ([snapshot.head_sha, snapshot.base_tip_sha], [snapshot.base_tip_sha],
                [snapshot.base_tip_sha, snapshot.head_sha, "e" * 40], ["d" * 40, snapshot.head_sha]):
            bad, unused, unused_payload = self.merge_event(parents=parents)
            with self.subTest(parents=parents), self.assertRaisesRegex(ContractError, "event_merge_parent_mismatch"):
                event_source_identity(bad, snapshot, {"number": 7}, event_source_basis="github_merge_reference_v1")
        bad = replace(event, commit_bytes=event.commit_bytes[:-1] + b"!")
        with self.assertRaisesRegex(ContractError, "event_merge_reference_sha_mismatch"):
            event_source_identity(bad, snapshot, {"number": 7}, event_source_basis="github_merge_reference_v1")
        head_reference = deepcopy(payload)
        head_reference["object"].update(sha=snapshot.head_sha,
            url="https://api.github.com/repos/fixture-owner/fixture-repo/git/commits/" + snapshot.head_sha)
        bad = EventSourceFact(self.fixture.objects.objects[snapshot.head_sha],
            replace(event.reference, body_bytes=canonical_bytes(head_reference)))
        with self.assertRaisesRegex(ContractError, "event_merge_parent_mismatch"):
            event_source_identity(bad, snapshot, {"number": 7}, event_source_basis="github_merge_reference_v1")

    def test_new_basis_cannot_fall_back_when_event_or_reference_is_missing(self):
        event, unused, unused_payload = self.merge_event()
        for supplied in (None, EventSourceFact(self.fixture.objects.objects[self.fixture.snapshot.head_sha]),
                replace(event, reference=None)):
            with self.subTest(supplied=supplied):
                result = self.fixture.bound(event_source=supplied, event_source_basis="github_merge_reference_v1")
                self.assertEqual("untrusted", result.obligation["outcome"])
                self.assertIn("event_merge_reference_missing", result.obligation["reason_codes"])
                self.assertIsNone(result.diagnostics["attestation_expected"])
        self.assertEqual("satisfied", self.fixture.bound().obligation["outcome"])

    def test_missing_new_reference_keeps_the_independent_ci_block(self):
        for role in ("jobs", "check_run"):
            row = self.fixture.capture_fixture.find(role)
            payload = self.fixture.capture_fixture.payload(row)
            (payload["jobs"][0] if role == "jobs" else payload)["conclusion"] = "failure"
            self.fixture.capture_fixture.set_payload(row, payload)
        result = self.fixture.bound(artifact=None, event_source_basis="github_merge_reference_v1")
        self.assertEqual("untrusted", result.obligation["outcome"])
        self.assertEqual("block", result.obligation["effect"])
        self.assertEqual({"artifact_missing", "event_merge_reference_missing"}, set(result.obligation["reason_codes"]))
        self.assertIsNone(result.diagnostics["attestation_expected"])

    def test_explicit_event_source_basis_rejects_unknown_or_mixed_interpretations(self):
        self.assertEqual("legacy_pr_field_v1", TestEvidenceInputs().event_source_basis)
        for basis in ("unknown_v2", None, 2):
            with self.subTest(basis=basis), self.assertRaisesRegex(ContractError, "event_source_basis_unsupported"):
                bind_test_evidence(**self.fixture.args(event_source_basis=basis))
        event, unused, unused_payload = self.merge_event()
        result = self.fixture.bound(event_source=event, event_source_basis="legacy_pr_field_v1")
        self.assertIn("event_source_basis_reference_conflict", result.obligation["reason_codes"])
        self.assertIsNone(result.diagnostics["attestation_expected"])

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

    def test_fresh_success_cannot_upgrade_failed_or_invalid_original_verification(self):
        expected = bind_test_evidence(**self.fixture.args()).diagnostics["attestation_expected"]
        fresh = self.fixture.fake_attestation(expected)
        for mode in ("native_failure", "timeout", "wrong_authenticated_claim"):
            manifest = fresh.manifest
            raw = dict(fresh.raw_files)
            if mode == "native_failure":
                manifest["process"]["exit_code"] = 1
            elif mode == "timeout":
                manifest["process"].update(exit_code=None, termination="timeout")
            else:
                output = json.loads(raw["stdout"])
                output[0]["verificationResult"]["signature"]["certificate"]["sourceRepositoryIdentifier"] = "9999"
                raw["stdout"] = json.dumps(output).encode()
                manifest["files"]["stdout"].update(sha256=digest(raw["stdout"]), size_bytes=len(raw["stdout"]))
            original = replace(fresh, manifest_bytes=canonical_bytes(manifest), raw_files=raw,
                               observation_bytes=b'{"status":"verified_claims"}')
            first = self.fixture.bound(attestation=original)
            replay = self.fixture.bound(attestation=fresh, attestation_retention=original)
            with self.subTest(mode=mode):
                self.assertEqual("untrusted", first.obligation["outcome"])
                self.assertEqual(first.obligation, replay.obligation)
                self.assertEqual(first.evidence, replay.evidence)
                self.assertEqual(first.raw_files, replay.raw_files)


if __name__ == "__main__":
    unittest.main()
