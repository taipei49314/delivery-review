"""Authored approved-profile/runtime controls; no hosted qualification claim."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.inspection_profile import load_inspection_profile, seal_inspection_profile
from delivery_review.job_matrix import RUNTIME, platform_job_name, qualified_matrix
from delivery_review.test_evidence import ProducerQualification
from test_inspection_profile import profile


def matrix_profile(*, versions=("3.11", "3.12"), ci=False):
    document = profile()
    document["profile_version"] = 2
    test = document["policy"]["requirements"][0]
    document["policy"]["requirements"] = document["policy"]["requirements"][1:]
    producer = document["producers"].pop(test["id"])
    cells = []
    for version in versions:
        item = deepcopy(test)
        item["id"] = "pytest-" + version
        item["selector"]["matrix"] = {"python": version}
        document["policy"]["requirements"].append(item)
        document["producers"][item["id"]] = deepcopy(producer)
        cells.append({key: deepcopy(item["selector"][key]) for key in ("workflow_id", "job_name", "matrix")} |
                     {"platform_job_name": "fixed measure / python " + version})
        if ci:
            document["policy"]["requirements"].append({"id": "ci-" + version, "kind": "ci_job", "required": True,
                "minimum_binding": "platform_bound", "selector": {
                    key: deepcopy(item["selector"][key]) for key in ("workflow_id", "app_id", "job_name", "matrix")}})
    document["github"]["workflows"][0]["job_names"] = [row["platform_job_name"] for row in cells]
    document["job_matrix"] = {"mapping_version": 1, "cells": cells}
    return document


def qualification(requirement, runtime=RUNTIME):
    selector = requirement["selector"]
    producer = {"repository_id": selector["producer_repository_id"], "path": selector["producer_path"], "sha": selector["producer_sha"]}
    receipt = {"kind": "delivery-review-producer-qualification", "qualification_version": 1, "result": "qualified",
               "producer": producer, "producer_profile": "pytest-isolated-container-v1", "test_path": "tests",
               "source_modes": ["pr_head"], "provenance": "authored unit control, not qualification"}
    if runtime is not None:
        receipt["runtime"] = deepcopy(runtime)
    raw = canonical_bytes(receipt)
    return ProducerQualification(**producer, status="qualified", producer_profile=receipt["producer_profile"],
        qualification_receipt_sha256=hashlib.sha256(raw).hexdigest(), qualification_receipt_bytes=raw)


class JobMatrixTests(unittest.TestCase):
    def test_v1_profile_bytes_and_identity_remain_valid(self):
        raw = (ROOT / "examples/inspection/profile.json").read_bytes()
        old = load_inspection_profile(raw)
        self.assertEqual(1, old["profile_version"])
        self.assertEqual(old, load_inspection_profile(seal_inspection_profile(old)))
        for field, value in (("job_matrix", {"mapping_version": 1, "cells": []}),
                             ("ledger_input", {"format": "ec-ledger-v1", "paths": []})):
            changed = deepcopy(old)
            changed[field] = value
            with self.subTest(field=field), self.assertRaises(ContractError):
                seal_inspection_profile(changed)

    def test_v2_without_extensions_and_supported_ledger_validate(self):
        original = profile()
        v1 = load_inspection_profile(seal_inspection_profile(original))
        original["profile_version"] = 2
        v2 = load_inspection_profile(seal_inspection_profile(original))
        self.assertNotEqual(v1["policy"]["policy_id"], v2["policy"]["policy_id"])
        original["ledger_input"] = {"format": "ec-ledger-v1", "paths": []}
        self.assertEqual(original["ledger_input"], load_inspection_profile(seal_inspection_profile(original))["ledger_input"])

    def test_ledger_declaration_is_validated_and_identity_bound(self):
        document = matrix_profile()
        document["ledger_input"] = {"format": "other-v1", "paths": ["audit/proof.json"]}
        saved = load_inspection_profile(seal_inspection_profile(document))
        changed = deepcopy(saved)
        changed["ledger_input"]["paths"] = ["different.json"]
        with self.assertRaisesRegex(ContractError, "inspection_policy_identity_mismatch"):
            load_inspection_profile(canonical_bytes(changed))
        for declaration in ({"format": "other-v1", "paths": []}, {"format": "ec-ledger-v1", "paths": ["proof.json"]},
                            {"format": "other-v1", "paths": ["../proof.json"]}):
            changed["ledger_input"] = declaration
            with self.subTest(declaration=declaration), self.assertRaises(ContractError):
                seal_inspection_profile(changed)

    def test_exact_mapping_is_not_display_name_inference(self):
        document = load_inspection_profile(seal_inspection_profile(matrix_profile(ci=True)))
        for requirement in document["policy"]["requirements"]:
            if "matrix" in requirement["selector"]:
                version = requirement["selector"]["matrix"]["python"]
                self.assertEqual("fixed measure / python " + version, platform_job_name(document, requirement))
        document["job_matrix"]["cells"][0]["platform_job_name"] = "unobserved arbitrary name"
        with self.assertRaisesRegex(ContractError, "matrix_platform_job_mismatch"):
            seal_inspection_profile(document)

    def test_mapping_mutation_cannot_keep_approved_identity(self):
        saved = load_inspection_profile(seal_inspection_profile(matrix_profile()))
        changed = deepcopy(saved)
        for cell in changed["job_matrix"]["cells"]:
            cell["platform_job_name"] += " changed"
        changed["github"]["workflows"][0]["job_names"] = [cell["platform_job_name"] for cell in changed["job_matrix"]["cells"]]
        with self.assertRaisesRegex(ContractError, "inspection_policy_identity_mismatch"):
            load_inspection_profile(canonical_bytes(changed))

    def test_missing_extra_duplicate_and_reused_cells_rejected(self):
        changes = [lambda p: p.pop("job_matrix"), lambda p: p.update(job_matrix=None),
            lambda p: p["job_matrix"].update(mapping_version=True),
            lambda p: p["job_matrix"].update(cells=[]), lambda p: p["job_matrix"]["cells"].pop(),
            lambda p: p["job_matrix"]["cells"].append(deepcopy(p["job_matrix"]["cells"][0])),
            lambda p: p["job_matrix"]["cells"][1].update(platform_job_name=p["job_matrix"]["cells"][0]["platform_job_name"]),
            lambda p: p["job_matrix"]["cells"][0].update(workflow_id=True),
            lambda p: p["job_matrix"]["cells"][0].update(extra="ignored"),
            lambda p: p["job_matrix"]["cells"][0]["matrix"].update(os="linux")]
        for mutate in changes:
            document = matrix_profile()
            mutate(document)
            with self.subTest(document=document), self.assertRaises(ContractError):
                seal_inspection_profile(document)

    def test_platform_only_matrix_requires_paired_test_proof(self):
        document = matrix_profile(ci=True)
        document["policy"]["requirements"] = [row for row in document["policy"]["requirements"] if row["id"] != "pytest-3.12"]
        document["producers"].pop("pytest-3.12")
        with self.assertRaisesRegex(ContractError, "matrix_test_proof_requirement_missing"):
            seal_inspection_profile(document)

    def test_missing_selector_identity_is_a_contract_rejection(self):
        for matrix in (False, True):
            for key in ("workflow_id", "job_name", "app_id"):
                document = matrix_profile() if matrix else profile()
                document["profile_version"] = 2
                requirement = next(row for row in document["policy"]["requirements"] if row["kind"] == "test_report")
                requirement["selector"].pop(key)
                with self.subTest(matrix=matrix, key=key), self.assertRaises(ContractError):
                    seal_inspection_profile(document)

    def test_measured_python_minor_requires_full_fixed_runtime(self):
        document = matrix_profile()
        requirement = next(row for row in document["policy"]["requirements"] if row["id"] == "pytest-3.11")
        self.assertEqual({"python": "3.11"}, qualified_matrix(requirement, qualification(requirement), "pr_head"))
        for runtime in (None, {}, dict(RUNTIME, python="3.12.1"), dict(RUNTIME, platform="windows"),
                        dict(RUNTIME, architecture="aarch64"), dict(RUNTIME, extra="ignored")):
            with self.subTest(runtime=runtime), self.assertRaisesRegex(ContractError, "matrix_runtime_unconfirmed"):
                qualified_matrix(requirement, qualification(requirement, runtime), "pr_head")

    def test_display_label_and_policy_cannot_promote_311_to_312(self):
        document = matrix_profile()
        requirement = next(row for row in document["policy"]["requirements"] if row["id"] == "pytest-3.12")
        self.assertEqual("fixed measure / python 3.12", platform_job_name(document, requirement))
        with self.assertRaisesRegex(ContractError, "matrix_runtime_mismatch"):
            qualified_matrix(requirement, qualification(requirement), "pr_head")

    def test_qualification_identity_digest_and_source_are_independent_gates(self):
        requirement = next(row for row in matrix_profile()["policy"]["requirements"] if row["id"] == "pytest-3.11")
        proof = qualification(requirement)
        for changed in (replace(proof, status="pending"), replace(proof, sha="f" * 40),
                        replace(proof, qualification_receipt_sha256="f" * 64), replace(proof, source_modes=("github_merge",))):
            with self.subTest(proof=changed), self.assertRaises(ContractError):
                qualified_matrix(requirement, changed, "pr_head")

    def test_ordinary_job_needs_no_matrix_runtime_upgrade(self):
        document = profile()
        requirement = document["policy"]["requirements"][0]
        self.assertEqual(requirement["selector"]["job_name"], platform_job_name(document, requirement))
        self.assertEqual({}, qualified_matrix(requirement, None, "pr_head"))


if __name__ == "__main__":
    unittest.main()
