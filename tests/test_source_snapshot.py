"""Authored local Git objects; these are not GitHub or scanner qualifications."""
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.source_snapshot import (SnapshotError, SnapshotLimits, materialize_snapshot,
    verify_snapshot, export_snapshot, restore_snapshot)
from delivery_review.contracts import MAX_JSON_BYTES, canonical_bytes


class Objects:
    def __init__(self):
        self.objects = {}

    def add(self, kind, payload):
        raw = kind.encode() + b" " + str(len(payload)).encode() + b"\0" + payload
        oid = hashlib.sha1(raw, usedforsecurity=False).hexdigest()
        self.objects[oid] = raw
        return oid

    def tree(self, entries):
        rows = sorted(entries, key=lambda row: row[1] + (b"/" if row[0] == b"40000" else b""))
        return self.add("tree", b"".join(mode + b" " + name + b"\0" + bytes.fromhex(oid) for mode, name, oid in rows))

    def files(self, values):
        return self.tree([(b"100644", name.encode(), self.add("blob", raw)) for name, raw in values.items()])

    def commit(self, tree, parents=(), message=b"synthetic fixture"):
        payload = b"tree " + tree.encode() + b"\n"
        payload += b"".join(b"parent " + parent.encode() + b"\n" for parent in parents)
        payload += b"author Fixture <fixture@example.invalid> 1 +0000\ncommitter Fixture <fixture@example.invalid> 1 +0000\n\n" + message + b"\n"
        return self.add("commit", payload)


def fixture():
    value = Objects()
    common = value.commit(value.files({"README.txt": b"base\r\n", "policy.json": b'{"threshold":10}\n'}))
    base = value.commit(value.files({"README.txt": b"target branch\n", "policy.json": b'{"threshold":12}\n'}), [common])
    head = value.commit(value.files({"README.txt": b"head\r\n", "policy.json": b'{"threshold":0}\n'}), [common])
    return value, common, base, head


class SourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "snapshot"
        self.git = shutil.which("git")
        self.assertIsNotNone(self.git, "Git is required for source snapshot controls")

    def build(self, value, base, head, **options):
        return materialize_snapshot(self.destination, repository="fixture-owner/fixture-repo", head_sha=head,
                                    base_tip_sha=base, objects=value.objects, git_executable=self.git, **options)

    def test_offline_export_restore_preserves_nondefault_compression_and_inventory(self):
        value, common, base, head = fixture()
        loose = {oid: zlib.compress(raw, level=0) for oid, raw in value.objects.items()}
        snapshot = self.build(value, base, head, loose_objects=loose)
        saved = export_snapshot(snapshot)
        self.assertEqual(loose, saved)
        restored = restore_snapshot(self.root / "restored", inventory_bytes=snapshot.inventory_bytes,
                                    loose_objects=saved, git_executable=self.git)
        self.assertEqual(snapshot.inventory_bytes, restored.inventory_bytes)
        self.assertEqual(loose, export_snapshot(restored))
        self.assertEqual(b"head\r\n", (restored.root / "README.txt").read_bytes())

    def test_restore_rejects_missing_changed_extra_and_concatenated_objects(self):
        value, common, base, head = fixture()
        snapshot = self.build(value, base, head)
        saved = export_snapshot(snapshot)
        oid = next(iter(saved))
        variants = [dict(saved), dict(saved), dict(saved), dict(saved)]
        variants[0].pop(oid)
        variants[1][oid] = zlib.compress(b"blob 0\0")
        variants[2]["f" * 40] = saved[oid]
        variants[3][oid] += zlib.compress(b"extra")
        for i, forged in enumerate(variants):
            with self.subTest(i=i), self.assertRaises(SnapshotError):
                restore_snapshot(self.root / f"bad{i}", inventory_bytes=snapshot.inventory_bytes,
                                 loose_objects=forged, git_executable=self.git)

    def test_restore_does_not_trust_inventory_merge_base_or_repository_membership(self):
        value, common, base, head = fixture()
        snapshot = self.build(value, base, head)
        saved = export_snapshot(snapshot)
        for i, (key, changed) in enumerate((("merge_base_sha", head), ("repository_membership", "confirmed"))):
            document = snapshot.inventory
            document[key] = changed
            with self.assertRaises(SnapshotError):
                restore_snapshot(self.root / f"claimed{i}", inventory_bytes=canonical_bytes(document) + b"\n",
                                 loose_objects=saved, git_executable=self.git)

    def test_exact_complete_source_keeps_head_base_and_merge_base_distinct(self):
        value, common, base, head = fixture()
        snapshot = self.build(value, base, head)
        self.assertEqual((head, base, common), (snapshot.head_sha, snapshot.base_tip_sha, snapshot.merge_base_sha))
        self.assertEqual(b"head\r\n", (snapshot.root / "README.txt").read_bytes())
        self.assertEqual(b'{"threshold":0}\n', (snapshot.root / "policy.json").read_bytes())
        self.assertEqual(head + "\n", (snapshot.root / ".git/HEAD").read_text())
        self.assertFalse((snapshot.root / ".git/index").exists())
        self.assertEqual("pr_head", snapshot.inventory["source_mode"])
        self.assertEqual("unconfirmed", snapshot.inventory["repository_membership"])
        self.assertEqual(snapshot.inventory_bytes, (snapshot.root / ".git/delivery-review-snapshot.json").read_bytes())
        for item in snapshot.inventory["objects"]:
            raw = zlib.decompress((snapshot.root / ".git/objects" / item["oid"][:2] / item["oid"][2:]).read_bytes())
            self.assertEqual(value.objects[item["oid"]], raw)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), item["sha256"])
        snapshot.inventory["files"].clear()
        self.assertEqual(2, len(snapshot.inventory["files"]))
        with patch("delivery_review.source_snapshot.subprocess.run", side_effect=AssertionError("verify must not execute Git")):
            verify_snapshot(snapshot)

    def test_git_receives_only_fixed_read_commands_and_allowlisted_environment(self):
        value, _, base, head = fixture()
        real_run = subprocess.run
        calls = []
        def checked(argv, **options):
            calls.append(argv)
            self.assertFalse(options["shell"])
            self.assertEqual(subprocess.DEVNULL, options["stdin"])
            environment = options["env"]
            for key in ("GIT_CONFIG_PARAMETERS", "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
                        "GIT_SSH_COMMAND", "GIT_ASKPASS", "GIT_TRACE", "PYTHONPATH", "LD_PRELOAD", "HTTPS_PROXY"):
                self.assertNotIn(key, environment)
            self.assertEqual(os.devnull, environment["GIT_CONFIG_GLOBAL"])
            self.assertEqual("1", environment["GIT_CONFIG_NOSYSTEM"])
            self.assertEqual("", environment["GIT_ALLOW_PROTOCOL"])
            self.assertIn("protocol.file.allow=never", argv)
            self.assertLessEqual(options["timeout"], 30)
            return real_run(argv, **options)
        malicious = {key: "must-not-be-used" for key in ("GIT_CONFIG_PARAMETERS", "GIT_OBJECT_DIRECTORY", "GIT_TRACE",
                     "GIT_ASKPASS", "GIT_SSH_COMMAND", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "PYTHONPATH", "LD_PRELOAD", "HTTPS_PROXY")}
        with patch.dict(os.environ, malicious), patch("delivery_review.source_snapshot.subprocess.run", side_effect=checked):
            self.build(value, base, head)
        self.assertEqual(["rev-parse", "rev-parse", "merge-base"], [argv[-3] if "rev-parse" in argv else argv[-4] for argv in calls])
        self.assertFalse(any(command in argv for argv in calls for command in ("checkout", "clone", "fetch", "reset", "read-tree")))

    def test_pr_filter_hooks_and_executable_content_are_materialized_without_execution(self):
        value = Objects()
        marker = self.root / "must-not-exist"
        script = ("from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n").encode()
        blob = value.add("blob", script)
        tree = value.tree([(b"100755", b"run.py", blob),
                           (b"100644", b".gitattributes", value.add("blob", b"* filter=evil diff=evil\n")),
                           (b"100644", b".gitconfig", value.add("blob", b"[filter \"evil\"]\n smudge = must-not-execute\n"))])
        commit = value.commit(tree)
        snapshot = self.build(value, commit, commit)
        self.assertFalse(marker.exists())
        self.assertEqual(script, (snapshot.root / "run.py").read_bytes())
        if os.name != "nt":
            self.assertFalse((snapshot.root / "run.py").stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        self.assertNotIn("evil", (snapshot.root / ".git/config").read_text())
        self.assertEqual([], list((snapshot.root / ".git/disabled-hooks").iterdir()))

    def test_missing_extra_wrong_type_and_tampered_objects_fail_before_writing(self):
        for problem in ("missing", "extra", "type", "tampered", "header"):
            with self.subTest(problem=problem):
                value, common, base, head = fixture()
                if problem == "missing":
                    del value.objects[common]
                elif problem == "extra":
                    value.add("blob", b"not reachable")
                elif problem == "type":
                    head = value.add("blob", b"not a commit")
                elif problem == "tampered":
                    value.objects[head] += b"tamper"
                else:
                    raw = b"blob 01\0x"
                    value.objects[hashlib.sha1(raw, usedforsecurity=False).hexdigest()] = raw
                with self.assertRaises(SnapshotError):
                    self.build(value, base, head)
                self.assertFalse(self.destination.exists())

    def test_symlink_gitlink_and_tag_objects_are_not_accepted(self):
        for mode in (b"120000", b"160000"):
            with self.subTest(mode=mode):
                value = Objects()
                blob = value.add("blob", b"../outside")
                commit = value.commit(value.tree([(mode, b"unsafe", blob)]))
                with self.assertRaisesRegex(SnapshotError, "unsupported_tree_mode"):
                    self.build(value, commit, commit)
                self.assertFalse(self.destination.exists())
        value = Objects()
        tag = value.add("tag", b"object " + b"a" * 40 + b"\ntype commit\ntag v1\n\nfixture\n")
        with self.assertRaisesRegex(SnapshotError, "invalid_object_header"):
            self.build(value, tag, tag)

    def test_traversal_alias_ntfs_and_reserved_names_are_rejected_portably(self):
        names = (b"..", b".", b".git", b".GIT", b"git~1", b"a/b", b"a\\b", b"C:escape", b"name:stream",
                 b"trail.", b"trail ", b"NUL", b"con.txt", b"CON .txt", b"COM1", b"LPT9.log", b"CONOUT$",
                 b"a\nb", b"bad\xff", "e\u0301.txt".encode(), ".g\u200cit".encode())
        for name in names:
            with self.subTest(name=name):
                value = Objects()
                commit = value.commit(value.tree([(b"100644", name, value.add("blob", b"unsafe"))]))
                with self.assertRaises(SnapshotError):
                    self.build(value, commit, commit)
                self.assertFalse(self.destination.exists())
        value = Objects()
        blob = value.add("blob", b"alias")
        commit = value.commit(value.tree([(b"100644", b"Readme", blob), (b"100644", b"README", blob)]))
        with self.assertRaisesRegex(SnapshotError, "tree_path_alias"):
            self.build(value, commit, commit)

    def test_nested_paths_and_reused_tree_expansion_obey_caps(self):
        value = Objects()
        leaf = value.tree([(b"100644", b"file.txt", value.add("blob", b"contents"))])
        tree = value.tree([(b"40000", b"one", leaf), (b"40000", b"two", leaf)])
        commit = value.commit(tree)
        snapshot = self.build(value, commit, commit)
        self.assertEqual(["one/file.txt", "two/file.txt"], [item["path"] for item in snapshot.inventory["files"]])
        self.destination = self.root / "over-limit"
        with self.assertRaisesRegex(SnapshotError, "materialized_size_limit"):
            self.build(value, commit, commit, limits=replace(SnapshotLimits(), max_files=1))
        self.assertFalse(self.destination.exists())
        with self.assertRaisesRegex(SnapshotError, "tree_path_limit"):
            self.build(value, commit, commit, limits=replace(SnapshotLimits(), max_depth=1))

    def test_all_object_file_disk_inventory_and_deadline_limits_fail_closed(self):
        value, _, base, head = fixture()
        self.assertEqual(MAX_JSON_BYTES, SnapshotLimits().max_inventory_bytes)
        with self.assertRaisesRegex(SnapshotError, "invalid_snapshot_limits"):
            self.build(value, base, head, limits=replace(SnapshotLimits(), max_inventory_bytes=MAX_JSON_BYTES + 1))
        for field in ("max_objects", "max_object_bytes", "max_total_object_bytes", "max_object_disk_bytes",
                      "max_materialized_bytes", "max_tree_entries", "max_path_bytes", "max_inventory_bytes"):
            with self.subTest(field=field), self.assertRaises(SnapshotError):
                self.build(value, base, head, limits=replace(SnapshotLimits(), **{field: 1}))
            self.assertFalse(self.destination.exists())
        with patch("delivery_review.source_snapshot.time.monotonic", side_effect=[0, 31]), self.assertRaisesRegex(SnapshotError, "deadline"):
            self.build(value, base, head)
        self.assertFalse(self.destination.exists())

    def test_unrelated_or_ambiguous_history_has_no_completed_inventory(self):
        for ambiguous in (False, True):
            with self.subTest(ambiguous=ambiguous):
                self.destination = self.root / str(ambiguous)
                value = Objects()
                tree = value.files({"file.txt": b"synthetic"})
                left = value.commit(tree, message=b"left")
                right = value.commit(tree, message=b"right")
                if ambiguous:
                    base = value.commit(tree, [left, right], message=b"merge-left")
                    head = value.commit(tree, [right, left], message=b"merge-right")
                else:
                    base, head = left, right
                with self.assertRaises(SnapshotError):
                    self.build(value, base, head)
                self.assertFalse((self.destination / ".git/delivery-review-snapshot.json").exists())

    def test_existing_destination_and_reparse_parent_cannot_be_reused(self):
        value, _, base, head = fixture()
        self.destination.mkdir()
        marker = self.destination / "keep"
        marker.write_text("original")
        with self.assertRaisesRegex(SnapshotError, "destination_exists"):
            self.build(value, base, head)
        self.assertEqual("original", marker.read_text())
        self.destination = self.root / "new"
        real_lstat = Path.lstat
        def reparse(path):
            result = real_lstat(path)
            if path == self.root:
                return type("Stat", (), {"st_mode": result.st_mode, "st_file_attributes": 0x400})()
            return result
        with patch("pathlib.Path.lstat", new=reparse), self.assertRaisesRegex(SnapshotError, "unsafe_destination_parent"):
            self.build(value, base, head)
        self.assertFalse(self.destination.exists())

    def test_git_timeout_or_wrong_identity_never_produces_a_successful_snapshot(self):
        for timeout in (False, True):
            self.destination = self.root / f"git-{timeout}"
            value, _, base, head = fixture()
            error = subprocess.TimeoutExpired("synthetic", 1) if timeout else None
            output = subprocess.CompletedProcess([], 0, b"0" * 40 + b"\n", b"")
            with patch("delivery_review.source_snapshot.subprocess.run", side_effect=error, return_value=output), self.assertRaises(SnapshotError):
                self.build(value, base, head)
            self.assertFalse((self.destination / ".git/delivery-review-snapshot.json").exists())

    def test_same_objects_have_deterministic_inventory_independent_of_destination(self):
        value, _, base, head = fixture()
        first = self.build(value, base, head)
        self.destination = self.root / "second"
        value.objects = dict(reversed(list(value.objects.items())))
        second = self.build(value, base, head)
        self.assertEqual(first.inventory_bytes, second.inventory_bytes)

    def test_reverification_rejects_changed_head_config_inventory_objects_and_files(self):
        value, _, base, head = fixture()
        snapshot = self.build(value, base, head)
        object_path = snapshot.root / ".git/objects" / head[:2] / head[2:]
        paths = [snapshot.root / "README.txt", snapshot.root / ".git/HEAD", snapshot.root / ".git/config",
                 snapshot.root / ".git/delivery-review-snapshot.json", object_path]
        for path in paths:
            original = path.read_bytes()
            path.write_bytes(original + b"tampered")
            with self.subTest(path=path.name), self.assertRaises(SnapshotError):
                verify_snapshot(snapshot)
            path.write_bytes(original)
        verify_snapshot(snapshot)

    def test_reverification_refuses_extra_files_dirs_refs_hooks_and_hardlinks(self):
        value, _, base, head = fixture()
        snapshot = self.build(value, base, head)
        for name in ("extra.txt", ".git/index", ".git/refs/replacement", ".git/disabled-hooks/pre-commit",
                     ".git/objects/info/alternates"):
            path = snapshot.root / name
            made_parent = not path.parent.exists()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"unexpected")
            with self.subTest(name=name), self.assertRaises(SnapshotError):
                verify_snapshot(snapshot)
            path.unlink()
            if made_parent:
                path.parent.rmdir()
        extra = snapshot.root / "empty-extra"
        extra.mkdir()
        with self.assertRaises(SnapshotError):
            verify_snapshot(snapshot)
        extra.rmdir()
        outside = self.root / "hardlink"
        os.link(snapshot.root / "README.txt", outside)
        with self.assertRaises(SnapshotError):
            verify_snapshot(snapshot)
        outside.unlink()
        verify_snapshot(snapshot)

    def test_forged_snapshot_identity_and_rewritten_inventory_are_rederived(self):
        value, common, base, head = fixture()
        snapshot = self.build(value, base, head)
        for forged in (replace(snapshot, head_sha=base), replace(snapshot, base_tip_sha=head),
                       replace(snapshot, merge_base_sha=head), replace(snapshot, repository="other/repo")):
            with self.subTest(forged=forged.head_sha), self.assertRaises(SnapshotError):
                verify_snapshot(forged)
        document = snapshot.inventory
        document["merge_base_sha"] = head
        raw = canonical_bytes(document) + b"\n"
        (snapshot.root / ".git/delivery-review-snapshot.json").write_bytes(raw)
        with self.assertRaisesRegex(SnapshotError, "merge_base"):
            verify_snapshot(replace(snapshot, merge_base_sha=head, inventory_bytes=raw))
        (snapshot.root / ".git/delivery-review-snapshot.json").write_bytes(snapshot.inventory_bytes)
        self.assertNotEqual(common, head)
        verify_snapshot(snapshot)

    def test_loose_object_bombs_and_appended_streams_fail_before_graph_parsing(self):
        value, _, base, head = fixture()
        snapshot = self.build(value, base, head)
        for oversized in (False, True):
            document = snapshot.inventory
            item = document["objects"][0]
            oid = item["oid"]
            path = snapshot.root / ".git/objects" / oid[:2] / oid[2:]
            original = path.read_bytes()
            changed = zlib.compress(b"X" * (SnapshotLimits().max_object_bytes + 1)) if oversized else original + b"extra stream"
            path.write_bytes(changed)
            item.update(loose_size_bytes=len(changed), loose_sha256=hashlib.sha256(changed).hexdigest())
            raw = canonical_bytes(document) + b"\n"
            (snapshot.root / ".git/delivery-review-snapshot.json").write_bytes(raw)
            with self.subTest(oversized=oversized), self.assertRaisesRegex(SnapshotError, "loose_object"):
                verify_snapshot(replace(snapshot, inventory_bytes=raw))
            path.write_bytes(original)
            (snapshot.root / ".git/delivery-review-snapshot.json").write_bytes(snapshot.inventory_bytes)


if __name__ == "__main__":
    unittest.main()
