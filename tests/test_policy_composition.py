"""Composed policy-change controls with authored GitHub/crypto envelopes."""
from copy import deepcopy
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
from test_composition import authored_engine
from test_policy_change import source
from test_test_evidence import AuthoredInspection, digest


class PolicyCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = AuthoredInspection(Path(self.temp.name))
        profile = self.fixture.profile
        profile["profile_version"] = 2
        profile["policy_input"] = {"format": "delivery-review-policy-v1", "path": "policy.json"}
        profile["policy"]["requirements"].append({"id": "policy-diff", "kind": "policy_change", "required": True,
            "minimum_binding": "reported", "selector": {}})
        self.fixture.profile_bytes = seal_inspection_profile(profile)
        self.fixture.profile = loads_strict(self.fixture.profile_bytes)
        self.original = deepcopy(self.fixture.profile["policy"])
        trust = patch.object(attest, "TRUSTED_ROOT_PINS", {digest(self.fixture.trusted_root)})
        trust.start()
        self.addCleanup(trust.stop)

    def prepare(self, before, after):
        fixture = self.fixture
        old = fixture.snapshot
        snapshot = source(fixture.root / "policy-source", before, after)
        capture = fixture.capture_fixture
        for record in capture.records:
            raw = capture.bodies[record["body"]["path"]]
            raw = raw.replace(old.head_sha.encode(), snapshot.head_sha.encode()).replace(old.base_tip_sha.encode(), snapshot.base_tip_sha.encode())
            capture.set_payload(record, raw)
            record["request"]["path"] = record["request"]["path"].replace(old.head_sha, snapshot.head_sha)
        fixture.snapshot = snapshot
        fixture.receipt["source"].update(head_sha=snapshot.head_sha, base_tip_sha=snapshot.base_tip_sha,
            checked_out_sha=snapshot.head_sha)
        fixture.rebuild()

    def compose(self, *, block=False, mode="live"):
        fixture = self.fixture
        return compose_report(profile_bytes=fixture.profile_bytes, capture=fixture.capture_fixture.seal(),
            snapshot=fixture.snapshot, engine_executions={name: authored_engine(fixture, name, block=block and name == "checkwash")
                for name in ("checkwash", "pinwash", "ledgerwash")},
            test_inputs={"pytest-linux": fixture.inputs()}, report_id="authored-policy-change", mode=mode)

    def test_e05_frozen_review_tuple_from_original_static_policy_change(self):
        relaxed = deepcopy(self.original)
        relaxed["requirements"][0]["selector"]["min_tests"] = 0
        relaxed["requirements"] = [row for row in relaxed["requirements"] if row["id"] != "checkwash"]
        self.prepare(canonical_bytes(self.original), canonical_bytes(relaxed))
        result = self.compose()
        expected = loads_strict((ROOT / "fixtures/cases/E05-policy-relaxation.json").read_bytes())["expected"]
        self.assertEqual(expected["state"], result.report["state"])
        self.assertEqual(expected["exit_code"], result.report["exit_code"])
        obligation = next(row for row in result.report["obligations"] if row["id"] == "policy-diff")
        self.assertEqual(expected["reason_codes"], obligation["reason_codes"])
        self.assertEqual("satisfied", obligation["outcome"])
        self.assertTrue(any(row["id"] == "checkwash" for row in result.report["obligations"]))
        self.assertEqual(self.original, self.fixture.profile["policy"])
        validate_report(result.report, self.original)
        for entry in result.report["raw_inventory"]:
            self.assertEqual(entry["sha256"], digest(result.raw_files[entry["path"]]))

    def test_unchanged_policy_is_clear_and_offline_replay_never_renews_freshness(self):
        self.prepare(canonical_bytes(self.original) + b"\r\n", canonical_bytes(self.original) + b"\n")
        live = self.compose()
        replay = self.compose(mode="offline_replay")
        again = self.compose(mode="offline_replay")
        self.assertEqual({"evidence": "complete", "freshness": "current", "policy": "clear"}, live.report["state"])
        self.assertEqual("unknown", replay.report["state"]["freshness"])
        self.assertEqual(canonical_bytes(replay.report), canonical_bytes(again.report))
        self.assertEqual(live.report["observed_at"], replay.report["observed_at"])

    def test_malformed_proposal_keeps_prior_real_native_block(self):
        self.prepare(canonical_bytes(self.original), b'{"bad":"<script>untrusted</script>"}')
        result = self.compose(block=True)
        self.assertEqual({"evidence": "error", "freshness": "current", "policy": "block"}, result.report["state"])
        self.assertEqual(2, result.report["exit_code"])
        self.assertEqual("block", next(row for row in result.report["engines"] if row["name"] == "checkwash")["verdict"])
        obligation = next(row for row in result.report["obligations"] if row["id"] == "policy-diff")
        self.assertEqual(["policy_document_invalid"], obligation["reason_codes"])
        self.assertEqual(b'{"bad":"<script>untrusted</script>"}', result.raw_files["policy-change/head.json"])

    def test_declared_but_absent_policy_is_incomplete_not_synthetic_no_change(self):
        self.prepare(None, None)
        result = self.compose()
        self.assertEqual({"evidence": "incomplete", "freshness": "current", "policy": "clear"}, result.report["state"])
        obligation = next(row for row in result.report["obligations"] if row["id"] == "policy-diff")
        self.assertEqual("missing", obligation["outcome"])
        self.assertEqual(["policy_proposal_missing"], obligation["reason_codes"])


if __name__ == "__main__":
    unittest.main()
