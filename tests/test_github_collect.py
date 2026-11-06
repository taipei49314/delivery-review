"""Scripted acquisition of authored raw responses; no live product qualification."""
from copy import deepcopy
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError, canonical_bytes
from delivery_review.evidence.capture import CaptureWriter, verify_capture
from delivery_review.observations import render_markdown, replay_observations
from delivery_review.providers.github_collect import collect_observations
from delivery_review.providers.github_observations import map_capture
from observation_fixtures import CaptureFixture, NOW, make_capture

PR_URL = "https://github.com/fixture-owner/fixture-repo/pull/7"
IDENTITY = {"version": "synthetic-test-fixture", "source_sha": "1" * 40}


class ScriptedTransport:
    def __init__(self, capture):
        self.capture = capture
        self.pending = deepcopy(capture.manifest["records"])
        self.calls = []

    def get(self, path, **limits):
        self.calls.append((path, limits))
        if not self.pending:
            raise AssertionError("unexpected extra network operation")
        record = self.pending.pop(0)
        if record["request"]["path"] != path:
            raise AssertionError((record["request"]["path"], path))
        entry = record["body"]
        body = self.capture.bodies[entry["path"]] if entry else None
        return {"status_code": record["status_code"], "response_headers": record["response_headers"],
                "body": body, "complete": entry["complete"] if entry else False, "error": record["error"]}


def write_capture(capture, destination):
    writer = CaptureWriter(destination, capture.profile_bytes, collector=IDENTITY, observed_at=NOW)
    for record in capture.manifest["records"]:
        entry = record["body"]
        writer.record({k: v for k, v in record.items() if k not in {"id", "body"}},
                      capture.bodies[entry["path"]] if entry else None,
                      complete=entry["complete"] if entry else False)
    return writer.seal(**{"status": capture.manifest["termination"]["status"],
                          "reason_codes": capture.manifest["termination"]["reason_codes"]})


class GitHubCollectTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def collect(self, capture, name="capture"):
        transport = ScriptedTransport(capture)
        result = collect_observations(PR_URL, capture.profile_bytes, self.root / name,
                                      transport=transport, collector=IDENTITY, clock=lambda: NOW)
        return result, transport

    def test_capture_to_live_observations_and_raw_offline_replay(self):
        source = make_capture(attempt=2, listing_attempt=1)
        captured, transport = self.collect(source)
        self.assertFalse(transport.pending)
        self.assertEqual(source.bodies, captured.bodies)
        live = map_capture(captured, mode="live")
        self.assertEqual("complete", live["collection_status"])
        self.assertEqual("current", live["freshness"])
        self.assertEqual(2, live["workflows"][0]["selection"]["attempt"])
        self.assertTrue(any("/attempts/2/jobs" in path for path, _ in transport.calls))
        self.assertFalse(any("/attempts/1/" in path for path, _ in transport.calls))
        before = {str(p.relative_to(self.root / "capture")): p.read_bytes()
                  for p in (self.root / "capture").rglob("*") if p.is_file()}
        with patch("socket.socket", side_effect=AssertionError("network in replay")), \
                patch("subprocess.Popen", side_effect=AssertionError("subprocess in replay")):
            first = replay_observations(self.root / "capture", self.root / "first")
            second = replay_observations(self.root / "capture", self.root / "second")
        self.assertEqual(first, second)
        self.assertEqual("unknown", first["freshness"])
        self.assertEqual("current", first["captured_freshness"])
        self.assertEqual(NOW, first["observed_at"])
        self.assertEqual(live, {**first, "mode": "live", "freshness": "current"})
        self.assertEqual((self.root / "first/observations.json").read_bytes(),
                         (self.root / "second/observations.json").read_bytes())
        self.assertFalse((self.root / "capture/report.json").exists())
        after = {str(p.relative_to(self.root / "capture")): p.read_bytes()
                 for p in (self.root / "capture").rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_inspection_stage_receives_immutable_initial_facts_before_final_recheck(self):
        source = make_capture()
        transport = ScriptedTransport(source)
        staged = []
        def stage(capture):
            staged.append(capture)
            self.assertEqual("final", transport.pending[0]["phase"])
            self.assertFalse(any(row["phase"] == "final" for row in capture.manifest["records"]))
            self.assertEqual(["collection_in_progress"], capture.manifest["termination"]["reason_codes"])
            self.assertFalse((self.root / "staged/capture.json").exists())
            with self.assertRaises(TypeError):
                capture.bodies["raw/changed.body"] = b"altered"
        completed = collect_observations(PR_URL, source.profile_bytes, self.root / "staged", transport=transport,
                                         collector=IDENTITY, clock=lambda: NOW, before_recheck=stage)
        self.assertEqual(1, len(staged))
        self.assertFalse(transport.pending)
        self.assertEqual("completed", completed.manifest["termination"]["status"])
        self.assertFalse(any(row["phase"] == "final" for row in staged[0].manifest["records"]))
        self.assertEqual("current", map_capture(completed, mode="live")["freshness"])

    def test_failed_inspection_stage_still_records_final_freshness_without_success_marker(self):
        source = make_capture()
        transport = ScriptedTransport(source)
        def stage(capture):
            raise ContractError("untrusted detail must not be persisted as a diagnostic")
        completed = collect_observations(PR_URL, source.profile_bytes, self.root / "stage-failed", transport=transport,
                                         collector=IDENTITY, clock=lambda: NOW, before_recheck=stage)
        self.assertFalse(transport.pending)
        self.assertEqual("stopped", completed.manifest["termination"]["status"])
        self.assertEqual(["inspection_stage_failed"], completed.manifest["termination"]["reason_codes"])
        self.assertNotIn(b"untrusted detail", completed.manifest_bytes)
        self.assertTrue(any(row["phase"] == "final" for row in completed.manifest["records"]))

    def test_request_budget_stops_before_an_extra_network_operation(self):
        source = make_capture()
        profile = source.profile
        profile["limits"]["max_requests"] = 2
        transport = ScriptedTransport(source)
        capture = collect_observations(PR_URL, canonical_bytes(profile), self.root / "limited",
                                       transport=transport, collector=IDENTITY, clock=lambda: NOW)
        self.assertEqual(2, len(transport.calls))
        self.assertEqual("stopped", capture.manifest["termination"]["status"])
        self.assertIn("request_limit", capture.manifest["termination"]["reason_codes"])
        self.assertNotEqual("complete", map_capture(capture)["collection_status"])
        self.assertEqual(capture.manifest_bytes, verify_capture(self.root / "limited").manifest_bytes)

    def test_unavailable_subject_is_saved_without_fake_identities(self):
        fixture = CaptureFixture()
        fixture.unavailable(fixture.find("pull_request"), status_code=403)
        source = fixture.seal()
        capture, transport = self.collect(source)
        self.assertEqual(2, len(transport.calls))
        result = map_capture(capture)
        self.assertIsNone(result["subject"])
        self.assertEqual("error", result["collection_status"])
        self.assertIn("resource_unavailable", result["reasons"])
        self.assertEqual(source.bodies["raw/r000002.body"], capture.bodies["raw/r000002.body"])

    def test_failed_job_survives_check_error_and_final_drift(self):
        fixture = CaptureFixture()
        jobs = fixture.find("jobs")
        payload = fixture.payload(jobs)
        payload["jobs"][0]["conclusion"] = "failure"
        fixture.set_payload(jobs, payload)
        fixture.unavailable(fixture.find("check_run"), status_code=404)
        final_pr = fixture.find("pull_request", "final")
        payload = fixture.payload(final_pr)
        payload["base"]["sha"] = "d" * 40
        fixture.set_payload(final_pr, payload)
        capture, transport = self.collect(fixture.seal())
        self.assertFalse(transport.pending)
        result = map_capture(capture, mode="live")
        self.assertEqual("failure", result["workflows"][0]["jobs"][0]["conclusion"])
        self.assertEqual("stale", result["freshness"])
        self.assertNotEqual("complete", result["collection_status"])

    def test_missing_job_page_seals_a_stopped_capture_and_keeps_known_jobs(self):
        fixture = CaptureFixture()
        jobs = fixture.find("jobs")
        payload = fixture.payload(jobs)
        payload["total_count"] = 2
        payload["jobs"][0]["conclusion"] = "failure"
        fixture.set_payload(jobs, payload)
        capture, _ = self.collect(fixture.seal())
        self.assertEqual("stopped", capture.manifest["termination"]["status"])
        self.assertIn("pagination_incomplete", capture.manifest["termination"]["reason_codes"])
        result = map_capture(capture)
        self.assertEqual("partial", result["collection_status"])
        self.assertEqual("failure", result["workflows"][0]["jobs"][0]["conclusion"])

    def test_existing_destination_and_nested_replay_cannot_mutate_source(self):
        capture, _ = self.collect(make_capture())
        (self.root / "exists").mkdir()
        (self.root / "exists/marker").write_bytes(b"preserve")
        with self.assertRaises(FileExistsError):
            replay_observations(self.root / "capture", self.root / "exists")
        self.assertEqual(b"preserve", (self.root / "exists/marker").read_bytes())
        # ContractError is the expected boundary for a new child of the source.
        from delivery_review.contracts import ContractError
        with self.assertRaises(ContractError):
            replay_observations(self.root / "capture", self.root / "capture/nested")
        self.assertEqual(capture.manifest_bytes, verify_capture(self.root / "capture").manifest_bytes)

    def test_markdown_never_interprets_untrusted_job_markup(self):
        result = map_capture(make_capture())
        result["workflows"][0]["jobs"][0]["name"] = '<script>bad</script>![x](https://evil.example)\n# header|cell'
        rendered = render_markdown(result)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("![x](", rendered)
        self.assertNotIn("\n# header", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_offline_cli_operational_exit_and_rejected_tamper(self):
        write_capture(make_capture(), self.root / "saved")
        command = [sys.executable, str(ROOT / "tools/observations.py"), "replay", str(self.root / "saved"),
                   "--out", str(self.root / "result")]
        completed = subprocess.run(command, capture_output=True, timeout=30, cwd=ROOT)
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertIn(b'"freshness": "unknown"', completed.stdout)
        self.assertIn(b'"tested_source": "not_established"', completed.stdout)
        raw_path = self.root / "saved/raw/r000001.body"
        raw_path.write_bytes(raw_path.read_bytes() + b" ")
        command[-1] = str(self.root / "refused")
        refused = subprocess.run(command, capture_output=True, timeout=30, cwd=ROOT)
        self.assertEqual(2, refused.returncode)
        self.assertFalse((self.root / "refused").exists())

    def test_inspect_and_refresh_cli_use_saved_profile_and_new_destinations(self):
        spec = importlib.util.spec_from_file_location("observation_cli_controls", ROOT / "tools/observations.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        source = make_capture()
        profile_path = self.root / "profile.json"
        profile_path.write_bytes(source.profile_bytes)
        with patch.object(cli, "collector_identity", return_value=IDENTITY), \
                patch("delivery_review.providers.github_http.GitHubTransport", return_value=ScriptedTransport(source)) as factory, \
                patch("sys.stdout", new_callable=io.StringIO):
            code = cli.main(["inspect", PR_URL, "--profile", str(profile_path), "--out", str(self.root / "live"), "--anonymous"])
        self.assertEqual(0, code)
        factory.assert_called_once_with(token=None)
        original = (self.root / "live/capture/capture.json").read_bytes()
        with patch.object(cli, "collector_identity", return_value=IDENTITY), \
                patch("delivery_review.providers.github_http.GitHubTransport", return_value=ScriptedTransport(source)), \
                patch("sys.stdout", new_callable=io.StringIO):
            code = cli.main(["refresh", str(self.root / "live/capture"), "--out", str(self.root / "fresh"), "--anonymous"])
        self.assertEqual(0, code)
        self.assertEqual(original, (self.root / "live/capture/capture.json").read_bytes())
        self.assertEqual(source.profile_bytes, (self.root / "fresh/capture/profile.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
