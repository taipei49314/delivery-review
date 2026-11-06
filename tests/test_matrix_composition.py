"""Authored raw-API/crypto envelopes for matrix binding, not E06 execution."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters import github_attestation as attest
from delivery_review.composition import compose_report
from delivery_review.contracts import canonical_bytes, loads_strict, validate_report
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.job_matrix import RUNTIME
from delivery_review.test_evidence import bind_test_evidence
from test_composition import authored_engine
from test_test_evidence import AuthoredInspection, digest


class MatrixCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = AuthoredInspection(Path(self.temp.name))
        self.trust = patch.object(attest, "TRUSTED_ROOT_PINS", {digest(self.fixture.trusted_root)})
        self.trust.start()
        self.addCleanup(self.trust.stop)

    def configure(self, *, versions=("3.11",), observed="3.11", runtime=True, ci=True):
        fixture = self.fixture
        profile = fixture.profile
        profile["profile_version"] = 2
        first = next(row for row in profile["policy"]["requirements"] if row["kind"] == "test_report")
        profile["policy"]["requirements"].remove(first)
        producer = profile["producers"].pop(first["id"])
        cells = []
        for index, version in enumerate(versions):
            test = deepcopy(first)
            test["id"] = "pytest-linux" if index == 0 else "pytest-" + version
            test["selector"]["matrix"] = {"python": version}
            profile["policy"]["requirements"].append(test)
            profile["producers"][test["id"]] = deepcopy(producer)
            cells.append({key: deepcopy(test["selector"][key]) for key in ("workflow_id", "job_name", "matrix")} |
                {"platform_job_name": "authored measured cell (" + version + ")"})
            if ci:
                profile["policy"]["requirements"].append({"id": "ci-" + version, "kind": "ci_job", "required": True,
                    "minimum_binding": "platform_bound", "selector": {
                        key: deepcopy(test["selector"][key]) for key in ("workflow_id", "app_id", "job_name", "matrix")}})
        profile["job_matrix"] = {"mapping_version": 1, "cells": cells}
        capture = fixture.capture_fixture
        capture.profile["workflows"][0]["job_names"] = [row["platform_job_name"] for row in cells]
        physical = "authored measured cell (" + observed + ")"
        jobs = capture.find("jobs")
        body = capture.payload(jobs)
        body["jobs"][0]["name"] = physical
        capture.set_payload(jobs, body)
        check = capture.find("check_run")
        body = capture.payload(check)
        body["name"] = physical
        capture.set_payload(check, body)
        profile["github"] = deepcopy(capture.profile)
        fixture.profile_bytes = seal_inspection_profile(profile)
        fixture.profile = loads_strict(fixture.profile_bytes)
        if runtime:
            receipt = loads_strict(fixture.qualification.qualification_receipt_bytes)
            receipt["runtime"] = dict(RUNTIME)
            raw = canonical_bytes(receipt)
            fixture.qualification = replace(fixture.qualification, qualification_receipt_bytes=raw,
                qualification_receipt_sha256=digest(raw))

    def compose(self, *, inputs=True, mode="live"):
        fixture = self.fixture
        return compose_report(profile_bytes=fixture.profile_bytes, capture=fixture.capture_fixture.seal(),
            snapshot=fixture.snapshot, engine_executions={name: authored_engine(fixture, name)
                for name in ("checkwash", "pinwash", "ledgerwash")},
            test_inputs={"pytest-linux": fixture.inputs()} if inputs else {}, report_id="authored-matrix", mode=mode)

    def test_authenticated_fixed_runtime_supplies_logical_test_and_ci_identity(self):
        self.configure()
        result = self.compose()
        self.assertEqual({"evidence": "complete", "freshness": "current", "policy": "clear"}, result.report["state"])
        self.assertEqual(0, result.report["exit_code"])
        validate_report(result.report, self.fixture.profile["policy"])
        for row in result.report["evidence"]:
            if row["kind"] in {"test_report", "ci_job"}:
                self.assertEqual("pytest-linux", row["job_name"])
                self.assertEqual({"python": "3.11"}, row["matrix"])
        self.assertEqual("authored measured cell (3.11)", result.test_diagnostics["pytest-linux"]["matrix_binding"]["platform_job_name"])
        self.assertTrue(any(b"authored measured cell (3.11)" in raw for path, raw in result.raw_files.items() if path.startswith("github/")))
        self.assertTrue(any(path.endswith("matrix-binding.json") for path in result.raw_files))

    def test_e06_one_declared_cell_missing_retains_the_valid_cell(self):
        self.configure(versions=("3.11", "3.12"))
        result = self.compose()
        self.assertEqual({"evidence": "incomplete", "freshness": "current", "policy": "clear"}, result.report["state"])
        self.assertEqual(2, result.report["exit_code"])
        obligations = {row["id"]: row for row in result.report["obligations"]}
        self.assertEqual("satisfied", obligations["pytest-linux"]["outcome"])
        self.assertEqual("satisfied", obligations["ci-3.11"]["outcome"])
        for identity in ("pytest-3.12", "ci-3.12"):
            self.assertEqual("missing", obligations[identity]["outcome"])
            self.assertIn("required_matrix_cell_missing", obligations[identity]["reason_codes"])
        missing = next(row for row in result.report["evidence"] if row["id"] == "e-pytest-3.12")
        self.assertEqual({}, missing["matrix"])
        self.assertIsNone(missing["job_name"])

    def test_authentic_311_producer_does_not_establish_job_named_312(self):
        self.configure(versions=("3.12",), observed="3.12")
        result = self.compose()
        test = next(row for row in result.report["obligations"] if row["id"] == "pytest-linux")
        self.assertEqual("untrusted", test["outcome"])
        self.assertIn("matrix_runtime_mismatch", test["reason_codes"])
        for row in result.report["evidence"]:
            if row["kind"] in {"test_report", "ci_job"}:
                self.assertEqual({}, row["matrix"])
                self.assertEqual("authored measured cell (3.12)", row["job_name"])
        self.assertEqual("incomplete", result.report["state"]["evidence"])

    def test_historical_qualification_without_runtime_cannot_upgrade(self):
        self.configure(runtime=False)
        result = self.compose()
        obligations = {row["id"]: row for row in result.report["obligations"]}
        for identity in ("pytest-linux", "ci-3.11"):
            self.assertEqual("untrusted", obligations[identity]["outcome"])
            self.assertIn("matrix_runtime_unconfirmed", obligations[identity]["reason_codes"])
        self.assertFalse(result.report["ready_for_review"])

    def test_release_runtime_receipt_without_actual_crypto_cannot_supply_matrix(self):
        self.configure()
        result = bind_test_evidence(**self.fixture.args())
        self.assertEqual("untrusted", result.obligation["outcome"])
        self.assertIn("attestation_missing", result.obligation["reason_codes"])
        self.assertEqual({}, result.evidence["matrix"])
        self.assertEqual("reported", result.evidence["binding_level"])
        self.assertNotIn("matrix_binding", result.diagnostics)

    def test_incomplete_job_inventory_cannot_establish_cell_absence(self):
        self.configure(versions=("3.11", "3.12"))
        capture = self.fixture.capture_fixture
        record = capture.find("jobs")
        body = capture.payload(record)
        body["total_count"] = 2
        capture.set_payload(record, body)
        result = self.compose(inputs=False)
        for row in result.report["obligations"]:
            if row["kind"] in {"test_report", "ci_job"}:
                self.assertIn("job_pagination_unconfirmed", row["reason_codes"])
                self.assertNotIn("required_matrix_cell_missing", row["reason_codes"])
        self.assertEqual("incomplete", result.report["state"]["evidence"])

    def test_missing_matrix_proof_does_not_clear_observed_native_failure(self):
        self.configure(runtime=False)
        capture = self.fixture.capture_fixture
        for role in ("jobs", "check_run"):
            record = capture.find(role)
            body = capture.payload(record)
            target = body["jobs"][0] if role == "jobs" else body
            target["conclusion"] = "failure"
            capture.set_payload(record, body)
        result = self.compose(inputs=False)
        self.assertEqual({"evidence": "incomplete", "freshness": "current", "policy": "block"}, result.report["state"])
        ci = next(row for row in result.report["obligations"] if row["id"] == "ci-3.11")
        self.assertEqual("untrusted", ci["outcome"])
        self.assertEqual("block", ci["effect"])

    def test_same_v2_inputs_replay_deterministically_without_renewing_freshness(self):
        self.configure()
        first = self.compose(mode="offline_replay")
        second = self.compose(mode="offline_replay")
        self.assertEqual(canonical_bytes(first.report), canonical_bytes(second.report))
        self.assertEqual({"evidence": "complete", "freshness": "unknown", "policy": "clear"}, first.report["state"])
        self.assertEqual(2, first.report["exit_code"])


if __name__ == "__main__":
    unittest.main()
