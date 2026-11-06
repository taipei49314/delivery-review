"""Executable raw mapping controls over explicitly synthetic capture bytes."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import canonical_bytes
from delivery_review.providers.github_observations import map_capture
from delivery_review.providers.github_routes import request_path
from observation_fixtures import (BASE, HEAD, NOW, CaptureFixture, check_document,
                                  job_document, make_capture, run_document)


def mutate(fixture, role, change, phase="initial", **identity):
    record = fixture.find(role, phase, **identity)
    payload = fixture.payload(record)
    change(payload)
    fixture.set_payload(record, payload)
    return record


class GitHubObservationTests(unittest.TestCase):
    def test_complete_raw_capture_is_platform_observations_not_readiness(self):
        capture = make_capture()
        result = map_capture(capture)
        self.assertEqual("complete", result["collection_status"])
        self.assertEqual("current", result["captured_freshness"])
        self.assertEqual("unknown", result["freshness"])
        self.assertEqual("not_established", result["tested_source"])
        self.assertEqual(NOW, result["observed_at"])
        self.assertEqual(HEAD, result["subject"]["head_sha"])
        workflow = result["workflows"][0]
        self.assertEqual(1, workflow["selection"]["attempt"])
        self.assertEqual("matched", workflow["jobs"][0]["association"])
        self.assertEqual("matched", workflow["expected_jobs"][0]["status"])
        self.assertEqual([record["id"] for record in capture.manifest["records"]], result["raw_refs"])
        self.assertFalse({"ready_for_review", "state", "exit_code", "minimum_binding"} & result.keys())
        self.assertEqual("current", map_capture(capture, mode="live")["freshness"])

    def test_native_failures_and_non_execution_stay_distinct_in_complete_observations(self):
        for conclusion in ("failure", "skipped", "cancelled", "timed_out", "neutral"):
            with self.subTest(conclusion=conclusion):
                fixture = CaptureFixture()
                mutate(fixture, "run", lambda row: row.update(conclusion=conclusion))
                mutate(fixture, "attempt", lambda row: row.update(conclusion=conclusion))
                mutate(fixture, "jobs", lambda row: row["jobs"][0].update(conclusion=conclusion))
                mutate(fixture, "check_run", lambda row: row.update(conclusion=conclusion))
                result = map_capture(fixture.seal())
                self.assertEqual("complete", result["collection_status"])
                self.assertEqual(conclusion, result["workflows"][0]["run"]["conclusion"])
                self.assertEqual(conclusion, result["workflows"][0]["jobs"][0]["conclusion"])

    def test_new_pending_is_not_replaced_by_old_green_run(self):
        fixture = CaptureFixture()
        pending = run_document(status="pending", conclusion=None)
        mutate(fixture, "workflow_runs", lambda row: row.update(total_count=2, workflow_runs=[run_document(run_id=900, number=7), pending]))
        for role in ("run", "attempt"):
            fixture.set_payload(fixture.find(role), pending)
        mutate(fixture, "jobs", lambda row: row["jobs"][0].update(status="queued", conclusion=None))
        mutate(fixture, "check_run", lambda row: row.update(status="queued", conclusion=None))
        result = map_capture(fixture.seal())
        self.assertEqual(1001, result["workflows"][0]["selection"]["run_id"])
        self.assertEqual("pending", result["workflows"][0]["run"]["status"])
        self.assertIsNone(result["workflows"][0]["jobs"][0]["conclusion"])

    def test_current_run_advances_attempt_and_child_evidence_never_splices_attempts(self):
        fixture = CaptureFixture(attempt=2, listing_attempt=1)
        fixture.profile["workflows"][0]["job_names"] = ["pytest-linux", "lint"]
        mutate(fixture, "jobs", lambda row: row.update(jobs=[job_document(attempt=2, name="lint")]))
        fixture.add("jobs", {"total_count": 1, "jobs": [job_document(job_id=2002, attempt=1)]}, attempt=1)
        result = map_capture(fixture.seal())
        workflow = result["workflows"][0]
        self.assertEqual(2, workflow["selection"]["attempt"])
        self.assertEqual(1, workflow["selection"]["listing_attempt"])
        self.assertEqual(["lint"], [job["name"] for job in workflow["jobs"]])
        self.assertEqual("missing", workflow["expected_jobs"][0]["status"])
        self.assertEqual("partial", result["collection_status"])
        self.assertIn("attempt_advanced", result["reasons"])

    def test_inaccessible_attempt_never_falls_back_and_contradictory_current_attempt_is_unconfirmed(self):
        fixture = CaptureFixture(attempt=2)
        fixture.unavailable(fixture.find("attempt"))
        fixture.add("attempt", run_document(attempt=1), attempt=1)
        result = map_capture(fixture.seal())
        workflow = result["workflows"][0]
        self.assertEqual(2, workflow["selection"]["attempt"])
        self.assertIsNone(workflow["attempt"])
        self.assertEqual("unconfirmed", workflow["jobs"][0]["association"])
        self.assertEqual("partial", result["collection_status"])
        fixture = CaptureFixture(attempt=2)
        mutate(fixture, "run", lambda row: row.update(run_attempt=1, conclusion="failure"))
        result = map_capture(fixture.seal())
        self.assertIsNone(result["workflows"][0]["selection"]["attempt"])
        self.assertEqual("failure", result["workflows"][0]["run"]["conclusion"])
        self.assertIn("run_attempt_mismatch", result["reasons"])

    def test_all_parent_mismatches_survive_a_child_error(self):
        fixture = CaptureFixture()
        def mismatch(row):
            row["repository"]["id"] = 9999
            row.update(workflow_id=202, head_sha="d" * 40, conclusion="failure")
        mutate(fixture, "attempt", mismatch)
        mutate(fixture, "jobs", lambda row: row["jobs"][0].update(check_run_url="https://evil.example/check-runs/4001"))
        result = map_capture(fixture.seal())
        self.assertTrue({"run_repository_mismatch", "run_workflow_mismatch", "run_head_mismatch", "check_url_unconfirmed"}
                        <= set(result["reasons"]))
        self.assertEqual("failure", result["workflows"][0]["attempt"]["conclusion"])
        self.assertEqual("mismatch", result["workflows"][0]["jobs"][0]["association"])

    def test_independent_check_id_app_suite_and_head_are_all_checked(self):
        for field in ("id", "app", "check_suite", "head_sha"):
            with self.subTest(field=field):
                fixture = CaptureFixture()
                mutate(fixture, "check_run", lambda row: row.update({field: "d" * 40 if field == "head_sha" else
                                                                    {"id": 9999} if field in {"app", "check_suite"} else 9999}))
                result = map_capture(fixture.seal())
                self.assertEqual("complete", result["collection_status"])
                self.assertEqual("mismatch", result["workflows"][0]["association"])
                self.assertEqual("matched", result["workflows"][0]["run"]["association"])
                self.assertEqual("mismatch", result["workflows"][0]["jobs"][0]["association"])
                self.assertEqual("mismatch", result["workflows"][0]["expected_jobs"][0]["status"])

    def test_attempt_route_association_does_not_invent_missing_job_fields(self):
        fixture = CaptureFixture()
        mutate(fixture, "jobs", lambda row: [row["jobs"][0].pop(key) for key in ("run_id", "run_attempt", "head_sha")])
        result = map_capture(fixture.seal())
        job = result["workflows"][0]["jobs"][0]
        self.assertEqual("complete", result["collection_status"])
        self.assertEqual("matched", job["association"])
        self.assertTrue(all(job[key] is None for key in ("run_id", "run_attempt", "head_sha")))
        for key, value in (("run_id", 9999), ("run_attempt", 2), ("head_sha", "d" * 40)):
            with self.subTest(key=key):
                fixture = CaptureFixture()
                mutate(fixture, "jobs", lambda row: row["jobs"][0].update({key: value}))
                result = map_capture(fixture.seal())
                self.assertEqual("mismatch", result["workflows"][0]["jobs"][0]["association"])

    def test_missing_and_ambiguous_expected_job_names_remain_partial(self):
        fixture = CaptureFixture()
        mutate(fixture, "jobs", lambda row: row.update(total_count=0, jobs=[]))
        result = map_capture(fixture.seal())
        self.assertEqual("partial", result["collection_status"])
        self.assertIn("job_missing", result["reasons"])
        fixture = CaptureFixture()
        mutate(fixture, "jobs", lambda row: row.update(total_count=2, jobs=[job_document(), job_document(job_id=2002, check_id=4002)]))
        fixture.add("check_run", check_document(check_id=4002), job_id=2002, check_run_id=4002)
        result = map_capture(fixture.seal())
        self.assertEqual(2, len(result["workflows"][0]["jobs"]))
        self.assertEqual("ambiguous", result["workflows"][0]["expected_jobs"][0]["status"])
        self.assertEqual("partial", result["collection_status"])

    def test_job_pagination_is_complete_or_preserves_failure_as_partial(self):
        fixture = CaptureFixture(per_page=1)
        first = mutate(fixture, "jobs", lambda row: row.update(total_count=2, jobs=[job_document(conclusion="failure")]))
        second_path = request_path(fixture.profile, "jobs", workflow_id=101, run_id=1001, attempt=1, page=2)
        first["response_headers"]["link"] = f'<https://api.github.com{second_path}>; rel="next"'
        partial = map_capture(fixture.seal())
        self.assertEqual("partial", partial["collection_status"])
        self.assertEqual("failure", partial["workflows"][0]["jobs"][0]["conclusion"])
        fixture.add("jobs", {"total_count": 2, "jobs": [job_document(job_id=2002, name="lint", check_id=4002)]}, page=2)
        fixture.add("check_run", check_document(check_id=4002), job_id=2002, check_run_id=4002)
        complete = map_capture(fixture.seal())
        self.assertEqual("complete", complete["collection_status"])
        self.assertEqual([2001, 2002], [job["id"] for job in complete["workflows"][0]["jobs"]])

    def test_unknown_native_state_and_parse_error_preserve_known_failure(self):
        fixture = CaptureFixture()
        mutate(fixture, "run", lambda row: row.update(conclusion="failure"))
        mutate(fixture, "jobs", lambda row: row["jobs"][0].update(status="future_state", conclusion=None))
        result = map_capture(fixture.seal())
        self.assertEqual("partial", result["collection_status"])
        self.assertEqual("future_state", result["workflows"][0]["jobs"][0]["status"])
        self.assertEqual("unconfirmed", result["workflows"][0]["jobs"][0]["native_state"])
        self.assertEqual("failure", result["workflows"][0]["run"]["conclusion"])
        fixture.set_payload(fixture.find("check_run"), b"{invalid JSON")
        result = map_capture(fixture.seal())
        self.assertEqual("error", result["collection_status"])
        self.assertEqual("failure", result["workflows"][0]["run"]["conclusion"])

    def test_contradictory_native_state_is_explicitly_unconfirmed_separate_from_identity(self):
        for role in ("run", "attempt", "jobs", "check_run"):
            with self.subTest(role=role):
                fixture = CaptureFixture()
                mutate(fixture, role, lambda row: (row["jobs"][0] if role == "jobs" else row).update(status="completed", conclusion=None))
                result = map_capture(fixture.seal())
                workflow = result["workflows"][0]
                value = workflow["jobs"][0] if role == "jobs" else workflow["jobs"][0]["check"] if role == "check_run" else workflow[role]
                self.assertEqual("unconfirmed", value["native_state"])
                self.assertEqual("matched", workflow["association"])
                self.assertEqual("partial", result["collection_status"])

    def test_recheck_request_order_not_equal_timestamps_controls_freshness(self):
        fixture = CaptureFixture()
        mutate(fixture, "pull_request", lambda row: row["head"].update(sha="d" * 40), "final")
        fixture.records.sort(key=lambda record: 0 if record["phase"] == "final" else 1)
        result = map_capture(fixture.seal(), mode="live")
        self.assertEqual("error", result["collection_status"])
        self.assertEqual("unknown", result["captured_freshness"])
        self.assertEqual("unknown", result["freshness"])
        self.assertIn("recheck_order_invalid", result["reasons"])
        self.assertNotIn("head_sha_changed", result["reasons"])
        self.assertEqual("success", result["workflows"][0]["jobs"][0]["conclusion"])
        fixture = CaptureFixture()
        initial = [record for record in fixture.records if record["phase"] == "initial"]
        final = [record for record in fixture.records if record["phase"] == "final"]
        fixture.records = initial + list(reversed(final))
        result = map_capture(fixture.seal(), mode="live")
        self.assertEqual("error", result["collection_status"])
        self.assertEqual("unknown", result["captured_freshness"])
        self.assertIn("run_observation_order_invalid", result["reasons"])

    def test_partial_final_pr_retains_independently_confirmed_head_drift(self):
        fixture = CaptureFixture()
        def partial_change(row):
            row["head"]["sha"] = "d" * 40
            del row["base"]["sha"]
        mutate(fixture, "pull_request", partial_change, "final")
        result = map_capture(fixture.seal(), mode="live")
        self.assertEqual("error", result["collection_status"])
        self.assertEqual("stale", result["captured_freshness"])
        self.assertEqual("stale", result["freshness"])
        self.assertIn("head_sha_changed", result["reasons"])

    def test_http_and_transport_failures_remain_distinguishable(self):
        for status, reason in ((401, "authentication_unavailable"), (403, "resource_unavailable"),
                               (404, "resource_unavailable"), (429, "rate_limited"), (None, "request_timeout")):
            with self.subTest(status=status):
                fixture = CaptureFixture()
                fixture.unavailable(fixture.find("check_run"), status_code=status, code=reason)
                result = map_capture(fixture.seal())
                self.assertEqual("partial", result["collection_status"])
                self.assertIn(reason, result["reasons"])
                self.assertEqual(1001, result["workflows"][0]["selection"]["run_id"])

    def test_head_base_attempt_and_run_drift_are_stale_without_rewriting_initial_facts(self):
        for field in ("head", "base", "attempt", "run"):
            with self.subTest(field=field):
                fixture = CaptureFixture()
                if field in {"head", "base"}:
                    mutate(fixture, "pull_request", lambda row: row[field].update(sha="d" * 40), "final")
                elif field == "attempt":
                    mutate(fixture, "run", lambda row: row.update(run_attempt=2), "final")
                else:
                    newer = run_document(run_id=1002, number=9)
                    mutate(fixture, "workflow_runs", lambda row: row.update(workflow_runs=[newer]), "final")
                    fixture.records.remove(fixture.find("run", "final"))
                    fixture.add("run", newer, phase="final", run_id=1002)
                result = map_capture(fixture.seal())
                self.assertEqual("stale", result["captured_freshness"])
                self.assertEqual("unknown", result["freshness"])
                self.assertEqual(HEAD, result["subject"]["head_sha"])
                self.assertEqual(BASE, result["subject"]["base_tip_sha"])
                self.assertEqual(1, result["workflows"][0]["selection"]["attempt"])

    def test_known_drift_wins_over_missing_recheck_while_no_drift_stays_unknown(self):
        fixture = CaptureFixture()
        fixture.records.remove(fixture.find("workflow_runs", "final"))
        unknown = map_capture(fixture.seal())
        self.assertEqual("unknown", unknown["captured_freshness"])
        mutate(fixture, "pull_request", lambda row: row["head"].update(sha="d" * 40), "final")
        stale = map_capture(fixture.seal(), mode="live")
        self.assertEqual("stale", stale["captured_freshness"])
        self.assertEqual("stale", stale["freshness"])
        self.assertEqual("partial", stale["collection_status"])

    def test_final_count_mismatch_is_partial_and_cannot_hide_initial_failure(self):
        fixture = CaptureFixture()
        mutate(fixture, "jobs", lambda row: row["jobs"][0].update(conclusion="failure"))
        mutate(fixture, "workflow_runs", lambda row: row.update(total_count=2), "final")
        result = map_capture(fixture.seal())
        self.assertEqual("partial", result["collection_status"])
        self.assertEqual("unknown", result["captured_freshness"])
        self.assertIn("listing_count_mismatch", result["reasons"])
        self.assertEqual("partial", result["workflows"][0]["recheck"]["selection"]["status"])
        self.assertEqual("failure", result["workflows"][0]["jobs"][0]["conclusion"])

    def test_complete_absence_can_be_current_but_expected_jobs_are_still_missing(self):
        fixture = CaptureFixture()
        for phase in ("initial", "final"):
            mutate(fixture, "workflow_runs", lambda row: row.update(total_count=0, workflow_runs=[]), phase)
        fixture.records = [record for record in fixture.records if record["role"] not in {"run", "attempt", "jobs", "check_run"}]
        result = map_capture(fixture.seal())
        self.assertEqual("current", result["captured_freshness"])
        self.assertEqual("partial", result["collection_status"])
        self.assertEqual("none", result["workflows"][0]["selection"]["status"])
        self.assertIn("job_missing", result["reasons"])

    def test_initial_source_cannot_be_fabricated_or_expanded_to_forks(self):
        fixture = CaptureFixture()
        mutate(fixture, "pull_request", lambda row: row["head"].pop("sha"))
        result = map_capture(fixture.seal())
        self.assertIsNone(result["subject"])
        self.assertEqual("error", result["collection_status"])
        fixture = CaptureFixture()
        mutate(fixture, "repository", lambda row: row.update(id=9999))
        self.assertIsNone(map_capture(fixture.seal())["subject"])
        fixture = CaptureFixture()
        mutate(fixture, "pull_request", lambda row: row["head"]["repo"].update(id=9999))
        result = map_capture(fixture.seal())
        self.assertEqual("partial", result["collection_status"])
        self.assertIn("unsupported_fork", result["reasons"])
        self.assertEqual([], result["workflows"])

    def test_same_verified_bytes_map_deterministically_without_io_or_saved_report(self):
        capture = make_capture()
        before = (capture.manifest_bytes, capture.profile_bytes, dict(capture.bodies))
        with patch("builtins.open", side_effect=AssertionError("IO")), patch("pathlib.Path.open", side_effect=AssertionError("IO")), \
             patch("socket.socket", side_effect=AssertionError("network")), patch("subprocess.run", side_effect=AssertionError("subprocess")):
            first = canonical_bytes(map_capture(capture))
            second = canonical_bytes(map_capture(capture))
        self.assertEqual(first, second)
        self.assertEqual(before, (capture.manifest_bytes, capture.profile_bytes, dict(capture.bodies)))
        self.assertEqual(NOW, map_capture(capture)["observed_at"])
        with self.assertRaises(ValueError):
            map_capture(capture, mode="guess")


if __name__ == "__main__":
    unittest.main()
