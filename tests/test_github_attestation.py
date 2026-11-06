"""Authored parser/process-envelope controls, NOT signatures or live attestations.

Every native JSON in this file is synthetic. Mocked process responses exercise
policy only; official upstream signed vectors are separately run under work/.
"""
from copy import deepcopy
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters import github_attestation as adapter


ARTIFACT = b"Authored inert artifact bytes; not a real signed ZIP."


def expected():
    return {"repository_id": 9001, "repository_full_name": "fixture-owner/repo", "certificate_source_sha": "a" * 40,
            "certificate_event": "pull_request", "run_id": 1001, "run_attempt": 2,
            "producer_repository_full_name": "fixture-owner/producer", "producer_path": ".github/workflows/pytest.yml",
            "producer_sha": "b" * 40, "job_id": 2001, "check_run_id": 3001, "artifact_id": 4001,
            "artifact_sha256": hashlib.sha256(ARTIFACT).hexdigest(), "receipt_sha256": "c" * 64, "junit_sha256": "d" * 64}


def control():
    value = expected()
    common = {key: value[key] for key in adapter._COMMON - {"subject_name", "predicate_type"}}
    common.update(subject_name="https://github.com/fixture-owner/repo/actions/runs/1001/artifacts/4001", predicate_type=adapter.PREDICATE_TYPE)
    predicate = {key: value[key] for key in ("repository_id", "run_id", "run_attempt", "job_id", "check_run_id", "artifact_id",
                                           "artifact_sha256", "receipt_sha256", "junit_sha256")}
    predicate.update(predicate_version=1, producer_profile=adapter.PRODUCER_PROFILE)
    cert = {"issuer": adapter.ISSUER, "subjectAlternativeName": adapter._identity(common), "buildSignerURI": adapter._identity(common),
            "buildSignerDigest": value["producer_sha"], "runnerEnvironment": "github-hosted",
            "sourceRepositoryIdentifier": str(value["repository_id"]), "sourceRepositoryURI": "https://github.com/fixture-owner/repo",
            "sourceRepositoryDigest": value["certificate_source_sha"], "buildTrigger": "pull_request",
            "runInvocationURI": "https://github.com/fixture-owner/repo/actions/runs/1001/attempts/2"}
    native = [{"attestation": {"bundle": {}, "bundle_url": "", "initiator": ""}, "verificationResult": {
        "mediaType": "application/vnd.dev.sigstore.verificationresult+json;version=0.1", "signature": {"certificate": cert},
        "verifiedIdentity": {"subjectAlternativeName": {"subjectAlternativeName": adapter._identity(common)},
                             "issuer": {"issuer": "", "regexp": ".*"}, "runnerEnvironment": "github-hosted"},
        "verifiedTimestamps": [{"type": "Tlog", "uri": "https://rekor.sigstore.dev", "timestamp": "2026-11-06T00:00:00Z"}],
        "statement": {"_type": "https://in-toto.io/Statement/v1", "subject": [{"name": common["subject_name"], "digest": {"sha256": value["artifact_sha256"]}}],
                      "predicateType": adapter.PREDICATE_TYPE, "predicate": deepcopy(predicate)}}}]
    return common, predicate, native


def raw(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False).encode()


def authored_bundle(statement):
    return raw({"mediaType": "application/vnd.dev.sigstore.bundle.v0.3+json", "verificationMaterial": {},
                "dsseEnvelope": {"payloadType": "application/vnd.in-toto+json", "payload": base64.b64encode(raw(statement)).decode(),
                                 "signatures": [{"sig": "authored-not-a-signature"}]}})


