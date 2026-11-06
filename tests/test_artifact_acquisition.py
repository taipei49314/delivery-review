"""Authored raw API/archive/bundle controls; no hosted producer claim."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.evidence import artifacts
from delivery_review.providers.github_evidence_routes import artifact_path, attestation_path
from delivery_review.providers.github_http import _result
from test_bundle_encoding import literal

REPO = {"id": 9001, "full_name": "fixture-owner/fixture-repo"}
RUN, ATTEMPT, CHECK = 44, 2, 77
STAMP = "2026-01-01T00:00:00Z"
COLLECTOR = {"version": "1.0.0.dev0", "source_sha": "a" * 40}
ARCHIVE = b"PK\x03\x04 authored transport bytes, ZIP parsing belongs to the next layer\r\n"
BUNDLE = b'{"authored":"unsigned native bundle bytes"}\r\n'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def candidate(**changes):
    return {"id": 123, "name": f"delivery-review-{RUN}-{ATTEMPT}-{CHECK}", "expired": False,
        "size_in_bytes": len(ARCHIVE), "digest": "sha256:" + digest(ARCHIVE),
        "workflow_run": {"id": RUN, "repository_id": REPO["id"], "head_repository_id": REPO["id"],
                         "head_sha": "b" * 40, "head_branch": "fixture"}, **changes}


def response(body, headers=None, status=200, error=None):
    if isinstance(body, (dict, list)):
        body = (json.dumps(body, indent=2) + "\r\n").encode()
    return {"response": _result(error, status=status, headers=headers or {}, body=body, complete=True),
            "api_redirect": None}


def listing(values=None, total=None, headers=None):
    values = [candidate()] if values is None else values
    return response({"total_count": len(values) if total is None else total, "artifacts": values}, headers)


def discovery(bundles=(BUNDLE,), cursor=None, error=None, repository_id=REPO["id"]):
    raw_listing = b'{"authored":"ephemeral locator response deliberately omitted"}'
    return {"request_path": None,
        "discovery": {"status_code": 200, "response_headers": {},
            "body_retention": "omitted_ephemeral_storage_credentials", "body_sha256": digest(raw_listing),
            "body_size_bytes": len(raw_listing), "body_complete": True,
            "error": None if error is None else {"code": error}},
        "bundles": [{"ordinal": index, "repository_id": repository_id,
            "locator_sha256": digest(f"authored locator {index}".encode()),
            "response": _result(status=200, body=raw, complete=True)}
            for index, raw in enumerate(bundles, 1)], "next_cursor": cursor}


class ArtifactTransport:
    def __init__(self, values):
        self.values, self.calls = list(values), []

    def get(self, **kwargs):
        self.calls.append(kwargs)
        if not self.values:
            raise AssertionError("unexpected authored transport call")
        value = deepcopy(self.values.pop(0))
        if isinstance(value, Exception):
            raise value
        native = value["response"]
        if native["body"] is not None and len(native["body"]) > kwargs["max_bytes"]:
            native["body"] = native["body"][:kwargs["max_bytes"]]
            native["complete"] = False
            native["error"] = {"code": "response_limit"}
        return value


class AttestationTransport:
    def __init__(self, values):
        self.values, self.calls = list(values), []

    def get(self, repository, digest, predicate_type, **kwargs):
        self.calls.append((repository, digest, predicate_type, kwargs))
        if not self.values:
            raise AssertionError("unexpected authored discovery call")
        value = deepcopy(self.values.pop(0))
        value["request_path"] = attestation_path(repository, digest, predicate_type,
                                                  kwargs["per_page"], kwargs["cursor"])
        if value["next_cursor"] is not None:
            path = attestation_path(repository, digest, predicate_type, kwargs["per_page"], value["next_cursor"])
            value["discovery"]["response_headers"]["link"] = '<https://api.github.com' + path + '>; rel="next"'
        return value


class ArtifactCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "capture"

    def collect(self, values=None, proofs=None, **changes):
        self.transport = ArtifactTransport([listing(), response(candidate()), response(ARCHIVE)] if values is None else values)
        self.attestation_transport = AttestationTransport([discovery()] if proofs is None else proofs)
        return artifacts.collect_artifacts(repository=REPO, run_id=RUN, run_attempt=ATTEMPT,
            check_run_id=CHECK, destination=self.destination, transport=self.transport,
            attestation_transport=self.attestation_transport, collector=COLLECTOR,
            clock=lambda: STAMP, **changes)

    def test_complete_capture_replays_original_metadata_archive_and_unsigned_bundle(self):
        capture = self.collect()
        mapped = artifacts.map_artifact_capture(capture)
        self.assertEqual(("selected", [], 123, True), (mapped["status"], mapped["reason_codes"],
            mapped["artifact_id"], mapped["attestations_complete"]))
        self.assertEqual(ARCHIVE, mapped["archive_bytes"])
        self.assertEqual((BUNDLE,), mapped["bundles"])
        self.assertEqual(response(candidate())["response"]["body"], mapped["metadata"]["body_bytes"])
        self.assertEqual(STAMP, mapped["metadata"]["observed_at"])
        verified = artifacts.verify_artifact_capture(self.destination)
        with patch.object(ArtifactTransport, "get", side_effect=AssertionError("offline")), \
             patch.object(AttestationTransport, "get", side_effect=AssertionError("offline")):
            self.assertEqual(mapped, artifacts.map_artifact_capture(verified))
        self.assertEqual(capture.manifest_bytes, verified.manifest_bytes)
        self.assertEqual(dict(capture.raw_files), dict(verified.raw_files))
        self.assertEqual(["run_artifacts", "artifact", "archive"], [row["operation"] for row in self.transport.calls])
        self.assertEqual(digest(ARCHIVE), self.attestation_transport.calls[0][1])
        self.assertNotIn(b"signed locator", capture.manifest_bytes)
        with self.assertRaises(TypeError):
            capture.raw_files["injected"] = b"x"

    def test_raw_malformed_json_is_saved_before_mapping_and_failed_capture_can_replay(self):
        raw = b'{"broken original\r\n'
        read = artifacts.loads_strict
        def parsing(value):
            if value == raw:
                self.assertEqual(raw, (self.destination / "raw/r000001.body").read_bytes())
            return read(value)
        with patch.object(artifacts, "loads_strict", side_effect=parsing):
            capture = self.collect(values=[response(raw)])
        mapped = artifacts.map_artifact_capture(artifacts.verify_artifact_capture(self.destination))
        self.assertEqual(["artifact_metadata_invalid"], mapped["reason_codes"])
        self.assertEqual(raw, capture.raw_files["raw/r000001.body"])
        self.assertEqual("stopped", capture.manifest["termination"]["status"])

    def test_complete_pagination_precedes_candidate_selection(self):
        path = artifact_path("run_artifacts", REPO["full_name"], RUN, 2, 1)
        link = '<https://api.github.com' + path + '>; rel="next"'
        other = candidate(id=456, name="other")
        capture = self.collect(values=[listing([candidate()], total=2, headers={"link": link}), listing([other], total=2),
            response(candidate()), response(ARCHIVE)], limits=replace(artifacts.ArtifactLimits(), per_page=1))
        self.assertEqual("selected", artifacts.map_artifact_capture(capture)["status"])
        self.assertEqual([1, 2, None, None], [item.get("page") for item in self.transport.calls])

    def test_duplicate_ids_names_incomplete_count_and_changed_count_never_download(self):
        cases = [([listing([candidate(), candidate()])], "artifact_duplicate_identity"),
                 ([listing([candidate(), candidate(id=456)])], "artifact_ambiguous"),
                 ([listing([candidate()], total=2)], "artifact_listing_count_mismatch"),
                 ([listing([], total=0)], "artifact_missing")]
        for index, (values, reason) in enumerate(cases):
            self.destination = self.root / str(index)
            mapped = artifacts.map_artifact_capture(self.collect(values=values))
            self.assertEqual([reason], mapped["reason_codes"])
            self.assertEqual(1, len(self.transport.calls))
            self.assertFalse(self.attestation_transport.calls)

    def test_pagination_scope_and_page_limit_preserve_first_response(self):
        path = artifact_path("run_artifacts", "other/repo", RUN, 2, 100)
        capture = self.collect(values=[listing(total=2, headers={"link": '<https://api.github.com' + path + '>; rel="next"'})])
        self.assertEqual(["pagination_incomplete"], artifacts.map_artifact_capture(capture)["reason_codes"])
        self.assertEqual(1, len(capture.raw_files))
        self.destination = self.root / "limited"
        path = artifact_path("run_artifacts", REPO["full_name"], RUN, 2, 100)
        capture = self.collect(values=[listing(total=2, headers={"link": '<https://api.github.com' + path + '>; rel="next"'})],
            limits=replace(artifacts.ArtifactLimits(), max_pages=1))
        self.assertEqual(["pagination_limit"], artifacts.map_artifact_capture(capture)["reason_codes"])

    def test_changed_total_count_cannot_complete_a_raced_listing(self):
        path = artifact_path("run_artifacts", REPO["full_name"], RUN, 2, 1)
        capture = self.collect(values=[listing([candidate()], total=2,
            headers={"link": '<https://api.github.com' + path + '>; rel="next"'}),
            listing([candidate(id=456, name="other")], total=3)],
            limits=replace(artifacts.ArtifactLimits(), per_page=1))
        self.assertEqual(["artifact_listing_changed"], artifacts.map_artifact_capture(capture)["reason_codes"])
        self.assertEqual(2, len(capture.raw_files))

    def test_listing_detail_disagreement_wrong_run_expiry_and_oversize_fail_closed(self):
        wrong_run = {**candidate()["workflow_run"], "id": RUN + 1}
        cases = [([listing(), response(candidate(id=124))], "artifact_detail_mismatch"),
                 ([listing([candidate(workflow_run=wrong_run)])], "artifact_identity_mismatch"),
                 ([listing([candidate(expired=True)])], "artifact_expired"),
                 ([listing([candidate(size_in_bytes=26*1024*1024)])], "artifact_archive_limit")]
        for index, (values, reason) in enumerate(cases):
            self.destination = self.root / str(index)
            mapped = artifacts.map_artifact_capture(self.collect(values=values))
            self.assertEqual([reason], mapped["reason_codes"])
            self.assertIsNone(mapped["archive_bytes"])
            self.assertFalse(self.attestation_transport.calls)

    def test_actual_archive_digest_and_length_cannot_be_claimed_by_metadata(self):
        for index, body in enumerate((ARCHIVE[:-1], b"x" + ARCHIVE[1:])):
            self.destination = self.root / str(index)
            capture = self.collect(values=[listing(), response(candidate()), response(body)])
            mapped = artifacts.map_artifact_capture(capture)
            self.assertEqual(["artifact_archive_mismatch"], mapped["reason_codes"])
            self.assertIsNone(mapped["archive_bytes"])
            self.assertEqual(body, capture.raw_files["raw/r000003.body"])
            self.assertFalse(self.attestation_transport.calls)

    def test_discovery_empty_duplicate_bundle_wrong_repository_and_error_retained(self):
        cases = [(discovery(()), "selected", "attestation_missing"),
                 (discovery((BUNDLE, BUNDLE)), "ambiguous", "attestation_duplicate_bundle"),
                 (discovery(repository_id=22), "error", "attestation_repository_mismatch"),
                 (discovery(error="pagination_incomplete"), "error", "pagination_incomplete")]
        for index, (proof, status, reason) in enumerate(cases):
            self.destination = self.root / str(index)
            capture = self.collect(proofs=[proof])
            mapped = artifacts.map_artifact_capture(capture)
            self.assertEqual((status, [reason]), (mapped["status"], mapped["reason_codes"]))
            self.assertEqual(ARCHIVE, mapped["archive_bytes"])
            if proof["bundles"]:
                self.assertEqual(BUNDLE, capture.raw_files["raw/r000004-b001.body"])

    def test_discovery_pagination_and_cursor_cycle_are_rederived(self):
        second = b'{"authored":"another unsigned statement"}'
        capture = self.collect(proofs=[discovery(cursor={"after": "abc="}), discovery((second,))])
        mapped = artifacts.map_artifact_capture(capture)
        self.assertEqual((BUNDLE, second), mapped["bundles"])
        self.assertTrue(mapped["attestations_complete"])
        self.destination = self.root / "cycle"
        capture = self.collect(proofs=[discovery(cursor={"after": "a="}),
            discovery((second,), cursor={"after": "b="}), discovery((b"third",), cursor={"after": "a="})])
        self.assertEqual(["attestation_cursor_cycle"], artifacts.map_artifact_capture(capture)["reason_codes"])

    def test_snappy_original_retained_and_versioned_replay_decodes_same_json(self):
        encoded = literal(BUNDLE)
        proof = discovery((encoded,))
        proof["bundles"][0]["response"]["response_headers"]["content-type"] = "application/x-snappy"
        capture = self.collect(proofs=[proof])
        mapped = artifacts.map_artifact_capture(capture)
        self.assertEqual(2, capture.manifest["mapper_version"])
        self.assertEqual((BUNDLE,), mapped["bundles"])
        self.assertEqual(encoded, capture.raw_files["raw/r000004-b001.body"])
        self.assertEqual(mapped, artifacts.map_artifact_capture(artifacts.verify_artifact_capture(self.destination)))
        legacy = capture.manifest
        legacy["mapper_version"] = 1
        old = artifacts.parse_artifact_capture(artifacts.canonical_bytes(legacy), capture.raw_files)
        self.assertEqual((encoded,), artifacts.map_artifact_capture(old)["bundles"])

    def test_snappy_invalid_or_oversized_decoding_never_returns_a_candidate(self):
        for index, encoded in enumerate((b"\x01\x00xextra", b"\xff\xff\xff\xff\x0f")):
            self.destination = self.root / str(index)
            proof = discovery((encoded,))
            proof["bundles"][0]["response"]["response_headers"]["content-type"] = "application/x-snappy"
            capture = self.collect(proofs=[proof])
            mapped = artifacts.map_artifact_capture(capture)
            self.assertEqual("error", mapped["status"])
            self.assertEqual((), mapped["bundles"])
            self.assertFalse(mapped["attestations_complete"])
            self.assertEqual(encoded, capture.raw_files["raw/r000004-b001.body"])

    def test_budget_stop_retains_prior_bytes_and_replays_same_failure(self):
        capture = self.collect(limits=replace(artifacts.ArtifactLimits(), max_requests=4))
        mapped = artifacts.map_artifact_capture(artifacts.verify_artifact_capture(self.destination))
        self.assertEqual(["request_limit"], mapped["reason_codes"])
        self.assertEqual(ARCHIVE, mapped["archive_bytes"])
        self.assertFalse(self.attestation_transport.calls)
        self.assertEqual(3, len(capture.raw_files))
        self.destination = self.root / "prefix"
        capture = self.collect(limits=replace(artifacts.ArtifactLimits(), max_metadata_bytes=10))
        self.assertEqual(10, len(capture.raw_files["raw/r000001.body"]))
        self.assertEqual(["response_limit"], artifacts.map_artifact_capture(capture)["reason_codes"])

    def test_manifest_budget_completion_marker_never_claims_unstored_response(self):
        capture = self.collect(limits=replace(artifacts.ArtifactLimits(), max_manifest_bytes=1000))
        self.assertEqual({}, dict(capture.raw_files))
        self.assertEqual(["manifest_limit"], artifacts.map_artifact_capture(capture)["reason_codes"])
        self.assertEqual(["capture.json"], sorted(path.name for path in self.destination.iterdir()))
        self.assertEqual(capture.manifest_bytes, artifacts.verify_artifact_capture(self.destination).manifest_bytes)

    def test_final_request_deadline_preserves_bytes_without_accepting_completion(self):
        with patch.object(artifacts.time, "monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 301]):
            capture = self.collect(proofs=[discovery(())])
        mapped = artifacts.map_artifact_capture(artifacts.verify_artifact_capture(self.destination))
        self.assertEqual("error", mapped["status"])
        self.assertIn("collection_timeout", mapped["reason_codes"])
        self.assertFalse(mapped["attestations_complete"])
        self.assertEqual(4, len(capture.manifest["records"]))
        self.assertEqual(ARCHIVE, mapped["archive_bytes"])

    def test_final_decode_time_is_inside_collection_deadline(self):
        with patch.object(artifacts.time, "monotonic", side_effect=[0, 1, 2, 3, 4, 5, 6, 7, 8, 301]):
            capture = self.collect()
        mapped = artifacts.map_artifact_capture(capture)
        self.assertEqual("error", mapped["status"])
        self.assertIn("collection_timeout", mapped["reason_codes"])
        self.assertFalse(mapped["attestations_complete"])
        self.assertEqual(BUNDLE, capture.raw_files["raw/r000004-b001.body"])

    def test_native_unavailable_error_and_transport_exception_stay_incomplete(self):
        capture = self.collect(values=[response(b'{"message":"authored inaccessible"}', status=403, error="resource_unavailable")])
        mapped = artifacts.map_artifact_capture(capture)
        self.assertEqual(("unavailable", ["resource_unavailable"]), (mapped["status"], mapped["reason_codes"]))
        self.destination = self.root / "exception"
        capture = self.collect(values=[listing(), OSError("do not echo sensitive transport exception")])
        self.assertEqual(["artifact_transport_invalid"], artifacts.map_artifact_capture(capture)["reason_codes"])
        self.assertNotIn(b"sensitive", capture.manifest_bytes)
        self.assertEqual(1, len(capture.raw_files))

    def test_tampered_bytes_extra_files_and_hardlinks_are_refused(self):
        capture = self.collect()
        path = self.destination / "raw/r000003.body"
        path.write_bytes(b"x" + ARCHIVE[1:])
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.verify_artifact_capture(self.destination)
        path.write_bytes(ARCHIVE)
        unexpected = self.destination / "extra"
        unexpected.write_bytes(b"x")
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.verify_artifact_capture(self.destination)
        unexpected.unlink()
        linked = self.root / "linked"
        os.link(path, linked)
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.verify_artifact_capture(self.destination)
        linked.unlink()
        forged = dict(capture.raw_files)
        forged["raw/unexpected.body"] = b"x"
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.parse_artifact_capture(capture.manifest_bytes, forged)

    def test_caller_cannot_smuggle_normalized_acceptance_or_change_record_scope(self):
        capture = self.collect()
        value = capture.manifest
        value["selected"] = True
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.parse_artifact_capture(artifacts.canonical_bytes(value), capture.raw_files)
        value = capture.manifest
        value["records"][0]["request_path"] = value["records"][0]["request_path"].replace("runs/44", "runs/45")
        changed = artifacts.parse_artifact_capture(artifacts.canonical_bytes(value), capture.raw_files)
        self.assertEqual("error", artifacts.map_artifact_capture(changed)["status"])
        self.assertIn("artifact_request_scope_mismatch", artifacts.map_artifact_capture(changed)["reason_codes"])

    def test_malformed_saved_shapes_and_lower_reader_limits_raise_controlled_error(self):
        capture = self.collect()
        changes = [lambda value: value["limits"].pop("max_pages"),
                   lambda value: value["records"][-1]["payload"]["bundles"][0].update(response=[]),
                   lambda value: value["records"][-1]["payload"].update(bundles=[{}] * 11),
                   lambda value: value.update(capture_version=True),
                   lambda value: value["records"][0].update(observed_at="2025-01-01T00:00:00Z")]
        for change in changes:
            value = capture.manifest
            change(value)
            with self.assertRaises(artifacts.ArtifactCaptureError):
                artifacts.parse_artifact_capture(artifacts.canonical_bytes(value), capture.raw_files)
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.verify_artifact_capture(self.destination, limits=replace(artifacts.ArtifactLimits(), max_pages=1))

    def test_invalid_limits_inputs_and_existing_directory_are_refused(self):
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.ArtifactLimits(max_pages=True)
        with self.assertRaises(artifacts.ArtifactCaptureError):
            artifacts.ArtifactLimits(max_requests=201)
        self.destination.mkdir()
        marker = self.destination / "original"
        marker.write_bytes(b"untouched")
        with self.assertRaises(artifacts.ArtifactCaptureError):
            self.collect()
        self.assertEqual(b"untouched", marker.read_bytes())
        self.assertEqual([marker], list(self.destination.iterdir()))


if __name__ == "__main__":
    unittest.main()
