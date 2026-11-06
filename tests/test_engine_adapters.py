"""Authored public-shape engine controls, separate from actual CLI qualification.

No detector modules are imported. Native documents are synthetic expectations;
real installed-wheel probe receipts stay separately inventoried under work/.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review.adapters.engines import EngineAdapterError, PINS, normalize_engine_result
from delivery_review.contracts import validators

SUBJECT = {"merge_base_sha": "a" * 40, "head_sha": "b" * 40, "base_tip_sha": "c" * 40}
SCOPE = "supported EC task/ordinary-repair receipt inputs, records, history and source bytes"


def raw(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def finding(name, *, rule="AUTHORED_CONTROL", severity="high", allowlisted=False):
    row = {"rule": rule, "severity": severity, "fingerprint": rule + "/fixture/v1:" + "d" * 64,
           "path": "tests/example.py", "message": "Synthetic native observation", "before": None, "after": None}
    if name == "checkwash":
        row.update(allowlisted=allowlisted, escalators=[], deescalators=[], unit=None)
    else:
        row.update(before="before", after="after", locator="fixture")
    return row


def control(name="checkwash", *, block=False):
    version, wheel, contract = PINS[name]
    run = {name + "_version": version}
    argv = ["trusted/python", "-I", "-X", "utf8", "-m", name]
    if name == "ledgerwash":
        run.update(adapter="ec-ledger", epoch=SUBJECT["head_sha"], ref=SUBJECT["head_sha"],
                   spec_version=5, target="trusted/repo", require_complete=False)
        argv.extend(["scan", "trusted/repo", "--at", SUBJECT["head_sha"], "--fail-on", "high"])
    else:
        run.update(base=SUBJECT["merge_base_sha"][:7] if name == "checkwash" else SUBJECT["merge_base_sha"],
                   head=SUBJECT["head_sha"][:7] if name == "checkwash" else SUBJECT["head_sha"])
        argv.extend(["check", SUBJECT["merge_base_sha"] + ".." + SUBJECT["head_sha"]])
        if name == "checkwash":
            argv.extend(["--repo", "trusted/repo", "--format", "json", "--coverage-report", "trusted/coverage.json", "--fail-on", "high"])
        else:
            run["spec_version"] = 29
    native = {name + "_findings_version": contract, "run": run, "findings": [finding(name)] if block else [],
              "summary": {"info": 0, "warn": 0, "high": int(block), "critical": 0}, "verdict": "block" if block else "pass"}
    sidecar = None
    if name == "ledgerwash":
        checks = [{"path": path, "locator": "", "check": check, "status": "verified", "reason": "supported contract verified"}
                  for path, check in (("governance/tasks", "nonempty_tasks"), ("evidence/task-operations", "nonempty_receipts"))]
        native.update(finding_verdict=native["verdict"], residuals=[], observations=[],
                      coverage={"version": 1, "complete": True, "counts": {"verified": 2, "unverified": 0, "mismatch": 0},
                                "checks": checks, "scope": SCOPE, "limitations": ["execution authenticity unverified"]})
    else:
        native["config_errors"] = []
        if name == "checkwash":
            native["skipped_files"] = []
            sidecar = {"checkwash_coverage_version": 1, "run": deepcopy(run), "scope": "javascript_assertion_candidates",
                       "files": [], "gaps": [], "status": "no_known_gaps"}
        else:
            native["unknown_coverage"] = []
    args = {"engine_name": name, "engine_version": version, "wheel_sha256": wheel,
            "subject": deepcopy(SUBJECT), "process": {"argv": argv, "duration_ms": 20,
            "exit_code": int(block), "termination": "exited"}, "native_policy": {"threshold": "high"}}
    return args, native, sidecar


def normalize(args, native, sidecar=None):
    files = {"stdout": native if type(native) is bytes else raw(native), "stderr": b"synthetic diagnostic\r\n"}
    if sidecar is not None:
        files["coverage"] = raw(sidecar)
    return normalize_engine_result(**args, raw_files=files)


class EngineAdapterTests(unittest.TestCase):
    def test_pins_match_approved_candidate_lock_without_upgrading(self):
        lock = json.loads((ROOT / "locks/engine-candidates.json").read_bytes())
        for engine in lock["engines"]:
            self.assertEqual((engine["version"], engine["artifact"]["sha256"]), PINS[engine["name"]][:2])

    def test_all_public_shapes_preserve_pass_and_fit_frozen_engine_projection(self):
        validator = validators()["common"].evolve(schema={"$ref": "urn:delivery-review:contract:1:common#/$defs/engine"})
        for name in PINS:
            with self.subTest(engine=name):
                args, native, sidecar = control(name)
                result = normalize(args, native, sidecar)
                self.assertEqual(("completed", "pass", []), (result["status"], result["verdict"], result["reason_codes"]))
                self.assertEqual(native, result["parsed_native"])
                self.assertEqual([], list(validator.iter_errors(result["engine"])))
                self.assertNotIn("ready_for_review", result)
                self.assertNotIn("binding", result)

    def test_block_and_native_finding_details_are_not_rewritten(self):
        for name in PINS:
            args, native, sidecar = control(name, block=True)
            result = normalize(args, native, sidecar)
            self.assertEqual("block", result["verdict"])
            self.assertTrue(result["known_block"])
            self.assertEqual(native["findings"][0], result["findings"][0]["native"])
            self.assertEqual(hashlib.sha256(raw(native)).hexdigest(), result["engine"]["raw_report_sha256"])

    def test_native_errors_do_not_erase_a_parsed_block(self):
        for termination, code in (("exited", 2), ("timeout", None), ("output_limit", -9), ("spawn_error", None)):
            args, native, sidecar = control(block=True)
            args["process"].update(termination=termination, exit_code=code)
            result = normalize(args, native, sidecar)
            self.assertEqual(("error", "not_judged"), (result["status"], result["verdict"]))
            self.assertTrue(result["known_block"])
            self.assertEqual("block", result["parsed_native"]["verdict"])
            self.assertEqual(1, len(result["engine"]["findings"]))

    def test_allowlisted_and_added_exemption_dispositions_preserve_native_verdict(self):
        args, native, sidecar = control()
        native["findings"] = [finding("checkwash", severity="warn", allowlisted=True)]
        native["summary"]["warn"] = 1
        result = normalize(args, native, sidecar)
        self.assertEqual("allowlisted", result["engine"]["findings"][0]["disposition"])
        self.assertEqual("pass", result["verdict"])
        args, native, sidecar = control("pinwash")
        native["findings"] = [finding("pinwash", rule="EXEMPTION_ADDED", severity="warn")]
        native["summary"]["warn"] = 1
        result = normalize(args, native)
        self.assertTrue(result["review_required"])
        self.assertEqual("added_exemption", result["engine"]["findings"][0]["disposition"])
        self.assertEqual("pass", result["verdict"])

    def test_unknown_contract_keeps_original_json_without_completed_claim(self):
        args, native, sidecar = control(block=True)
        for version in (99, True, "2"):
            native["checkwash_findings_version"] = version
            result = normalize(args, native, sidecar)
            self.assertEqual("error", result["status"])
            self.assertEqual(native, result["parsed_native"])
            self.assertIn("unsupported_findings_contract", result["reason_codes"])

    def test_duplicate_invalid_utf8_nonfinite_and_deep_json_are_errors(self):
        args, native, sidecar = control()
        for body in (b'{"x":1,"x":2}', b"\xff", b'{"x":NaN}', b'{"x":1e999}', b'"not object"',
                     b"[" * 34 + b"0" + b"]" * 34, b'\xef\xbb\xbf{}'):
            result = normalize(args, body, sidecar)
            self.assertEqual("error", result["status"])
            self.assertIn("stdout_invalid_json", result["reason_codes"])

    def test_finite_native_quantities_survive_while_integral_counts_stay_strict(self):
        args, native, sidecar = control("ledgerwash")
        native["observations"] = [{"kind": "opaque_native_quantity", "value": 1.25}]
        result = normalize(args, native)
        self.assertEqual("completed", result["status"])
        self.assertEqual(1.25, result["parsed_native"]["observations"][0]["value"])
        native["summary"]["high"] = 0.0
        self.assertIn("native_summary_mismatch", normalize(args, native)["reason_codes"])

    def test_subject_and_pins_and_exact_public_cli_are_checked(self):
        cases = (lambda a: a.update(wheel_sha256="0" * 64),
                 lambda a: a.update(engine_version="0.9.0"),
                 lambda a: a["process"]["argv"].remove("-I"),
                 lambda a: a["process"]["argv"].__setitem__(7, "HEAD..WORKTREE"),
                 lambda a: a["process"]["argv"].__setitem__(-1, "critical"))
        for change in cases:
            args, native, sidecar = control()
            change(args)
            self.assertEqual("error", normalize(args, native, sidecar)["status"])
        args, native, sidecar = control()
        native["run"]["head"] = "f" * 7
        self.assertIn("engine_run_identity_mismatch", normalize(args, native, sidecar)["reason_codes"])

    def test_missing_or_different_checkwash_coverage_never_means_complete_coverage(self):
        args, native, sidecar = control(block=True)
        result = normalize(args, native)
        self.assertEqual("error", result["status"])
        self.assertEqual("unknown", result["coverage"]["status"])
        self.assertTrue(result["known_block"])
        sidecar["run"]["head"] = "f" * 7
        self.assertIn("coverage_run_identity_mismatch", normalize(args, native, sidecar)["reason_codes"])

    def test_reported_gaps_are_independent_of_native_pass(self):
        args, native, sidecar = control("pinwash")
        native["unknown_coverage"] = [{"path": ".claude/settings.json", "reason": "new surface file unparseable"}]
        result = normalize(args, native)
        self.assertEqual(("completed", "pass", "gaps"), (result["status"], result["verdict"], result["coverage"]["status"]))
        native["config_errors"] = ["base allow.toml unreadable as utf-8"]
        result = normalize(args, native)
        self.assertEqual("error", result["status"])
        self.assertEqual("pass", result["parsed_native"]["verdict"])
        self.assertEqual(native["unknown_coverage"], result["parsed_native"]["unknown_coverage"])

    def test_ledger_absence_and_strict_coverage_block_are_not_clean_ledger(self):
        args, native, sidecar = control("ledgerwash")
        native["residuals"] = [{"path": "governance/tasks", "reason": "no task records found at epoch"},
                               {"path": "evidence/task-operations", "reason": "no operation receipts found at epoch"}]
        for row in native["coverage"]["checks"]:
            row["status"] = "unverified"
        native["coverage"].update(complete=False, counts={"verified": 0, "unverified": 2, "mismatch": 0})
        result = normalize(args, native)
        self.assertEqual(("not_provided", "not_judged", "not_provided"), (result["status"], result["verdict"], result["coverage"]["status"]))
        self.assertEqual("pass", result["parsed_native"]["verdict"])
        native["residuals"] = []
        native["verdict"] = "block"
        native["run"]["require_complete"] = True
        args["process"]["argv"].append("--require-complete")
        args["process"]["exit_code"] = 1
        result = normalize(args, native)
        self.assertEqual(("completed", "block", "gaps"), (result["status"], result["verdict"], result["coverage"]["status"]))
        self.assertEqual("pass", result["parsed_native"]["finding_verdict"])

    def test_exit_verdict_summary_and_coverage_contradictions_are_errors(self):
        args, native, sidecar = control(block=True)
        native["verdict"] = "pass"
        args["process"]["exit_code"] = 0
        result = normalize(args, native, sidecar)
        self.assertEqual("error", result["status"])
        self.assertTrue(result["known_block"])
        args, native, sidecar = control("ledgerwash")
        native["coverage"]["checks"] = []
        native["coverage"]["counts"]["verified"] = 0
        self.assertIn("coverage_state_mismatch", normalize(args, native)["reason_codes"])

    def test_frozen_representation_limits_do_not_truncate_original_findings(self):
        args, native, sidecar = control(block=True)
        native["findings"][0]["fingerprint"] = "x" * 257
        result = normalize(args, native, sidecar)
        self.assertEqual("error", result["status"])
        self.assertEqual("x" * 257, result["parsed_native"]["findings"][0]["fingerprint"])
        self.assertEqual([], result["engine"]["findings"])

    def test_controller_limits_and_types_are_checked_before_parse(self):
        args, native, sidecar = control()
        for change in (lambda a: a["process"].update(exit_code=True), lambda a: a["process"].update(duration_ms=-1),
                       lambda a: a["subject"].update(head_sha="short"), lambda a: a["native_policy"].update(threshold="warn")):
            altered = deepcopy(args)
            change(altered)
            with self.assertRaises(EngineAdapterError):
                normalize(altered, native, sidecar)
        with self.assertRaises(EngineAdapterError):
            normalize_engine_result(**args, raw_files={"stdout": b"x" * (2097152 + 1), "stderr": b""})

    def test_fresh_private_cache_invocation_and_windows_exit_preserve_native(self):
        for name in PINS:
            args, native, sidecar = control(name, block=True)
            args["process"]["argv"][4:4] = ["-B", "-X", "pycache_prefix=trusted/new-cache"]
            result = normalize(args, native, sidecar)
            self.assertEqual(("completed", "block"), (result["status"], result["verdict"]))
            args["process"]["exit_code"] = 0xC0000005
            result = normalize(args, native, sidecar)
            self.assertEqual("error", result["status"])
            self.assertEqual(0xC0000005, result["process"]["exit_code"])
            self.assertIsNone(result["engine"]["exit_code"])
            self.assertTrue(result["known_block"])
            self.assertIn("report_exit_limit", result["reason_codes"])

    def test_pure_mapping_does_not_mutate_inputs_or_use_io(self):
        args, native, sidecar = control(block=True)
        before = deepcopy((args, native, sidecar))
        with patch("builtins.open", side_effect=AssertionError("file IO")), patch("pathlib.Path.open", side_effect=AssertionError("file IO")), \
             patch("subprocess.Popen", side_effect=AssertionError("process")), patch("socket.socket", side_effect=AssertionError("network")):
            result = normalize(args, native, sidecar)
        self.assertEqual(before, (args, native, sidecar))
        result["subject"]["head_sha"] = "changed"
        self.assertEqual(SUBJECT["head_sha"], args["subject"]["head_sha"])


if __name__ == "__main__":
    unittest.main()
