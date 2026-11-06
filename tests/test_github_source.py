"""Authored source transport controls; the synthetic fetch never contacts a host."""
from dataclasses import replace
import base64
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.providers import github_source as source
from delivery_review.source_snapshot import verify_snapshot
from test_source_snapshot import Objects, fixture


REPOSITORY = {"id": 9001, "full_name": "fixture-owner/fixture-repo"}


class GitHubSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.destination = self.root / "acquisition"
        self.git = shutil.which("git")
        self.assertIsNotNone(self.git)
        self.value, self.common, self.base, self.head = fixture()
        self.calls = []
        self.real_execute = source._execute

    def fetch(self, argv, **options):
        """Populate only own fresh quarantine; no protocol.file fallback exists."""
        self.calls.append((argv, options))
        if "fetch" not in argv and "--fetch-worker" not in argv:
            return self.real_execute(argv, **options)
        quarantine = Path(options["cwd"])
        for oid, raw in self.value.objects.items():
            directory = quarantine / "objects" / oid[:2]
            directory.mkdir(exist_ok=True)
            (directory / oid[2:]).write_bytes(zlib.compress(raw))
        options["monitor"]()
        return ({"argv": argv, "duration_ms": 0, "exit_code": 0, "termination": "exited"},
                {"stdout": b"", "stderr": b"authored synthetic fetch\n"})

    def acquire(self, **options):
        with patch.object(source, "_execute", side_effect=self.fetch):
            return source.acquire_public_source(self.destination, repository=REPOSITORY,
                head_sha=self.head, base_tip_sha=self.base, git_executable=self.git, **options)

    def acquire_authenticated(self, token="ghs_authored_fixture_not_a_real_token"):
        with patch.object(source, "_execute", side_effect=self.fetch):
            return source.acquire_source(self.destination, repository=REPOSITORY,
                head_sha=self.head, base_tip_sha=self.base, git_executable=self.git, token=token)

    def test_authenticated_fetch_token_travels_only_in_worker_stdin(self):
        token = "ghs_authored_fixture_not_a_real_token"
        result = self.acquire_authenticated(token)
        verify_snapshot(result.snapshot)
        self.assertEqual("authenticated_https", result.evidence["mode"])
        worker_calls = [(argv, options) for argv, options in self.calls if "--fetch-worker" in argv]
        self.assertEqual(1, len(worker_calls))
        argv, options = worker_calls[0]
        self.assertEqual([sys.executable, "-I", "-B", str(Path(source.__file__).resolve()), "--fetch-worker"], argv)
        request = source.loads_strict(options["stdin_bytes"])
        self.assertEqual(token, request["token"])
        self.assertEqual((REPOSITORY, self.head, self.base), (request["repository"], request["head"], request["base"]))
        for command, options in self.calls:
            self.assertNotIn(token, str(command) + str(options["env"]))
            self.assertEqual("0", options["env"]["GIT_CONFIG_COUNT"])
            self.assertFalse(any(key.startswith("GIT_CONFIG_KEY_") or key.startswith("GIT_CONFIG_VALUE_") for key in options["env"]))
            if "--fetch-worker" not in command:
                self.assertNotIn(token.encode(), options["stdin_bytes"] or b"")
        for path in self.destination.rglob("*"):
            if path.is_file():
                for secret in source._credential_forms(token):
                    self.assertNotIn(secret.encode(), path.read_bytes())

    def test_worker_sets_one_url_scoped_header_and_execs_fixed_git_without_new_process_group(self):
        self.acquire()
        quarantine = self.destination / "quarantine"
        token = "ghs_authored_worker_fixture"
        request = source.canonical_bytes({"git": self.git, "quarantine": str(quarantine), "repository": REPOSITORY,
                                          "head": self.head, "base": self.base, "token": token})
        polluted = {key: "must-not-inherit" for key in ("GIT_TRACE", "GIT_TRACE_CURL", "HTTPS_PROXY", "GH_TOKEN",
                    "GIT_CONFIG_PARAMETERS", "GIT_ASKPASS", "GIT_SSL_NO_VERIFY", "GIT_CONFIG_VALUE_0")}
        with patch.dict(os.environ, polluted), patch.object(source.Path, "cwd", return_value=quarantine), \
             patch.object(source, "_exec_fetch") as execute:
            source._fetch_worker(request)
        executable, argv, environment = execute.call_args.args
        self.assertEqual(Path(self.git), executable)
        self.assertEqual(source._argv(Path(self.git), quarantine, source._fetch_arguments(REPOSITORY, self.head, self.base)), argv)
        self.assertNotIn(token, str(argv))
        self.assertEqual("1", environment["GIT_CONFIG_COUNT"])
        self.assertEqual("http.https://github.com/fixture-owner/fixture-repo.git.extraHeader", environment["GIT_CONFIG_KEY_0"])
        basic = base64.b64encode(("x-access-token:" + token).encode()).decode()
        self.assertEqual("Authorization: Basic " + basic, environment["GIT_CONFIG_VALUE_0"])
        self.assertFalse((set(polluted) - {"GIT_CONFIG_VALUE_0"}) & set(environment))
        self.assertEqual(str(self.destination / "home"), environment["HOME"])

    def test_windows_worker_preserves_spaced_argv_native_exit_and_outer_job(self):
        git = r"C:\Program Files\Git\cmd\git.exe"
        argv = [git, r"--git-dir=C:\private source\quarantine", "fetch", "https://github.com/owner/repo.git"]
        environment = {"GIT_CONFIG_COUNT": "1"}
        with patch.object(source.os, "name", "nt"), patch.object(source.subprocess, "call", return_value=128) as execute:
            result = source._exec_fetch(git, argv, environment)
        self.assertEqual(128, result)
        self.assertEqual((argv,), execute.call_args.args)
        self.assertEqual(git, execute.call_args.kwargs["executable"])
        self.assertIs(environment, execute.call_args.kwargs["env"])
        self.assertEqual(source.subprocess.DEVNULL, execute.call_args.kwargs["stdin"])
        self.assertEqual(getattr(source.subprocess, "CREATE_NO_WINDOW", 0), execute.call_args.kwargs["creationflags"])
        self.assertTrue(execute.call_args.kwargs["close_fds"])

    def test_posix_worker_replaces_process_after_disconnecting_private_stdin(self):
        with patch.object(source.os, "name", "posix"), patch.object(source.os, "dup2") as replace_stdin, \
             patch.object(source.os, "execve") as execute:
            source._exec_fetch("/usr/bin/git", ["/usr/bin/git", "fetch"], {"GIT_CONFIG_COUNT": "1"})
        replace_stdin.assert_called_once()
        self.assertEqual(0, replace_stdin.call_args.args[1])
        execute.assert_called_once_with("/usr/bin/git", ["/usr/bin/git", "fetch"], {"GIT_CONFIG_COUNT": "1"})

    def test_worker_rejects_modified_config_and_non_fixed_request_before_exec(self):
        self.acquire()
        quarantine = self.destination / "quarantine"
        request = {"git": self.git, "quarantine": str(quarantine), "repository": REPOSITORY,
                   "head": self.head, "base": self.base, "token": "authored_fixture"}
        for change in ({"head": "refs/heads/main"}, {"repository": {"id": 1, "full_name": "owner/repo/extra"}},
                       {"token": "secret\nextraHeader=injected"}, {"argv": ["checkout"]}):
            with self.subTest(change=change), patch.object(source.Path, "cwd", return_value=quarantine), \
                 patch.object(source.os, "execve", side_effect=AssertionError("must not exec")), self.assertRaises(source.ContractError):
                source._fetch_worker(source.canonical_bytes({**request, **change}))
        (quarantine / "config").write_bytes(source._CONFIG + b"[http]\nsslVerify=false\n")
        with patch.object(source.Path, "cwd", return_value=quarantine), self.assertRaisesRegex(source.SourceAcquisitionError, "invalid_source_worker"):
            source._fetch_worker(source.canonical_bytes(request))

    def test_raw_basic_and_truncated_credential_echo_is_sanitized_before_any_stream_persistence(self):
        token = "ghs_authored_echo_fixture_1234567890"
        for label, echo, termination in (("raw", token.encode(), "exited"),
                ("basic", source._credential_forms(token)[1].encode(), "exited"),
                ("partial", source._credential_forms(token)[1][:25].encode(), "output_limit")):
            self.destination = self.root / label
            def echoing(argv, **options):
                if "--fetch-worker" in argv:
                    return ({"argv": argv, "duration_ms": 0, "exit_code": 128, "termination": termination},
                            {"stdout": b"authored harmless output", "stderr": b"authored echo: " + echo})
                return self.real_execute(argv, **options)
            with self.subTest(label=label), patch.object(source, "_execute", side_effect=echoing), \
                 self.assertRaisesRegex(source.SourceAcquisitionError, "sensitive_source_response_refused"):
                source.acquire_source(self.destination, repository=REPOSITORY, head_sha=self.head,
                    base_tip_sha=self.base, git_executable=self.git, token=token)
            self.assertEqual(b"sensitive_source_response_refused\n", (self.destination / "raw/fetch.stderr").read_bytes())
            error = source.loads_strict((self.destination / "acquisition-error.json").read_bytes())
            self.assertEqual("sanitized_sensitive_output", error["processes"][-1]["stream_retention"])
            self.assertEqual(128, error["processes"][-1]["process"]["exit_code"])
            self.assertFalse((self.destination / "acquisition.json").exists())
            for path in self.destination.rglob("*"):
                if path.is_file():
                    self.assertNotIn(echo, path.read_bytes())

    def test_invalid_auth_token_is_rejected_before_creating_destination(self):
        for token in ("", True, "bad\nsecret", "not:valid", "x" * 4097):
            with self.subTest(token_type=type(token).__name__), patch.object(source, "_execute", side_effect=AssertionError("must not spawn")), \
                 self.assertRaisesRegex(source.SourceAcquisitionError, "invalid_source_token"):
                source.acquire_source(self.destination, repository=REPOSITORY, head_sha=self.head,
                    base_tip_sha=self.base, git_executable=self.git, token=token)
            self.assertFalse(self.destination.exists())

    def test_auth_failure_deadline_and_output_refusal_never_retry_anonymously_or_produce_snapshot(self):
        for termination, exit_code in (("exited", 128), ("timeout", None), ("output_limit", None)):
            self.destination = self.root / termination
            seen = []
            def failed(argv, **options):
                seen.append(argv)
                if "--fetch-worker" in argv:
                    return ({"argv": argv, "duration_ms": 1, "exit_code": exit_code, "termination": termination},
                            {"stdout": b"", "stderr": b"authored acquisition incomplete\n"})
                return self.real_execute(argv, **options)
            with self.subTest(termination=termination), patch.object(source, "_execute", side_effect=failed), \
                 self.assertRaisesRegex(source.SourceAcquisitionError, "source_git_process_failed"):
                source.acquire_source(self.destination, repository=REPOSITORY, head_sha=self.head,
                    base_tip_sha=self.base, git_executable=self.git, token="ghs_authored_failure_fixture")
            self.assertEqual(2, len(seen))
            self.assertFalse(any("fetch" in argv for argv in seen))
            self.assertFalse((self.destination / "snapshot").exists())
            self.assertFalse((self.destination / "acquisition.json").exists())
            error = source.loads_strict((self.destination / "acquisition-error.json").read_bytes())
            self.assertEqual(termination, error["processes"][-1]["process"]["termination"])
            self.assertEqual(b"authored acquisition incomplete\n", (self.destination / "raw/fetch.stderr").read_bytes())

    def test_real_isolated_worker_invalid_message_is_bounded_and_does_not_echo(self):
        # First-party worker only; no network operation or reviewed code runs.
        process, streams = self.real_execute([sys.executable, "-I", "-B", str(Path(source.__file__).resolve()), "--fetch-worker"],
            cwd=self.root, env=source._environment(Path(self.git), self.root, self.root),
            deadline=time.monotonic() + 10, limits=SimpleNamespace(max_stdout_bytes=1024, max_stderr_bytes=1024),
            stdin_bytes=b'{"token":"authored-secret-must-not-echo"}')
        self.assertEqual(("exited", 70), (process["termination"], process["exit_code"]))
        self.assertEqual({"stdout": b"", "stderr": b"source_fetch_worker_failed\n"}, streams)

    def test_fixed_sha_acquisition_preserves_subject_graph_and_separate_api_precondition(self):
        result = self.acquire()
        verify_snapshot(result.snapshot)
        self.assertEqual((self.head, self.base, self.common), (result.snapshot.head_sha, result.snapshot.base_tip_sha, result.snapshot.merge_base_sha))
        self.assertEqual("anonymous_public", result.evidence["mode"])
        self.assertEqual("supplied_not_reverified", result.evidence["api_identity"])
        self.assertEqual(REPOSITORY, result.evidence["repository"])
        self.assertEqual("unconfirmed", result.snapshot.inventory["repository_membership"])
        self.assertEqual(hashlib.sha256(result.snapshot.inventory_bytes).hexdigest(), result.evidence["snapshot_inventory_sha256"])
        self.assertEqual(result.evidence_bytes, (self.destination / "acquisition.json").read_bytes())
        self.assertFalse((self.destination / "acquisition-error.json").exists())
        self.assertEqual(b"head\r\n", (result.snapshot.root / "README.txt").read_bytes())
        for item in result.evidence["quarantine_files"]:
            raw = (self.destination / item["path"]).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (item["size_bytes"], item["sha256"]))

    def test_only_reachable_objects_enter_snapshot_and_extra_native_objects_stay_diagnostic(self):
        extra = self.value.add("blob", b"authored unrelated captured object")
        tag = self.value.add("tag", b"object " + self.head.encode() + b"\ntype commit\ntag synthetic\n\ntag\n")
        result = self.acquire()
        self.assertEqual(2, result.evidence["excluded_object_count"])
        ids = {item["oid"] for item in result.snapshot.inventory["objects"]}
        self.assertFalse({extra, tag} & ids)
        self.assertEqual(len(self.value.objects) - 2, result.evidence["reachable_object_count"])

    def test_fetch_argv_and_environment_cannot_inherit_auth_proxy_redirect_or_pr_execution(self):
        injected = {key: "must-not-leak" for key in ("GH_TOKEN", "GITHUB_TOKEN", "HTTPS_PROXY", "ALL_PROXY", "GIT_CONFIG_COUNT",
                    "GIT_CONFIG_PARAMETERS", "GIT_ASKPASS", "SSH_ASKPASS", "GIT_SSH_COMMAND", "GIT_TRACE", "PYTHONPATH", "LD_PRELOAD")}
        with patch.dict(os.environ, injected):
            self.acquire()
        for argv, options in self.calls:
            environment = options["env"]
            self.assertFalse((set(injected) - {"GIT_CONFIG_COUNT"}) & set(environment))
            self.assertEqual("0", environment["GIT_CONFIG_COUNT"])
            self.assertEqual("https", environment["GIT_ALLOW_PROTOCOL"])
            self.assertEqual(str(self.destination / "home"), environment["HOME"])
            self.assertEqual(os.devnull, environment["GIT_CONFIG_GLOBAL"])
            self.assertNotIn("must-not-leak", str(argv) + str(environment))
            self.assertFalse(any(word in argv for word in ("checkout", "clone", "reset", "archive", "submodule")))
        fetch = next(argv for argv, _ in self.calls if "fetch" in argv)
        self.assertIn("https://github.com/fixture-owner/fixture-repo.git", fetch)
        self.assertIn("+" + self.head + ":refs/delivery-review/head", fetch)
        self.assertIn("+" + self.base + ":refs/delivery-review/base", fetch)
        self.assertIn("--no-write-fetch-head", fetch)
        config = (self.destination / "quarantine/config").read_text()
        for setting in ("followRedirects = false", "proxy =", "sslVerify = true", "cookieFile =", "helper ="):
            self.assertIn(setting, config)

    def test_private_auth_and_invalid_identity_are_refused_before_disk_or_process(self):
        for values in ({"token": "secret"}, {"repository": {"id": True, "full_name": "owner/repo"}},
                       {"repository": {"id": 1, "full_name": "evil.example/path/repo"}}, {"head_sha": "main"},
                       {"repository": {"id": 1, "full_name": "../repo"}}):
            arguments = {"repository": REPOSITORY, "head_sha": self.head, "base_tip_sha": self.base, "git_executable": self.git, **values}
            with self.subTest(values=values), patch.object(source, "_execute", side_effect=AssertionError("must not spawn")), \
                 self.assertRaises(source.SourceAcquisitionError) as caught:
                source.acquire_public_source(self.destination, **arguments)
            self.assertNotIn("secret", str(caught.exception))
            self.assertFalse(self.destination.exists())

    def test_missing_parent_never_uses_a_shallow_or_wrong_merge_base(self):
        del self.value.objects[self.common]
        with self.assertRaisesRegex(source.SourceAcquisitionError, "closure_incomplete"):
            self.acquire()
        self.assertFalse((self.destination / "acquisition.json").exists())
        self.assertFalse((self.destination / "snapshot").exists())
        self.assertTrue((self.destination / "acquisition-error.json").exists())

    def test_native_fetch_failure_remains_diagnostic_without_successful_snapshot(self):
        def failed(argv, **options):
            if "fetch" in argv:
                return ({"argv": argv, "duration_ms": 1, "exit_code": 128, "termination": "exited"},
                        {"stdout": b"", "stderr": b"authored HTTP failure\n"})
            return self.real_execute(argv, **options)
        with patch.object(source, "_execute", side_effect=failed), self.assertRaises(source.SourceAcquisitionError):
            source.acquire_public_source(self.destination, repository=REPOSITORY, head_sha=self.head,
                base_tip_sha=self.base, git_executable=self.git)
        error = (self.destination / "acquisition-error.json").read_bytes()
        self.assertIn(b'"exit_code":128', error)
        self.assertEqual(b"authored HTTP failure\n", (self.destination / "raw/fetch.stderr").read_bytes())
        self.assertFalse((self.destination / "acquisition.json").exists())

    def test_disk_pack_count_file_count_and_links_are_monitored_during_fetch(self):
        for problem in ("disk", "pack", "files", "link"):
            with self.subTest(problem=problem):
                self.destination = self.root / problem
                def oversized(argv, **options):
                    if "fetch" not in argv:
                        return self.real_execute(argv, **options)
                    quarantine = Path(options["cwd"])
                    path = quarantine / "objects/problem"
                    if problem == "pack":
                        path.write_bytes(struct.pack(">4sII", b"PACK", 2, 20001))
                    elif problem == "link":
                        os.link(quarantine / "config", path)
                    elif problem == "files":
                        for index in range(10):
                            (quarantine / f"extra-{index}").mkdir()
                    else:
                        path.write_bytes(b"X" * 2048)
                    options["monitor"]()
                    self.fail("resource monitor must refuse")
                limits = replace(source.SourceFetchLimits(), max_disk_bytes=1024) if problem == "disk" else \
                         replace(source.SourceFetchLimits(), max_quarantine_files=10) if problem == "files" else source.SourceFetchLimits()
                with patch.object(source, "_execute", side_effect=oversized), self.assertRaises(source.SourceAcquisitionError):
                    source.acquire_public_source(self.destination, repository=REPOSITORY, head_sha=self.head,
                        base_tip_sha=self.base, git_executable=self.git, limits=limits)
                self.assertFalse((self.destination / "acquisition.json").exists())

    def test_object_metadata_bounds_precede_reading_contents(self):
        def huge(argv, **options):
            if "--batch-all-objects" in argv:
                return ({"argv": argv, "duration_ms": 0, "exit_code": 0, "termination": "exited"},
                        {"stdout": self.head.encode() + b" commit 9999999999\n", "stderr": b""})
            self.assertNotIn("--batch", argv)
            return self.fetch(argv, **options)
        with patch.object(source, "_execute", side_effect=huge), self.assertRaisesRegex(source.SourceAcquisitionError, "object_size_limit"):
            source.acquire_public_source(self.destination, repository=REPOSITORY, head_sha=self.head,
                base_tip_sha=self.base, git_executable=self.git)
        self.assertFalse((self.destination / "acquisition.json").exists())

    def test_source_symlink_or_gitlink_is_not_materialized(self):
        value = Objects()
        blob = value.add("blob", b"outside")
        self.head = self.base = value.commit(value.tree([(b"120000", b"link", blob)]))
        self.value = value
        with self.assertRaisesRegex(source.SourceAcquisitionError, "unsupported_tree_mode"):
            self.acquire()
        self.assertFalse((self.destination / "snapshot").exists())

    def test_limits_cannot_be_raised_or_boolean_and_existing_destination_is_untouched(self):
        for limits in (replace(source.SourceFetchLimits(), timeout_seconds=True),
                       replace(source.SourceFetchLimits(), max_disk_bytes=129 * 1024 * 1024)):
            with self.assertRaisesRegex(source.SourceAcquisitionError, "invalid_source_fetch_limits"):
                self.acquire(limits=limits)
            self.assertFalse(self.destination.exists())
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_bytes(b"original")
        with self.assertRaises(source.SourceAcquisitionError):
            self.acquire()
        self.assertEqual([marker], list(self.destination.iterdir()))
        self.assertEqual(b"original", marker.read_bytes())


if __name__ == "__main__":
    unittest.main()
