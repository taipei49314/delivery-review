"""Authored composition controls; no claim of real GitHub/crypto acceptance."""
from copy import deepcopy
from dataclasses import replace
import json
import io
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType
import unittest
import zipfile
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
from test_test_evidence import AuthoredInspection, digest, fact


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

    def test_v2_frozen_negative_dispositions_are_derived_from_original_api_and_archive_bytes(self):
        """Authored raw controls exercise the real binder, not saved verdicts."""
        cases = ("E07-required-timeout", "E07-required-skipped", "E07-required-neutral", "E07-required-cancelled",
                 "E08-wrong-check-app", "E08-ambiguous-same-name", "E10-artifact-digest-mismatch",
                 "E10-cross-attempt-receipt", "E10-self-consistent-untrusted-producer",
                 "E16-archive-traversal", "E16-xml-entity")
        for version in (1, 2):
            for case_id in cases:
                with self.subTest(profile_version=version, case_id=case_id):
                    case_root = Path(self.temp.name) / (str(version) + case_id)
                    case_root.mkdir()
                    fixture = AuthoredInspection(case_root)
                    fixture.profile["profile_version"] = version
                    fixture.profile_bytes = seal_inspection_profile(fixture.profile)
                    inputs = fixture.inputs()
                    retained = []
                    if case_id.startswith("E07-"):
                        conclusion = {"timeout": "timed_out"}.get(case_id.rsplit("-", 1)[1], case_id.rsplit("-", 1)[1])
                        for role in ("jobs", "check_run"):
                            row = fixture.capture_fixture.find(role)
                            payload = fixture.capture_fixture.payload(row)
                            (payload["jobs"][0] if role == "jobs" else payload).update(status="completed", conclusion=conclusion)
                            fixture.capture_fixture.set_payload(row, payload)
                            retained.append(fixture.capture_fixture.bodies[row["body"]["path"]])
                        inputs = replace(inputs, artifact=None, attestation=None)
                    elif case_id == "E08-wrong-check-app":
                        row = fixture.capture_fixture.find("check_run")
                        payload = fixture.capture_fixture.payload(row)
                        payload["app"]["id"] = 99999
                        fixture.capture_fixture.set_payload(row, payload)
                        retained.append(fixture.capture_fixture.bodies[row["body"]["path"]])
                    elif case_id == "E08-ambiguous-same-name":
                        row = fixture.capture_fixture.find("jobs")
                        payload = fixture.capture_fixture.payload(row)
                        second = dict(payload["jobs"][0], id=2002,
                            check_run_url="https://api.github.com/repos/fixture-owner/fixture-repo/check-runs/4002")
                        payload["jobs"].append(second)
                        payload["total_count"] = 2
                        fixture.capture_fixture.set_payload(row, payload)
                        check = fixture.capture_fixture.payload(fixture.capture_fixture.find("check_run"))
                        fixture.capture_fixture.add("check_run", dict(check, id=4002), job_id=2002, check_run_id=4002)
                        retained.append(fixture.capture_fixture.bodies[row["body"]["path"]])
                    elif case_id == "E10-artifact-digest-mismatch":
                        metadata = dict(fixture.metadata, digest="sha256:" + "f" * 64)
                        inputs = replace(inputs, artifact_metadata=fact(inputs.artifact_metadata.request_path, metadata))
                        retained.extend((inputs.artifact_metadata.body_bytes, fixture.artifact.archive_bytes))
                    elif case_id == "E10-cross-attempt-receipt":
                        fixture.receipt["producer"]["attempt"] = 2
                        fixture.rebuild()
                        metadata = dict(fixture.metadata, name="delivery-review-1001-2-4001")
                        inputs = replace(inputs, artifact=fixture.artifact,
                            artifact_metadata=fact(inputs.artifact_metadata.request_path, metadata))
                        retained.append(fixture.artifact.archive_bytes)
                    elif case_id == "E10-self-consistent-untrusted-producer":
                        row = fixture.capture_fixture.find("attempt")
                        payload = fixture.capture_fixture.payload(row)
                        payload["referenced_workflows"] = []
                        fixture.capture_fixture.set_payload(row, payload)
                        retained.append(fixture.capture_fixture.bodies[row["body"]["path"]])
                    else:
                        xml = (b'<!DOCTYPE testsuites [<!ENTITY secret SYSTEM "file:///authored-sentinel">]>' + fixture.junit
                               if case_id == "E16-xml-entity" else fixture.junit)
                        stream = io.BytesIO()
                        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
                            archive.writestr("receipt.json", canonical_bytes(fixture.receipt))
                            archive.writestr("junit.xml", xml)
                            if case_id == "E16-archive-traversal":
                                archive.writestr("../authored-escape", b"must never be materialized")
                        candidate = stream.getvalue()
                        metadata = dict(fixture.metadata, digest="sha256:" + digest(candidate), size_in_bytes=len(candidate))
                        inputs = replace(inputs, artifact=None, archive_bytes=candidate,
                            artifact_metadata=fact(inputs.artifact_metadata.request_path, metadata))
                        retained.append(candidate)
                    result = compose_report(profile_bytes=fixture.profile_bytes, capture=fixture.capture_fixture.seal(),
                        snapshot=fixture.snapshot, engine_executions={name: authored_engine(fixture, name)
                            for name in ("checkwash", "pinwash", "ledgerwash")}, test_inputs={"pytest-linux": inputs},
                        report_id="authored-negative-" + case_id, mode="live")
                    frozen = json.loads((ROOT / "fixtures/cases" / (case_id + ".json")).read_bytes())["expected"]
                    state = dict(frozen["state"])
                    if version == 1:
                        state["policy"] = "clear"
                        if case_id in {"E10-cross-attempt-receipt", "E10-self-consistent-untrusted-producer"}:
                            state["evidence"] = "error"
                    self.assertEqual(state, result.report["state"])
                    self.assertEqual(frozen["exit_code"], result.report["exit_code"])
                    self.assertFalse(result.report["ready_for_review"])
                    if version == 2:
                        reasons = {reason for row in result.report["obligations"] for reason in row["reason_codes"]}
                        self.assertLessEqual(set(frozen["reason_codes"]), reasons)
                    for raw_bytes in retained:
                        self.assertIn(raw_bytes, result.raw_files.values())

    def test_v2_ci_timeout_and_wrong_app_use_the_same_confirmed_dispositions(self):
        self.fixture.profile["profile_version"] = 2
        test = self.fixture.profile["policy"]["requirements"][0]
        self.fixture.profile["policy"]["requirements"].append({"id": "ci", "kind": "ci_job", "required": True,
            "minimum_binding": "platform_bound", "selector": {key: test["selector"][key]
                for key in ("workflow_id", "app_id", "job_name")}})
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)
        for conclusion, app_id, outcome, reason in (("timed_out", 15368, "missing", "required_job_timeout"),
                                                    ("success", 99999, "mismatch", "check_app_mismatch")):
            with self.subTest(conclusion=conclusion, app_id=app_id):
                for role in ("jobs", "check_run"):
                    row = self.fixture.capture_fixture.find(role)
                    payload = self.fixture.capture_fixture.payload(row)
                    target = payload["jobs"][0] if role == "jobs" else payload
                    target.update(status="completed", conclusion=conclusion)
                    if role == "check_run":
                        target["app"]["id"] = app_id
                    self.fixture.capture_fixture.set_payload(row, payload)
                result = self.compose(test_inputs={})
                obligation = next(row for row in result.report["obligations"] if row["id"] == "ci")
                self.assertEqual((outcome, "block"), (obligation["outcome"], obligation["effect"]))
                self.assertIn(reason, obligation["reason_codes"])

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
