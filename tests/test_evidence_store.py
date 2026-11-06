"""Controlled local storage tests, not GitHub/engine integration evidence.

The documents below are synthetic copies of M0 shapes. All retained bytes are
authored here and have independently computed digests; no platform execution,
producer authentication, archive extraction or JUnit parsing is claimed.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import canonical_digest, validate_report
from delivery_review.evidence.store import (
    BundleError, BundleLimits, create_bundle, verify_bundle,
)


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_bytes(value: object) -> bytes:
    # Deliberately preserve noncanonical whitespace, CRLF and UTF-8.
    return (json.dumps(value, indent=2, ensure_ascii=False).replace("\n", "\r\n") + "\r\n").encode("utf-8")


def inventory(raw_files: dict[str, bytes]) -> list[dict]:
    return [{"path": path, "sha256": digest(raw), "size_bytes": len(raw)}
            for path, raw in sorted(raw_files.items())]


def synthetic_documents(state: str = "clear") -> tuple[dict, dict, dict[str, bytes]]:
    """Author a storage control, with valid claims but no authenticity claim."""
    policy = json.loads((ROOT / "examples/policy.json").read_bytes())
    report = json.loads((ROOT / "examples/report.json").read_bytes())
    raw_files = {
        "raw/api/producer.json": '{ "synthetic": true, "note": "原始位元組" }\r\n'.encode("utf-8"),
        "raw/archive.bin": b"synthetic opaque archive bytes\x00\xff\r\n",
        "raw/junit.xml": b'<testsuite name="synthetic" tests="3"/>\r\n',
    }
    item = report["evidence"][0]
    item["raw_sha256"] = digest(raw_files["raw/junit.xml"])
    item["artifact_sha256"] = digest(raw_files["raw/archive.bin"])
    item["producer_proof"]["api_response_sha256"] = digest(raw_files["raw/api/producer.json"])
    report["limitations"].append("Synthetic M1 storage control; 原始資料，非平台執行證據。")
    report["raw_inventory"] = inventory(raw_files)
    if state != "clear":
        item["conclusion"] = "failure"
        item["test_counts"]["passed"] = 2
        item["test_counts"]["failed"] = 1
        report["obligations"][0].update(outcome="failed", effect="block", reason_codes=["test_failure"])
        report["state"]["policy"] = "block"
        report["ready_for_review"] = False
        report["exit_code"] = 1
        if state in {"incomplete_block", "error_block"}:
            requirement = {"id": "integrity", "kind": "integrity", "required": True,
                           "minimum_binding": "reported", "selector": {}}
            policy["requirements"].append(requirement)
            outcome = "missing" if state == "incomplete_block" else "error"
            report["obligations"].append({
                "id": "integrity", "kind": "integrity", "required": True,
                "minimum_binding": "reported", "outcome": outcome,
                "effect": "clear", "evidence_ids": [], "reason_codes": ["integrity_" + outcome],
            })
            report["policy"]["canonical_sha256"] = canonical_digest(policy)
            report["state"]["evidence"] = "incomplete" if outcome == "missing" else "error"
            report["exit_code"] = 2
    return policy, report, raw_files


def create_synthetic(destination: Path, state: str = "clear") -> tuple[bytes, bytes, dict[str, bytes]]:
    policy, report, raw_files = synthetic_documents(state)
    policy_raw, report_raw = json_bytes(policy), json_bytes(report)
    create_bundle(destination, policy_bytes=policy_raw, report_bytes=report_raw, raw_files=raw_files)
    return policy_raw, report_raw, raw_files


def synthetic_engine_documents() -> tuple[dict, dict, dict[str, bytes]]:
    """Use M0 field shapes, but retain separately authored synthetic raw bytes."""
    case = json.loads((ROOT / "fixtures/cases/E13-weakening.json").read_bytes())
    policy, report = case["policy"], case["report"]
    raw_files = {}
    replacements = {}

    def retain(original_digest):
        if original_digest not in replacements:
            raw = json_bytes({"origin": "synthetic_m1_storage_control", "label": original_digest})
            path = f"raw/observation-{len(raw_files)}.json"
            raw_files[path] = raw
            replacements[original_digest] = digest(raw)
        return replacements[original_digest]

    for item in report["evidence"]:
        for key in ("raw_sha256", "artifact_sha256"):
            if item[key] is not None:
                item[key] = retain(item[key])
        if item["producer_proof"] is not None:
            proof = item["producer_proof"]
            proof["api_response_sha256"] = retain(proof["api_response_sha256"])
    for engine in report["engines"]:
        if engine["raw_report_sha256"] is not None:
            engine["raw_report_sha256"] = retain(engine["raw_report_sha256"])
    report["raw_inventory"] = inventory(raw_files)
    report["limitations"].append("Synthetic storage control; no installed scanner executed.")
    return policy, report, raw_files


def tree_bytes(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


class EvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"

    def write_documents(self, policy=None, report=None, raw_files=None, **kwargs):
        default_policy, default_report, default_raw = synthetic_documents()
        return create_bundle(
            self.bundle,
            policy_bytes=json_bytes(default_policy if policy is None else policy),
            report_bytes=json_bytes(default_report if report is None else report),
            raw_files=default_raw if raw_files is None else raw_files,
            **kwargs,
        )

    def edit_manifest(self, mutate):
        path = self.bundle / "manifest.json"
        manifest = json.loads(path.read_bytes())
        mutate(manifest)
        path.write_bytes(json_bytes(manifest))

    def test_round_trip_preserves_original_bytes_and_records_independent_digests(self):
        policy_raw, report_raw, raw_files = create_synthetic(self.bundle)
        verified = verify_bundle(self.bundle)
        self.assertEqual(policy_raw, verified.policy_bytes)
        self.assertEqual(report_raw, verified.report_bytes)
        self.assertEqual(raw_files, verified.raw_files)
        self.assertEqual(json.loads(report_raw), verified.report)
        self.assertEqual(json.loads(policy_raw), verified.policy)
        manifest = json.loads(verified.manifest_bytes)
        self.assertEqual("delivery-review-evidence-bundle", manifest["kind"])
        self.assertEqual(1, manifest["bundle_version"])
        self.assertEqual("saved_normalized_report", manifest["replay_basis"])
        self.assertEqual(verified.report["observed_at"], manifest["observed_at"])
        self.assertIsNone(manifest["derived_from"])
        expected = {"policy.json": policy_raw, "report.json": report_raw, **raw_files}
        self.assertEqual(inventory(expected), sorted(manifest["files"], key=lambda row: row["path"]))
        self.assertNotEqual(digest(policy_raw), verified.report["policy"]["canonical_sha256"])
        self.assertEqual(canonical_digest(verified.policy), verified.report["policy"]["canonical_sha256"])

    def test_verified_raw_snapshot_cannot_be_mutated_by_caller(self):
        create_synthetic(self.bundle)
        verified = verify_bundle(self.bundle)
        with self.assertRaises(TypeError):
            verified.raw_files["raw/junit.xml"] = b"replacement"
        changed = verified.report
        changed["observed_at"] = "2099-01-01T00:00:00Z"
        self.assertEqual("2026-10-29T00:00:00Z", verified.report["observed_at"])

    def test_existing_destination_is_never_reused_or_overwritten(self):
        for existing_file in (False, True):
            with self.subTest(existing_file=existing_file):
                destination = self.root / str(existing_file)
                if existing_file:
                    destination.write_bytes(b"existing")
                else:
                    destination.mkdir()
                    (destination / "keep.txt").write_bytes(b"existing")
                policy, report, raw = synthetic_documents()
                with self.assertRaises(BundleError):
                    create_bundle(destination, policy_bytes=json_bytes(policy), report_bytes=json_bytes(report), raw_files=raw)
                preserved = destination if existing_file else destination / "keep.txt"
                self.assertEqual(b"existing", preserved.read_bytes())

    def test_raw_policy_and_report_tampering_are_rejected_without_repair(self):
        for relative in ("raw/junit.xml", "policy.json", "report.json"):
            with self.subTest(relative=relative):
                destination = self.root / relative.replace("/", "-")
                create_synthetic(destination)
                target = destination / relative
                target.write_bytes(target.read_bytes() + b" ")
                before = tree_bytes(destination)
                with self.assertRaises(BundleError):
                    verify_bundle(destination)
                self.assertEqual(before, tree_bytes(destination))

    def test_same_length_tampering_is_detected(self):
        create_synthetic(self.bundle)
        target = self.bundle / "raw/junit.xml"
        original = target.read_bytes()
        target.write_bytes(original.replace(b"3", b"4"))
        self.assertEqual(len(original), target.stat().st_size)
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)

    def test_missing_unlisted_files_and_unlisted_directories_are_rejected(self):
        for mutation in ("missing", "extra_file", "extra_directory"):
            with self.subTest(mutation=mutation):
                destination = self.root / mutation
                create_synthetic(destination)
                if mutation == "missing":
                    (destination / "raw/junit.xml").unlink()
                elif mutation == "extra_file":
                    (destination / "raw/unlisted.txt").write_bytes(b"unlisted")
                else:
                    (destination / "raw/unlisted").mkdir()
                with self.assertRaises(BundleError):
                    verify_bundle(destination)

    def test_directory_without_final_manifest_is_not_a_bundle(self):
        self.bundle.mkdir()
        (self.bundle / "policy.json").write_bytes(b"{}")
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)

    def test_interrupted_write_leaves_no_completion_marker_and_cannot_be_retried_in_place(self):
        actual_open = Path.open

        def fail_report_write(path, mode="r", *args, **kwargs):
            if path == self.bundle / "report.json" and mode == "xb":
                raise OSError("simulated storage failure")
            return actual_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", fail_report_write), self.assertRaises(BundleError):
            create_synthetic(self.bundle)
        self.assertTrue((self.bundle / "policy.json").is_file())
        self.assertFalse((self.bundle / "manifest.json").exists())
        before = tree_bytes(self.bundle)
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)
        with self.assertRaises(BundleError):
            create_synthetic(self.bundle)
        self.assertEqual(before, tree_bytes(self.bundle))

    def test_manifest_requires_exact_fields_and_versions(self):
        mutations = (
            lambda m: m.update(bundle_version=2),
            lambda m: m.update(bundle_version=True),
            lambda m: m.update(kind="other"),
            lambda m: m.update(replay_basis="authenticated_github"),
            lambda m: m.update(approved=True),
            lambda m: m.pop("observed_at"),
            lambda m: m.update(observed_at="2026-10-30T00:00:00Z"),
            lambda m: m["files"][0].update(size_bytes=True),
            lambda m: m["files"][0].update(size_bytes=-1),
            lambda m: m["files"][0].update(size_bytes=0),
            lambda m: m["files"][0].update(sha256="0" * 64),
            lambda m: m["files"][0].update(extra="ignored"),
            lambda m: m["files"].append(deepcopy(m["files"][0])),
            lambda m: m.update(derived_from={"manifest_sha256": "a" * 64}),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                self.bundle = self.root / f"bundle-{index}"
                create_synthetic(self.bundle)
                self.edit_manifest(mutate)
                with self.assertRaises(BundleError):
                    verify_bundle(self.bundle)

    def test_malformed_manifest_json_is_rejected(self):
        inputs = (
            b'{"bundle_version":1,"bundle_version":1}', b"\xff", b"\xef\xbb\xbf{}",
            b'{"bundle_version":1.0}', b'{"size_bytes":NaN}',
            b"[" * 40 + b"0" + b"]" * 40,
        )
        create_synthetic(self.bundle)
        for raw in inputs:
            with self.subTest(raw=repr(raw[:60])):
                (self.bundle / "manifest.json").write_bytes(raw)
                with self.assertRaises(BundleError):
                    verify_bundle(self.bundle)

    def test_inventory_must_match_raw_files_exactly(self):
        policy, report, raw_files = synthetic_documents()
        for mutation in ("missing_entry", "extra_entry", "bad_size", "bad_hash"):
            self.bundle = self.root / mutation
            candidate = deepcopy(report)
            if mutation == "missing_entry":
                candidate["raw_inventory"].pop()
            elif mutation == "extra_entry":
                candidate["raw_inventory"].append({"path": "raw/missing.txt", "sha256": digest(b"missing"), "size_bytes": 7})
            elif mutation == "bad_size":
                candidate["raw_inventory"][0]["size_bytes"] += 1
            else:
                candidate["raw_inventory"][0]["sha256"] = "0" * 64
            with self.subTest(mutation=mutation), self.assertRaises(BundleError):
                self.write_documents(policy=policy, report=candidate, raw_files=raw_files)

    def test_each_referenced_digest_requires_retained_bytes(self):
        for field in ("raw_sha256", "artifact_sha256", "producer_proof"):
            self.bundle = self.root / field
            policy, report, raw_files = synthetic_documents()
            if field == "producer_proof":
                report["evidence"][0][field]["api_response_sha256"] = "0" * 64
            else:
                report["evidence"][0][field] = "0" * 64
            with self.subTest(field=field), self.assertRaises(BundleError):
                self.write_documents(policy=policy, report=report, raw_files=raw_files)

    def test_engine_report_digests_are_verified_without_requiring_wheel_bytes(self):
        policy, report, raw_files = synthetic_engine_documents()
        create_bundle(self.bundle, policy_bytes=json_bytes(policy), report_bytes=json_bytes(report), raw_files=raw_files)
        self.assertEqual(report["engines"], verify_bundle(self.bundle).report["engines"])
        self.assertTrue(all(engine["artifact_sha256"] not in {digest(raw) for raw in raw_files.values()}
                            for engine in report["engines"]))
        # An unfinished engine can preserve raw output without a completed
        # evidence link; its non-null report digest still requires actual bytes.
        engine = report["engines"][0]
        engine.update(status="error", verdict="not_judged", exit_code=2, raw_report_sha256="0" * 64)
        report["obligations"][1].update(outcome="error", reason_codes=["scanner_error"])
        report["state"]["evidence"] = "error"
        report["exit_code"] = 2
        validate_report(report, policy)
        with self.assertRaises(BundleError):
            create_bundle(self.root / "missing-engine-output", policy_bytes=json_bytes(policy),
                          report_bytes=json_bytes(report), raw_files=raw_files)

    def test_malformed_control_documents_and_nonbyte_content_are_rejected(self):
        policy, report, raw_files = synthetic_documents()
        variants = (
            {"policy_bytes": b"\xff", "report_bytes": json_bytes(report), "raw_files": raw_files},
            {"policy_bytes": json_bytes(policy), "report_bytes": b'{"schema_version":1,"schema_version":1}', "raw_files": raw_files},
            {"policy_bytes": json_bytes(policy), "report_bytes": json_bytes(report),
             "raw_files": {**raw_files, "raw/junit.xml": bytearray(raw_files["raw/junit.xml"])}},
        )
        for index, arguments in enumerate(variants):
            with self.subTest(variant=index), self.assertRaises(BundleError):
                create_bundle(self.root / f"malformed-{index}", **arguments)

    def test_placeholder_m0_report_is_not_a_verified_bundle(self):
        with self.assertRaises(BundleError):
            create_bundle(self.bundle, policy_bytes=(ROOT / "examples/policy.json").read_bytes(),
                          report_bytes=(ROOT / "examples/report.json").read_bytes(), raw_files={})

    def test_policy_substitution_is_rejected_even_with_new_manifest_hash(self):
        create_synthetic(self.bundle)
        policy_path = self.bundle / "policy.json"
        policy = json.loads(policy_path.read_bytes())
        policy["requirements"][0]["selector"]["min_tests"] = 2
        replacement = json_bytes(policy)
        policy_path.write_bytes(replacement)
        def update_policy_entry(manifest):
            entry = next(row for row in manifest["files"] if row["path"] == "policy.json")
            entry.update(sha256=digest(replacement), size_bytes=len(replacement))
        self.edit_manifest(update_policy_entry)
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)

    def test_unsafe_and_nonportable_raw_paths_are_refused_before_writing(self):
        unsafe = ("../escape", "/escape", "C:/escape", "raw/../escape", "raw\\escape",
                  "raw//empty", "raw/./dot", "raw/file:stream", "raw/NUL.txt", "raw/con",
                  "raw/AUX", "raw/COM1.txt", "raw/LPT9", "raw/trailing.", "raw/trailing ",
                  "raw/證據.json", "raw/zero\x00byte", "policy.json", "report.json", "manifest.json")
        for index, path in enumerate(unsafe):
            policy, report, raw_files = synthetic_documents()
            raw_files[path] = b"unsafe"
            report["raw_inventory"] = inventory(raw_files)
            with self.subTest(path=path), self.assertRaises(BundleError):
                create_bundle(self.root / f"invalid-{index}", policy_bytes=json_bytes(policy),
                              report_bytes=json_bytes(report), raw_files=raw_files)
        self.assertFalse((self.root / "escape").exists())

    def test_file_directory_and_case_collisions_are_rejected(self):
        collisions = (("raw/a", "raw/a/b"), ("raw/name", "raw/NAME"),
                      ("raw/API/a", "raw/api/b"))
        for index, paths in enumerate(collisions):
            self.bundle = self.root / f"collision-{index}"
            policy, report, raw_files = synthetic_documents()
            raw_files.update({path: b"collision" for path in paths})
            report["raw_inventory"] = inventory(raw_files)
            with self.subTest(paths=paths), self.assertRaises(BundleError):
                self.write_documents(policy=policy, report=report, raw_files=raw_files)

    def test_reader_rejects_manifest_path_escape(self):
        outside = self.root / "outside.txt"
        outside.write_bytes(b"outside")
        create_synthetic(self.bundle)
        self.edit_manifest(lambda m: m["files"][0].update(path="../outside.txt", sha256=digest(b"outside"), size_bytes=7))
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)
        self.assertEqual(b"outside", outside.read_bytes())

    def test_reader_and_writer_enforce_each_local_limit(self):
        policy, report, raw_files = synthetic_documents()
        policy_raw, report_raw = json_bytes(policy), json_bytes(report)
        sizes = [len(policy_raw), len(report_raw), *(len(raw) for raw in raw_files.values())]
        limits = (
            BundleLimits(max_files=len(raw_files) + 1),
            BundleLimits(max_file_bytes=max(sizes) - 1),
            BundleLimits(max_total_bytes=sum(sizes) - 1),
            BundleLimits(max_manifest_bytes=1),
        )
        create_synthetic(self.bundle)
        for index, limit in enumerate(limits):
            with self.subTest(limit=limit):
                with self.assertRaises(BundleError):
                    create_bundle(self.root / f"limited-{index}", policy_bytes=policy_raw,
                                  report_bytes=report_raw, raw_files=raw_files, limits=limit)
                with self.assertRaises(BundleError):
                    verify_bundle(self.bundle, limits=limit)

    def test_actual_file_size_is_bounded_independently_of_manifest_claim(self):
        create_synthetic(self.bundle)
        (self.bundle / "raw/junit.xml").write_bytes(b"x" * 20000)
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle, limits=BundleLimits(max_file_bytes=10000))

    def test_limits_reject_nonpositive_or_boolean_values(self):
        for field in ("max_files", "max_file_bytes", "max_total_bytes", "max_manifest_bytes"):
            for value in (0, -1, True, 1.5):
                with self.subTest(field=field, value=value), self.assertRaises((BundleError, ValueError)):
                    BundleLimits(**{field: value})

    def test_hard_linked_raw_file_is_rejected(self):
        create_synthetic(self.bundle)
        target = self.bundle / "raw/junit.xml"
        linked = self.root / "shared.xml"
        os.link(target, linked)
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)
        self.assertEqual(target.read_bytes(), linked.read_bytes())

    def test_symlinked_raw_file_or_bundle_root_is_rejected_when_supported(self):
        create_synthetic(self.bundle)
        outside = self.root / "outside.xml"
        original = (self.bundle / "raw/junit.xml").read_bytes()
        outside.write_bytes(original)
        probe = self.root / "probe-link"
        try:
            probe.symlink_to(outside)
        except (OSError, NotImplementedError):
            # Windows may deny creating symlinks. Exercise the same public
            # rejection boundary with reparse metadata on root, parent and leaf.
            # Actual junction creation is a separate host qualification check.
            actual_lstat = Path.lstat
            for linked_path in (self.bundle, self.bundle / "raw", self.bundle / "raw/junit.xml"):
                def reported_reparse(path, *, _linked=linked_path):
                    result = actual_lstat(path)
                    if path == _linked:
                        attributes = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
                        attributes["st_file_attributes"] = getattr(result, "st_file_attributes", 0) | stat.FILE_ATTRIBUTE_REPARSE_POINT
                        return SimpleNamespace(**attributes)
                    return result
                with self.subTest(reparse_path=linked_path), patch.object(Path, "lstat", reported_reparse):
                    with self.assertRaises(BundleError):
                        verify_bundle(self.bundle)
            return
        probe.unlink()
        target = self.bundle / "raw/junit.xml"
        target.unlink()
        target.symlink_to(outside)
        with self.assertRaises(BundleError):
            verify_bundle(self.bundle)
        target.unlink()
        target.write_bytes(original)
        linked_root = self.root / "linked-bundle"
        linked_root.symlink_to(self.bundle, target_is_directory=True)
        with self.assertRaises(BundleError):
            verify_bundle(linked_root)


if __name__ == "__main__":
    unittest.main()
