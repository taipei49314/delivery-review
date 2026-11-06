"""Offline CLI boundary controls; delegated collectors are explicitly mocked."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review import cli, inspection_cli
from delivery_review.contracts import ContractError, canonical_bytes, loads_strict
from delivery_review.evidence.inspection import MANDATORY, write_inspection


class InspectionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = (ROOT / "examples/inspection/profile.json").read_bytes()
        self.policy = self.root / "approved.json"
        self.policy.write_bytes(self.profile)
        self.tool_config = {"git_executable": "git", "python_executable": "python", "gh_executable": "gh",
            "trusted_root": "root.json", "engine_wheels": {name: name + ".whl" for name in ("checkwash", "pinwash", "ledgerwash")}}
        for value in [self.tool_config[key] for key in ("git_executable", "python_executable", "gh_executable", "trusted_root")] + list(self.tool_config["engine_wheels"].values()):
            (self.root / value).write_bytes(b"authored inert placeholder, never executed")
        self.config = self.root / "tools.json"
        self.config.write_bytes(canonical_bytes(self.tool_config))
        self.saved = self.root / "saved"
        files = {name: b"authored inert placeholder" for name in MANDATORY}
        files["raw/inspection-profile.json"] = self.profile
        files["raw/execution-plan.json"] = canonical_bytes({"pr_url": "https://github.com/fixture-owner/fixture-repo/pull/7"})
        write_inspection(self.saved, files=files, collector={"version": "authored-test", "source_sha": "1" * 40},
            observed_at="2026-11-06T00:00:00Z", report_id="authored-cli")
        self.identity = {"version": "authored-current", "source_sha": "2" * 40}
        self.report = {"state": {"evidence": "incomplete", "freshness": "unknown", "policy": "block"},
                       "ready_for_review": False, "exit_code": 2}

    def run_cli(self, argv, **options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = inspection_cli.main(argv, **options)
        return code, stdout.getvalue(), stderr.getvalue()

    def arguments(self, operation):
        first = ["inspect", "https://github.com/fixture-owner/fixture-repo/pull/7", "--policy", str(self.policy)] if operation == "inspect" else [operation, str(self.saved)]
        return first + ["--out", str(self.root / "new"), "--tools", str(self.config)]

    def test_tool_paths_resolve_relative_to_config_and_replay_needs_no_wheels(self):
        tools = inspection_cli.load_tools(self.config, live=True)
        self.assertEqual(self.root / "git", tools.git_executable)
        self.assertEqual(self.root / "checkwash.whl", tools.engine_wheels["checkwash"])
        config = dict(self.tool_config, engine_wheels={})
        self.config.write_bytes(canonical_bytes(config))
        self.assertEqual({}, inspection_cli.load_tools(self.config, live=False).engine_wheels)
        with self.assertRaisesRegex(ContractError, "missing_toolchain_wheels"):
            inspection_cli.load_tools(self.config, live=True)

    def test_unknown_keys_missing_files_nonstring_paths_and_unknown_wheels_refused(self):
        cases = [dict(self.tool_config, allow_unpinned=True), dict(self.tool_config, git_executable=123),
                 dict(self.tool_config, gh_executable="missing"), dict(self.tool_config, python_executable="bad\npath"),
                 dict(self.tool_config, engine_wheels={"unknown": "git"})]
        for value in cases:
            self.config.write_bytes(canonical_bytes(value))
            with self.subTest(value=value), self.assertRaises((ContractError, OSError)):
                inspection_cli.load_tools(self.config, live=True)

    def test_verify_checks_local_integrity_without_tools_authentication_or_source_identity(self):
        with patch.object(inspection_cli, "load_tools", side_effect=AssertionError("no tools for verify")):
            code, stdout, stderr = self.run_cli(["verify", str(self.saved)], identity_provider=lambda: self.fail("no provenance on verify"))
        self.assertEqual(0, code)
        self.assertEqual("not_established", json.loads(stdout)["authentication"])
        self.assertEqual("local_file_integrity", json.loads(stdout)["scope"])
        self.assertEqual("", stderr)

    def test_verify_refuses_saved_byte_tampering_and_unlisted_files(self):
        target = self.saved / "report.html"
        original = target.read_bytes()
        target.write_bytes(original + b"altered")
        self.assertEqual(2, self.run_cli(["verify", str(self.saved)])[0])
        target.write_bytes(original)
        (self.saved / "unlisted.txt").write_bytes(b"extra")
        self.assertEqual(2, self.run_cli(["verify", str(self.saved)])[0])

    def test_replay_delegates_offline_without_env_tokens_or_current_collector_identity(self):
        with patch.dict(os.environ, {"GH_TOKEN": "authored-private-token", "GITHUB_TOKEN": "another-secret"}), \
             patch.object(inspection_cli, "replay_inspection", return_value=self.report) as replay, \
             patch.object(inspection_cli, "inspect_pr", side_effect=AssertionError("no online collector on replay")):
            code, stdout, stderr = self.run_cli(self.arguments("replay"), identity_provider=lambda: self.fail("no current source required on replay"))
        self.assertEqual(2, code)
        self.assertEqual({"tools"}, set(replay.call_args.kwargs))
        self.assertEqual("unknown", json.loads(stdout)["state"]["freshness"])
        self.assertNotIn("authored-private-token", stdout + stderr)

    def test_same_length_tamper_has_closed_integrity_reason_and_creates_no_report(self):
        target = self.saved / "report.html"
        original = target.read_bytes()
        target.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        for operation in ("verify", "replay", "refresh"):
            args = ["verify", str(self.saved)] if operation == "verify" else self.arguments(operation)
            with self.subTest(operation=operation), patch.object(inspection_cli, "inspect_pr", side_effect=AssertionError("no network")):
                code, stdout, stderr = self.run_cli(args)
            self.assertEqual(2, code)
            self.assertEqual("", stdout)
            self.assertEqual({"operation": operation, "status": "error", "reason": "raw_evidence_digest_mismatch"}, json.loads(stderr))
            self.assertFalse((self.root / "new").exists())
        self.assertEqual(bytes([original[0] ^ 1]) + original[1:], target.read_bytes())

    def test_integrity_diagnostic_does_not_relay_untrusted_or_unrelated_errors(self):
        for failure in (ValueError("bundle file digest mismatch"), ValueError("secret-containing untrusted source text")):
            with patch.object(inspection_cli, "verify_inspection", side_effect=failure):
                code, stdout, stderr = self.run_cli(["verify", str(self.saved)])
            self.assertEqual(2, code)
            self.assertEqual("", stdout)
            self.assertEqual("invalid_input_or_operation_failed", json.loads(stderr)["reason"])
            self.assertNotIn("secret-containing", stderr)

    def test_refresh_reuses_original_policy_and_pr_and_new_collector_identity(self):
        before = {path.relative_to(self.saved).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in self.saved.rglob("*") if path.is_file()}
        with patch.dict(os.environ, {"GH_TOKEN": "authored-secret"}), \
             patch.object(inspection_cli, "inspect_pr", return_value=self.report) as inspect:
            code, _, _ = self.run_cli(self.arguments("refresh"), identity_provider=lambda: self.identity)
        self.assertEqual(2, code)
        self.assertEqual("https://github.com/fixture-owner/fixture-repo/pull/7", inspect.call_args.args[0])
        self.assertEqual(self.profile, inspect.call_args.args[1])
        self.assertEqual(self.identity, inspect.call_args.kwargs["collector"])
        after = {path.relative_to(self.saved).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in self.saved.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_nested_refresh_output_is_refused_before_collection(self):
        args = self.arguments("refresh")
        args[args.index("--out") + 1] = str(self.saved / "nested")
        with patch.object(inspection_cli, "inspect_pr", side_effect=AssertionError("must not collect")):
            code, _, _ = self.run_cli(args, identity_provider=lambda: self.identity)
        self.assertEqual(2, code)
        self.assertFalse((self.saved / "nested").exists())

    def test_anonymous_live_mode_ignores_both_ambient_tokens(self):
        with patch.dict(os.environ, {"GH_TOKEN": "authored-secret", "GITHUB_TOKEN": "another-secret"}), \
             patch.object(inspection_cli, "inspect_pr", return_value=self.report) as inspect:
            self.run_cli(self.arguments("inspect") + ["--anonymous"], identity_provider=lambda: self.identity)
        self.assertIsNone(inspect.call_args.kwargs["token"])

    def test_collector_errors_are_sanitized_and_known_blocks_not_rewritten(self):
        with patch.object(inspection_cli, "inspect_pr", side_effect=ValueError("secret-containing untrusted source text")):
            code, stdout, stderr = self.run_cli(self.arguments("inspect"), identity_provider=lambda: self.identity)
        self.assertEqual(2, code)
        self.assertEqual("", stdout)
        self.assertNotIn("secret-containing", stderr)
        with patch.object(inspection_cli, "inspect_pr", return_value=self.report):
            code, stdout, _ = self.run_cli(self.arguments("inspect"), identity_provider=lambda: self.identity)
        self.assertEqual("block", json.loads(stdout)["state"]["policy"])
        self.assertEqual(2, code)

    def test_top_level_dispatch_preserves_full_arguments(self):
        for operation in ("inspect", "replay", "refresh", "verify"):
            args = [operation, "authored", "--help"]
            with patch("delivery_review.inspection_cli.main", return_value=2) as delegated:
                self.assertEqual(2, cli.main(args))
            delegated.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
