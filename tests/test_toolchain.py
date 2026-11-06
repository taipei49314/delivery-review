"""Authored bootstrap boundary controls; these tests make no network requests."""
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from email.message import Message
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import warnings
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from delivery_review import cli, toolchain, toolchain_cli, toolchain_download
from delivery_review.contracts import ContractError, canonical_bytes
from delivery_review.inspection_cli import load_tools


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class Response(io.BytesIO):
    def __init__(self, raw, url, status=200, headers=()):
        super().__init__(raw)
        self.url, self.status, self.headers, self.reads = url, status, Message(), []
        for key, value in headers:
            self.headers[key] = value

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self, size=-1):
        self.reads.append(size)
        return super().read(size)


def archive_fixture(kind="zip", *, member=None, duplicate=False, link=False, binary=b"authored inert executable bytes"):
    stream = io.BytesIO()
    selected = "bin/gh.exe" if kind == "zip" else "gh_2.98.0_linux_amd64/bin/gh"
    member = selected if member is None else member
    if kind == "zip":
        with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for unused in range(2 if duplicate else 1):
                entry = zipfile.ZipInfo(member)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK if link else stat.S_IFREG) << 16
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    archive.writestr(entry, binary)
    else:
        with tarfile.open(fileobj=stream, mode="w:gz") as archive:
            for unused in range(2 if duplicate else 1):
                entry = tarfile.TarInfo(member)
                entry.size = 0 if link else len(binary)
                if link:
                    entry.type, entry.linkname = tarfile.SYMTYPE, "../outside"
                archive.addfile(entry, None if link else io.BytesIO(binary))
    raw = stream.getvalue()
    spec = {"filename": "authored.zip" if kind == "zip" else "authored.tar.gz", "size_bytes": len(raw),
        "sha256": digest(raw), "member": selected, "binary_size_bytes": len(binary), "binary_sha256": digest(binary)}
    return raw, spec, binary


class ToolchainTransportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name) / "download"
        self.lock = toolchain.load_lock()
        self.url = self.lock["engines"]["ledgerwash"]["url"]
        self.gh_url = self.lock["gh"]["linux-amd64"]["url"]
        self.asset = "https://release-assets.githubusercontent.com/github-production-release-asset/1234/abcd-1234?sig=authored-signed-query"
        self.raw = b"authored tool bytes"

    def fetch(self, responses, *, url=None, raw=None):
        raw = self.raw if raw is None else raw
        opener = Mock()
        opener.open.side_effect = responses
        with patch.object(toolchain_download.urllib.request, "build_opener", return_value=opener) as build:
            toolchain_download.download(url or self.url, self.destination, size_bytes=len(raw), sha256=digest(raw), timeout_seconds=1)
        return opener, build

    def test_direct_download_exact_bytes_without_auth_or_proxy(self):
        response = Response(self.raw, self.url, headers=[("Content-Length", str(len(self.raw)))])
        opener, build = self.fetch([response])
        self.assertEqual(self.destination.read_bytes(), self.raw)
        self.assertEqual(build.call_args.args[0].proxies, {})
        request = opener.open.call_args.args[0]
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(request.headers["Accept-encoding"], "identity")
        self.assertTrue(response.closed)

    def test_only_one_official_release_asset_redirect_is_followed(self):
        initial = Response(b"never read this signed redirect body", self.gh_url, 302, [("Location", self.asset)])
        final = Response(self.raw, self.asset)
        opener, unused = self.fetch([initial, final], url=self.gh_url)
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(initial.reads, [])
        self.assertEqual(self.destination.read_bytes(), self.raw)

    def test_redirect_escapes_and_ambiguous_location_are_refused_before_body(self):
        destinations = ["http://release-assets.githubusercontent.com/github-production-release-asset/1/a",
            "https://release-assets.githubusercontent.com.evil.example/github-production-release-asset/1/a",
            "https://user:secret@release-assets.githubusercontent.com/github-production-release-asset/1/a",
            "https://release-assets.githubusercontent.com:443/github-production-release-asset/1/a",
            "https://release-assets.githubusercontent.com/github-production-release-asset/1/a#fragment",
            "https://api.github.com/secret", "https://127.0.0.1/secret", "/relative", self.asset + "\n"]
        for destination in destinations:
            with self.subTest(destination=destination):
                response = Response(b"must not retain", self.gh_url, 302, [("Location", destination)])
                with self.assertRaises(ValueError):
                    self.fetch([response], url=self.gh_url)
                self.assertEqual(response.reads, [])
                self.assertFalse(self.destination.exists())
        for locations in ([], [("Location", self.asset), ("Location", self.asset)]):
            with self.assertRaises(ValueError):
                self.fetch([Response(b"", self.gh_url, 302, locations)], url=self.gh_url)

    def test_pypi_and_second_redirects_are_refused(self):
        with self.assertRaises(ValueError):
            self.fetch([Response(b"", self.url, 302, [("Location", self.asset)])])
        initial = Response(b"", self.gh_url, 302, [("Location", self.asset)])
        with self.assertRaises(ValueError):
            self.fetch([initial, Response(b"", self.asset, 302, [("Location", self.asset)])], url=self.gh_url)

    def test_http_errors_encoding_and_wrong_length_never_read_body(self):
        for status, headers in [(403, []), (200, [("Content-Encoding", "gzip")]),
                (200, [("Content-Length", "999")]), (200, [("Content-Length", "19"), ("Content-Length", "19")])]:
            response = Response(self.raw, self.url, status, headers)
            with self.subTest(status=status, headers=headers), self.assertRaises(ValueError):
                self.fetch([response])
            self.assertEqual(response.reads, [])

    def test_overflow_truncation_and_same_size_mutation_cannot_succeed(self):
        for supplied in (self.raw + b"extra", self.raw[:-1], b"x" * len(self.raw)):
            with self.subTest(supplied=supplied), tempfile.TemporaryDirectory() as temporary:
                self.destination = Path(temporary) / "download"
                response = Response(supplied, self.url)
                with self.assertRaises(ValueError):
                    self.fetch([response])
                self.assertLessEqual(max(response.reads), len(self.raw) + 1)
                if self.destination.exists():
                    self.assertLessEqual(self.destination.stat().st_size, len(self.raw))

    def test_implicit_redirect_and_existing_output_cannot_succeed(self):
        with self.assertRaises(ValueError):
            self.fetch([Response(self.raw, "https://other.example")])
        self.destination.write_bytes(b"operator existing bytes")
        with self.assertRaises(FileExistsError):
            self.fetch([Response(self.raw, self.url)])
        self.assertEqual(self.destination.read_bytes(), b"operator existing bytes")

    def test_worker_timeout_kills_process_and_never_exports_tokens(self):
        process = Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("worker", 1)
        process.poll.return_value = None
        secret = "authored-not-a-real-secret"
        with patch.dict(os.environ, {"GH_TOKEN": secret, "GITHUB_TOKEN": secret, "HTTPS_PROXY": secret, "PYTHONPATH": secret,
                "HOME": secret, "PATH": secret}), patch.object(toolchain.subprocess, "Popen", return_value=process) as popen:
            with self.assertRaisesRegex(ContractError, "tool_download_timeout"):
                toolchain._download(self.lock["engines"]["ledgerwash"], deadline=time.monotonic() + 1)
        process.kill.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=10)
        call = popen.call_args
        self.assertIn("-I", call.args[0])
        self.assertNotIn(secret, repr(call.kwargs))
        self.assertNotIn(secret.encode(), process.communicate.call_args.args[0])
        self.assertEqual(call.kwargs["stderr"], subprocess.DEVNULL)


class ToolchainProvisionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_real_lock_matches_frozen_engine_and_crypto_pins_and_original_root(self):
        lock = toolchain.load_lock()
        root = toolchain._trusted_root(lock["trusted_root"])
        self.assertEqual(len(root), 34634)
        self.assertEqual(digest(root), "65ca537f6ed8a47fd0e560c421baa1f6c1efb8b25fc200d8c5c02c0e92eb2b9c")
        self.assertEqual(len(root.splitlines()), 2)

    def test_moved_download_or_binary_pin_is_not_accepted(self):
        actual = toolchain.definition_bytes
        for section, key, field in [("engines", "checkwash", "url"), ("gh", "linux-amd64", "binary_sha256")]:
            lock = toolchain.load_lock()
            lock[section][key][field] = "https://example.invalid/file" if field == "url" else "0" * 64
            with patch.object(toolchain, "definition_bytes", side_effect=lambda group, name: canonical_bytes(lock)
                    if name == "toolchain-lock.json" else actual(group, name)):
                with self.subTest(section=section), self.assertRaises(ContractError):
                    toolchain.load_lock()

    def test_only_exact_regular_unique_archive_member_is_read(self):
        for kind in ("zip", "tar"):
            raw, spec, binary = archive_fixture(kind)
            with self.subTest(kind=kind):
                self.assertEqual(toolchain._extract_gh(raw, spec), binary)
            for options in ({"duplicate": True}, {"link": True}, {"member": "../bin/gh"}, {"member": "/bin/gh"}):
                raw, spec, unused = archive_fixture(kind, **options)
                with self.subTest(kind=kind, options=options), self.assertRaises(ContractError):
                    toolchain._extract_gh(raw, spec)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_archive_hash_is_checked_before_parser_and_binary_hash_after(self):
        raw, spec, unused = archive_fixture()
        with patch.object(toolchain.zipfile, "ZipFile") as parser, self.assertRaisesRegex(ContractError, "digest_mismatch"):
            toolchain._extract_gh(raw + b"mutation", spec)
        parser.assert_not_called()
        with self.assertRaisesRegex(ContractError, "digest_mismatch"):
            toolchain._extract_gh(raw, {**spec, "binary_sha256": "0" * 64})

    def authored_install(self, output, *, fail=False):
        lock = deepcopy(toolchain.load_lock())
        raw_files = {}
        for name, entry in lock["engines"].items():
            raw = b"authored inert wheel " + name.encode()
            entry.update(sha256=digest(raw), size_bytes=len(raw))
            raw_files[entry["url"]] = raw
        raw, spec, unused = archive_fixture("zip")
        entry = lock["gh"]["windows-amd64"]
        entry.update({key: value for key, value in spec.items() if key != "filename"})
        raw_files[entry["url"]] = raw
        git, python = self.root / "git", self.root / "python"
        git.write_bytes(b"authored operator Git; not executed")
        python.write_bytes(b"authored operator Python; not executed")
        calls = []
        def download(entry, *, deadline):
            calls.append(entry["url"])
            if fail and len(calls) == 2:
                raise ContractError("tool_download_failed")
            return raw_files[entry["url"]]
        with patch.object(toolchain, "_operator_tools", return_value=("windows-amd64", git, python)), \
                patch.object(toolchain, "load_lock", return_value=lock), patch.object(toolchain, "_download", side_effect=download):
            return toolchain.install_tools(output)

    def test_new_toolchain_loads_and_downloaded_paths_remain_relative(self):
        output = self.root / "tools"
        config = self.authored_install(output)
        values = json.loads(config.read_bytes())
        for path in [values["gh_executable"], values["trusted_root"], *values["engine_wheels"].values()]:
            self.assertFalse(Path(path).is_absolute())
        tools = load_tools(config, live=True)
        self.assertEqual(set(tools.engine_wheels), {"checkwash", "pinwash", "ledgerwash"})
        self.assertEqual(tools.gh_executable.read_bytes(), b"authored inert executable bytes")
        self.assertIn(digest(tools.trusted_root_bytes), toolchain.TRUSTED_ROOT_PINS)
        receipt = json.loads((output / "download-receipt.json").read_bytes())
        self.assertEqual(receipt["qualification"], "not_established")
        self.assertEqual(len(receipt["downloads"]), 4)

    def test_partial_install_has_no_configuration_or_completed_receipt(self):
        output = self.root / "partial"
        with self.assertRaisesRegex(ContractError, "tool_download_failed"):
            self.authored_install(output, fail=True)
        self.assertTrue(output.is_dir())
        self.assertFalse((output / "toolchain.json").exists())
        self.assertFalse((output / "download-receipt.json").exists())

    def test_existing_output_and_unsupported_runtime_fail_before_download(self):
        existing = self.root / "existing"
        existing.mkdir()
        marker = existing / "keep"
        marker.write_bytes(b"unchanged")
        with patch.object(toolchain, "_download") as fetch, self.assertRaises(FileExistsError):
            toolchain.install_tools(existing)
        fetch.assert_not_called()
        self.assertEqual(marker.read_bytes(), b"unchanged")
        for target, value, error in [("sys.version_info", (3, 13), "unsupported_toolchain_python"),
                ("platform.machine", lambda: "arm64", "unsupported_toolchain_platform"),
                ("shutil.which", lambda name: None, "git_executable_unavailable")]:
            with patch("delivery_review.toolchain." + target, value), patch.object(toolchain, "_download") as fetch:
                with self.subTest(error=error), self.assertRaisesRegex(ContractError, error):
                    toolchain.install_tools(self.root / "new")
                fetch.assert_not_called()
                self.assertFalse((self.root / "new").exists())

    def test_cli_delegates_and_sanitizes_failures(self):
        out, err = io.StringIO(), io.StringIO()
        with patch.object(toolchain_cli, "install_tools", return_value=self.root / "toolchain.json") as install, \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(["tools", "install", "--out", str(self.root / "new")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["qualification"], "not_established")
        install.assert_called_once_with(self.root / "new")
        with patch.object(toolchain_cli, "install_tools", side_effect=OSError("https://private.example?sig=secret")), \
                redirect_stdout(io.StringIO()), redirect_stderr(err):
            code = cli.main(["tools", "install", "--out", str(self.root / "new")])
        self.assertEqual(code, 2)
        self.assertNotIn("secret", err.getvalue())
        self.assertEqual(json.loads(err.getvalue())["reason"], "tool_install_failed")


if __name__ == "__main__":
    unittest.main()
