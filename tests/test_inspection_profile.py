"""Approved selectors cannot change under an unchanged policy identity."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import ContractError, canonical_bytes, canonical_digest
from delivery_review.inspection_profile import load_inspection_profile, seal_inspection_profile, PREDICATE


def profile():
    policy = json.loads((ROOT / "examples/policy.json").read_bytes())
    github = json.loads((ROOT / "examples/github-observations/profile.json").read_bytes())
    test = policy["requirements"][0]
    workflow = github["workflows"][0]
    test["selector"].update(workflow_id=workflow["id"], app_id=workflow["app_id"], job_name=workflow["job_names"][0])
    for name in ("checkwash", "pinwash"):
        policy["requirements"].append({"id": name, "kind": "scanner", "required": True,
                                       "minimum_binding": "reported", "selector": {"engine": name}})
    policy["requirements"].append({"id": "ledgerwash", "kind": "ledger", "required": False,
                                   "minimum_binding": "reported", "selector": {"engine": "ledgerwash"}})
    return {"kind": "delivery-review-inspection-profile", "profile_version": 1, "name": "authored-example",
            "policy": policy, "github": github,
            "producers": {test["id"]: {"repository_full_name": "fixture-owner/producer",
                "artifact_name_rule": "delivery_review_run_attempt_check_v1", "receipt_member": "receipt.json",
                "junit_member": "junit.xml", "dialect": "pytest-8.4.2-xunit2", "predicate_type": PREDICATE}},
            "engine_options": {"checkwash": {"threshold": "high"}, "pinwash": {"threshold": "high"},
                               "ledgerwash": {"threshold": "high", "require_complete": True}}}


class InspectionProfileTests(unittest.TestCase):
    def test_declared_source_and_platform_job_obligations_are_supported(self):
        document = profile()
        document["policy"]["requirements"].extend([
            {"id": "source", "kind": "source", "required": True,
             "minimum_binding": "reported", "selector": {}},
            {"id": "ci", "kind": "ci_job", "required": True,
             "minimum_binding": "platform_bound", "selector": {
                 "workflow_id": 101, "app_id": 15368, "job_name": "pytest-linux"}}])
        saved = load_inspection_profile(seal_inspection_profile(document))
        self.assertEqual(6, len(saved["policy"]["requirements"]))

    def test_sealing_is_deterministic_and_does_not_mutate_input(self):
        original = profile()
        before = deepcopy(original)
        raw = seal_inspection_profile(original)
        saved = load_inspection_profile(raw)
        self.assertEqual(before, original)
        self.assertEqual(raw, seal_inspection_profile(saved))
        basis = deepcopy(saved)
        basis["policy"].pop("policy_id")
        self.assertEqual("profile-" + canonical_digest(basis), saved["policy"]["policy_id"])

    def test_external_selector_changes_require_a_new_policy_identity(self):
        original = load_inspection_profile(seal_inspection_profile(profile()))
        changes = [lambda p: p["github"]["workflows"][0].update(path=".github/workflows/other.yml"),
                   lambda p: p["github"]["limits"].update(max_requests=1),
                   lambda p: p["producers"]["pytest-linux"].update(repository_full_name="different/producer"),
                   lambda p: p["engine_options"]["ledgerwash"].update(require_complete=False),
                   lambda p: p["policy"]["requirements"][0]["selector"].update(min_tests=2)]
        for mutate in changes:
            changed = deepcopy(original)
            mutate(changed)
            with self.subTest(changed=changed), self.assertRaisesRegex(ContractError, "inspection_policy_identity_mismatch"):
                load_inspection_profile(canonical_bytes(changed))
            sealed = load_inspection_profile(seal_inspection_profile(changed))
            self.assertNotEqual(original["policy"]["policy_id"], sealed["policy"]["policy_id"])
            self.assertNotEqual(canonical_digest(original["policy"]), canonical_digest(sealed["policy"]))

    def test_unknown_versions_fields_and_unsupported_protocols_are_not_sealed(self):
        mutations = [lambda p: p.update(profile_version=True), lambda p: p.update(profile_version=2),
                     lambda p: p.update(extra="ignored"), lambda p: p["policy"].update(repository_id=999),
                     lambda p: p["producers"]["pytest-linux"].update(dialect="generic-junit"),
                     lambda p: p["producers"]["pytest-linux"].update(junit_member="../junit.xml"),
                     lambda p: p["producers"]["pytest-linux"].update(predicate_type="unapproved"),
                     lambda p: p["engine_options"]["checkwash"].update(threshold="critical"),
                     lambda p: p["engine_options"]["ledgerwash"].update(require_complete=1),
                     lambda p: p["policy"]["engine_pins"][0].update(version="unqualified"),
                     lambda p: p["policy"]["requirements"][0]["selector"].update(job_name="different"),
                     lambda p: p["policy"]["requirements"][0]["selector"].update(matrix={"python": "3.11"}),
                     lambda p: p["policy"]["requirements"][0].update(minimum_binding="reported"),
                     lambda p: p["policy"]["requirements"][1].update(required=False),
                     lambda p: p["producers"].clear()]
        for mutate in mutations:
            changed = profile()
            mutate(changed)
            with self.subTest(change=changed), self.assertRaises(ContractError):
                seal_inspection_profile(changed)


if __name__ == "__main__":
    unittest.main()
