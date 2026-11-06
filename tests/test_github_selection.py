"""Authored, fictional API-shaped selector controls, not GitHub recordings.

All inputs are raw JSON bytes plus request/response capture records. None is a
prefilled normalized report, a producer receipt, or E01-E18 live qualification.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.providers import select_workflow_runs

REPOSITORY = {"id": 9001, "full_name": "fixture-owner/fixture-repo"}
PR = {"number": 7, "head": {"sha": "b" * 40, "repo": {"id": 9001}},
      "base": {"sha": "a" * 40, "repo": {"id": 9001}}}
WORKFLOW = {"id": 101, "path": ".github/workflows/ci.yml", "event": "pull_request",
            "app_id": 9009, "job_names": ["pytest"]}


def run(run_id=1001, number=8, attempt=1, status="completed", conclusion="success"):
    return {"id": run_id, "run_number": number, "run_attempt": attempt,
            "workflow_id": WORKFLOW["id"], "path": WORKFLOW["path"],
            "event": "pull_request", "head_sha": PR["head"]["sha"],
            "repository": deepcopy(REPOSITORY), "head_repository": {"id": 9001},
            "pull_requests": [deepcopy(PR)], "status": status, "conclusion": conclusion,
            "created_at": "2026-10-28T23:50:00Z", "updated_at": "2026-10-28T23:55:00Z"}


def request_path(page=1, per_page=100):
    return (f"/repos/{REPOSITORY['full_name']}/actions/workflows/{WORKFLOW['id']}/runs"
            f"?event=pull_request&exclude_pull_requests=false&head_sha={PR['head']['sha']}&page={page}&per_page={per_page}")


def capture(rows, *, page=1, per_page=100, total=None, next_page=None):
    raw = json.dumps({"total_count": len(rows) if total is None else total,
                      "workflow_runs": rows}, indent=2).encode() + b"\n"
    path = f"raw/runs-{page}.json"
    record = {"request": {"method": "GET", "path": request_path(page, per_page),
                           "api_version": "2026-03-10"},
              "status_code": 200, "response_headers": {}, "error": None,
              "body": {"path": path, "size_bytes": len(raw),
                       "sha256": hashlib.sha256(raw).hexdigest(), "complete": True}}
    if next_page is not None:
        record["response_headers"]["link"] = f'<https://api.github.com{request_path(next_page, per_page)}>; rel="next"'
    return record, {path: raw}


def select(rows=None, **options):
    record, bodies = capture([run()] if rows is None else rows)
    return select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW,
                                pages=[record], bodies=bodies, **options)


class GitHubSelectionTests(unittest.TestCase):
    def test_raw_mapping_retains_identity_without_checkout_attempt_or_readiness_claims(self):
        record, bodies = capture([run()])
        result = select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW,
                                      pages=[record], bodies=bodies)
        self.assertEqual("selected", result["status"])
        self.assertEqual("confirmed", result["association"])
        self.assertEqual((1001, 8, 1), tuple(result["selected"][k] for k in ("id", "run_number", "run_attempt")))
        self.assertEqual("unconfirmed", result["checkout_source"])
        self.assertEqual("unconfirmed", result["attempt_status"])
        self.assertEqual(record["body"]["sha256"], result["raw_refs"][0]["sha256"])
        self.assertTrue(result["listing_complete"])
        self.assertFalse({"ready_for_review", "state", "binding_level"} & result.keys())

    def test_candidate_scope_is_exact_and_absence_is_not_success(self):
        for field, value in (("repository", {"id": 9999}), ("workflow_id", 9999),
                             ("event", "push"), ("head_sha", "c" * 40)):
            with self.subTest(field=field):
                candidate = run()
                candidate[field] = value
                result = select([candidate])
                self.assertEqual("missing", result["status"])
                self.assertIsNone(result["selected"])
                self.assertFalse(result["history"][0]["eligible"])
        self.assertEqual("missing", select([])["status"])
        for field in ("repository", "head_sha", "event"):
            with self.subTest(missing_new_run_field=field):
                newer = run(1002, 9)
                del newer[field]
                result = select([run(), newer])
                self.assertEqual("error", result["status"])
                self.assertIsNone(result["selected"])
        page, bodies = capture([run()])
        fork = deepcopy(PR)
        fork["head"]["repo"]["id"] = 9002
        result = select_workflow_runs(repository=REPOSITORY, pr=fork, workflow=WORKFLOW, pages=[page], bodies=bodies)
        self.assertEqual(["unsupported_fork"], result["reasons"])
        self.assertEqual("unconfirmed", result["status"])

    def test_new_pending_run_beats_old_success_independent_of_order_id_or_updated_time(self):
        old, new = run(9999, 8), run(1, 9, status="pending", conclusion=None)
        old["updated_at"] = "2026-11-06T00:00:00Z"
        first = select([old, new])
        second = select([new, old])
        self.assertEqual({k: v for k, v in first.items() if k != "raw_refs"},
                         {k: v for k, v in second.items() if k != "raw_refs"})
        self.assertNotEqual(first["raw_refs"], second["raw_refs"])
        self.assertEqual(1, first["selected"]["id"])
        self.assertEqual("pending", first["selected"]["status"])
        self.assertIsNone(first["selected"]["conclusion"])

    def test_failed_listing_attempt_is_preserved_and_unqualified(self):
        result = select([run(1001, 8, 4), run(1002, 9, 2, conclusion="failure")])
        self.assertEqual(1002, result["selected"]["id"])
        self.assertEqual(2, result["selected"]["run_attempt"])
        self.assertEqual("failure", result["selected"]["conclusion"])
        self.assertEqual("unconfirmed", result["attempt_status"])
        self.assertEqual("selected", result["status"])  # Selection is not a pass verdict.

    def test_new_untrusted_metadata_never_falls_back_to_old_green_run(self):
        variants = [("path", ".github/workflows/other.yml", "mismatch"),
                    ("head_repository", {"id": 9999}, "mismatch"),
                    ("pull_requests", [], "unconfirmed"),
                    ("pull_requests", [{**deepcopy(PR), "number": 8}], "mismatch"),
                    ("pull_requests", [deepcopy(PR), deepcopy(PR)], "unconfirmed"),
                    ("head_repository", None, "unconfirmed")]
        for field, value, association in variants:
            with self.subTest(field=field, value=value):
                new = run(1002, 9)
                new[field] = value
                result = select([run(), new])
                self.assertEqual(1002, result["selected"]["id"])
                self.assertEqual("unconfirmed", result["status"])
                self.assertEqual(association, result["association"])
        new = run(1002, 9)
        new["pull_requests"][0]["head"]["sha"] = "c" * 40
        self.assertEqual("mismatch", select([run(), new])["association"])
        for suffix in ("@refs/heads/main", "@" + "c" * 40, "@", "@main@other"):
            with self.subTest(path_suffix=suffix):
                new = run(1002, 9)
                new["path"] += suffix
                result = select([run(), new])
                self.assertEqual(new["path"], result["selected"]["path"])
                self.assertEqual("unconfirmed" if suffix in {"@", "@main@other"} else "selected", result["status"])

    def test_native_non_success_and_unknown_states_are_preserved(self):
        for conclusion in ("failure", "neutral", "cancelled", "skipped", "timed_out"):
            with self.subTest(conclusion=conclusion):
                result = select([run(conclusion=conclusion)])
                self.assertEqual("selected", result["status"])
                self.assertEqual(conclusion, result["selected"]["conclusion"])
        for status, conclusion, reason in (("future_state", None, "unsupported_native_status"),
                                           ("completed", "future_result", "unsupported_native_status"),
                                           ("completed", None, "invalid_native_state"),
                                           ("in_progress", "success", "invalid_native_state")):
            with self.subTest(status=status, conclusion=conclusion):
                result = select([run(status=status, conclusion=conclusion)])
                self.assertEqual("unconfirmed", result["status"])
                self.assertEqual(conclusion, result["selected"]["conclusion"])
                self.assertIn(reason, result["reasons"])

    def test_complete_pagination_and_missing_page_do_not_have_same_selection(self):
        first, a = capture([run()], per_page=1, total=2, next_page=2)
        second, b = capture([run(1002, 9)], page=2, per_page=1, total=2)
        arguments = dict(repository=REPOSITORY, pr=PR, workflow=WORKFLOW, bodies=a | b, per_page=1)
        complete = select_workflow_runs(pages=[first, second], **arguments)
        self.assertEqual(1002, complete["selected"]["id"])
        self.assertEqual(complete, select_workflow_runs(pages=[second, first], **arguments))
        partial = select_workflow_runs(pages=[first], **arguments)
        self.assertEqual("unconfirmed", partial["status"])
        self.assertIsNone(partial["selected"])
        self.assertIn("pagination_incomplete", partial["reasons"])
        first["response_headers"] = {}
        broken = select_workflow_runs(pages=[first, second], **arguments)
        self.assertIn("pagination_chain_broken", broken["reasons"])

    def test_links_cannot_change_scope_loop_or_request_excluded_associations(self):
        links = [f"http://api.github.com{request_path(2)}", f"https://evil.example{request_path(2)}",
                 f"https://api.github.com{request_path(1)}", f"https://api.github.com{request_path(3)}",
                 f"https://api.github.com{request_path(2)}&exclude_pull_requests=true",
                 f"https://api.github.com{request_path(2)}&page=2",
                 f"https://api.github.com{request_path(2)}\n",
                 "https://api.github.com" + request_path(2).replace("event=", "event=\t"),
                 f"https://api.github.com{request_path(2).replace('&exclude_pull_requests=false', '')}",
                 f"https://api.github.com{request_path(2).replace('workflows/101', 'workflows/102')}"]
        for link in links:
            with self.subTest(link=link):
                page, bodies = capture([run()])
                page["response_headers"]["link"] = f'<{link}>; rel="next"'
                result = select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW, pages=[page], bodies=bodies)
                self.assertEqual("error", result["status"])
        page, bodies = capture([run()])
        page["request"]["path"] += "&exclude_pull_requests=true"
        self.assertEqual("error", select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW,
                                                      pages=[page], bodies=bodies)["status"])

    def test_duplicate_ids_or_run_numbers_are_ambiguous_not_tiebroken(self):
        for rows, reason in (([run(), run()], "duplicate_run_id"),
                             ([run(), run(attempt=2, conclusion="failure")], "duplicate_run_id"),
                             ([run(), run(1002, 8)], "ambiguous_run_sequence")):
            with self.subTest(rows=rows):
                result = select(rows)
                self.assertEqual("unconfirmed", result["status"])
                self.assertIsNone(result["selected"])
                self.assertIn(reason, result["reasons"])

    def test_raw_integrity_and_strict_json_are_checked_before_mapping(self):
        for mutation in ("digest", "size", "missing", "duplicate_json_key", "truncated", "permission", "float_http", "bool_http"):
            with self.subTest(mutation=mutation):
                page, bodies = capture([run()])
                path = page["body"]["path"]
                expected = "error"
                if mutation == "digest":
                    bodies[path] += b" "
                elif mutation == "size":
                    page["body"]["size_bytes"] += 1
                elif mutation == "missing":
                    bodies.clear()
                elif mutation == "duplicate_json_key":
                    raw = b'{"total_count":0,"total_count":0,"workflow_runs":[]}'
                    bodies[path] = raw
                    page["body"].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                elif mutation == "truncated":
                    page["body"]["complete"] = False
                    expected = "unconfirmed"
                elif mutation in {"float_http", "bool_http"}:
                    page["status_code"] = 200.0 if mutation == "float_http" else True
                else:
                    page["status_code"] = 403
                    expected = "unconfirmed"
                result = select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW, pages=[page], bodies=bodies)
                self.assertEqual(expected, result["status"])
                self.assertIsNone(result["selected"])

    def test_page_bound_and_filtered_thousand_result_cap_remain_unconfirmed(self):
        page, bodies = capture([run()], page=999999999)
        bounded = select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW, pages=[page], bodies=bodies)
        self.assertEqual(["page_limit_exceeded"], bounded["reasons"])
        pages, bodies = [], {}
        for page_number in range(1, 11):
            rows = [run(10000 + n, n + 1) for n in range((page_number - 1) * 100, page_number * 100)]
            page, raw = capture(rows, page=page_number, total=1000, next_page=page_number + 1 if page_number < 10 else None)
            pages.append(page)
            bodies.update(raw)
        capped = select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW, pages=pages, bodies=bodies)
        self.assertEqual("unconfirmed", capped["status"])
        self.assertEqual(["filtered_result_limit_reached"], capped["reasons"])

    def test_saved_report_and_other_attempt_jobs_are_not_fact_inputs(self):
        page, bodies = capture([run()])
        arguments = dict(repository=REPOSITORY, pr=PR, workflow=WORKFLOW, pages=[page])
        original = deepcopy((page, bodies, REPOSITORY, PR, WORKFLOW))
        actual = select_workflow_runs(bodies=bodies, **arguments)
        bodies["raw/saved-report.json"] = b'{"ready_for_review":true,"selected_runs":[]}'
        bodies["raw/old-attempt-jobs.json"] = b'{"jobs":[{"conclusion":"success"}]}'
        self.assertEqual(actual, select_workflow_runs(bodies=bodies, **arguments))
        self.assertEqual(original[0], page)
        self.assertEqual(original[2:], (REPOSITORY, PR, WORKFLOW))

    def test_invalid_candidate_preserves_failure_and_rejection_history_without_selection(self):
        failed, invalid = run(conclusion="failure"), run(1002, 9)
        del invalid["repository"]
        histories = []
        for rows in ([failed, invalid], [invalid, failed]):
            with self.subTest(order=[row["id"] for row in rows]):
                result = select(rows)
                self.assertEqual("error", result["status"])
                self.assertIsNone(result["selected"])
                self.assertFalse(result["listing_complete"])
                history = {row["id"]: row for row in result["history"]}
                self.assertEqual({1001, 1002}, set(history))
                self.assertEqual("failure", history[1001]["conclusion"])
                self.assertTrue(history[1001]["eligible"])
                self.assertFalse(history[1002]["eligible"])
                self.assertIn("invalid_run_eligibility", history[1002]["reasons"])
                self.assertEqual(result["raw_refs"][0]["path"], history[1001]["raw_ref"])
                histories.append(result["history"])
        self.assertEqual(histories[0], histories[1])

    def test_later_page_errors_and_partial_listings_retain_observed_failure(self):
        for problem in ("missing_page", "permission", "invalid_json", "integrity", "unsafe_link"):
            with self.subTest(problem=problem):
                first, a = capture([run(conclusion="failure")], per_page=1, total=2, next_page=2)
                second, b = capture([run(1002, 9)], page=2, per_page=1, total=2)
                pages, bodies = [first, second], a | b
                expected = "error"
                if problem == "missing_page":
                    pages = [first]
                    expected = "unconfirmed"
                elif problem == "permission":
                    second["status_code"] = 403
                    expected = "unconfirmed"
                elif problem == "invalid_json":
                    raw = b"{ invalid json"
                    bodies[second["body"]["path"]] = raw
                    second["body"].update(size_bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
                elif problem == "integrity":
                    bodies[second["body"]["path"]] += b" "
                else:
                    first["response_headers"]["link"] = f'<https://evil.example{request_path(2, 1)}>; rel="next"'
                result = select_workflow_runs(repository=REPOSITORY, pr=PR, workflow=WORKFLOW,
                                              pages=pages, bodies=bodies, per_page=1)
                self.assertEqual(expected, result["status"])
                self.assertIsNone(result["selected"])
                self.assertFalse(result["listing_complete"])
                observed = next(row for row in result["history"] if row["id"] == 1001)
                self.assertEqual("failure", observed["conclusion"])
                self.assertEqual(first["body"]["path"], observed["raw_ref"])

    def test_malformed_pr_associations_remain_unconfirmed_without_older_fallback(self):
        bad_sha = deepcopy(PR)
        bad_sha["head"]["sha"] = 7
        for associations in ([{}], [{"number": "7"}], [bad_sha]):
            with self.subTest(associations=associations):
                newer = run(1002, 9)
                newer["pull_requests"] = associations
                result = select([run(), newer])
                self.assertEqual(1002, result["selected"]["id"])
                self.assertEqual("unconfirmed", result["status"])
                self.assertEqual("unconfirmed", result["association"])
                self.assertIn("pr_association_unconfirmed", result["reasons"])
        newer["path"] = ".github/workflows/wrong.yml"
        self.assertEqual("mismatch", select([run(), newer])["association"])

    def test_repository_locator_rejects_dot_segments_consistently(self):
        page, bodies = capture([run()])
        for locator in ("owner/..", "../repo", "owner/.", "./repo"):
            with self.subTest(locator=locator):
                repository = {**REPOSITORY, "full_name": locator}
                result = select_workflow_runs(repository=repository, pr=PR, workflow=WORKFLOW,
                                              pages=[page], bodies=bodies)
                self.assertEqual("error", result["status"])
                self.assertEqual(["invalid_repository"], result["reasons"])
                self.assertIsNone(result["selected"])


if __name__ == "__main__":
    unittest.main()
