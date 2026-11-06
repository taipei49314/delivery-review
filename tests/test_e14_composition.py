"""E14 authored raw receipt/XML controls; not hosted signature qualification."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters import github_attestation as attest
from delivery_review.composition import compose_report
from delivery_review.inspection_profile import seal_inspection_profile
from test_composition import authored_engine
from test_test_evidence import AuthoredInspection, digest


class E14CompositionTests(unittest.TestCase):
    def test_authenticated_raw_counts_and_collection_phase_match_frozen_v2_states(self):
        with tempfile.TemporaryDirectory() as temporary:
            for version in (1, 2):
                for case, counts, native in (("E14-zero-tests", {"passed": 0}, 0),
                        ("E14-all-skipped", {"passed": 0, "skipped": 3}, 0),
                        ("E14-collection-error", {"passed": 0, "errors": 1}, 2)):
                    with self.subTest(profile_version=version, case=case):
                        directory = Path(temporary) / (str(version) + case)
                        directory.mkdir()
                        fixture = AuthoredInspection(directory)
                        fixture.profile["profile_version"] = version
                        fixture.profile_bytes = seal_inspection_profile(fixture.profile)
                        fixture.junit = fixture.xml(**counts)
                        if case == "E14-collection-error":
                            fixture.junit = fixture.junit.replace(b'<error message="authored">',
                                b'<error message="collection failure">')
                        fixture.receipt["execution"]["test_exit"] = native
                        fixture.rebuild()
                        with patch.object(attest, "TRUSTED_ROOT_PINS", {digest(fixture.trusted_root)}):
                            result = compose_report(profile_bytes=fixture.profile_bytes, capture=fixture.capture_fixture.seal(),
                                snapshot=fixture.snapshot, engine_executions={name: authored_engine(fixture, name)
                                    for name in ("checkwash", "pinwash", "ledgerwash")},
                                test_inputs={"pytest-linux": fixture.inputs()}, report_id="authored-" + case, mode="live")
                        expected = json.loads((ROOT / "fixtures/cases" / (case + ".json")).read_bytes())["expected"]
                        state = dict(expected["state"])
                        if version == 1 and case != "E14-collection-error":
                            state["policy"] = "clear"
                        self.assertEqual(state, result.report["state"])
                        self.assertEqual(expected["exit_code"], result.report["exit_code"])
                        self.assertFalse(result.report["ready_for_review"])
                        obligation = next(row for row in result.report["obligations"] if row["id"] == "pytest-linux")
                        if version == 2:
                            self.assertLessEqual(set(expected["reason_codes"]), set(obligation["reason_codes"]))
                        self.assertIn(fixture.junit, result.raw_files.values())
                        self.assertIn(fixture.artifact.archive_bytes, result.raw_files.values())


if __name__ == "__main__":
    unittest.main()
