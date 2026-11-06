"""Authored release-resource controls, never hosted producer qualification."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.producer_registry import qualified_producer


SELECTOR = {"producer_repository_id": 9002, "producer_path": ".github/workflows/pytest.yml", "producer_sha": "d" * 40}


def receipt():
    return {"kind": "delivery-review-producer-qualification", "qualification_version": 1, "result": "qualified",
        "producer": {"repository_id": 9002, "path": ".github/workflows/pytest.yml", "sha": "d" * 40},
        "producer_profile": "pytest-isolated-container-v1", "test_path": "tests", "source_modes": ["pr_head"],
        "provenance": "authored unit control, no actual producer qualification"}


def resources(*receipts):
    files, rows = {}, []
    for value in receipts:
        raw = canonical_bytes(value) + b"\n"
        digest = hashlib.sha256(raw).hexdigest()
        name = "producer-qualification-" + digest + ".json"
        rows.append({"receipt": name, "sha256": digest})
        files[name] = raw
    files["producer-qualifications.json"] = canonical_bytes({"kind": "delivery-review-qualified-producers", "registry_version": 1, "producers": rows})
    return files


class ProducerRegistryTests(unittest.TestCase):
    def lookup(self, files, selector=SELECTOR):
        calls = []
        def read(group, name):
            self.assertEqual("formats", group)
            calls.append(name)
            return files[name]
        with patch("delivery_review.producer_registry.definition_bytes", side_effect=read):
            result = qualified_producer(selector)
        return result, calls

    def test_environment_cannot_award_unregistered_producer_qualification(self):
        with patch.dict(os.environ, {"DELIVERY_REVIEW_QUALIFIED_PRODUCER": "true", "GITHUB_TOKEN": "authored-secret"}):
            self.assertIsNone(qualified_producer(SELECTOR))

    def test_release_resources_admit_only_exact_reviewed_paths_modes_and_runtime(self):
        for filename, mode in (("pytest-producer.yml", "pr_head"), ("pytest-producer-merge.yml", "github_merge")):
            selector = {"producer_repository_id": 1438021135, "producer_path": ".github/workflows/" + filename,
                "producer_sha": "3d3e54599066c98f52384c66953b130a01eaaff8"}
            value = qualified_producer(selector)
            self.assertIsNotNone(value)
            self.assertEqual((mode,), value.source_modes)
            receipt_value = loads_strict(value.qualification_receipt_bytes)
            self.assertEqual({"python": "3.11.16", "implementation": "cpython", "platform": "linux", "architecture": "x86_64"},
                receipt_value["runtime"])
            self.assertEqual("95605b33d82da5c11da7d04ca2543ab18a2b3e8fdb2beff35601f1e1d44b6e90", receipt_value["evidence"]["sha256"])
            self.assertIsNone(qualified_producer(dict(selector, producer_sha="f" * 40)))

    def test_exact_bundled_resource_identity_and_digest_are_required(self):
        files = resources(receipt())
        result, calls = self.lookup(files)
        self.assertEqual("qualified", result.status)
        self.assertEqual(("pr_head",), result.source_modes)
        self.assertEqual(hashlib.sha256(result.qualification_receipt_bytes).hexdigest(), result.qualification_receipt_sha256)
        self.assertEqual(2, len(calls))
        for key, bad in (("producer_repository_id", 9003), ("producer_path", ".github/workflows/other.yml"), ("producer_sha", "f" * 40)):
            self.assertIsNone(self.lookup(files, dict(SELECTOR, **{key: bad}))[0])

    def test_empty_duplicate_unknown_or_scalar_modes_are_rejected_before_selection(self):
        for modes in ([], ["pr_head", "pr_head"], ["queue_merge"], "pr_head", None, [True]):
            value = receipt(); value["source_modes"] = modes
            with self.subTest(modes=modes), self.assertRaises(ContractError):
                self.lookup(resources(value))

    def test_receipt_cannot_self_declare_unqualified_status_or_subset_command(self):
        for key, bad in (("result", "pending"), ("qualification_version", True), ("producer_profile", "other"), ("test_path", "tests/subset")):
            value = receipt(); value[key] = bad
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.lookup(resources(value))

    def test_raw_receipt_tamper_is_not_rehashed_as_approved(self):
        files = resources(receipt())
        name = next(name for name in files if name != "producer-qualifications.json")
        files[name] += b" "
        with self.assertRaisesRegex(ContractError, "producer_qualification_digest_mismatch"):
            self.lookup(files)

    def test_duplicate_producer_identity_cannot_silently_pick_one_receipt(self):
        first = receipt(); second = receipt(); second["provenance"] += " additional"
        for values in ((first, first), (first, second)):
            with self.assertRaisesRegex(ContractError, "duplicate_producer_qualification"):
                self.lookup(resources(*values))

    def test_unknown_registry_fields_or_version_and_unsafe_resource_names_refused(self):
        cases = [{"kind": "delivery-review-qualified-producers", "registry_version": True, "producers": []},
            {"kind": "delivery-review-qualified-producers", "registry_version": 1, "producers": [], "approved": True},
            {"kind": "delivery-review-qualified-producers", "registry_version": 1,
             "producers": [{"receipt": "../../untrusted.json", "sha256": "f" * 64}]}]
        for value in cases:
            with self.assertRaises(ContractError):
                self.lookup({"producer-qualifications.json": canonical_bytes(value)})

    def test_bad_identity_types_paths_and_shas_do_not_match_by_coercion(self):
        for key, bad in (("repository_id", True), ("repository_id", "9002"), ("path", "../producer.yml"),
                         ("sha", "d" * 7), ("unknown", "field")):
            value = receipt(); value["producer"][key] = bad
            with self.subTest(key=key), self.assertRaises(ContractError):
                self.lookup(resources(value))


if __name__ == "__main__":
    unittest.main()
