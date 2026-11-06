"""Authored raw API controls for the independently resolved listing boundary."""
from pathlib import Path
import tempfile
import unittest

from test_test_evidence import AuthoredInspection
from delivery_review.composition import compose_report
from delivery_review.inspection_profile import seal_inspection_profile
from delivery_review.providers.github_observations import map_capture

NEW = "scoped_original_refusals_v1"
OLD = "legacy_missing_v1"


class ResolvedListingAppRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.fixture = AuthoredInspection(Path(temporary.name))
        requirement = self.fixture.profile["policy"]["requirements"][0]
        self.fixture.profile["policy"]["requirements"].append({"id": "ci", "kind": "ci_job", "required": True,
            "minimum_binding": "platform_bound", "selector": {key: requirement["selector"][key]
                for key in ("workflow_id", "app_id", "job_name")}})
        self.profile(2)
        self.change("check_run", lambda value: value["app"].update(id=999999))

    def profile(self, version):
        self.fixture.profile["profile_version"] = version
        self.fixture.profile_bytes = seal_inspection_profile(self.fixture.profile)

    def change(self, role, mutate, phase="initial"):
        fixture = self.fixture.capture_fixture
        row = fixture.find(role, phase)
        value = fixture.payload(row)
        mutate(value)
        fixture.set_payload(row, value)

    def omit_listing(self, absent=False):
        def mutate(value):
            for row in value["workflow_runs"]:
                if absent:
                    row.pop("pull_requests")
                else:
                    row["pull_requests"] = []
        for phase in ("initial", "final"):
            self.change("workflow_runs", mutate, phase)

    def compose(self, basis):
        return compose_report(profile_bytes=self.fixture.profile_bytes, capture=self.fixture.capture_fixture.seal(),
            snapshot=self.fixture.snapshot, engine_executions={}, test_inputs={}, report_id="authored-app-refusal",
            mode="live", evidence_selection_basis=basis)

    def test_resolved_omission_keeps_numeric_wrong_app_block_only_for_new_v2(self):
        for absent in (False, True):
            with self.subTest(absent=absent):
                if absent:
                    # Reset both authored listings before removing the field.
                    for phase in ("initial", "final"):
                        self.change("workflow_runs", lambda value: value["workflow_runs"][0].update(pull_requests=[]), phase)
                self.omit_listing(absent)
                observations = map_capture(self.fixture.capture_fixture.seal())
                workflow = observations["workflows"][0]
                self.assertEqual("resolved", workflow["selection"]["listing_association"]["resolution"])
                self.assertIn("pr_association_unconfirmed", workflow["reasons"])
                for version, basis, expected in ((1, OLD, ("untrusted", "clear")), (1, NEW, ("untrusted", "clear")),
                        (2, OLD, ("untrusted", "clear")), (2, NEW, ("mismatch", "block"))):
                    with self.subTest(profile=version, basis=basis):
                        self.profile(version)
                        result = self.fixture.bound(artifact=None, evidence_selection_basis=basis)
                        self.assertEqual(expected, (result.obligation["outcome"], result.obligation["effect"]))
                        self.assertEqual(expected[0] == "mismatch", "check_app_mismatch" in result.obligation["reason_codes"])
                        self.assertIsNone(result.diagnostics["attestation_expected"])
                        composed = self.compose(basis)
                        for identity in ("pytest-linux", "ci"):
                            obligation = next(row for row in composed.report["obligations"] if row["id"] == identity)
                            self.assertEqual(expected, (obligation["outcome"], obligation["effect"]))
                        self.assertEqual(expected[1], composed.report["state"]["policy"])
                        raw = self.fixture.capture_fixture.find("check_run")["body"]["path"]
                        self.assertIn(self.fixture.capture_fixture.bodies[raw], composed.raw_files.values())

    def test_unresolved_or_conflicting_details_cannot_become_a_scoped_app_block(self):
        self.omit_listing()
        fixture = self.fixture.capture_fixture
        for role, change in (("run", lambda row: row.pop("pull_requests")),
                ("attempt", lambda row: row.pop("pull_requests")),
                ("attempt", lambda row: row.update(check_suite_id=999999)),
                ("run", lambda row: row.update(head_sha="e" * 40)),
                ("attempt", lambda row: row.update(run_attempt=2))):
            row = fixture.find(role)
            original = fixture.bodies[row["body"]["path"]]
            with self.subTest(role=role, mutation=change):
                self.change(role, change)
                observed = map_capture(fixture.seal())["workflows"][0]
                self.assertNotEqual("resolved", observed["selection"]["listing_association"]["resolution"])
                result = self.fixture.bound(artifact=None, evidence_selection_basis=NEW)
                self.assertEqual("clear", result.obligation["effect"])
                self.assertNotIn("check_app_mismatch", result.obligation["reason_codes"])
            fixture.set_payload(row, original)

    def test_unresolved_listing_or_additional_check_conflict_remains_untrusted(self):
        self.omit_listing()
        fixture = self.fixture.capture_fixture
        row = fixture.find("workflow_runs")
        original = fixture.bodies[row["body"]["path"]]
        for value in (None, [{"number": 8}]):
            with self.subTest(listing=value):
                self.change("workflow_runs", lambda payload: payload["workflow_runs"][0].update(pull_requests=value))
                result = self.fixture.bound(artifact=None, evidence_selection_basis=NEW)
                self.assertEqual("clear", result.obligation["effect"])
                self.assertNotIn("check_app_mismatch", result.obligation["reason_codes"])
        fixture.set_payload(row, original)
        self.change("check_run", lambda payload: payload.update(head_sha="e" * 40))
        result = self.fixture.bound(artifact=None, evidence_selection_basis=NEW)
        self.assertEqual("clear", result.obligation["effect"])
        self.assertNotIn("check_app_mismatch", result.obligation["reason_codes"])

    def test_legacy_mapper_does_not_gain_a_resolution_from_new_execution_basis(self):
        self.omit_listing()
        self.fixture.capture_fixture.mapper_version = 1
        result = self.fixture.bound(artifact=None, evidence_selection_basis=NEW)
        self.assertEqual(("untrusted", "clear"), (result.obligation["outcome"], result.obligation["effect"]))
        self.assertNotIn("check_app_mismatch", result.obligation["reason_codes"])


if __name__ == "__main__":
    unittest.main()
