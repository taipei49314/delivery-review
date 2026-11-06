"""Installed identity and offline resources; actual wheel smoke is separate."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from build_identity import expected_package_files, source_files, source_identity, verify_build_package

from delivery_review import __version__
from delivery_review.contracts import ContractError, validators
from delivery_review.provenance import _installed_identity, collector_identity
from delivery_review.resources import definition_bytes


class PackageBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.package = Path(temporary.name) / "package"
        self.package.mkdir()
        (self.package / "module.py").write_bytes(b"# authored package fixture\n")
        self.info = {"kind": "delivery-review-build", "version": 1, "package_version": __version__,
                     "source_sha": "a" * 40, "source_state": "clean", "files": {
                         "module.py": hashlib.sha256((self.package / "module.py").read_bytes()).hexdigest()}}
        self.save()

    def save(self):
        (self.package / "_build_info.json").write_text(json.dumps(self.info), encoding="utf-8")

    def test_resources_match_frozen_original_bytes_and_never_use_cwd(self):
        for path in (ROOT / "schemas").glob("*.json"):
            self.assertEqual(path.read_bytes(), definition_bytes("schemas", path.name))
        self.assertEqual(6, len(validators()))
        for group, name in (("other", "x.json"), ("schemas", "../policy.schema.json"),
                            ("schemas", "..\\policy.schema.json"), ("schemas", "x.py")):
            with self.subTest(group=group, name=name), self.assertRaises(ValueError):
                definition_bytes(group, name)

    def test_consistent_installation_identity_and_untracked_bytecode(self):
        (self.package / "__pycache__").mkdir()
        (self.package / "__pycache__/module.pyc").write_bytes(b"not evidence")
        self.assertEqual({"version": __version__, "source_sha": "a" * 40}, _installed_identity(self.package))

    def test_package_manager_hardlinks_are_hash_checked_without_weakening_bundle_rules(self):
        cache_module = self.package.parent / "cached-module.py"
        cache_manifest = self.package.parent / "cached-build.json"
        os.link(self.package / "module.py", cache_module)
        os.link(self.package / "_build_info.json", cache_manifest)
        self.assertEqual("a" * 40, _installed_identity(self.package)["source_sha"])
        from delivery_review.evidence.store import _read, BundleError
        with self.assertRaises(BundleError):
            _read(self.package / "module.py", 4096)
        cache_module.write_bytes(b"changed through package cache alias")
        with self.assertRaisesRegex(ContractError, "collector_installation_changed"):
            _installed_identity(self.package)

    def test_changed_missing_and_extra_modules_refuse_live_identity(self):
        path = self.package / "module.py"
        original = path.read_bytes()
        path.write_bytes(b"modified")
        with self.assertRaises(ContractError):
            _installed_identity(self.package)
        path.unlink()
        with self.assertRaises(ContractError):
            _installed_identity(self.package)
        path.write_bytes(original)
        (self.package / "extra.py").write_bytes(b"unexpected")
        with self.assertRaises(ContractError):
            _installed_identity(self.package)

    def test_unverified_or_malformed_build_cannot_claim_source_revision(self):
        original = deepcopy(self.info)
        for changes in ({"source_state": "unverified"}, {"source_sha": None}, {"source_sha": "a" * 7},
                        {"source_sha": "A" * 40}, {"version": True}, {"version": 2},
                        {"package_version": "other"}, {"files": []}, {"files": {"../outside.py": "a" * 64}}):
            self.info = {**original, **changes}
            self.save()
            with self.subTest(changes=changes), self.assertRaises(ContractError):
                _installed_identity(self.package)

    def test_source_git_has_no_ambient_credentials_or_git_overrides(self):
        path = ROOT / "src/delivery_review/__init__.py"
        raw, oid = path.read_bytes(), b"c" * 40
        header = oid + b" blob " + str(len(raw)).encode() + b"\n"
        responses = [b"b" * 40 + b"\n", str(ROOT).encode() + b"\n", b"",
                     b"100644 blob " + oid + b"\tsrc/delivery_review/__init__.py\0", header, header + raw + b"\n"]
        with patch.dict(os.environ, {"GH_TOKEN": "credential", "GIT_CONFIG_COUNT": "99",
                                     "GIT_DIR": "other", "PYTHONPATH": "bad"}), \
                patch("delivery_review.provenance.source_paths", return_value=[path]), \
                patch("delivery_review._source_identity.subprocess.check_output", side_effect=responses) as run:
            self.assertEqual("b" * 40, collector_identity()["source_sha"])
        for call in run.call_args_list:
            env = call.kwargs["env"]
            self.assertFalse({"GH_TOKEN", "GIT_CONFIG_COUNT", "GIT_DIR", "PYTHONPATH"} & set(env))
            self.assertEqual(os.devnull, env["GIT_CONFIG_GLOBAL"])
            self.assertEqual(["git", "-c", "core.fsmonitor=false"], call.args[0][:3])

    def test_dirty_or_nested_checkout_refuses_source_identity(self):
        for top, status in ((ROOT, b" M src/module.py\n"), (ROOT.parent, b"")):
            with self.subTest(top=top, status=status), \
                    patch("delivery_review._source_identity.subprocess.check_output",
                          side_effect=[b"a" * 40, str(top).encode(), status]), self.assertRaises(ContractError):
                collector_identity()


class BuildIdentityTests(unittest.TestCase):
    """Actual temporary Git repositories, with only authored harmless files."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "source"
        self.root.mkdir()
        for name in ("pyproject.toml", "setup.py", "MANIFEST.in", "build_identity.py", "README.md", "LICENSE", "NOTICE"):
            (self.root / name).write_bytes(b"synthetic build fixture\n")
        package = self.root / "src/delivery_review"
        package.mkdir(parents=True)
        (package / "__init__.py").write_bytes(b"# authored fixture\n")
        for group in ("schemas", "formats", "locks"):
            directory = self.root / group
            directory.mkdir()
            (directory / "fixture.json").write_bytes(b'{"synthetic":true}\r\n')
        self.git_executable = shutil.which("git")
        self.assertIsNotNone(self.git_executable, "Git is required for build provenance controls")

    def git(self, *args):
        environment = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
        environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        return subprocess.check_output([self.git_executable, *args], cwd=self.root, env=environment,
                                       stderr=subprocess.DEVNULL).decode().strip()

    def initialize(self):
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "synthetic build fixture")
        return source_identity(self.root)

    def record(self, value):
        (self.root / "_source_provenance.json").write_text(json.dumps(value), encoding="utf-8")

    def test_clean_own_checkout_uses_actual_git_and_inventories_build_helper(self):
        identity = self.initialize()
        self.assertEqual("clean", identity["source_state"])
        self.assertEqual(self.git("rev-parse", "HEAD"), identity["source_sha"])
        self.assertIn("build_identity.py", identity["files"])

    def test_valid_archive_record_cannot_override_dirty_own_checkout(self):
        identity = self.initialize()
        self.record(identity)
        (self.root / "README.md").write_bytes(b"dirty metadata\n")
        result = source_identity(self.root)
        self.assertEqual("unverified", result["source_state"])
        self.assertIsNone(result["source_sha"])
        (self.root / "README.md").write_bytes(b"synthetic build fixture\n")
        self.assertEqual("unverified", source_identity(self.root)["source_state"])

    def test_broken_or_unavailable_own_git_never_falls_back_to_archive_record(self):
        identity = self.initialize()
        self.record(identity)
        with patch("build_identity._source.subprocess.check_output", side_effect=OSError("Git unavailable")):
            self.assertEqual("unverified", source_identity(self.root)["source_state"])
        # A worktree marker also denotes own Git metadata, even if it is broken.
        other = self.root.parent / "archive"
        shutil.copytree(self.root, other, ignore=shutil.ignore_patterns(".git"))
        (other / ".git").write_text("gitdir: nonexistent\n")
        with patch("build_identity._source.subprocess.check_output", side_effect=OSError("Git unavailable")):
            self.assertEqual("unverified", source_identity(other)["source_state"])

    def test_archive_record_requires_exact_all_build_inputs_without_git_lookup(self):
        identity = {"source_state": "clean", "source_sha": "a" * 40, "files": source_files(self.root)}
        self.record(identity)
        with patch("build_identity._source.subprocess.check_output", side_effect=AssertionError("no enclosing Git lookup")):
            self.assertEqual(identity, source_identity(self.root))
            (self.root / "NOTICE").write_bytes(b"changed release notice\n")
            self.assertEqual("unverified", source_identity(self.root)["source_state"])

    def test_source_archive_without_valid_record_cannot_inherit_enclosing_checkout(self):
        self.assertEqual("unverified", source_identity(self.root)["source_state"])
        self.record({"source_state": "clean", "source_sha": "a" * 40, "files": {}})
        self.assertEqual("unverified", source_identity(self.root)["source_state"])
        (self.root / "_source_provenance.json").write_text("{broken")
        self.assertEqual("unverified", source_identity(self.root)["source_state"])

    def test_build_package_exactly_matches_modules_and_recursive_resource_bytes(self):
        nested = self.root / "formats/nested"
        nested.mkdir()
        (nested / "fixture.json").write_bytes(b'{"nested":true}\n')
        expected = expected_package_files(self.root)
        self.assertIn("data/formats/nested/fixture.json", expected)
        destination = self.root.parent / "build/delivery_review"
        for name, source in expected.items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        inventory = verify_build_package(destination, expected, complete=True)
        self.assertEqual(set(expected), set(inventory))
        for name in expected:
            self.assertEqual(hashlib.sha256(expected[name].read_bytes()).hexdigest(), inventory[name])
        for extra in ("stale.py", "stale.pyd", "data/schemas/stale.json", "__pycache__/stale.pyc"):
            path = destination / extra
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unexpected build bytes")
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                verify_build_package(destination, expected, complete=False)
            path.unlink()
        (destination / "__init__.py").write_bytes(b"stale same-name module")
        with self.assertRaises(ValueError):
            verify_build_package(destination, expected, complete=True)

    def test_index_flags_cannot_hide_changed_source_from_build_or_runtime(self):
        self.initialize()
        path = self.root / "src/delivery_review/__init__.py"
        original = path.read_bytes()
        for enable, disable in (("--assume-unchanged", "--no-assume-unchanged"), ("--skip-worktree", "--no-skip-worktree")):
            self.git("update-index", enable, "src/delivery_review/__init__.py")
            path.write_bytes(b"# changed while Git status is clean\n")
            self.assertEqual("", self.git("status", "--porcelain"))
            with self.subTest(flag=enable):
                self.assertEqual("unverified", source_identity(self.root)["source_state"])
                with patch("delivery_review.provenance.__file__", str(path.parent / "provenance.py")), self.assertRaises(ContractError):
                    collector_identity()
            path.write_bytes(original)
            self.git("update-index", disable, "src/delivery_review/__init__.py")
        self.assertEqual("clean", source_identity(self.root)["source_state"])

    def test_worktree_crlf_conversion_is_unverified_even_if_git_status_is_clean(self):
        self.initialize()
        self.git("config", "core.autocrlf", "true")
        path = self.root / "src/delivery_review/__init__.py"
        path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
        self.git("add", "src/delivery_review/__init__.py")
        self.assertEqual("", self.git("status", "--porcelain"))
        self.assertEqual("unverified", source_identity(self.root)["source_state"])
        with patch("delivery_review.provenance.__file__", str(path.parent / "provenance.py")), self.assertRaises(ContractError):
            collector_identity()

    def test_runtime_build_record_cannot_override_own_source_checkout(self):
        self.initialize()
        package = self.root / "src/delivery_review"
        (package / "_build_info.json").write_text("{}")
        with patch("delivery_review.provenance.__file__", str(package / "provenance.py")), \
             patch("delivery_review.provenance._installed_identity", return_value={"source_sha": "a" * 40}) as installed, \
             self.assertRaises(ContractError):
            collector_identity()
        installed.assert_not_called()


if __name__ == "__main__":
    unittest.main()
