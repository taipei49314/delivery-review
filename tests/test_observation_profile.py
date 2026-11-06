"""The observation-only profile cannot masquerade as a readiness policy."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import ContractError, validate_policy
from delivery_review.observation_profile import load_observation_profile


class ObservationProfileTests(unittest.TestCase):
    def setUp(self):
        self.raw = (ROOT / "examples/github-observations/profile.json").read_bytes()
        self.profile = json.loads(self.raw)

    def test_authored_example_is_valid_and_has_no_readiness_claim(self):
        Draft202012Validator.check_schema(json.loads(
            (ROOT / "formats/github-observation-profile-v1.schema.json").read_bytes()))
        profile = load_observation_profile(self.raw)
        self.assertEqual("latest_applicable_run_number_v1", profile["selection"])
        self.assertNotIn("ready_for_review", profile)
        with self.assertRaises(ContractError):
            validate_policy(profile)

    def test_versions_source_modes_events_and_unknown_fields_are_not_coerced(self):
        mutations = [
            lambda p: p.update(profile_version=True), lambda p: p.update(profile_version=2),
            lambda p: p.update(api_version="latest"), lambda p: p.update(source_mode="github_merge"),
            lambda p: p.update(selection="latest_success"), lambda p: p.update(ready_for_review=True),
            lambda p: p["workflows"][0].update(event="workflow_dispatch"),
            lambda p: p["workflows"][0].update(producer_path=".github/workflows/ci.yml"),
        ]
        for mutate in mutations:
            profile = deepcopy(self.profile)
            mutate(profile)
            with self.subTest(profile=profile), self.assertRaises(ContractError):
                load_observation_profile(json.dumps(profile).encode())

    def test_duplicate_or_ambiguous_selectors_are_rejected(self):
        for duplicate in ("id", "path", "job_name"):
            profile = deepcopy(self.profile)
            if duplicate == "job_name":
                profile["workflows"][0]["job_names"] *= 2
            else:
                second = deepcopy(profile["workflows"][0])
                second["path" if duplicate == "id" else "id"] = ".github/workflows/other.yml" if duplicate == "id" else 202
                profile["workflows"].append(second)
            with self.subTest(duplicate=duplicate), self.assertRaises(ContractError):
                load_observation_profile(json.dumps(profile).encode())

    def test_request_inputs_and_resource_limits_are_bounded(self):
        controls = [
            lambda p: p["repository"].update(id=True),
            lambda p: p["repository"].update(full_name="owner/repo?token=secret"),
            lambda p: p["repository"].update(full_name="owner/repo\n"),
            lambda p: p["repository"].update(full_name="owner/.."),
            lambda p: p["repository"].update(full_name="../repo"),
            lambda p: p.update(profile_id="valid\n"),
            lambda p: p["workflows"][0].update(path="../workflow.yml"),
            lambda p: p["workflows"][0].update(path=".github/workflows/ci.yml\n"),
            lambda p: p["limits"].update(max_response_bytes=2097153),
            lambda p: p["limits"].update(per_page=101),
            lambda p: p["limits"].update(timeout_seconds=0),
            lambda p: p["limits"].update(max_pages=True),
        ]
        for mutate in controls:
            profile = deepcopy(self.profile)
            mutate(profile)
            with self.subTest(profile=profile), self.assertRaises(ContractError):
                load_observation_profile(json.dumps(profile).encode())

    def test_duplicate_json_keys_are_rejected(self):
        with self.assertRaises(ContractError):
            load_observation_profile(self.raw.replace(b'"profile_version": 1', b'"profile_version": 1, "profile_version": 1'))


if __name__ == "__main__":
    unittest.main()
