"""Authored composition controls; no claim of real GitHub/crypto acceptance."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters import github_attestation as attest
from delivery_review.composition import compose_report
from delivery_review.contracts import ContractError, canonical_bytes, validate_report
from delivery_review.engine_runner import EngineExecution
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.reporting import render_report_html, render_report_markdown
from delivery_review.test_evidence import EventSourceFact, TestEvidenceInputs, bind_test_evidence
from test_engine_adapters import SUBJECT, control, raw
from test_test_evidence import AuthoredInspection, digest


def authored_engine(fixture, name, *, block=False, exit_code=None, termination="exited", malformed=False,
                    gaps=False, absent=False, integrity=False, added_exemption=False):
    args, native, coverage = control(name, block=block)
    replacements = {SUBJECT["merge_base_sha"]: fixture.snapshot.merge_base_sha,
                    SUBJECT["head_sha"]: fixture.snapshot.head_sha,
                    SUBJECT["base_tip_sha"]: fixture.snapshot.base_tip_sha}
    def substitute(value):
        serialized = json.dumps(value)
        for before, after in replacements.items():
            serialized = serialized.replace(before, after)
        if name == "checkwash":
            serialized = serialized.replace('"' + SUBJECT["merge_base_sha"][:7] + '"', '"' + fixture.snapshot.merge_base_sha[:7] + '"')
            serialized = serialized.replace('"' + SUBJECT["head_sha"][:7] + '"', '"' + fixture.snapshot.head_sha[:7] + '"')
        return json.loads(serialized)
    args, native, coverage = substitute(args), substitute(native), substitute(coverage)
    if name == "ledgerwash":
        native["run"]["require_complete"] = True
        args["process"]["argv"].append("--require-complete")
    if added_exemption:
        from test_engine_adapters import finding
        native["findings"] = [finding(name, rule="EXEMPTION_ADDED", severity="warn")]
        native["summary"]["warn"] = 1
    if gaps and name == "pinwash":
        native["unknown_coverage"] = [{"path": "authored.py", "reason": "authored unknown coverage"}]
    if absent:
        native["residuals"] = [{"path": path, "reason": reason} for path, reason in (
            ("governance/tasks", "no task records found at epoch"), ("evidence/task-operations", "no operation receipts found at epoch"))]
        native["coverage"]["complete"] = False
        native["coverage"]["counts"] = {"verified": 0, "unverified": 2, "mismatch": 0}
        for row in native["coverage"]["checks"]:
            row["status"] = "unverified"
        native["verdict"] = "block"
        args["process"]["exit_code"] = 1
    if exit_code is not None or termination != "exited":
        args["process"].update(exit_code=exit_code, termination=termination)
    files = {"stdout": b"invalid-json" if malformed else raw(native), "stderr": b"authored native stderr"}
    if coverage is not None:
        files["coverage"] = raw(coverage)
    native_policy = dict(fixture.profile["engine_options"][name], evaluation_date="2026-11-06")
    manifest = {"kind": "delivery-review-engine-execution", "execution_version": 1,
        "engine": {"name": name, "version": args["engine_version"], "wheel_sha256": args["wheel_sha256"]},
        "subject": args["subject"], "source_inventory_sha256": digest(fixture.snapshot.inventory_bytes),
        "source_integrity": {"before": "verified", "after": "failed" if integrity else "verified"},
        "installation_integrity": {"before": "verified", "after": "verified"},
        "controller_integrity_errors": ["source_post_integrity_failed"] if integrity else [],
        "native_policy_sha256": digest(canonical_bytes(native_policy)), "process": args["process"],
        "streams": {key: {"path": "raw/" + key, "size_bytes": len(value), "sha256": digest(value)} for key, value in files.items()}}
    return EngineExecution(fixture.root, canonical_bytes(manifest), MappingProxyType(files), b'{"status":"untrusted-saved-flag"}')


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = AuthoredInspection(Path(self.temp.name))
        self.engines = {name: authored_engine(self.fixture, name) for name in ("checkwash", "pinwash", "ledgerwash")}
        self.trust = patch.object(attest, "TRUSTED_ROOT_PINS", {digest(self.fixture.trusted_root)})
        self.trust.start()
        self.addCleanup(self.trust.stop)

    def compose(self, **changes):
        values = dict(profile_bytes=self.fixture.profile_bytes, capture=self.fixture.capture_fixture.seal(), snapshot=self.fixture.snapshot,
            engine_executions=self.engines, report_id="authored-composition", mode="live")
        if "test_inputs" not in changes:
            values["test_inputs"] = {"pytest-linux": self.fixture.inputs()}
        values.update(changes)
        return compose_report(**values)

    def test_authored_complete_control_is_schema_valid_and_raw_traceable(self):
        result = self.compose()
        self.assertEqual({"evidence": "complete", "freshness": "current", "policy": "clear"}, result.report["state"])
        self.assertTrue(result.report["ready_for_review"])
        validate_report(result.report, self.fixture.profile["policy"])
        for entry in result.report["raw_inventory"]:
            self.assertEqual(entry["sha256"], digest(result.raw_files[entry["path"]]))
        self.assertIn("known_checkwash_419_escape", result.report["limitations"])

    def test_offline_same_inputs_preserve_evidence_policy_and_time_but_never_freshness(self):
        first = self.compose()
        replay = self.compose(mode="offline_replay")
        again = self.compose(mode="offline_replay")
        self.assertEqual(first.report["obligations"], replay.report["obligations"])
        self.assertEqual(first.report["observed_at"], replay.report["observed_at"])
        self.assertEqual("unknown", replay.report["state"]["freshness"])
        self.assertEqual(2, replay.report["exit_code"])
        self.assertEqual(canonical_bytes(replay.report), canonical_bytes(again.report))

    def test_known_native_block_survives_another_engine_timeout_or_invalid_json(self):
        self.engines["checkwash"] = authored_engine(self.fixture, "checkwash", block=True)
        for kwargs in ({"termination": "timeout"}, {"exit_code": 2}, {"malformed": True}):
            self.engines["pinwash"] = authored_engine(self.fixture, "pinwash", **kwargs)
            result = self.compose()
            self.assertEqual({"evidence": "error", "freshness": "current", "policy": "block"}, result.report["state"])
            self.assertEqual(2, result.report["exit_code"])
            self.assertEqual("block", result.report["engines"][0]["verdict"])
            self.assertEqual(1, len(result.report["engines"][0]["findings"]))

    def test_same_engine_native_block_survives_post_execution_integrity_failure(self):
        self.engines["checkwash"] = authored_engine(self.fixture, "checkwash", block=True, integrity=True)
        result = self.compose()
        engine = next(row for row in result.report["engines"] if row["name"] == "checkwash")
        self.assertEqual(("error", "not_judged"), (engine["status"], engine["verdict"]))
        self.assertEqual(1, len(engine["findings"]))
        self.assertEqual("block", result.report["state"]["policy"])

    def test_required_coverage_gap_prevents_complete_without_erasing_block(self):
        self.engines["pinwash"] = authored_engine(self.fixture, "pinwash", block=True, gaps=True)
        result = self.compose()
        self.assertEqual("incomplete", result.report["state"]["evidence"])
        self.assertEqual("block", result.report["state"]["policy"])
        self.assertIn("authored unknown coverage", result.report["engines"][1]["coverage"]["details"][0])

    def test_native_added_exemption_keeps_verdict_and_adds_review(self):
        self.engines["pinwash"] = authored_engine(self.fixture, "pinwash", added_exemption=True)
        result = self.compose()
        engine = next(row for row in result.report["engines"] if row["name"] == "pinwash")
        self.assertEqual("pass", engine["verdict"])
        self.assertEqual("review", result.report["state"]["policy"])
        self.assertEqual(1, result.report["exit_code"])

    def test_optional_absence_does_not_block_even_strict_native_absence_verdict(self):
        self.engines["ledgerwash"] = authored_engine(self.fixture, "ledgerwash", absent=True)
        result = self.compose()
        self.assertEqual("complete", result.report["state"]["evidence"])
        self.assertEqual("clear", result.report["state"]["policy"])
        self.assertEqual("block", result.engine_observations["ledgerwash"]["parsed_native"]["verdict"])
        self.assertEqual("not_provided", result.engine_observations["ledgerwash"]["status"])
        self.fixture.profile["policy"]["requirements"][-1]["required"] = True
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        result = self.compose()
        self.assertEqual("incomplete", result.report["state"]["evidence"])

    def test_missing_artifact_never_becomes_complete_from_green_jobs(self):
        result = self.compose(test_inputs={})
        self.assertEqual("incomplete", result.report["state"]["evidence"])
        self.assertIn("artifact_missing", result.report["obligations"][0]["reason_codes"])

    def test_test_failure_is_complete_block_zero_and_allskip_incomplete(self):
        for values, native, evidence_state, policy in (({"passed": 0, "failed": 1}, 1, "complete", "block"),
            ({"passed": 0}, 5, "incomplete", "clear"), ({"passed": 0, "skipped": 1}, 0, "incomplete", "clear")):
            self.fixture.junit = self.fixture.xml(**values)
            self.fixture.receipt["execution"]["test_exit"] = native
            self.fixture.rebuild()
            result = self.compose()
            self.assertEqual((evidence_state, policy), (result.report["state"]["evidence"], result.report["state"]["policy"]))

    def test_head_base_and_attempt_drift_are_stale(self):
        for field in ("head", "base", "attempt"):
            fixture = self.fixture.capture_fixture
            original = fixture.data()
            if field in {"head", "base"}:
                record = fixture.find("pull_request", "final")
                value = fixture.payload(record)
                value[field]["sha"] = "f" * 40
                fixture.set_payload(record, value)
            else:
                record = fixture.find("run", "final")
                value = fixture.payload(record)
                value["run_attempt"] = 2
                fixture.set_payload(record, value)
            result = self.compose()
            self.assertEqual("stale", result.report["state"]["freshness"], result.report["limitations"])
            self.assertFalse(result.report["ready_for_review"])
            # Restore exact authored inputs without mutating a saved report.
            fixture.records, fixture.bodies = deepcopy(original[0]["records"]), dict(original[2])

    def test_unconfirmed_final_recheck_cannot_claim_current(self):
        record = self.fixture.capture_fixture.find("pull_request", "final")
        self.fixture.capture_fixture.unavailable(record)
        result = self.compose()
        self.assertEqual("unknown", result.report["state"]["freshness"])

    def test_original_profile_and_policy_date_are_independent_of_native_saved_claim(self):
        execution = self.engines["checkwash"]
        manifest = execution.manifest
        manifest["native_policy_sha256"] = "f" * 64
        self.engines["checkwash"] = replace(execution, manifest_bytes=canonical_bytes(manifest))
        with self.assertRaisesRegex(ContractError, "engine_execution_policy_mismatch"):
            self.compose()

    def test_full_reports_render_escaped_html_and_include_native_details(self):
        result = self.compose()
        self.assertIn("Delivery", render_report_markdown(result.report, self.fixture.profile["policy"]))
        self.assertIn("Content-Security-Policy", render_report_html(result.report, self.fixture.profile["policy"]))
        report = deepcopy(result.report)
        report["limitations"].append('<script>alert("authored")</script>')
        html = render_report_html(report, self.fixture.profile["policy"])
        self.assertNotIn('<script>alert', html)
        self.assertIn('&lt;script&gt;', html)

    def test_qualified_merge_checkout_coexists_with_head_bound_static_analysis(self):
        value, snapshot = self.fixture.objects, self.fixture.snapshot
        merge = value.commit(value.files({"README.txt": b"authored qualified merge"}), [snapshot.base_tip_sha, snapshot.head_sha])
        event = EventSourceFact(value.objects[merge])
        for phase in ("initial", "final"):
            row = self.fixture.capture_fixture.find("pull_request", phase)
            document = self.fixture.capture_fixture.payload(row)
            document["merge_commit_sha"] = merge
            self.fixture.capture_fixture.set_payload(row, document)
        self.fixture.profile["policy"]["allowed_source_modes"] = ["github_merge"]
        self.fixture.profile["policy"]["requirements"][:0] = [
            {"id": "source", "kind": "source", "required": True, "minimum_binding": "reported", "selector": {}},
            {"id": "ci", "kind": "ci_job", "required": True, "minimum_binding": "platform_bound",
             "selector": {"workflow_id": 101, "app_id": 15368, "job_name": "pytest-linux"}}]
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        self.fixture.receipt["source"].update(mode="github_merge", checked_out_sha=merge,
                                            merge_parents=[snapshot.base_tip_sha, snapshot.head_sha])
        qualification = json.loads(self.fixture.qualification_bytes)
        qualification["source_modes"] = ["github_merge"]
        qbytes = canonical_bytes(qualification)
        self.fixture.qualification = replace(self.fixture.qualification, source_modes=("github_merge",),
            qualification_receipt_bytes=qbytes, qualification_receipt_sha256=digest(qbytes))
        self.fixture.rebuild()
        args = self.fixture.args(event_source=event)
        expected = bind_test_evidence(**args).diagnostics["attestation_expected"]
        proof = self.fixture.fake_attestation(expected)
        inputs = TestEvidenceInputs(**{key: value for key, value in args.items()
            if key not in {"profile_bytes", "capture", "snapshot", "requirement_id"}}, attestation=proof)
        result = self.compose(test_inputs={"pytest-linux": inputs})
        self.assertEqual({"evidence": "complete", "freshness": "current", "policy": "clear"}, result.report["state"])
        for evidence in result.report["evidence"]:
            self.assertEqual(snapshot.head_sha if evidence["kind"] in {"scanner", "ledger"} else merge, evidence["subject_sha"])
        self.assertEqual([snapshot.base_tip_sha, snapshot.head_sha], result.report["subject"]["merge_parents"])


if __name__ == "__main__":
    unittest.main()
