"""Bounded local Git object snapshots for static readers, never PR execution.

The caller supplies exact uncompressed Git objects, not a repository directory.
Network acquisition and producer/checkout authentication are separate concerns.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import hashlib
import os
from pathlib import Path
import re
import stat
import subprocess
import time
import unicodedata
import zlib

from .contracts import ContractError, MAX_JSON_BYTES, canonical_bytes, loads_strict
from .evidence.store import _read


class SnapshotError(ContractError):
    """A source could not be safely and completely materialized."""


@dataclass(frozen=True)
class SnapshotLimits:
    max_objects: int = 20000
    max_object_bytes: int = 8 * 1024 * 1024
    max_total_object_bytes: int = 128 * 1024 * 1024
    max_object_disk_bytes: int = 128 * 1024 * 1024
    max_files: int = 10000
    max_materialized_bytes: int = 128 * 1024 * 1024
    max_tree_entries: int = 50000
    max_depth: int = 32
    max_path_bytes: int = 240
    max_inventory_bytes: int = MAX_JSON_BYTES
    timeout_seconds: int = 30


@dataclass(frozen=True)
class SourceSnapshot:
    root: Path
    repository: str
    head_sha: str
    base_tip_sha: str
    merge_base_sha: str
    inventory_bytes: bytes

    @property
    def inventory(self) -> dict:
        return loads_strict(self.inventory_bytes)


_SHA = re.compile(r"[0-9a-f]{40}")
_RESERVED = re.compile(r"CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]", re.IGNORECASE)
_CONFIG = (b'[core]\n\trepositoryformatversion = 0\n\tbare = false\n\tfilemode = false\n'
           b'\tautocrlf = false\n\tfsmonitor = false\n\thooksPath = .git/disabled-hooks\n'
           b'[credential]\n\thelper =\n[protocol]\n\tallow = never\n[protocol "file"]\n\tallow = never\n')


def _need(condition, code):
    if not condition:
        raise SnapshotError(code)


def _sha(value):
    return type(value) is str and _SHA.fullmatch(value) is not None


def _sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def _check_deadline(deadline):
    _need(time.monotonic() < deadline, "snapshot_deadline_exceeded")


def _name(raw):
    try:
        name = raw.decode("utf-8", "strict")
    except UnicodeError as exc:
        raise SnapshotError("unsupported_path_encoding") from exc
    _need(name and name not in {".", ".."} and unicodedata.normalize("NFC", name) == name,
          "unsafe_tree_path")
    _need(not any(unicodedata.category(char) in {"Cc", "Cf"}
                  or char in '<>:"/\\|?*~' for char in name), "unsafe_tree_path")
    _need(not name.endswith((".", " ")) and name.casefold() != ".git"
          and not _RESERVED.fullmatch(name.split(".", 1)[0].rstrip(" ")),
          "unsafe_tree_path")
    return name


def _commit(payload):
    header, separator, _ = payload.partition(b"\n\n")
    _need(separator and b"\0" not in header, "invalid_commit")
    lines = header.split(b"\n")
    _need(re.fullmatch(b"tree [0-9a-f]{40}", lines[0]) is not None, "invalid_commit_tree")
    tree = lines[0][5:].decode("ascii")
    parents, ended = [], False
    for line in lines[1:]:
        if line.startswith(b"parent "):
            _need(not ended and re.fullmatch(b"parent [0-9a-f]{40}", line) is not None, "invalid_commit_parent")
            parents.append(line[7:].decode("ascii"))
        else:
            _need(not line.startswith(b"tree "), "invalid_commit_tree")
            ended = True
    _need(len(parents) == len(set(parents)), "duplicate_commit_parent")
    return tree, parents


def _tree(payload):
    entries, aliases, previous, position = [], set(), None, 0
    while position < len(payload):
        space, nul = payload.find(b" ", position), payload.find(b"\0", position)
        _need(position < space < nul and nul + 21 <= len(payload), "invalid_tree")
        mode, raw_name = payload[position:space], payload[space + 1:nul]
        _need(mode in {b"40000", b"100644", b"100755"}, "unsupported_tree_mode")
        name = _name(raw_name)
        key = raw_name + (b"/" if mode == b"40000" else b"")
        _need(previous is None or previous < key, "noncanonical_tree_order")
        _need(name.casefold() not in aliases, "tree_path_alias")
        aliases.add(name.casefold())
        previous = key
        entries.append((mode.decode("ascii"), name, payload[nul + 1:nul + 21].hex()))
        position = nul + 21
    return entries


def _objects(objects, limits, deadline):
    _need(isinstance(objects, Mapping) and 0 < len(objects) <= limits.max_objects, "object_count_limit")
    parsed, inventory, total, disk, entry_count = {}, [], 0, 0, 0
    for oid, raw in sorted(objects.items()):
        _check_deadline(deadline)
        _need(_sha(oid) and type(raw) is bytes, "invalid_object_input")
        _need(len(raw) <= limits.max_object_bytes, "object_size_limit")
        total += len(raw)
        _need(total <= limits.max_total_object_bytes, "total_object_size_limit")
        header, separator, payload = raw.partition(b"\0")
        match = re.fullmatch(b"(blob|tree|commit) (0|[1-9][0-9]{0,9})", header)
        _need(separator and match is not None and int(match[2]) == len(payload), "invalid_object_header")
        _need(hashlib.sha1(raw, usedforsecurity=False).hexdigest() == oid, "object_identity_mismatch")
        kind = match[1].decode("ascii")
        relation = _commit(payload) if kind == "commit" else _tree(payload) if kind == "tree" else None
        if kind == "tree":
            entry_count += len(relation)
            _need(entry_count <= limits.max_tree_entries, "tree_entry_limit")
        compressed = zlib.compress(raw)
        compressed_size = len(compressed)
        disk += compressed_size
        _need(disk <= limits.max_object_disk_bytes, "object_disk_limit")
        parsed[oid] = (kind, payload, relation)
        inventory.append({"oid": oid, "type": kind, "size_bytes": len(raw), "sha256": _sha256(raw),
                          "loose_size_bytes": compressed_size, "loose_sha256": _sha256(compressed)})
    return parsed, inventory, total, disk


def _closure(parsed, head, base, deadline):
    pending, reached = [(head, "commit"), (base, "commit")], set()
    while pending:
        _check_deadline(deadline)
        oid, expected = pending.pop()
        _need(oid in parsed, "object_closure_incomplete")
        kind, _, relation = parsed[oid]
        _need(kind == expected, "object_type_mismatch")
        if oid in reached:
            continue
        reached.add(oid)
        if kind == "commit":
            tree, parents = relation
            pending.append((tree, "tree"))
            pending.extend((parent, "commit") for parent in parents)
        elif kind == "tree":
            pending.extend((child, "tree" if mode == "40000" else "blob") for mode, _, child in relation)
    _need(reached == set(parsed), "unreachable_objects")


def _files(parsed, tree, limits, deadline):
    pending, files, total, expanded = [(tree, (), frozenset())], [], 0, 0
    while pending:
        _check_deadline(deadline)
        oid, prefix, ancestors = pending.pop()
        _need(oid not in ancestors, "tree_cycle")
        for mode, name, child in parsed[oid][2]:
            expanded += 1
            _need(expanded <= limits.max_tree_entries, "expanded_tree_entry_limit")
            components = prefix + (name,)
            path = "/".join(components)
            _need(len(components) <= limits.max_depth and len(path.encode("utf-8")) <= limits.max_path_bytes,
                  "tree_path_limit")
            if mode == "40000":
                pending.append((child, components, ancestors | {oid}))
            else:
                payload = parsed[child][1]
                total += len(payload)
                _need(len(files) < limits.max_files and total <= limits.max_materialized_bytes, "materialized_size_limit")
                files.append({"path": path, "mode": mode, "oid": child, "size_bytes": len(payload), "sha256": _sha256(payload)})
    return sorted(files, key=lambda item: item["path"]), total


def _merge_base(parsed, head, base, deadline):
    def ancestors(start):
        found, pending = set(), [start]
        while pending:
            _check_deadline(deadline)
            oid = pending.pop()
            if oid not in found:
                found.add(oid)
                pending.extend(parsed[oid][2][1])
        return found
    common = ancestors(head) & ancestors(base)
    superseded = {parent for oid in common for parent in parsed[oid][2][1]}
    best = common - superseded
    _need(len(best) == 1, "ambiguous_or_missing_merge_base")
    return next(iter(best))


def _plain_directory(path):
    info = path.lstat()
    _need(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
          and not getattr(info, "st_file_attributes", 0) & 0x400, "unsafe_destination_parent")


def _destination(value):
    path = Path(os.path.abspath(value))
    _need(not os.path.lexists(path), "destination_exists")
    for ancestor in (path.parent, *path.parent.parents):
        _plain_directory(ancestor)
    return path


def _write(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)
    path.chmod(0o600)


def _git_environment(executable):
    # Deliberate allowlist: no inherited GIT_*, proxy, credential, tracing,
    # Python, loader or shell injection settings reach this static Git process.
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR") if key in os.environ}
    environment.update(PATH=str(executable.parent), LC_ALL="C", LANG="C", GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, GIT_CONFIG_COUNT="0", GIT_ATTR_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0", GIT_NO_REPLACE_OBJECTS="1", GIT_NO_LAZY_FETCH="1", GIT_OPTIONAL_LOCKS="0",
        GIT_ALLOW_PROTOCOL="", GIT_PROTOCOL_FROM_USER="0")
    return environment


def _git(executable, root, arguments, limits, deadline):
    _check_deadline(deadline)
    # Git 2.43 lacks the --no-lazy-fetch CLI spelling. The environment flag,
    # complete local closure, no remotes/promisors and denied protocols suffice.
    argv = [str(executable), "--no-pager", "--no-replace-objects", "--no-optional-locks",
            "--git-dir=" + str(root / ".git"), "--work-tree=" + str(root),
            "-c", "credential.helper=", "-c", "protocol.allow=never", "-c", "protocol.file.allow=never",
            "-c", "core.fsmonitor=false", *arguments]
    try:
        completed = subprocess.run(argv, cwd=root, env=_git_environment(executable), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                   timeout=max(0.001, deadline - time.monotonic()))
    except subprocess.TimeoutExpired as exc:
        raise SnapshotError("git_deadline_exceeded") from exc
    # Fixed rev-parse/merge-base output is bounded by the bounded commit count;
    # no command prints arbitrary blob, commit-message or diff contents.
    _need(completed.returncode == 0, "git_object_verification_failed")
    _need(len(completed.stdout) <= 41 * limits.max_objects and len(completed.stderr) <= 65536, "git_output_limit")
    return completed.stdout


def materialize_snapshot(destination, *, repository: str, head_sha: str, base_tip_sha: str,
                         objects: Mapping[str, bytes], git_executable: str | Path,
                         limits: SnapshotLimits = SnapshotLimits()) -> SourceSnapshot:
    """Build a new private snapshot from the exact complete reachable object set.

    This does not fetch or attest GitHub repository membership. The caller owns
    the parent directory and approved Git executable. On failure a partial new
    directory may remain without the final inventory; it must not be scanned.
    """
    try:
        _need(type(limits) is SnapshotLimits, "invalid_snapshot_limits")
        ceiling = SnapshotLimits()
        _need(all(type(getattr(limits, field.name)) is int and 0 < getattr(limits, field.name) <= getattr(ceiling, field.name)
                  for field in fields(limits)), "invalid_snapshot_limits")
        deadline = time.monotonic() + limits.timeout_seconds
        _need(type(repository) is str and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
              and all(part not in {".", ".."} for part in repository.split("/")), "invalid_repository")
        _need(_sha(head_sha) and _sha(base_tip_sha), "invalid_source_sha")
        executable = Path(git_executable)
        _need(executable.is_absolute() and executable.is_file(), "invalid_git_executable")
        root = _destination(destination)
        _need(isinstance(objects, Mapping) and 0 < len(objects) <= limits.max_objects, "object_count_limit")
        objects = dict(objects)
        parsed, object_inventory, total, disk = _objects(objects, limits, deadline)
        _closure(parsed, head_sha, base_tip_sha, deadline)
        files, materialized = _files(parsed, parsed[head_sha][2][0], limits, deadline)
        # Validate paths/expansion in every historical tree, including BASE policy
        # trees an engine may read. Their bytes remain objects, not a checkout.
        for tree in sorted({value[2][0] for value in parsed.values() if value[0] == "commit"}):
            _files(parsed, tree, limits, deadline)
        inventory = {"kind": "delivery-review-source-snapshot", "snapshot_version": 1,
                     "source_mode": "pr_head", "acquisition": "operator_supplied_objects",
                     "repository": repository, "repository_membership": "unconfirmed",
                     "head_sha": head_sha, "base_tip_sha": base_tip_sha, "merge_base_sha": None,
                     "object_bytes": total, "object_disk_bytes": disk, "materialized_bytes": materialized,
                     "objects": object_inventory, "files": files}
        _need(len(canonical_bytes(inventory)) + 42 <= limits.max_inventory_bytes, "inventory_size_limit")
        root.mkdir(mode=0o700)
        git_dir = root / ".git"
        git_dir.mkdir(mode=0o700)
        for name in ("objects", "refs", "disabled-hooks"):
            (git_dir / name).mkdir(mode=0o700)
        _write(git_dir / "HEAD", (head_sha + "\n").encode("ascii"))
        _write(git_dir / "config", _CONFIG)
        for oid, raw in sorted(objects.items()):
            _check_deadline(deadline)
            directory = git_dir / "objects" / oid[:2]
            directory.mkdir(mode=0o700, exist_ok=True)
            _write(directory / oid[2:], zlib.compress(raw))
        for oid in (head_sha, base_tip_sha):
            _need(_git(executable, root, ["rev-parse", "--verify", oid + "^{commit}"], limits, deadline) == (oid + "\n").encode(),
                  "git_commit_identity_mismatch")
        bases = _git(executable, root, ["merge-base", "--all", base_tip_sha, head_sha], limits, deadline).splitlines()
        _need(len(bases) == 1 and _sha(bases[0].decode("ascii")), "ambiguous_merge_base")
        merge_base = bases[0].decode("ascii")
        _need(merge_base in parsed and parsed[merge_base][0] == "commit", "invalid_merge_base")
        _need(merge_base == _merge_base(parsed, head_sha, base_tip_sha, deadline), "git_merge_base_mismatch")
        inventory["merge_base_sha"] = merge_base
        for item in files:
            _check_deadline(deadline)
            target = root.joinpath(*item["path"].split("/"))
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write(target, parsed[item["oid"]][1])
        inventory_bytes = canonical_bytes(inventory) + b"\n"
        _need(len(inventory_bytes) <= limits.max_inventory_bytes, "inventory_size_limit")
        _check_deadline(deadline)
        _write(git_dir / "delivery-review-snapshot.json", inventory_bytes)
        return SourceSnapshot(root, repository, head_sha, base_tip_sha, merge_base, inventory_bytes)
    except SnapshotError:
        raise
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        # Do not expose PR paths, object contents or subprocess stderr in errors.
        raise SnapshotError("source_snapshot_failed") from exc


def _scan_snapshot(root, files, deadline):
    directories = {".git", ".git/objects", ".git/refs", ".git/disabled-hooks"}
    directories |= {"/".join(path.split("/")[:index]) for path in files for index in range(1, len(path.split("/")))}
    pending, seen_files, seen_directories = [root], set(), set()
    while pending:
        _check_deadline(deadline)
        directory = pending.pop()
        _plain_directory(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                path = directory / entry.name
                name = path.relative_to(root).as_posix()
                info = path.lstat()
                _need(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                      "snapshot_link_refused")
                if stat.S_ISDIR(info.st_mode):
                    _need(name in directories, "unexpected_snapshot_directory")
                    seen_directories.add(name)
                    pending.append(path)
                else:
                    _need(name in files and stat.S_ISREG(info.st_mode) and info.st_nlink == 1,
                          "unexpected_or_unsafe_snapshot_file")
                    if os.name != "nt":
                        _need(not info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH), "snapshot_executable_file")
                    seen_files.add(name)
    _need(seen_files == files and seen_directories == directories, "snapshot_inventory_mismatch")


def verify_snapshot(snapshot: SourceSnapshot) -> None:
    """Recheck the complete private filesystem before/after static scanning.

    A dataclass or an inventory is not authority. Re-derive every identity from
    bounded, no-follow reads, including the unique merge base from the graph.
    No Git command, clock refresh or network acquisition is performed.
    """
    try:
        limits = SnapshotLimits()
        deadline = time.monotonic() + limits.timeout_seconds
        _need(type(snapshot) is SourceSnapshot and isinstance(snapshot.root, Path) and snapshot.root.is_absolute(),
              "invalid_snapshot")
        _need(type(snapshot.inventory_bytes) is bytes and len(snapshot.inventory_bytes) <= limits.max_inventory_bytes,
              "inventory_size_limit")
        document = loads_strict(snapshot.inventory_bytes)
        _need(type(document) is dict and type(document.get("objects")) is list
              and 0 < len(document["objects"]) <= limits.max_objects, "invalid_snapshot_inventory")
        _need(_sha(snapshot.head_sha) and _sha(snapshot.base_tip_sha) and _sha(snapshot.merge_base_sha), "invalid_source_sha")
        _need(type(snapshot.repository) is str and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", snapshot.repository)
              and all(part not in {".", ".."} for part in snapshot.repository.split("/")), "invalid_repository")
        root = snapshot.root
        for ancestor in (root, *root.parents):
            _plain_directory(ancestor)
        _need(_read(root / ".git/delivery-review-snapshot.json", limits.max_inventory_bytes) == snapshot.inventory_bytes,
              "snapshot_inventory_changed")
        _need(_read(root / ".git/HEAD", 41) == (snapshot.head_sha + "\n").encode(), "snapshot_head_changed")
        _need(_read(root / ".git/config", len(_CONFIG)) == _CONFIG, "snapshot_config_changed")
        objects, loose, disk, expanded = {}, {}, 0, 0
        for item in document["objects"]:
            _check_deadline(deadline)
            _need(type(item) is dict and _sha(item.get("oid")) and item["oid"] not in objects
                  and type(item.get("loose_size_bytes")) is int and 0 < item["loose_size_bytes"] <= limits.max_object_disk_bytes
                  and type(item.get("loose_sha256")) is str and re.fullmatch(r"[0-9a-f]{64}", item["loose_sha256"]),
                  "invalid_snapshot_inventory")
            oid = item["oid"]
            disk += item["loose_size_bytes"]
            _need(disk <= limits.max_object_disk_bytes, "object_disk_limit")
            raw = _read(root / ".git/objects" / oid[:2] / oid[2:], item["loose_size_bytes"],
                        {"size_bytes": item["loose_size_bytes"], "sha256": item["loose_sha256"]})
            decoder = zlib.decompressobj()
            content = decoder.decompress(raw, limits.max_object_bytes + 1)
            _need(len(content) <= limits.max_object_bytes and decoder.eof and not decoder.unused_data
                  and not decoder.unconsumed_tail, "invalid_or_oversized_loose_object")
            expanded += len(content)
            _need(expanded <= limits.max_total_object_bytes, "total_object_size_limit")
            objects[oid] = content
            loose[oid] = (len(raw), _sha256(raw))
        parsed, object_inventory, total, _ = _objects(objects, limits, deadline)
        _closure(parsed, snapshot.head_sha, snapshot.base_tip_sha, deadline)
        _need(snapshot.merge_base_sha == _merge_base(parsed, snapshot.head_sha, snapshot.base_tip_sha, deadline),
              "snapshot_merge_base_mismatch")
        files, materialized = _files(parsed, parsed[snapshot.head_sha][2][0], limits, deadline)
        for tree in sorted({value[2][0] for value in parsed.values() if value[0] == "commit"}):
            _files(parsed, tree, limits, deadline)
        for item in object_inventory:
            item["loose_size_bytes"], item["loose_sha256"] = loose[item["oid"]]
        expected = {"kind": "delivery-review-source-snapshot", "snapshot_version": 1, "source_mode": "pr_head",
                    "acquisition": "operator_supplied_objects", "repository": snapshot.repository,
                    "repository_membership": "unconfirmed", "head_sha": snapshot.head_sha,
                    "base_tip_sha": snapshot.base_tip_sha, "merge_base_sha": snapshot.merge_base_sha,
                    "object_bytes": total, "object_disk_bytes": disk, "materialized_bytes": materialized,
                    "objects": object_inventory, "files": files}
        _need(canonical_bytes(expected) + b"\n" == snapshot.inventory_bytes, "snapshot_inventory_mismatch")
        names = {".git/HEAD", ".git/config", ".git/delivery-review-snapshot.json"}
        names |= {".git/objects/" + oid[:2] + "/" + oid[2:] for oid in objects}
        names |= {item["path"] for item in files}
        _scan_snapshot(root, names, deadline)
        for item in files:
            _check_deadline(deadline)
            _read(root.joinpath(*item["path"].split("/")), limits.max_object_bytes, item)
        _scan_snapshot(root, names, deadline)
    except SnapshotError:
        raise
    except (ContractError, OSError, ValueError, TypeError, KeyError, UnicodeError, zlib.error) as exc:
        raise SnapshotError("source_snapshot_verification_failed") from exc
