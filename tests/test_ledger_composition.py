"""Authored full-composer ledger controls; API and cryptography are mocks."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters import github_attestation as attest
from delivery_review.composition import compose_report
from delivery_review.contracts import loads_strict, validate_report
from delivery_review.inspection_profile import seal_inspection_profile
from test_composition import authored_engine
from test_test_evidence import AuthoredInspection, digest


class LedgerCompositionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AuthoredInspection(Path(temporary.name))
        self.fixture.profile["profile_version"] = 2
        trust = patch.object(attest, "TRUSTED_ROOT_PINS", {digest(self.fixture.trusted_root)})
        trust.start()
        self.addCleanup(trust.stop)

    def compose(self, path, required=True, block=False, malformed=False):
        fixture = self.fixture
        fixture.profile["ledger_input"] = {"format": "external-authored-v7", "paths": [path]}
        fixture.profile["policy"]["requirements"][-1]["required"] = required
        fixture.profile_bytes = seal_inspection_profile(fixture.profile)
        fixture.profile = loads_strict(fixture.profile_bytes)
        engines = {name: authored_engine(fixture, name) for name in ("checkwash", "pinwash")}
        engines["ledgerwash"] = authored_engine(fixture, "ledgerwash", absent=not block,
            block=block, malformed=malformed)
        return compose_report(profile_bytes=fixture.profile_bytes, capture=fixture.capture_fixture.seal(),
            snapshot=fixture.snapshot, engine_executions=engines,
            test_inputs={"pytest-linux": fixture.inputs()}, report_id="authored-ledger-composition", mode="live")

    def test_required_unknown_format_matches_frozen_axes_and_retains_source_proof(self):
        result = self.compose("README.txt")
        expected = loads_strict((ROOT / "fixtures/cases/E15-unsupported-required-ledger.json").read_bytes())["expected"]
        self.assertEqual(expected["state"], result.report["state"])
        self.assertEqual(expected["exit_code"], result.report["exit_code"])
        obligation = next(row for row in result.report["obligations"] if row["kind"] == "ledger")
        self.assertEqual("unsupported", obligation["outcome"])
        self.assertTrue(set(expected["reason_codes"]) <= set(obligation["reason_codes"]))
        receipt = loads_strict(result.raw_files["ledger/input.json"])
        self.assertEqual(self.fixture.snapshot.head_sha, receipt["head_sha"])
        self.assertTrue(receipt["files"][0]["present"])
        self.assertIn("ledger/input.json", {row["path"] for row in result.report["raw_inventory"]})
        self.assertEqual("block", result.engine_observations["ledgerwash"]["parsed_native"]["verdict"])
        validate_report(result.report, self.fixture.profile["policy"])

    def test_missing_declared_ledger_remains_distinct_with_optional_and_required_dispositions(self):
        for required, case in ((False, "optional-no-ledger"), (True, "required-no-ledger")):
            with self.subTest(required=required):
                result = self.compose("missing.custom", required=required)
                expected = loads_strict((ROOT / ("fixtures/cases/E15-" + case + ".json")).read_bytes())["expected"]
                self.assertEqual(expected["state"], result.report["state"])
                obligation = next(row for row in result.report["obligations"] if row["kind"] == "ledger")
                self.assertTrue(set(expected["reason_codes"]) <= set(obligation["reason_codes"]))
                self.assertEqual("not_provided", result.engine_observations["ledgerwash"]["status"])

    def test_declaration_never_erases_native_error_or_blocking_findings(self):
        blocked = self.compose("README.txt", block=True)
        self.assertEqual("block", blocked.report["state"]["policy"])
        self.assertTrue(blocked.engine_observations["ledgerwash"]["engine"]["findings"])
        errored = self.compose("README.txt", malformed=True)
        self.assertEqual("error", errored.report["state"]["evidence"])
        self.assertEqual("error", errored.engine_observations["ledgerwash"]["status"])


if __name__ == "__main__":
    unittest.main()
