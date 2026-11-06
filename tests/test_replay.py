"""Synthetic E17 storage/replay controls; live refresh remains untested."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import validate_report
from delivery_review.evidence.store import BundleError, create_bundle, verify_bundle
from delivery_review.evidence.replay import replay_bundle
from test_evidence_store import create_synthetic, digest, json_bytes, synthetic_documents, synthetic_engine_documents, tree_bytes


class OfflineReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.destination = self.root / "replay"

    def test_replay_preserves_observations_without_renewing_freshness(self):
        policy_raw, report_raw, raw_files = create_synthetic(self.source)
        original = json.loads(report_raw)
        before = tree_bytes(self.source)
        result = replay_bundle(self.source, self.destination)
        self.assertEqual("offline_replay", result["mode"])
        self.assertEqual(original["observed_at"], result["observed_at"])
        self.assertIsNone(result["recheck"])
        self.assertEqual({"evidence": "complete", "freshness": "unknown", "policy": "clear"}, result["state"])
        self.assertIs(result["ready_for_review"], False)
        self.assertEqual(2, result["exit_code"])
        self.assertNotEqual(original["report_id"], result["report_id"])
        for key in ("subject", "policy", "evidence", "obligations", "engines", "selected_runs", "raw_inventory", "action_items"):
            self.assertEqual(original[key], result[key], key)
        self.assertEqual(original["limitations"], result["limitations"])
        verified = verify_bundle(self.destination)
        self.assertEqual(policy_raw, verified.policy_bytes)
        self.assertEqual(raw_files, verified.raw_files)
        self.assertEqual(result, verified.report)
        validate_report(result, json.loads(policy_raw))
        self.assertEqual(before, tree_bytes(self.source))
        derived_from = json.loads(verified.manifest_bytes)["derived_from"]
        self.assertEqual({"manifest_sha256": digest(before["manifest.json"]),
                          "report_sha256": digest(report_raw)}, derived_from)

    def test_same_saved_inputs_produce_deterministic_replay(self):
        create_synthetic(self.source)
        first = replay_bundle(self.source, self.destination)
        second_destination = self.root / "replay-two"
        second = replay_bundle(self.source, second_destination)
        self.assertEqual(first, second)
        self.assertEqual(tree_bytes(self.destination), tree_bytes(second_destination))

    def test_blocking_policy_survives_complete_incomplete_and_error_replay(self):
        expected_evidence = {"block": "complete", "incomplete_block": "incomplete", "error_block": "error"}
        for state, evidence in expected_evidence.items():
            with self.subTest(state=state):
                source = self.root / state
                create_synthetic(source, state)
                result = replay_bundle(source, self.root / (state + "-replay"))
                self.assertEqual({"evidence": evidence, "freshness": "unknown", "policy": "block"}, result["state"])
                self.assertFalse(result["ready_for_review"])
                self.assertEqual(2, result["exit_code"])
                self.assertEqual("failed", result["obligations"][0]["outcome"])
                self.assertEqual("block", result["obligations"][0]["effect"])

    def test_replay_does_not_open_network_or_start_subprocesses(self):
        create_synthetic(self.source)
        with patch("socket.create_connection", side_effect=AssertionError("network not allowed")), \
             patch("subprocess.Popen", side_effect=AssertionError("subprocess not allowed")):
            result = replay_bundle(self.source, self.destination)
        self.assertEqual("unknown", result["state"]["freshness"])

    def test_replay_preserves_native_engine_findings_and_wheel_pins(self):
        policy, report, raw_files = synthetic_engine_documents()
        create_bundle(self.source, policy_bytes=json_bytes(policy), report_bytes=json_bytes(report), raw_files=raw_files)
        result = replay_bundle(self.source, self.destination)
        self.assertEqual(report["engines"], result["engines"])
        self.assertEqual(report["evidence"], result["evidence"])
        self.assertEqual("block", result["state"]["policy"])
        self.assertEqual("checkwash/synthetic/v1:weakened", result["engines"][0]["findings"][0]["fingerprint"])
        self.assertEqual(2, result["exit_code"])

    def test_full_limitations_inventory_can_replay_without_losing_or_adding_claims(self):
        policy, report, raw_files = synthetic_documents()
        report["limitations"] = [f"synthetic_storage_limit_{index}" for index in range(1000)]
        create_bundle(self.source, policy_bytes=json_bytes(policy), report_bytes=json_bytes(report), raw_files=raw_files)
        result = replay_bundle(self.source, self.destination)
        self.assertEqual(report["limitations"], result["limitations"])
        self.assertEqual(1000, len(result["limitations"]))
        self.assertEqual("unknown", result["state"]["freshness"])

    def test_tampered_source_cannot_produce_replay_output(self):
        create_synthetic(self.source)
        (self.source / "raw/junit.xml").write_bytes(b"altered")
        before = tree_bytes(self.source)
        with self.assertRaises(BundleError):
            replay_bundle(self.source, self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(before, tree_bytes(self.source))

    def test_destination_inside_source_is_rejected_without_source_changes(self):
        create_synthetic(self.source)
        before = tree_bytes(self.source)
        for destination in (self.source, self.source / "nested", self.source / "raw/new-replay"):
            with self.subTest(destination=destination), self.assertRaises(BundleError):
                replay_bundle(self.source, destination)
        self.assertEqual(before, tree_bytes(self.source))

    def test_existing_replay_destination_is_not_overwritten(self):
        create_synthetic(self.source)
        replay_bundle(self.source, self.destination)
        before = tree_bytes(self.destination)
        with self.assertRaises(BundleError):
            replay_bundle(self.source, self.destination)
        self.assertEqual(before, tree_bytes(self.destination))

    def test_replaying_an_offline_bundle_still_has_unknown_freshness(self):
        create_synthetic(self.source)
        first = replay_bundle(self.source, self.destination)
        second = replay_bundle(self.destination, self.root / "replay-again")
        self.assertEqual(first["observed_at"], second["observed_at"])
        self.assertEqual(first["state"], second["state"])
        self.assertEqual(2, second["exit_code"])
        self.assertFalse(second["ready_for_review"])


if __name__ == "__main__":
    unittest.main()
