"""Executable raw mapping controls over explicitly synthetic capture bytes."""
from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import ContractError, canonical_bytes, canonical_digest
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


def omitted_listings(*, absent=False, **options):
    fixture = CaptureFixture(**options)
    for phase in ("initial", "final"):
        def omit(payload):
            row = payload["workflow_runs"][0]
            if absent:
                row.pop("pull_requests")
            else:
                row["pull_requests"] = []
        mutate(fixture, "workflow_runs", omit, phase)
    return fixture


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


class ListingPrecedenceTests(unittest.TestCase):
    def test_v1_canonical_bytes_are_unchanged_across_version_dispatch(self):
        # Recorded from pre-ADR-002 mapper source 8747e16, before implementing v2.
        # Fixtures are authored responses, not recorded GitHub observations.
        happy = CaptureFixture()
        partial = omitted_listings()
        stale = omitted_listings()
        mutate(stale, "pull_request", lambda row: row["head"].update(sha="d" * 40), "final")
        cases = ((happy, "a0423df48216c7f91785dce2d53c695f25f682650e32f21d7a2de171f808f11c"),
                 (partial, "c2a1afa6ae263a97181b88d658666c93442a99b3b66f5823b65b54fb90165a72"),
                 (stale, "498928cbd85914aabb647dea890f1835b72e4de45cf340f9a999e34dcefd338a"))
        for fixture, digest in cases:
            with self.subTest(digest=digest):
                result = map_capture(fixture.seal())
                self.assertEqual(digest, canonical_digest(result))
                self.assertEqual(1, result["mapper_version"])
                self.assertNotIn("listing_association", result["workflows"][0]["selection"])

    def test_only_v2_resolves_absent_or_empty_listing_associations_with_raw_provenance(self):
        for absent in (False, True):
            with self.subTest(absent=absent):
                fixture = omitted_listings(absent=absent, mapper_version=2)
                result = map_capture(fixture.seal())
                workflow = result["workflows"][0]
                self.assertEqual("complete", result["collection_status"])
                self.assertEqual("matched", workflow["association"])
                self.assertEqual("matched", workflow["expected_jobs"][0]["status"])
                self.assertEqual("current", result["captured_freshness"])
                self.assertEqual("unknown", result["freshness"])
                self.assertEqual("not_established", result["tested_source"])
                self.assertEqual(2, result["mapper_version"])
                self.assertTrue({"pr_association_unconfirmed", "listing_pr_association_resolved"} <= set(result["reasons"]))
                for phase, selection, detail in (("initial", workflow["selection"], workflow["run"]),
                                                 ("final", workflow["recheck"]["selection"], workflow["recheck"]["run"])):
                    original = selection["listing_association"]
                    self.assertEqual("unconfirmed", original["status"])
                    self.assertEqual(["pr_association_unconfirmed"], original["reasons"])
                    self.assertEqual([fixture.find("workflow_runs", phase)["id"]], original["raw_refs"])
                    self.assertEqual("resolved", original["resolution"])
                    self.assertEqual(sorted(detail["raw_refs"] + workflow["attempt"]["raw_refs"]), original["resolved_by"])
                    self.assertIn("pr_association_unconfirmed", selection["reasons"])
                    if phase == "final":
                        self.assertTrue(set(original["resolved_by"]) <= set(workflow["recheck"]["raw_refs"]))
                fixture.mapper_version = 1
                self.assertEqual("partial", map_capture(fixture.seal())["collection_status"])

    def test_null_malformed_ambiguous_and_conflicting_listings_cannot_be_resolved_or_fallback(self):
        correct = run_document()["pull_requests"][0]
        wrong_head = deepcopy(correct)
        wrong_head["head"]["sha"] = "d" * 40
        cases = (None, {}, "missing", [{}], [{"number": "7"}], [correct, correct], [{"number": 8}], [wrong_head])
        for association in cases:
            with self.subTest(association=association):
                fixture = omitted_listings(mapper_version=2)
                newer = run_document()
                newer["pull_requests"] = association
                mutate(fixture, "workflow_runs", lambda row: row.update(total_count=2,
                    workflow_runs=[run_document(run_id=900, number=7), newer]))
                result = map_capture(fixture.seal())
                workflow = result["workflows"][0]
                self.assertEqual(1001, workflow["selection"]["run_id"])
                self.assertNotEqual("resolved", workflow["selection"]["listing_association"]["resolution"])
                self.assertEqual([], workflow["selection"]["listing_association"]["resolved_by"])
                self.assertNotEqual("matched", workflow["association"])
                if association not in ([{"number": 8}], [wrong_head]):
                    self.assertEqual("partial", result["collection_status"])

    def test_other_listing_identity_gaps_or_mismatches_cannot_be_overridden(self):
        for change in (lambda row: row.pop("path"), lambda row: row.update(path=".github/workflows/other.yml"),
                       lambda row: row.pop("head_repository"), lambda row: row["head_repository"].update(id=9999)):
            fixture = omitted_listings(mapper_version=2)
            mutate(fixture, "workflow_runs", lambda payload: change(payload["workflow_runs"][0]))
            result = map_capture(fixture.seal())
            self.assertEqual("partial", result["collection_status"])
            self.assertNotEqual("resolved", result["workflows"][0]["selection"]["listing_association"]["resolution"])

    def test_independent_run_and_attempt_must_match_every_identity_and_suite(self):
        changes = (lambda row: row.update(id=9999), lambda row: row.update(run_number=9),
                   lambda row: row["repository"].update(id=9999), lambda row: row.update(workflow_id=202),
                   lambda row: row.update(event="push"), lambda row: row.update(head_sha="d" * 40),
                   lambda row: row.update(path=".github/workflows/other.yml"),
                   lambda row: row["head_repository"].update(id=9999),
                   lambda row: row.update(pull_requests=[]), lambda row: row.update(pull_requests=[{}]),
                   lambda row: row["pull_requests"][0].update(number=8),
                   lambda row: row["pull_requests"][0]["head"]["repo"].update(id=9999),
                   lambda row: row["pull_requests"][0]["base"]["repo"].update(id=9999),
                   lambda row: row["pull_requests"][0]["head"].update(sha="d" * 40),
                   lambda row: row.pop("check_suite_id"), lambda row: row.update(check_suite_id=9999))
        for role in ("run", "attempt"):
            for index, change in enumerate(changes):
                with self.subTest(role=role, identity=index):
                    fixture = omitted_listings(mapper_version=2)
                    mutate(fixture, role, change)
                    result = map_capture(fixture.seal())
                    self.assertNotEqual("complete", result["collection_status"])
                    self.assertEqual("unresolved", result["workflows"][0]["selection"]["listing_association"]["resolution"])
        fixture = omitted_listings(mapper_version=2)
        mutate(fixture, "workflow", lambda row: row.update(path=".github/workflows/other.yml"))
        self.assertEqual("unresolved", map_capture(fixture.seal())["workflows"][0]["selection"]["listing_association"]["resolution"])

    def test_missing_unavailable_and_out_of_order_detail_cannot_resolve_listing(self):
        for role in ("run", "attempt"):
            for failure in ("missing", "unavailable", "order"):
                with self.subTest(role=role, failure=failure):
                    fixture = omitted_listings(mapper_version=2)
                    record = fixture.find(role)
                    if failure == "missing":
                        fixture.records.remove(record)
                    elif failure == "unavailable":
                        fixture.unavailable(record)
                    else:
                        fixture.records.remove(record)
                        fixture.records.insert(0, record)
                    result = map_capture(fixture.seal())
                    self.assertNotEqual("complete", result["collection_status"])
                    self.assertEqual("unresolved", result["workflows"][0]["selection"]["listing_association"]["resolution"])

    def test_current_attempt_advancement_uses_only_exact_new_attempt_evidence(self):
        fixture = omitted_listings(mapper_version=2, attempt=2, listing_attempt=1)
        result = map_capture(fixture.seal())
        self.assertEqual("complete", result["collection_status"])
        self.assertEqual(2, result["workflows"][0]["selection"]["attempt"])
        self.assertIn("attempt_advanced", result["reasons"])
        fixture.records.remove(fixture.find("attempt"))
        fixture.add("attempt", run_document(attempt=1), attempt=1)
        result = map_capture(fixture.seal())
        self.assertEqual("partial", result["collection_status"])
        self.assertEqual("unresolved", result["workflows"][0]["selection"]["listing_association"]["resolution"])
        self.assertIsNone(result["workflows"][0]["attempt"])

    def test_final_new_run_or_attempt_cannot_reuse_old_detail_and_stale_wins(self):
        for change in ("attempt", "run"):
            with self.subTest(change=change):
                fixture = omitted_listings(mapper_version=2)
                if change == "attempt":
                    mutate(fixture, "run", lambda row: row.update(run_attempt=2), "final")
                else:
                    newer = run_document(run_id=1002, number=9)
                    fixture.records.remove(fixture.find("run", "final"))
                    fixture.add("run", newer, phase="final", run_id=1002)
                    newer = deepcopy(newer)
                    newer["pull_requests"] = []
                    mutate(fixture, "workflow_runs", lambda row: row.update(workflow_runs=[newer]), "final")
                result = map_capture(fixture.seal(), mode="live")
                self.assertEqual("partial", result["collection_status"])
                self.assertEqual("stale", result["freshness"])
                workflow = result["workflows"][0]
                self.assertEqual("resolved", workflow["selection"]["listing_association"]["resolution"])
                self.assertEqual("unresolved", workflow["recheck"]["selection"]["listing_association"]["resolution"])

    def test_final_detail_order_blocks_resolution_and_does_not_claim_current(self):
        for reverse_all in (False, True):
            fixture = omitted_listings(mapper_version=2)
            if reverse_all:
                fixture.records.sort(key=lambda record: record["phase"] != "final")
            else:
                final = fixture.find("run", "final")
                fixture.records.remove(final)
                fixture.records.insert(fixture.records.index(fixture.find("workflow_runs", "final")), final)
            result = map_capture(fixture.seal())
            self.assertEqual("error", result["collection_status"])
            self.assertEqual("unknown", result["captured_freshness"])
            self.assertEqual("unresolved", result["workflows"][0]["recheck"]["selection"]["listing_association"]["resolution"])

    def test_resolved_association_never_resolves_unknown_native_state_or_erases_failure(self):
        for role in ("workflow_runs", "run", "attempt", "jobs", "check_run"):
            for invalid in ({"status": "future_state", "conclusion": None}, {"status": "completed", "conclusion": None}):
                with self.subTest(role=role, invalid=invalid):
                    fixture = omitted_listings(mapper_version=2)
                    def change(payload):
                        row = payload["workflow_runs"][0] if role == "workflow_runs" else payload["jobs"][0] if role == "jobs" else payload
                        row.update(invalid)
                    mutate(fixture, role, change)
                    result = map_capture(fixture.seal())
                    self.assertEqual("partial", result["collection_status"])
                    self.assertEqual("resolved", result["workflows"][0]["selection"]["listing_association"]["resolution"])
        fixture = omitted_listings(mapper_version=2)
        for role in ("run", "attempt", "check_run"):
            mutate(fixture, role, lambda row: row.update(conclusion="failure"))
        mutate(fixture, "jobs", lambda row: row["jobs"][0].update(conclusion="failure"))
        result = map_capture(fixture.seal())
        self.assertEqual("complete", result["collection_status"])
        self.assertEqual("failure", result["workflows"][0]["jobs"][0]["conclusion"])

    def test_unrelated_pagination_check_and_parse_failures_survive_resolution(self):
        for problem in ("app", "missing_check", "json", "pagination"):
            with self.subTest(problem=problem):
                fixture = omitted_listings(mapper_version=2)
                if problem == "app":
                    mutate(fixture, "check_run", lambda row: row["app"].update(id=9999))
                elif problem == "missing_check":
                    fixture.records.remove(fixture.find("check_run"))
                elif problem == "json":
                    fixture.set_payload(fixture.find("check_run"), b'{"id":4001,"id":4002}')
                else:
                    mutate(fixture, "jobs", lambda row: row.update(total_count=2))
                result = map_capture(fixture.seal())
                self.assertEqual("resolved", result["workflows"][0]["selection"]["listing_association"]["resolution"])
                expected = "complete" if problem == "app" else "error" if problem == "json" else "partial"
                self.assertEqual(expected, result["collection_status"])
                if problem == "app":
                    self.assertEqual("mismatch", result["workflows"][0]["association"])

    def test_v2_is_deterministic_without_io_and_unknown_versions_are_rejected(self):
        capture = omitted_listings(mapper_version=2).seal()
        before = capture.manifest_bytes, capture.profile_bytes, dict(capture.bodies)
        with patch("builtins.open", side_effect=AssertionError("IO")), patch("pathlib.Path.open", side_effect=AssertionError("IO")), \
             patch("socket.socket", side_effect=AssertionError("network")), patch("subprocess.run", side_effect=AssertionError("subprocess")):
            self.assertEqual(canonical_bytes(map_capture(capture)), canonical_bytes(map_capture(capture)))
        self.assertEqual(before, (capture.manifest_bytes, capture.profile_bytes, dict(capture.bodies)))
        for version in (None, True, "2", 0, 3):
            with self.subTest(version=version), self.assertRaises(ContractError):
                map_capture(SimpleNamespace(manifest={"mapper_version": version}))


if __name__ == "__main__":
    unittest.main()
