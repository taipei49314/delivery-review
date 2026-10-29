"""M0 authored expectations, never evidence of a live integration run.

These tests validate frozen contract fixtures. They do not execute a collector,
scanner, workflow, XML parser, artifact extractor, or scenario-to-report mapper.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.contracts import canonical_digest, validate_case  # noqa: E402

MANIFEST_PATH = ROOT / "acceptance" / "manifest.json"
MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
ENTRIES = MANIFEST["cases"]


def load_case(entry: dict) -> dict:
    return json.loads((ROOT / entry["path"]).read_text(encoding="utf-8"))


def by_id(case_id: str) -> dict:
    return load_case(next(entry for entry in ENTRIES if entry["id"] == case_id))


class AcceptanceContractTests(unittest.TestCase):
    def test_manifest_covers_the_frozen_eighteen_families_without_extra_case_files(self):
        assert MANIFEST["schema_version"] == 1
        assert MANIFEST["scope"] == "M0_CONTRACT"
        assert MANIFEST["full_integration"] == "NOT_RUN"
        assert MANIFEST["case_count"] == len(ENTRIES) == 40
        assert MANIFEST["family_count"] == 18
        assert {entry["family_id"] for entry in ENTRIES} == {
            f"E{number:02d}" for number in range(1, 19)
        }
        assert len({entry["id"] for entry in ENTRIES}) == len(ENTRIES)
        assert len({entry["path"] for entry in ENTRIES}) == len(ENTRIES)
        assert {entry["path"] for entry in ENTRIES} == {
            path.relative_to(ROOT).as_posix()
            for path in (ROOT / "fixtures" / "cases").glob("*.json")
        }


    def test_frozen_case_bytes_expectations_and_contract(self):
        for entry in ENTRIES:
            with self.subTest(case_id=entry["id"]):
                path = ROOT / entry["path"]
                assert path.resolve().is_relative_to((ROOT / "fixtures" / "cases").resolve())
                raw = path.read_bytes()
                assert hashlib.sha256(raw).hexdigest() == entry["sha256"]
                case = json.loads(raw)
                assert case["case_id"] == entry["id"]
                assert case["family_id"] == entry["family_id"]
                assert canonical_digest(case["expected"]) == entry["expected_sha256"]
                assert case["report"]["policy"]["canonical_sha256"] == canonical_digest(case["policy"])
                validate_case(case)


    def test_expected_output_never_claims_an_executed_mapping_or_integration(self):
        for entry in ENTRIES:
            with self.subTest(case_id=entry["id"]):
                case = load_case(entry)
                assert case["origin"] == "synthetic_contract"
                assert case["report_role"] == "preregistered_expected_normalized_output"
                assert case["full_integration"] == "NOT_RUN"
                references = case["scenario"]["raw_fixture_refs"]
                if case["case_id"] == "E01-happy-head":
                    assert references == [
                        "fixtures/github/pr-start.json",
                        "fixtures/github/workflow-run-attempt1.json",
                        "fixtures/github/jobs-attempt1.json",
                        "fixtures/github/artifacts.json",
                        "fixtures/github/merge-commit.json",
                    ]
                    assert any("mapping" in fact and "NOT_RUN" in fact for fact in case["scenario"]["facts"])
                    assert all((ROOT / reference).is_file() for reference in references)
                else:
                    assert references == []
                assert "synthetic_contract_fixture" in case["report"]["limitations"]
                assert "No collector, scanner, GitHub execution, or live validation has run." in case["report"]["limitations"]


    def test_known_419_escape_is_a_truthful_limited_positive(self):
        case = by_id("E18-known-419-escape")
        assert case["expected"]["exit_code"] == 0
        assert "known_checkwash_419_escape" in case["report"]["limitations"]
        checkwash = next(engine for engine in case["report"]["engines"] if engine["name"] == "checkwash")
        assert checkwash["verdict"] == "pass"
        assert checkwash["findings"] == []
        assert case["full_integration"] == "NOT_RUN"


    def test_self_consistency_does_not_satisfy_the_workflow_binding_requirement(self):
        case = by_id("E10-self-consistent-untrusted-producer")
        evidence = next(item for item in case["report"]["evidence"] if item["id"] == "pytest-report")
        obligation = next(item for item in case["report"]["obligations"] if item["id"] == "pytest-linux")
        assert evidence["binding_level"] == "platform_bound"
        assert evidence["producer_proof"] is None
        assert obligation["minimum_binding"] == "workflow_bound"
        assert obligation["outcome"] == "untrusted"
        assert case["expected"]["state"]["evidence"] == "incomplete"
        assert case["expected"]["exit_code"] == 2


    def test_recorded_failure_is_complete_evidence_without_being_success(self):
        for case_id in ("E09-latest-failed", "E14-collection-error"):
            with self.subTest(case_id=case_id):
                case = by_id(case_id)
                assert case["expected"]["state"] == {
                    "evidence": "complete", "freshness": "current", "policy": "block"
                }
                assert case["expected"]["exit_code"] == 1
                assert not case["expected"]["ready_for_review"]


    def test_evidence_staleness_and_snapshot_staleness_are_different(self):
        old_ci = by_id("E02-old-head-ci")
        changed = by_id("E04-source-changed")
        replay = by_id("E17-offline-replay")
        assert old_ci["expected"]["state"] == {
            "evidence": "incomplete", "freshness": "current", "policy": "clear"
        }
        assert changed["expected"]["state"]["freshness"] == "stale"
        assert replay["expected"]["state"]["freshness"] == "unknown"
        assert replay["report"]["mode"] == "offline_replay"
        assert replay["report"]["recheck"]["observed_at"] == "2026-10-29T00:00:01Z"


if __name__ == "__main__":
    unittest.main()
