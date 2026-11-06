"""Authored full orchestration controls, not hosted/E01 product acceptance.

Git objects and disk replay are real. HTTP, native scanner outputs and signing
are explicit authored mocks; none can become a release qualification receipt.
"""
from contextlib import ExitStack
from dataclasses import replace
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from delivery_review import inspection_runner as runner
from delivery_review.adapters import github_attestation as attest
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.evidence.inspection import verify_inspection
from delivery_review.evidence.inspection import InspectionWriter
from delivery_review.test_evidence import bind_test_evidence
from test_test_evidence import AuthoredInspection, digest
from test_composition import authored_engine
from test_github_collect import ScriptedTransport
from test_artifact_acquisition import ArtifactTransport, AttestationTransport, response, discovery
from observation_fixtures import NOW


class InspectionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        fixture_root = self.root / "fixture"
        fixture_root.mkdir()
        self.fixture = AuthoredInspection(fixture_root)
        for row in self.fixture.capture_fixture.records:
            if row["role"] == "pull_request":
                payload = self.fixture.capture_fixture.payload(row)
                payload["merge_commit_sha"] = None
                self.fixture.capture_fixture.set_payload(row, payload)
        self.capture = self.fixture.capture_fixture.seal()
        first = bind_test_evidence(**self.fixture.args())
        self.proof = self.fixture.fake_attestation(first.diagnostics["attestation_expected"])
        gh = self.root / "authored-gh.bin"
        gh.write_bytes(b"Authored placeholder, never executed")
        self.tools = runner.InspectionTools(Path(shutil.which("git")), Path(sys.executable),
            {name: self.root / (name + ".whl") for name in ("checkwash", "pinwash", "ledgerwash")}, gh, self.fixture.trusted_root)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for module, name, value in ((runner, "GH_PINS", {("authored", "fixture"): digest(gh.read_bytes())}),
                (runner, "TRUSTED_ROOT_PINS", {digest(self.fixture.trusted_root)}),
                (attest, "TRUSTED_ROOT_PINS", {digest(self.fixture.trusted_root)})):
            self.stack.enter_context(patch.object(module, name, value))
        self.identity = {"version": "authored-test-only", "source_sha": "1" * 40}
        self.events = []

    def live(self, name="live", *, qualified=True, malformed_zip=False, source_error=False,
             crypto_fail=False, crypto_unavailable=False):
        scripted = ScriptedTransport(self.capture)
        fixture, events = self.fixture, self.events
        class Platform:
            def get(self, path, **kwargs):
                if path == "/repos/fixture-owner/producer":
                    return response({"id": 9002, "full_name": "fixture-owner/producer"})["response"]
                if scripted.pending[0]["phase"] == "final":
                    events.append("final_recheck")
                return scripted.get(path, **kwargs)
        archive = b"not-a-zip" if malformed_zip else fixture.artifact.archive_bytes
        metadata = {**fixture.metadata, "size_in_bytes": len(archive), "digest": "sha256:" + digest(archive)}
        artifacts = ArtifactTransport([response({"total_count": 1, "artifacts": [metadata]}), response(metadata), response(archive)])
        proofs = AttestationTransport([discovery((self.proof.raw_files["bundle"],))])
        def source(*args, **kwargs):
            events.append("source")
            if source_error:
                raise ContractError("authored_source_failure")
            return SimpleNamespace(directory=fixture.root, snapshot=fixture.snapshot,
                evidence_bytes=canonical_bytes({"origin": "authored-source-controller", "processes": [], "quarantine_files": []}))
        def engine(snapshot, *, engine_name, **kwargs):
            events.append(engine_name)
            execution = authored_engine(fixture, engine_name, absent=engine_name == "ledgerwash")
            manifest = execution.manifest
            install = b"[]\n"
            location = self.root / (name + "-engine-" + engine_name)
            location.mkdir()
            (location / "installation.json").write_bytes(install)
            manifest.update(setup=[], installation={"path": "installation.json", "sha256": digest(install), "size_bytes": len(install)})
            return replace(execution, directory=location, manifest_bytes=canonical_bytes(manifest))
        def verify(**kwargs):
            events.append("crypto")
            self.assertEqual(fixture.artifact.archive_bytes, kwargs["artifact_bytes"])
            self.assertEqual(self.proof.raw_files["bundle"], kwargs["bundle_bytes"])
            if crypto_unavailable:
                raise OSError("authored controller unavailable")
            result = fixture.fake_attestation(kwargs["expected"])
            if crypto_fail:
                manifest = result.manifest
                manifest["process"]["exit_code"] = 1
                result = replace(result, manifest_bytes=canonical_bytes(manifest))
            return result
        with patch.object(runner, "acquire_source", side_effect=source), patch.object(runner, "run_engine", side_effect=engine), \
                patch.object(runner, "verify_github_attestation", side_effect=verify), \
                patch.object(runner, "qualified_producer", return_value=fixture.qualification if qualified else None):
            result = runner.inspect_pr("https://github.com/fixture-owner/fixture-repo/pull/7", fixture.profile_bytes,
                self.root / name, tools=self.tools, collector=self.identity, platform_transport=Platform(),
                artifact_transport=artifacts, attestation_transport=proofs, clock=lambda: NOW)
        self.assertFalse(scripted.pending)
        return result

    def replay(self, name, *, crypto_fail=False, qualified=True):
        def verify(**kwargs):
            self.events.append("replay_crypto")
            result = self.fixture.fake_attestation(kwargs["expected"])
            if crypto_fail:
                manifest = result.manifest
                manifest["process"]["exit_code"] = 1
                return replace(result, manifest_bytes=canonical_bytes(manifest))
            return result
        with patch.object(runner, "acquire_source", side_effect=AssertionError("network source on replay")), \
                patch.object(runner, "run_engine", side_effect=AssertionError("engine rerun on replay")), \
                patch("delivery_review.providers.github_http.GitHubTransport.get", side_effect=AssertionError("HTTP on replay")), \
                patch("delivery_review.providers.github_artifact_http.GitHubArtifactTransport.get", side_effect=AssertionError("artifact HTTP on replay")), \
                patch("delivery_review.providers.github_attestation_http.GitHubAttestationTransport.get", side_effect=AssertionError("attestation HTTP on replay")), \
                patch.object(runner, "verify_github_attestation", side_effect=verify), \
                patch.object(runner, "qualified_producer", return_value=self.fixture.qualification if qualified else None):
            return runner.replay_inspection(self.root / "live", self.root / name, tools=self.tools)

    def test_live_then_two_offline_replays_rederive_results_and_preserve_original_tree(self):
        live = self.live()
        self.assertEqual({"evidence": "complete", "freshness": "current", "policy": "clear"}, live["state"])
        self.assertTrue(live["ready_for_review"])
        self.assertLess(self.events.index("crypto"), self.events.index("final_recheck"))
        before = verify_inspection(self.root / "live")
        first = self.replay("replay-1")
        second = self.replay("replay-2")
        self.assertEqual(first, second)
        self.assertEqual("unknown", first["state"]["freshness"])
        self.assertFalse(first["ready_for_review"])
        self.assertEqual(2, self.events.count("replay_crypto"))
        self.assertEqual((self.root / "replay-1/report.json").read_bytes(), (self.root / "replay-2/report.json").read_bytes())
        after = verify_inspection(self.root / "live")
        self.assertEqual(before.manifest_bytes, after.manifest_bytes)
        self.assertEqual(before.files, after.files)
        self.assertTrue((self.root / "live/report.html").read_text().startswith("<!doctype html>"))

    def test_no_real_producer_qualification_is_not_promoted_by_valid_mocked_crypto(self):
        report = self.live(qualified=False)
        self.assertEqual("incomplete", report["state"]["evidence"])
        self.assertFalse(report["ready_for_review"])
        self.assertIn("producer_not_qualified", next(o for o in report["obligations"] if o["id"] == "pytest-linux")["reason_codes"])

    def test_replay_cannot_add_later_producer_qualification_to_original_evidence(self):
        self.live(qualified=False)
        report = self.replay("later-qualified", qualified=True)
        self.assertEqual("incomplete", report["state"]["evidence"])
        self.assertIn("producer_not_qualified", next(o for o in report["obligations"] if o["id"] == "pytest-linux")["reason_codes"])
        self.assertFalse(any(row["path"].endswith("qualification.json") for row in report["raw_inventory"]))

    def test_replay_reverifies_cryptography_instead_of_trusting_saved_success(self):
        self.live()
        report = self.replay("bad-crypto", crypto_fail=True)
        self.assertFalse(report["ready_for_review"])
        self.assertNotEqual("complete", report["state"]["evidence"])
        self.assertIn("attestation_verification_failed", next(o for o in report["obligations"] if o["id"] == "pytest-linux")["reason_codes"])

    def test_replay_does_not_upgrade_original_failed_verification(self):
        live = self.live(crypto_fail=True)
        replayed = self.replay("fresh-crypto-success")
        self.assertNotEqual("complete", live["state"]["evidence"])
        self.assertEqual(live["state"]["evidence"], replayed["state"]["evidence"])
        self.assertIn("attestation_verification_failed", next(o for o in replayed["obligations"] if o["id"] == "pytest-linux")["reason_codes"])

    def test_replay_does_not_insert_originally_unavailable_verification(self):
        live = self.live(crypto_unavailable=True)
        replayed = self.replay("new-crypto-audit")
        self.assertEqual(live["state"]["evidence"], replayed["state"]["evidence"])
        self.assertIn("attestation_missing", next(o for o in replayed["obligations"] if o["id"] == "pytest-linux")["reason_codes"])
        self.assertFalse(any("/attestation/" in row["path"] for row in replayed["raw_inventory"]))

    def test_malformed_obtained_zip_is_raw_error_evidence_not_missing_success(self):
        report = self.live(malformed_zip=True)
        self.assertEqual("error", report["state"]["evidence"])
        self.assertNotIn("crypto", self.events)
        envelope = verify_inspection(self.root / "live")
        self.assertIn(b"not-a-zip", envelope.files.values())

    def test_fundamental_source_failure_retains_capture_without_false_completion_manifest(self):
        with self.assertRaises(ValueError):
            self.live(source_error=True)
        self.assertTrue((self.root / "live/raw/github/capture.json").is_file())
        self.assertTrue((self.root / "live/inspection-error.json").is_file())
        self.assertFalse((self.root / "live/inspection.json").exists())

    def test_existing_nested_or_tampered_destinations_cannot_overwrite_evidence(self):
        self.live()
        with self.assertRaises(ContractError):
            runner.replay_inspection(self.root / "live", self.root / "live", tools=self.tools)

    def test_full_envelope_rejects_added_missing_changed_and_linked_files(self):
        self.live()
        for case in ("extra", "missing", "changed", "hardlink"):
            copy = self.root / case
            shutil.copytree(self.root / "live", copy)
            target = copy / "report.json"
            if case == "extra": (copy / "extra").write_bytes(b"unexpected")
            elif case == "missing": target.unlink()
            elif case == "changed": target.write_bytes(b"{}")
            else:
                import os
                os.link(target, self.root / "report-link")
            with self.subTest(case=case), self.assertRaises((ContractError, OSError)):
                verify_inspection(copy)

    def test_cumulative_budget_refuses_write_before_disk_growth_and_reserves_capture(self):
        writer = InspectionWriter(self.root / "bounded")
        raw = {}
        with patch.object(runner, "MAX_TOTAL", 10):
            runner._keep(writer, raw, "first.bin", b"123456")
            with self.assertRaises(ContractError):
                runner._keep(writer, raw, "second.bin", b"78901")
            self.assertFalse((writer.root / "raw/second.bin").exists())
            runner._keep(writer, raw, "first.bin", b"123456")
            self.assertEqual(6, sum(map(len, raw.values())))
            writer.reserved_bytes = 4
            with self.assertRaises(ContractError):
                runner._keep(writer, raw, "third.bin", b"1")
            runner._keep(writer, raw, "github/final.bin", b"1234")
            self.assertEqual(0, writer.reserved_bytes)
        with patch.object(runner, "MAX_FILES", 5), self.assertRaises(ContractError):
            runner._keep(writer, raw, "fourth.bin", b"")

    def test_raw_path_escape_refused_before_creating_a_file(self):
        writer = InspectionWriter(self.root / "paths")
        for name in ("../../outside", "CON/file", "source/../secret", "source\\secret", "/absolute"):
            with self.subTest(name=name), self.assertRaises(ContractError):
                runner._keep(writer, {}, name, b"content")
        self.assertEqual([], list(writer.root.iterdir()))

    def test_report_direct_inventory_does_not_flatten_large_envelope_object_sets(self):
        # Inert storage entries test the report/envelope limit boundary only;
        # they are not a fabricated successful large Git source qualification.
        from delivery_review.composition import compose_report
        composition = compose_report(profile_bytes=self.fixture.profile_bytes, capture=self.capture,
            snapshot=self.fixture.snapshot, engine_executions={name: authored_engine(self.fixture, name)
                for name in ("checkwash", "pinwash", "ledgerwash")}, test_inputs={"pytest-linux": self.fixture.inputs()},
            report_id="authored-large-inventory", mode="live")
        raw = dict(composition.raw_files)
        raw.update({"execution-plan.json": b"{}", "trusted-root.jsonl": self.fixture.trusted_root})
        raw.update({f"source/objects/{i:040x}.zlib": b"inert storage boundary control" for i in range(1100)})
        writer = SimpleNamespace(root=self.root / "unwritten", seal=Mock())
        with patch.object(runner, "_keep", side_effect=lambda writer, raw, name, data: raw.__setitem__(name, data)):
            report = runner._publish(writer, composition, raw, collector=self.identity)
        self.assertLess(len(report["raw_inventory"]), 1000)
        self.assertIn("source/inventory.json", {row["path"] for row in report["raw_inventory"]})
        self.assertGreater(len(writer.seal.call_args.kwargs["files"]), 1100)


if __name__ == "__main__":
    unittest.main()
