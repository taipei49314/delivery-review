"""Authored route controls: no network, credentials, or platform qualification."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError
from delivery_review.observation_profile import load_observation_profile
from delivery_review.providers.github_routes import (
    API_VERSION, check_run_id_from_url, next_page, parse_pr_url, request_path,
    validate_api_path, validate_record_route, validate_response_headers,
)


class GitHubRouteTests(unittest.TestCase):
    def setUp(self):
        self.profile = load_observation_profile((ROOT / "examples/github-observations/profile.json").read_bytes())
        self.path = request_path(self.profile, "workflow_runs", workflow_id=101, head_sha="b" * 40, page=1)

    def test_constructed_paths_have_fixed_scope_and_canonical_query(self):
        validate_api_path(self.path)
        self.assertIn("?event=pull_request&exclude_pull_requests=false&head_sha=", self.path)
        self.assertTrue(self.path.endswith("&page=1&per_page=100"))
        self.assertEqual(7, parse_pr_url("https://github.com/fixture-owner/FIXTURE-REPO/pull/7", self.profile))
        self.assertEqual(88, check_run_id_from_url("https://api.github.com/repos/fixture-owner/fixture-repo/check-runs/88", self.profile))

    def test_unsafe_routes_credentials_and_extra_query_are_refused(self):
        for path in (self.path + "&token=secret", self.path.replace("event=", "%65vent="),
                     self.path.replace("page=1", "page=0"), self.path + "\n", self.path + "#fragment",
                     "https://api.github.com" + self.path, "//evil.example/repos/o/r",
                     "/repos/o/../pulls/1", "/repos/o/r/actions/runs/1/rerun",
                     "/repos/o/r/check-runs/9007199254740992", self.path + "&page=2"):
            with self.subTest(path=path), self.assertRaises(ContractError):
                validate_api_path(path)

    def test_links_retain_page_limit_diagnostics_but_cannot_change_scope(self):
        next_path = self.path.replace("page=1&", "page=2&")
        headers = {"link": f'<https://api.github.com{next_path}>; rel="next"'}
        self.assertEqual(2, next_page(headers, self.path, max_pages=10))
        with self.assertRaisesRegex(ContractError, "page_limit"):
            next_page(headers, self.path, max_pages=1)
        large = {"link": f'<https://api.github.com{self.path.replace("page=1&", "page=100&")}>; rel="last"'}
        validate_response_headers(large)
        for link in (headers["link"].replace("api.github.com", "evil.example"),
                     headers["link"].replace("workflows/101", "workflows/102"),
                     headers["link"].replace("page=2", "page=1"),
                     headers["link"].replace("page=2", "page=2&token=secret")):
            with self.subTest(link=link), self.assertRaises(ContractError):
                next_page({"link": link}, self.path, max_pages=10)

    def test_record_route_binds_nullable_id_fields_and_role(self):
        record = {"role": "workflow_runs", "phase": "initial", "workflow_id": 101, "run_id": None,
                  "attempt": None, "job_id": None, "check_run_id": None, "page": 1,
                  "request": {"method": "GET", "path": self.path, "api_version": API_VERSION}}
        validate_record_route(record, self.profile)
        for changes in ({"run_id": 10}, {"workflow_id": True}, {"page": 2}, {"role": "repository"},
                        {"role": "pull_request"}, {"phase": "other"}):
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                validate_record_route(dict(record, **changes), self.profile)

    def test_header_allowlist_and_untrusted_url_controls(self):
        for headers in ({"authorization": "secret"}, {"set-cookie": "value"}, {"etag": "x\nsecret"},
                        {"date": "a" * 8193}, {"Link": ""}):
            with self.subTest(headers=headers), self.assertRaises(ContractError):
                validate_response_headers(headers)
        for url in ("https://api.github.com@evil.example/repos/fixture-owner/fixture-repo/check-runs/88",
                    "https://api.github.com/repos/fixture-owner/fixture-repo/check-runs/88?token=x",
                    "https://api.github.com/repos/other/repo/check-runs/88"):
            with self.subTest(url=url), self.assertRaises(ContractError):
                check_run_id_from_url(url, self.profile)


if __name__ == "__main__":
    unittest.main()