class AttestationParserTests(unittest.TestCase):
    def parse(self, native, *, common=None, predicate=None):
        base, product, _ = control()
        return adapter._interpret(raw(native), common or base, hashlib.sha256(ARTIFACT).hexdigest(), predicate or product)

    def test_matching_authored_claims_are_only_semantic_matches(self):
        common, predicate, native = control()
        before = deepcopy((common, predicate, native))
        states, reasons, parsed = self.parse(native)
        self.assertEqual({"certificate": "matched", "subject": "matched", "predicate": "matched"}, states)
        self.assertEqual([], reasons)
        self.assertEqual(native, parsed)
        self.assertEqual(before, (common, predicate, native))
        self.assertNotIn("cryptographic_verification", states)

    def test_certificate_issuer_repo_sha_attempt_runner_and_exact_signer_are_required(self):
        for field, bad in (("issuer", "https://attacker.invalid"), ("sourceRepositoryIdentifier", "9002"),
            ("sourceRepositoryURI", "https://github.com/fixture-owner/other"), ("sourceRepositoryDigest", "f" * 40),
            ("runInvocationURI", "https://github.com/fixture-owner/repo/actions/runs/1001/attempts/1"),
            ("runnerEnvironment", "self-hosted"), ("buildSignerDigest", "f" * 40),
            ("buildSignerURI", "https://github.com/fixture-owner/producer/.github/workflows/pytest.yml@" + "b" * 40 + "-suffix"),
            ("subjectAlternativeName", "https://github.com/fixture-owner/unapproved/.github/workflows/pytest.yml@" + "b" * 40),
            ("buildTrigger", "pull_request_target")):
            with self.subTest(field=field):
                _, _, native = control()
                native[0]["verificationResult"]["signature"]["certificate"][field] = bad
                states, reasons, _ = self.parse(native)
                self.assertEqual("unconfirmed", states["certificate"])
                self.assertIn("certificate_identity_mismatch", reasons)

    def test_repeated_certificate_fields_cannot_disagree(self):
        _, _, native = control()
        native[0]["verificationResult"]["signature"]["certificate"]["githubWorkflowSHA"] = "f" * 40
        self.assertIn("certificate_repeated_identity_mismatch", self.parse(native)[1])

    def test_same_bytes_under_new_artifact_id_and_multiple_subjects_are_refused(self):
        for mode in ("new_id", "digest", "extra"):
            _, _, native = control()
            subject = native[0]["verificationResult"]["statement"]["subject"]
            if mode == "new_id":
                subject[0]["name"] = subject[0]["name"].replace("4001", "4002")
            elif mode == "digest":
                subject[0]["digest"]["sha256"] = "f" * 64
            else:
                subject.append(deepcopy(subject[0]))
            self.assertIn("signed_subject_mismatch", self.parse(native)[1])

    def test_custom_predicate_exact_types_ids_hashes_and_keys(self):
        for key, value in (("job_id", 2002), ("check_run_id", 3002), ("run_attempt", True), ("predicate_version", 1.0),
            ("receipt_sha256", "f" * 64), ("junit_sha256", "f" * 64), ("artifact_sha256", "f" * 64),
            ("producer_profile", "unapproved"), ("unknown", "claimed")):
            _, _, native = control()
            native[0]["verificationResult"]["statement"]["predicate"][key] = value
            self.assertIn("signed_predicate_mismatch", self.parse(native)[1])

    def test_unknown_schema_ambiguous_records_and_absent_timestamp_fail_closed(self):
        _, _, original = control()
        for mutate, reason in ((lambda value: value.append(deepcopy(value[0])), "verification_record_count"),
            (lambda value: value[0]["verificationResult"].update(mediaType="unknown"), "unsupported_verification_contract"),
            (lambda value: value[0]["verificationResult"].update(verifiedTimestamps=[]), "verified_timestamp_missing"),
            (lambda value: value[0]["verificationResult"]["signature"]["certificate"].update(check_run_id="3001"), "unsupported_certificate_contract"),
            (lambda value: value[0]["verificationResult"]["statement"].update(predicateType="unapproved"), "unsupported_statement_contract")):
            native = deepcopy(original)
            mutate(native)
            self.assertIn(reason, self.parse(native)[1])

    def test_malformed_duplicate_nonfinite_utf8_and_deep_output_retains_raw_outside_parser(self):
        common, predicate, _ = control()
        for value in (b"\xff", b'[{"a":1,"a":2}]', b'[NaN]', b'[1e400]', b"[" * 50 + b"0" + b"]" * 50):
            states, reasons, parsed = adapter._interpret(value, common, hashlib.sha256(ARTIFACT).hexdigest(), predicate)
            self.assertEqual(["verification_output_invalid_json"], reasons)
            self.assertIsNone(parsed)
            self.assertTrue(all(state == "unconfirmed" for state in states.values()))

    def test_structural_mutation_does_not_crash_native_parser(self):
        _, _, original = control()
        for key in original[0]["verificationResult"]:
            for bad in (None, True, 1, [], {}, "unsupported"):
                native = deepcopy(original)
                native[0]["verificationResult"][key] = bad
                self.assertTrue(self.parse(native)[1], (key, bad))

    def test_duplicate_original_signed_keys_are_refused_before_normalization(self):
        _, _, native = control()
        bundle = json.loads(authored_bundle(native[0]["verificationResult"]["statement"]))
        payload = raw(native[0]["verificationResult"]["statement"]).replace(b'"predicate_version": 1', b'"predicate_version": 0, "predicate_version": 1')
        bundle["dsseEnvelope"]["payload"] = base64.b64encode(payload).decode()
        self.assertEqual("bundle_claim_invalid_json", adapter._bundle_claim(raw(bundle))[1])


class AttestationExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.tool = self.root / "authored-tool"
        self.binary = b"Synthetic mocked verifier; this file is never executed."
        self.tool.write_bytes(self.binary)
        self.digest = hashlib.sha256(self.binary).hexdigest()
        self.root_bytes = b"Authored mocked trusted root; not a real trust root."
        self.calls = []
        self.common, self.predicate, self.native = control()
        self.termination, self.exit_code = "exited", 0
        self.mutate = None

    def execute(self, argv, **options):
        self.calls.append((list(argv), options))
        facts = {"argv": list(argv), "duration_ms": 1, "exit_code": 0, "termination": "exited"}
        if argv[1:] == ["--version"]:
            return facts, {"stdout": b"gh version 2.98.0 (authored mocked response)\n", "stderr": b""}
        if self.mutate:
            self.mutate(Path(options["cwd"]))
        facts.update(termination=self.termination, exit_code=self.exit_code)
        return facts, {"stdout": raw(self.native), "stderr": b"authored diagnostic\r\n"}

    def invoke(self, **changes):
        arguments = dict(artifact_bytes=ARTIFACT, bundle_bytes=authored_bundle(control()[2][0]["verificationResult"]["statement"]), trusted_root_bytes=self.root_bytes,
            gh_executable=self.tool, gh_sha256=self.digest, expected=expected(), destination=self.root / "execution")
        arguments.update(changes)
        with patch.dict(adapter.GH_PINS, {(sys.platform, "amd64"): self.digest}), \
             patch.object(adapter, "TRUSTED_ROOT_PINS", frozenset({hashlib.sha256(self.root_bytes).hexdigest()})), \
             patch.object(adapter, "_execute", side_effect=self.execute), patch.dict(os.environ, {"GH_TOKEN": "never-propagate", "GH_DEBUG": "api"}):
            return adapter.verify_github_attestation(**arguments)

    def test_fresh_official_process_required_and_all_platform_claims_stay_unconfirmed(self):
        result = self.invoke()
        self.assertEqual(2, len(self.calls))
        argv, options = self.calls[1]
        self.assertEqual("verify", argv[2])
        for flag in ("--bundle", "--custom-trusted-root", "--cert-identity", "--cert-oidc-issuer", "--signer-digest", "--deny-self-hosted-runners", "--predicate-type"):
            self.assertIn(flag, argv)
        self.assertNotIn("--signer-workflow", argv)
        self.assertTrue(argv[argv.index("--bundle") + 1].endswith(".json"))
        for forbidden in ("GH_TOKEN", "GH_DEBUG", "GITHUB_TOKEN", "PYTHONPATH"):
            self.assertNotIn(forbidden, options["env"])
        self.assertEqual("http://127.0.0.1:1", options["env"]["HTTPS_PROXY"])
        self.assertEqual("matched", result.observation["predicate"])
        for key in ("platform_binding", "producer_qualification", "tested_source_binding"):
            self.assertEqual("unconfirmed", result.observation[key])
        self.assertEqual(b"authored diagnostic\r\n", result.raw_files["stderr"])
        self.assertEqual(result.manifest_bytes, (result.directory / "verification.json").read_bytes())
        result.observation["expected"]["run_attempt"] = 99
        self.assertEqual(2, result.observation["expected"]["run_attempt"])
        with self.assertRaises(TypeError):
            result.raw_files["stdout"] = b"changed"

    def test_saved_verified_json_cannot_bypass_the_process(self):
        self.exit_code = 1
        result = self.invoke(bundle_bytes=raw(self.native))
        self.assertEqual(2, len(self.calls))
        self.assertEqual("error", result.observation["status"])
        self.assertEqual("unconfirmed", result.observation["cryptographic_verification"])
        self.assertEqual("unconfirmed", result.observation["predicate"])
        self.assertEqual(self.native, result.observation["parsed_native"])

    def test_timeout_with_valid_looking_output_cannot_be_verified(self):
        self.termination = "timeout"
        result = self.invoke()
        self.assertEqual("unconfirmed", result.observation["cryptographic_verification"])
        self.assertIn("verification_timeout", result.observation["reason_codes"])

    def test_post_input_tampering_preserves_raw_result_but_refuses_proof(self):
        self.mutate = lambda root: (root / "raw/artifact.zip").write_bytes(b"changed")
        result = self.invoke()
        self.assertEqual("unconfirmed", result.observation["cryptographic_verification"])
        self.assertIn("attestation_post_integrity_failed", result.observation["reason_codes"])
        self.assertEqual(ARTIFACT, result.raw_files["artifact"])
        self.assertEqual(self.native, result.observation["parsed_native"])

    def test_unapproved_tool_root_and_excess_bytes_fail_before_invocation(self):
        for change, reason in (({"gh_sha256": "f" * 64}, "unsupported_attestation_tool_pin"),
            ({"trusted_root_bytes": b"subject chosen root"}, "unapproved_attestation_trusted_root"),
            ({"limits": adapter.AttestationLimits(max_bundle_bytes=1)}, "attestation_input_limit"),
            ({"artifact_bytes": ARTIFACT + b"changed"}, "expected_artifact_digest_mismatch")):
            with self.subTest(reason=reason), self.assertRaisesRegex(adapter.AttestationError, reason):
                self.invoke(**change)
        self.assertFalse(self.calls)
        self.assertFalse((self.root / "execution").exists())

    def test_unknown_native_version_never_confirms_cryptography(self):
        self.native[0]["verificationResult"]["mediaType"] = "unknown"
        result = self.invoke()
        self.assertEqual("error", result.observation["status"])
        self.assertEqual("unconfirmed", result.observation["cryptographic_verification"])

    def test_expected_types_and_hard_limits_cannot_be_weakened(self):
        for key, value in (("job_id", True), ("run_attempt", 1.0), ("artifact_id", -1), ("producer_sha", "short"),
                           ("certificate_event", "pull_request_target"), ("producer_path", ".github/workflows/../bad.yml")):
            changed = expected()
            changed[key] = value
            with self.subTest(key=key), self.assertRaises(adapter.AttestationError):
                self.invoke(expected=changed)
        with self.assertRaises(adapter.AttestationError):
            adapter.AttestationLimits(timeout_seconds=121)
        with self.assertRaises(adapter.AttestationError):
            adapter.AttestationLimits(max_binary_bytes=True)
        self.assertFalse(self.calls)


if __name__ == "__main__":
    unittest.main()
