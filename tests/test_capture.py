"""Authored synthetic storage controls; not GitHub recordings or qualification.

Repository, IDs, source revision and response bodies below are fictional. These
tests prove bounded byte/inventory handling, never the truth of API claims.
"""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import canonical_bytes
from delivery_review.evidence.capture import (
    CaptureError, CaptureLimits, CaptureWriter, parse_capture, verify_capture,
)
from delivery_review.evidence.store import BundleError, verify_bundle
from delivery_review.providers.github_routes import API_VERSION, request_path


NOW = "2026-11-06T00:00:00Z"
COLLECTOR = {"version": "synthetic-storage-test", "source_sha": "a" * 40}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def profile_bytes():
    profile = json.loads((ROOT / "examples/github-observations/profile.json").read_bytes())
    profile["workflows"][0]["job_names"].append("合成測試")
    # Deliberately noncanonical bytes must survive unchanged.
    return (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").replace("\n", "\r\n").encode()


def metadata(profile, role="repository", *, phase="initial", page=1, pr_number=7, **changes):
    ids = {name: None for name in ("workflow_id", "run_id", "attempt", "job_id", "check_run_id", "page")}
    if role not in {"repository", "pull_request"}:
        ids["workflow_id"] = profile["workflows"][0]["id"]
    if role in {"run", "attempt", "jobs", "check_run"}:
        ids["run_id"] = 801
    if role in {"attempt", "jobs", "check_run"}:
        ids["attempt"] = 2
    if role == "check_run":
        ids.update(job_id=901, check_run_id=1001)
    if role in {"workflow_runs", "jobs"}:
        ids["page"] = page
    path = request_path(profile, role, pr_number=pr_number, head_sha="b" * 40,
                        **{name: value for name, value in ids.items() if name != "job_id"})
    result = {"phase": phase, "role": role, **ids,
              "request": {"method": "GET", "path": path, "api_version": API_VERSION},
              "observed_at": NOW, "status_code": 200, "response_headers": {}, "error": None}
    result.update(changes)
    return result


class CaptureStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "capture"
        self.profile_raw = profile_bytes()
        self.profile = json.loads(self.profile_raw)

    def writer(self, destination=None, *, raw=None, **kwargs):
        return CaptureWriter(destination or self.destination, raw or self.profile_raw,
                             collector=COLLECTOR, observed_at=NOW, **kwargs)

    def capture(self, body=b'{ "synthetic": true }\r\n'):
        writer = self.writer()
        writer.record(metadata(self.profile), body)
        return writer.seal()

    def assertInvalidManifest(self, source, mutate):
        manifest = source.manifest
        mutate(manifest)
        with self.assertRaises(CaptureError):
            parse_capture(canonical_bytes(manifest), source.profile_bytes, source.bodies)

    def test_incremental_preservation_completion_marker_and_immutable_snapshot(self):
        writer = self.writer()
        self.assertEqual(self.profile_raw, (self.destination / "profile.json").read_bytes())
        self.assertFalse((self.destination / "capture.json").exists())
        with self.assertRaises(CaptureError):
            verify_capture(self.destination)
        raw = ' { "fictional": "原始位元組" }\r\n'.encode() + b"\xff"
        request = metadata(self.profile)
        record = writer.record(request, raw)
        request["request"]["path"] = "changed after save"
        self.assertEqual(raw, (self.destination / "raw/r000001.body").read_bytes())
        self.assertEqual(digest(raw), record["body"]["sha256"])
        record["body"]["sha256"] = "0" * 64
        self.assertFalse((self.destination / "capture.json").exists())
        saved = writer.seal()
        replay_input = verify_capture(self.destination)
        self.assertEqual(saved.manifest_bytes, replay_input.manifest_bytes)
        self.assertEqual(self.profile_raw, replay_input.profile_bytes)
        self.assertEqual(raw, replay_input.bodies["raw/r000001.body"])
        self.assertEqual(digest(raw), saved.manifest["records"][0]["body"]["sha256"])
        altered = saved.manifest
        altered["records"].clear()
        saved.profile["repository"]["id"] = 99
        self.assertEqual(1, len(saved.manifest["records"]))
        self.assertEqual(9001, saved.profile["repository"]["id"])
        with self.assertRaises(TypeError):
            saved.bodies["raw/r000001.body"] = b"changed"
        with self.assertRaises(FrozenInstanceError):
            saved.profile_bytes = b"changed"

    def test_incomplete_and_invalid_json_bodies_are_diagnostic_opaque_bytes(self):
        writer = self.writer()
        writer.record(metadata(self.profile), b"{bad-json")
        writer.record(metadata(self.profile, "pull_request", error={"code": "response_limit"}),
                      b'{"truncated":', complete=False)
        saved = writer.seal(status="stopped", reason_codes=["collection_incomplete"])
        self.assertFalse(saved.manifest["records"][1]["body"]["complete"])
        self.assertEqual(b'{"truncated":', verify_capture(self.destination).bodies["raw/r000002.body"])

    def test_empty_stopped_capture_does_not_fabricate_a_subject(self):
        saved = self.writer().seal(status="stopped", reason_codes=["subject_unavailable"])
        self.assertEqual([], saved.manifest["records"])
        self.assertEqual({}, saved.bodies)
        self.assertEqual({"capture.json", "profile.json"}, {p.name for p in self.destination.iterdir()})
        self.assertNotIn("subject", saved.manifest)
        self.assertNotIn("report", saved.manifest)
        self.assertEqual(saved.manifest_bytes, verify_capture(self.destination).manifest_bytes)

    def test_http_and_transport_errors_are_preserved_without_stderr(self):
        writer = self.writer()
        writer.record(metadata(self.profile, status_code=403, error={"code": "resource_unavailable"},
                               response_headers={"x-github-request-id": "fictional-123"}), b'{"message":"denied"}')
        writer.record(metadata(self.profile, "pull_request", status_code=None,
                               error={"code": "request_timeout"}), None)
        saved = writer.seal(status="stopped", reason_codes=["collection_incomplete"])
        first, second = saved.manifest["records"]
        self.assertEqual(403, first["status_code"])
        self.assertIsNone(second["body"])
        self.assertEqual({"code": "request_timeout"}, second["error"])

    def test_invalid_status_error_and_completion_combinations_are_rejected(self):
        cases = [({"status_code": None}, b"x", True),
                 ({"status_code": 403}, b"x", True), ({}, None, True), ({}, b"x", False),
                 ({"status_code": True}, b"x", True), ({"status_code": 600}, b"x", True),
                 ({"error": {"code": "subject_unavailable"}}, b"x", True),
                 ({"error": {"code": "http_error", "stderr": "secret"}}, b"x", True),
                 ({"status_code": None, "error": {"code": "transport_error"},
                   "response_headers": {"etag": "claimed"}}, None, True)]
        for index, (changes, body, complete) in enumerate(cases):
            with self.subTest(changes=changes):
                writer = self.writer(self.root / str(index))
                with self.assertRaises(CaptureError):
                    writer.record(metadata(self.profile, **changes), body, complete=complete)
                self.assertFalse((self.root / str(index) / "raw").exists())

    def test_writer_is_exclusive_and_cannot_be_reused_after_seal(self):
        writer = self.writer()
        writer.record(metadata(self.profile), b"x")
        writer.seal()
        original = (self.destination / "capture.json").read_bytes()
        for action in (lambda: self.writer(), lambda: writer.record(metadata(self.profile), b"x"), writer.seal):
            with self.assertRaises(CaptureError):
                action()
        self.assertEqual(original, (self.destination / "capture.json").read_bytes())
        empty = self.root / "existing-empty"
        empty.mkdir()
        with self.assertRaises(CaptureError):
            self.writer(empty)
        self.assertEqual([], list(empty.iterdir()))

    def test_manifest_types_versions_and_inventory_are_strict(self):
        saved = self.capture()
        mutations = [lambda m: m.update(kind="delivery-review-evidence-bundle"),
                     lambda m: m.update(capture_version=2), lambda m: m.update(mapper_version=True),
                     lambda m: m.update(report={}), lambda m: m.pop("collector"),
                     lambda m: m["profile"].update(size_bytes=True),
                     lambda m: m["records"][0].update(id="r000002"),
                     lambda m: m["records"][0].update(extra="field"),
                     lambda m: m["records"][0]["body"].update(complete=1),
                     lambda m: m["records"][0]["body"].update(sha256="A" * 64),
                     lambda m: m["termination"].update(status=[]),
                     lambda m: m["termination"].update(status="stopped", reason_codes=[]),
                     lambda m: m["termination"].update(reason_codes=["same", "same"])]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertInvalidManifest(saved, mutate)
        for bodies in ({}, {**saved.bodies, "raw/extra.body": b"x"},
                       {"raw/r000001.body": b"X" * len(saved.bodies["raw/r000001.body"])}):
            with self.subTest(bodies=list(bodies)), self.assertRaises(CaptureError):
                parse_capture(saved.manifest_bytes, saved.profile_bytes, bodies)
        changed_profile = saved.profile_bytes.replace(b"fixture-owner", b"fictuxe-owner")
        with self.assertRaises(CaptureError):
            parse_capture(saved.manifest_bytes, changed_profile, saved.bodies)

    def test_mapper_versions_are_explicit_preserved_and_never_silently_upgraded(self):
        default = self.capture()
        self.assertEqual(2, default.manifest["mapper_version"])
        for version in (1, 2):
            with self.subTest(version=version):
                destination = self.root / f"mapper-{version}"
                writer = self.writer(destination, mapper_version=version)
                writer.record(metadata(self.profile), b'{"synthetic":true}')
                saved = writer.seal()
                verified = verify_capture(destination)
                parsed = parse_capture(saved.manifest_bytes, saved.profile_bytes, saved.bodies)
                self.assertEqual(1, verified.manifest["capture_version"])
                self.assertEqual(version, verified.manifest["mapper_version"])
                self.assertEqual(version, parsed.manifest["mapper_version"])
                self.assertEqual(saved.manifest_bytes, verified.manifest_bytes)
                self.assertEqual(saved.manifest_bytes, parsed.manifest_bytes)
        for index, version in enumerate((None, True, False, 0, 3, 1.0, 2.0, "1", "2")):
            with self.subTest(invalid=version):
                manifest = default.manifest
                manifest["mapper_version"] = version
                with self.assertRaises(CaptureError):
                    parse_capture(json.dumps(manifest).encode(), default.profile_bytes, default.bodies)
                destination = self.root / f"invalid-mapper-{index}"
                with self.assertRaises(CaptureError):
                    self.writer(destination, mapper_version=version)
                self.assertFalse(destination.exists())

    def test_untrusted_json_is_bounded_and_strict(self):
        saved = self.capture()
        bad_json = [b'\xef\xbb\xbf{}', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1.0}',
                    b"\xff", b"[" * 34 + b"0" + b"]" * 34]
        for raw in bad_json:
            with self.subTest(raw=raw[:32]), self.assertRaises(CaptureError):
                parse_capture(raw, saved.profile_bytes, saved.bodies)

    def test_paths_cannot_escape_alias_or_select_a_different_body(self):
        saved = self.capture()
        for path in ("../outside", "raw/../profile.json", "raw\\r000001.body", "raw/R000001.body",
                     "raw/r000001.body:stream", "raw/r000001.body.", "C:/outside", "raw/r000002.body"):
            with self.subTest(path=path):
                self.assertInvalidManifest(saved, lambda m: m["records"][0]["body"].update(path=path))

    def test_route_binding_duplicate_logical_roles_and_time_order(self):
        writer = self.writer()
        writer.record(metadata(self.profile, "pull_request"), b"x")
        # A different PR path does not evade the one-initial-PR role constraint.
        with self.assertRaises(CaptureError):
            writer.record(metadata(self.profile, "pull_request", pr_number=8), b"y")
        with self.assertRaises(CaptureError):
            writer.record(metadata(self.profile, observed_at="2026-11-05T23:59:59Z"), b"x")
        saved = writer.seal()
        mutations = [lambda r: r.update(role=[]), lambda r: r.update(workflow_id=101),
                     lambda r: r.update(observed_at="2026-02-31T00:00:00Z"),
                     lambda r: r["request"].update(method="POST"),
                     lambda r: r["request"].update(api_version="2022-11-28"),
                     lambda r: r["request"].update(path="/repos/other/repo/pulls/7")]
        for index, mutate in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertInvalidManifest(saved, lambda m: mutate(m["records"][0]))

    def test_unsafe_headers_are_rejected_without_echoing_secret(self):
        secrets = [{"authorization": "Bearer synthetic-secret"}, {"set-cookie": "synthetic-secret"},
                   {"etag": "synthetic-secret\r\nInjected: bad"}, {"etag": "x" * 8193},
                   {"link": '<https://evil.test/repos/a/b>; rel="next"'},
                   {"link": '<https://api.github.com/repos/a/b?token=synthetic-secret>; rel="next"'}]
        for index, headers in enumerate(secrets):
            with self.subTest(index=index):
                writer = self.writer(self.root / str(index))
                with self.assertRaises(CaptureError) as caught:
                    writer.record(metadata(self.profile, response_headers=headers), b"body")
                self.assertNotIn("synthetic-secret", str(caught.exception))
                self.assertFalse((self.root / str(index) / "raw").exists())

    def test_safe_out_of_scope_or_over_limit_link_is_kept_for_mapper_diagnostics(self):
        writer = self.writer()
        link = '<https://api.github.com/repos/other/repo/actions/runs/99/attempts/1/jobs?page=11&per_page=100>; rel="next"'
        writer.record(metadata(self.profile, "workflow_runs", response_headers={"link": link}), b"{}")
        saved = writer.seal(status="stopped", reason_codes=["pagination_incomplete"])
        self.assertEqual(link, saved.manifest["records"][0]["response_headers"]["link"])

    def test_local_limits_have_hard_ceilings_and_do_not_coerce(self):
        ceilings = {"max_response_bytes": 2097152, "max_total_bytes": 104857600, "max_requests": 200,
                    "max_pages": 10, "max_manifest_bytes": 2097152, "max_profile_bytes": 2097152}
        for name, cap in ceilings.items():
            for value in (0, -1, True, 1.5, cap + 1):
                with self.subTest(name=name, value=value), self.assertRaises(CaptureError):
                    CaptureLimits(**{name: value})

    def test_reader_enforces_independent_response_total_metadata_and_page_limits(self):
        writer = self.writer()
        writer.record(metadata(self.profile), b"12345678")
        writer.record(metadata(self.profile, "workflow_runs", page=2), b"abcdefgh")
        saved = writer.seal()
        limits = [CaptureLimits(max_response_bytes=7), CaptureLimits(max_total_bytes=15),
                  CaptureLimits(max_requests=1), CaptureLimits(max_pages=1),
                  CaptureLimits(max_manifest_bytes=len(saved.manifest_bytes) - 1),
                  CaptureLimits(max_profile_bytes=len(saved.profile_bytes) - 1)]
        for limit in limits:
            with self.subTest(limit=limit):
                with self.assertRaises(CaptureError):
                    parse_capture(saved.manifest_bytes, saved.profile_bytes, saved.bodies, limits=limit)
                with self.assertRaises(CaptureError):
                    verify_capture(self.destination, limits=limit)

    def test_profile_limits_further_bound_writer_and_failed_requests_count(self):
        profile = deepcopy(self.profile)
        profile["limits"].update(max_response_bytes=8, max_total_bytes=10, max_requests=2)
        writer = self.writer(raw=canonical_bytes(profile))
        with self.assertRaises(CaptureError):
            writer.record(metadata(profile), b"123456789")
        writer.record(metadata(profile), b"12345678")
        with self.assertRaises(CaptureError):
            writer.record(metadata(profile, "pull_request"), b"abc")
        writer.record(metadata(profile, "pull_request", status_code=None, error={"code": "request_timeout"}), None)
        with self.assertRaises(CaptureError):
            writer.record(metadata(profile, "workflow", status_code=None, error={"code": "transport_error"}), None)
        saved = writer.seal(status="stopped", reason_codes=["request_limit"])
        self.assertEqual(2, len(saved.manifest["records"]))
        self.assertEqual(8, sum(len(body) for body in saved.bodies.values()))

    def test_writer_local_limits_prevent_partial_record_commit(self):
        writer = self.writer(limits=CaptureLimits(max_response_bytes=4, max_total_bytes=5, max_requests=2, max_pages=1))
        for request, body in ((metadata(self.profile), b"12345"),
                              (metadata(self.profile, "workflow_runs", page=2), b"x")):
            with self.assertRaises(CaptureError):
                writer.record(request, body)
        writer.record(metadata(self.profile), b"1234")
        with self.assertRaises(CaptureError):
            writer.record(metadata(self.profile, "pull_request"), b"ab")
        self.assertEqual(["r000001.body"], [p.name for p in (self.destination / "raw").iterdir()])

    def test_manifest_and_profile_limits_apply_before_destination_creation(self):
        for index, limits in enumerate((CaptureLimits(max_manifest_bytes=1), CaptureLimits(max_profile_bytes=1))):
            path = self.root / str(index)
            with self.assertRaises(CaptureError):
                self.writer(path, limits=limits)
            self.assertFalse(path.exists())

    def test_manifest_budget_refusal_leaves_room_to_seal_diagnostic_stop(self):
        limits = CaptureLimits(max_manifest_bytes=1600)
        writer = self.writer(limits=limits)
        writer.record(metadata(self.profile), b"first response")
        with self.assertRaisesRegex(CaptureError, "^capture manifest byte limit exceeded$"):
            writer.record(metadata(self.profile, "pull_request"), b"uncommitted response")
        self.assertFalse((self.destination / "raw/r000002.body").exists())
        saved = writer.seal(status="stopped", reason_codes=["capture_limit"])
        self.assertEqual(1, len(saved.manifest["records"]))
        self.assertLessEqual(len(saved.manifest_bytes), limits.max_manifest_bytes)
        self.assertEqual(saved.manifest_bytes, verify_capture(self.destination, limits=limits).manifest_bytes)

    def test_file_tampering_extra_entries_and_missing_files_fail_without_repair(self):
        saved = self.capture(b"original")
        raw_path = self.destination / "raw/r000001.body"
        raw_path.write_bytes(b"modified")
        with self.assertRaises(CaptureError):
            verify_capture(self.destination)
        self.assertEqual(b"modified", raw_path.read_bytes())
        self.assertEqual(saved.manifest_bytes, (self.destination / "capture.json").read_bytes())
        raw_path.write_bytes(b"original")
        extra = self.destination / "unexpected"
        for directory in (False, True):
            extra.mkdir() if directory else extra.write_bytes(b"extra")
            with self.assertRaises(CaptureError):
                verify_capture(self.destination)
            extra.rmdir() if directory else extra.unlink()
        raw_path.unlink()
        with self.assertRaises(CaptureError):
            verify_capture(self.destination)

    def test_actual_file_size_is_checked_independently_of_declared_size(self):
        self.capture(b"x")
        (self.destination / "raw/r000001.body").write_bytes(b"x" * 100)
        with self.assertRaises(CaptureError):
            verify_capture(self.destination, limits=CaptureLimits(max_response_bytes=10))

    def test_seal_refuses_changed_bytes_and_io_failure_poisoned_writer(self):
        writer = self.writer()
        writer.record(metadata(self.profile), b"original")
        (self.destination / "raw/r000001.body").write_bytes(b"modified")
        with self.assertRaises(CaptureError):
            writer.seal()
        self.assertFalse((self.destination / "capture.json").exists())
        with self.assertRaises(CaptureError):
            writer.record(metadata(self.profile, "pull_request"), b"x")
        failed = self.writer(self.root / "failed")
        with patch("delivery_review.evidence.capture._write", side_effect=OSError("synthetic-secret")):
            with self.assertRaises(CaptureError) as caught:
                failed.record(metadata(self.profile), b"x")
        self.assertNotIn("synthetic-secret", str(caught.exception))
        with self.assertRaises(CaptureError):
            failed.seal(status="stopped", reason_codes=["collection_incomplete"])
        self.assertFalse((self.root / "failed/capture.json").exists())

    def test_hardlinks_and_reported_reparse_points_are_rejected(self):
        self.capture()
        raw_path = self.destination / "raw/r000001.body"
        linked = self.root / "linked-body"
        os.link(raw_path, linked)
        with self.assertRaises(CaptureError):
            verify_capture(self.destination)
        linked.unlink()
        actual_lstat = Path.lstat
        for linked_path in (self.destination, self.destination / "raw", raw_path):
            # Synthetic Windows metadata control, not actual junction qualification.
            def reported_reparse(path, *, _linked=linked_path):
                result = actual_lstat(path)
                if path == _linked:
                    fields = {name: getattr(result, name) for name in dir(result) if name.startswith("st_")}
                    fields["st_file_attributes"] = getattr(result, "st_file_attributes", 0) | 0x400
                    return SimpleNamespace(**fields)
                return result
            with self.subTest(path=linked_path), patch.object(Path, "lstat", reported_reparse):
                with self.assertRaises(CaptureError):
                    verify_capture(self.destination)

    def test_capture_and_normalized_bundle_formats_reject_each_other(self):
        from test_evidence_store import create_synthetic
        self.capture()
        with self.assertRaises(BundleError):
            verify_bundle(self.destination)
        bundle = self.root / "bundle"
        create_synthetic(bundle)
        with self.assertRaises(CaptureError):
            verify_capture(bundle)

    def test_pure_parser_does_not_open_files_network_or_processes(self):
        saved = self.capture()
        inputs = dict(saved.bodies)
        with patch.object(Path, "open", side_effect=AssertionError("filesystem")), \
             patch("builtins.open", side_effect=AssertionError("filesystem")), \
             patch.object(socket, "create_connection", side_effect=AssertionError("network")), \
             patch.object(subprocess, "Popen", side_effect=AssertionError("process")):
            verified = parse_capture(saved.manifest_bytes, saved.profile_bytes, inputs)
            self.assertEqual(9001, verified.profile["repository"]["id"])
        inputs.clear()
        self.assertEqual(1, len(verified.bodies))


if __name__ == "__main__":
    unittest.main()
