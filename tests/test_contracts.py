from copy import deepcopy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import (
    ContractError, canonical_digest, load_document, loads_strict, validate_case,
    validate_document, validate_engine_lock, validate_policy, validate_receipt,
    validate_report,
)


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.policy = load_document(ROOT / "examples/policy.json")
        self.report = load_document(ROOT / "examples/report.json")

    def reject(self, report=None, policy=None):
        with self.assertRaises(ContractError):
            validate_report(self.report if report is None else report,
                            self.policy if policy is None else policy)

    def bind_policy(self):
        self.report["policy"]["canonical_sha256"] = canonical_digest(self.policy)

    def state(self, evidence, freshness, policy, exit_code):
        self.report["state"] = {"evidence": evidence, "freshness": freshness, "policy": policy}
        self.report["exit_code"] = exit_code
        self.report["ready_for_review"] = exit_code == 0

    def test_valid_examples_and_candidate_lock(self):
        validate_report(self.report, self.policy)
        validate_receipt(load_document(ROOT / "examples/receipt.json"))
        validate_case(load_document(ROOT / "examples/acceptance-template.json"))
        validate_engine_lock(load_document(ROOT / "locks/engine-candidates.json"))

    def test_json_rejects_duplicate_keys_numbers_encoding_and_depth(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":1.0}', b'{"a":NaN}', b'{"a":1e999}',
                    b'\xef\xbb\xbf{}', b'\xff', b'"\\ud800"',
                    b'[' * 40 + b'0' + b']' * 40, b'9007199254740992'):
            with self.subTest(raw=repr(raw[:50])), self.assertRaises(ContractError):
                loads_strict(raw)

    def test_rejects_unknown_fields_version_and_boolean_identifiers(self):
        for index, mutate in enumerate((
            lambda r: r.update(schema_version=2),
            lambda r: r.update(schema_version=True),
            lambda r: r.update(approved=True),
            lambda r: r["subject"].update(repository_id=True),
            lambda r: r.update(observed_at="2026-02-30T00:00:00Z"),
        )):
            candidate = deepcopy(self.report)
            mutate(candidate)
            with self.subTest(mutation=index):
                self.reject(candidate)

    def test_policy_changes_cannot_silently_redefine_requirements(self):
        self.policy["requirements"][0]["required"] = False
        self.reject()

    def test_no_required_obligations_is_not_an_acceptance_policy(self):
        self.policy["requirements"][0]["required"] = False
        self.bind_policy()
        with self.assertRaises(ContractError):
            validate_policy(self.policy)

    def test_missing_obligation_cannot_disappear_from_report(self):
        self.report["obligations"] = []
        self.reject()

    def test_stale_ci_cannot_support_current_head(self):
        self.report["evidence"][0]["subject_sha"] = "1" * 40
        self.reject()

    def test_valid_merge_source_and_wrong_parents(self):
        self.report["evidence"][0]["subject_sha"] = self.report["subject"]["merge_commit_sha"]
        validate_report(self.report, self.policy)
        self.report["subject"]["merge_parents"][0] = "1" * 40
        self.reject()

    def test_snapshot_drift_is_stale_even_with_complete_evidence(self):
        self.report["recheck"]["head_sha"] = "1" * 40
        self.reject()
        self.state("complete", "stale", "clear", 2)
        validate_report(self.report, self.policy)

    def test_attempt_drift_is_stale(self):
        self.report["recheck"]["selected_runs"][0]["attempt"] = 2
        self.reject()
        self.state("complete", "stale", "clear", 2)
        validate_report(self.report, self.policy)

    def test_offline_replay_cannot_refresh_live_freshness(self):
        self.report["mode"] = "offline_replay"
        self.reject()
        self.state("complete", "unknown", "clear", 2)
        validate_report(self.report, self.policy)

    def test_observed_failure_is_complete_and_blocked(self):
        self.report["obligations"][0].update(outcome="failed", effect="block", reason_codes=["TEST_FAILED"])
        self.report["evidence"][0].update(conclusion="failure", test_counts={"passed":2,"failed":1,"errors":0,"skipped":0})
        self.state("complete", "current", "block", 1)
        validate_report(self.report, self.policy)

    def test_skipped_cannot_be_relabelled_as_complete_failure(self):
        self.report["obligations"][0].update(outcome="failed", effect="block", reason_codes=["TEST_FAILED"])
        self.report["evidence"][0].update(conclusion="skipped", test_counts={"passed":0,"failed":0,"errors":0,"skipped":3})
        self.state("complete", "current", "block", 1)
        self.reject()

    def test_zero_tests_and_collection_errors_cannot_satisfy_success(self):
        for counts in ({"passed":0,"failed":0,"errors":0,"skipped":0},
                       {"passed":0,"failed":0,"errors":0,"skipped":3},
                       {"passed":0,"failed":0,"errors":1,"skipped":0}):
            candidate = deepcopy(self.report)
            candidate["evidence"][0]["test_counts"] = counts
            self.reject(candidate)

    def test_coverage_error_does_not_hide_existing_blocker(self):
        self.report["obligations"][0].update(outcome="error", effect="block", reason_codes=["REPORT_CORRUPT"])
        self.state("error", "current", "block", 2)
        validate_report(self.report, self.policy)
        self.report["state"]["policy"] = "clear"
        self.reject()

    def test_wrong_app_workflow_job_matrix_or_producer_is_rejected(self):
        for field, value in (("app_id", 9), ("workflow_id", 9), ("job_name", "not-pytest")):
            candidate = deepcopy(self.report)
            candidate["evidence"][0][field] = value
            self.reject(candidate)
        self.report["evidence"][0]["producer_proof"]["sha"] = "1" * 40
        self.reject()

    def test_missing_producer_proof_cannot_self_promote_binding(self):
        self.report["evidence"][0]["producer_proof"] = None
        self.reject()

    def test_lower_binding_cannot_satisfy_workflow_requirement(self):
        self.report["evidence"][0]["binding_level"] = "platform_bound"
        self.reject()

    def test_missing_platform_identity_cannot_bypass_selected_attempt(self):
        self.policy["requirements"][0]["minimum_binding"] = "platform_bound"
        self.report["obligations"][0]["minimum_binding"] = "platform_bound"
        self.report["evidence"][0]["binding_level"] = "platform_bound"
        self.report["evidence"][0]["run_id"] = None
        self.bind_policy()
        self.reject()

    def test_cross_attempt_evidence_is_rejected(self):
        self.report["evidence"][0]["attempt"] = 2
        self.reject()

    def test_completed_test_evidence_needs_outer_artifact_digest(self):
        self.report["evidence"][0]["artifact_sha256"] = None
        self.reject()

    def test_receipt_cannot_claim_its_enclosing_archive_digest(self):
        receipt = load_document(ROOT / "examples/receipt.json")
        receipt["artifact"] = {"id": 3001, "sha256": "8" * 64}
        with self.assertRaises(ContractError):
            validate_receipt(receipt)

    def test_dangling_and_duplicate_evidence(self):
        self.report["obligations"][0]["evidence_ids"] = ["nonexistent"]
        self.reject()
        self.report["obligations"][0]["evidence_ids"] = ["pytest-report"]
        self.report["evidence"].append(deepcopy(self.report["evidence"][0]))
        self.reject()

    def test_raw_paths_cannot_escape_bundle(self):
        for path in ("../secret", "/absolute", "C:/secret", "raw\\secret", "raw//data", "raw/./data"):
            candidate = deepcopy(self.report)
            candidate["raw_inventory"] = [{"path":path,"sha256":"a"*64,"size_bytes":1}]
            self.reject(candidate)

    def test_receipt_cannot_self_assign_verified_binding(self):
        receipt = load_document(ROOT / "examples/receipt.json")
        receipt["binding_level"] = "workflow_bound"
        with self.assertRaises(ContractError):
            validate_receipt(receipt)

    def test_receipt_source_and_path_are_checked(self):
        receipt = load_document(ROOT / "examples/receipt.json")
        receipt["source"]["checked_out_sha"] = "1" * 40
        with self.assertRaises(ContractError):
            validate_receipt(receipt)
        receipt = load_document(ROOT / "examples/receipt.json")
        receipt["execution"]["junit"]["path"] = "../report.xml"
        with self.assertRaises(ContractError):
            validate_receipt(receipt)

    def test_engine_lock_requires_exact_unique_inventory(self):
        lock = load_document(ROOT / "locks/engine-candidates.json")
        lock["engines"] = [deepcopy(lock["engines"][0]) for _ in range(3)]
        with self.assertRaises(ContractError):
            validate_engine_lock(lock)

    def test_engine_lock_metadata_must_match_pin(self):
        lock = load_document(ROOT / "locks/engine-candidates.json")
        lock["engines"][0]["verification"]["metadata"]["version"] = "9.9.9"
        with self.assertRaises(ContractError):
            validate_engine_lock(lock)

    def test_engine_error_cannot_be_native_pass(self):
        case = load_document(ROOT / "fixtures/cases/E13-legal-refactor.json")
        engine = next(e for e in case["report"]["engines"] if e["name"] == "pinwash")
        engine["exit_code"] = 2
        with self.assertRaises(ContractError):
            validate_report(case["report"], case["policy"])

    def test_scanner_selector_cannot_be_removed(self):
        case = load_document(ROOT / "fixtures/cases/E13-legal-refactor.json")
        req = next(r for r in case["policy"]["requirements"] if r["selector"].get("engine") == "pinwash")
        req["selector"] = {}
        case["report"]["engines"] = [e for e in case["report"]["engines"] if e["name"] != "pinwash"]
        case["report"]["policy"]["canonical_sha256"] = canonical_digest(case["policy"])
        with self.assertRaises(ContractError):
            validate_report(case["report"], case["policy"])

    def test_merge_only_ci_policy_still_allows_head_static_scan(self):
        case = load_document(ROOT / "fixtures/cases/E03-valid-merge.json")
        case["policy"]["allowed_source_modes"] = ["github_merge"]
        case["report"]["policy"]["canonical_sha256"] = canonical_digest(case["policy"])
        validate_report(case["report"], case["policy"])


if __name__ == "__main__":
    unittest.main()
