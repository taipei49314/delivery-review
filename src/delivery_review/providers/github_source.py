"""Fixed-SHA GitHub HTTPS acquisition into a private quarantine.

Only approved Git tooling runs. Reviewed files are never checked out or executed.
Numeric repository identity remains a caller-supplied API observation, not a
claim authenticated by a Git URL or a locally consistent object graph.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
import base64
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import time
from types import SimpleNamespace

# Isolated workers import only this installed first-party package, never source
# files from the reviewed repository or an inherited PYTHONPATH.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from delivery_review.contracts import ContractError, MAX_JSON_BYTES, canonical_bytes, loads_strict
from delivery_review.engine_runner import _execute
from delivery_review.evidence.store import _read
from delivery_review.providers.github_http import _contains_token, _valid_token
from delivery_review.source_snapshot import (SnapshotError, SnapshotLimits, SourceSnapshot, _commit,
                               _destination, _git_environment, _plain_directory, _sha, _tree,
                               _write, materialize_snapshot)


class SourceAcquisitionError(ContractError):
    """Fixed, non-secret acquisition failure; no partial snapshot is successful."""


@dataclass(frozen=True)
class SourceFetchLimits:
    timeout_seconds: int = 120
    max_disk_bytes: int = 128 * 1024 * 1024
    max_objects: int = 20000
    max_object_bytes: int = 8 * 1024 * 1024
    max_total_object_bytes: int = 128 * 1024 * 1024
    max_quarantine_files: int = 1024
    max_process_output_bytes: int = 1024 * 1024


@dataclass(frozen=True)
class GitHubSourceAcquisition:
    directory: Path
    snapshot: SourceSnapshot
    evidence_bytes: bytes

    @property
    def evidence(self):
        return loads_strict(self.evidence_bytes)


def _need(condition, reason):
    if not condition:
        raise SourceAcquisitionError(reason)


def _digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _native_parent(path):
    """Refuse network/compatibility mounts for Git's mutable quarantine."""
    if os.name == "nt":
        import ctypes
        function = ctypes.windll.kernel32.GetDriveTypeW
        function.argtypes, function.restype = [ctypes.c_wchar_p], ctypes.c_uint
        kind = function(str(path.anchor))
        _need(kind in {3, 6}, "source_native_filesystem_required")
    elif Path("/proc/self/mountinfo").is_file():
        candidates = []
        for row in Path("/proc/self/mountinfo").read_text().splitlines():
            left, separator, right = row.partition(" - ")
            if not separator:
                continue
            mount = left.split()[4]
            mount = re.sub(r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), mount)
            directory = Path(mount)
            if path == directory or directory in path.parents:
                candidates.append((len(directory.parts), right.split()[0]))
        _need(candidates and max(candidates)[1] in {"ext2", "ext3", "ext4", "xfs", "btrfs", "tmpfs", "overlay", "zfs"},
              "source_native_filesystem_required")
    else:
        raise SourceAcquisitionError("source_platform_unsupported")


def _environment(git, home, temporary):
    environment = _git_environment(git)
    environment.update(HOME=str(home), USERPROFILE=str(home), XDG_CONFIG_HOME=str(home),
                       TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
                       GIT_ALLOW_PROTOCOL="https", GIT_PROTOCOL_FROM_USER="0")
    return environment


def _argv(git, quarantine, arguments):
    # No caller-controlled ref, URL, config name, credential or executable tail.
    return [str(git), "--no-pager", "--no-replace-objects", "--no-optional-locks",
            "--git-dir=" + str(quarantine), "-c", "credential.helper=", "-c", "core.fsmonitor=false", *arguments]


def _repository_valid(repository):
    return (type(repository) is dict and set(repository) == {"id", "full_name"}
            and type(repository["id"]) is int and 0 < repository["id"] <= 2**53 - 1
            and type(repository["full_name"]) is str
            and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository["full_name"]) is not None
            and all(part not in {".", ".."} for part in repository["full_name"].split("/")))


def _fetch_arguments(repository, head, base):
    return ["fetch", "--quiet", "--no-progress", "--no-tags", "--no-recurse-submodules",
            "--no-write-fetch-head", "--no-auto-gc", "https://github.com/" + repository["full_name"] + ".git",
            "+" + head + ":refs/delivery-review/head", "+" + base + ":refs/delivery-review/base"]


def _credential_forms(token):
    if token is None:
        return ()
    return (token, base64.b64encode(("x-access-token:" + token).encode("ascii")).decode("ascii"))


_MAX_WORKER_INPUT = 16384


def _exec_fetch(git, arguments, environment):
    """Keep the parent's tree; Windows CRT exec does not quote spaced argv."""
    if os.name == "nt":
        # Popen quotes Windows argv correctly and waits for the real Git exit.
        # No new process group or breakaway flag: the child inherits the outer
        # controller's Job Object and bounded stdout/stderr handles.
        return subprocess.call(arguments, executable=str(git), env=environment,
            stdin=subprocess.DEVNULL, close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    descriptor = os.open(os.devnull, os.O_RDONLY)
    try:
        os.dup2(descriptor, 0)
    finally:
        os.close(descriptor)
    os.execve(str(git), arguments, environment)


def _fetch_worker(raw):
    """Run one fixed fetch in the parent's already constrained process tree."""
    _need(type(raw) is bytes and len(raw) <= _MAX_WORKER_INPUT, "invalid_source_worker_input")
    request = loads_strict(raw)
    _need(type(request) is dict and set(request) == {"git", "quarantine", "repository", "head", "base", "token"},
          "invalid_source_worker_input")
    _need(_repository_valid(request["repository"]) and _sha(request["head"]) and _sha(request["base"])
          and request["token"] is not None and _valid_token(request["token"]), "invalid_source_worker_input")
    git, quarantine = Path(request["git"]), Path(request["quarantine"])
    _need(git.is_absolute() and git.is_file() and quarantine.is_absolute()
          and quarantine == Path.cwd(), "invalid_source_worker_input")
    home = quarantine.parent / "home"
    _plain_directory(quarantine)
    _plain_directory(home)
    _need(_read(quarantine / "config", 4096) == _CONFIG and not list(home.iterdir()), "invalid_source_worker_input")
    environment = _environment(git, home, quarantine / "tmp")
    environment.update(GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="http.https://github.com/" + request["repository"]["full_name"] + ".git.extraHeader",
        GIT_CONFIG_VALUE_0="Authorization: Basic " + _credential_forms(request["token"])[1])
    arguments = _argv(git, quarantine, _fetch_arguments(request["repository"], request["head"], request["base"]))
    # The credential message has been consumed. Git must not read it as stdin.
    return _exec_fetch(git, arguments, environment)


def _fetch_worker_main():
    try:
        exit_code = _fetch_worker(sys.stdin.buffer.read(_MAX_WORKER_INPUT + 1))
        if type(exit_code) is int:
            return exit_code
    except Exception:
        # Never interpolate argv, JSON, token, headers or Git's environment.
        sys.stderr.buffer.write(b"source_fetch_worker_failed\n")
    return 70


_CONFIG = (b'[core]\n\trepositoryformatversion = 0\n\tbare = true\n\tfsmonitor = false\n\tcreateObject = rename\n'
           b'\thooksPath = disabled-hooks\n[credential]\n\thelper =\n[protocol]\n\tallow = never\n'
           b'[protocol "https"]\n\tallow = always\n[protocol "file"]\n\tallow = never\n'
           b'[http]\n\tfollowRedirects = false\n\tproxy =\n\tsslVerify = true\n\temptyAuth = false\n'
           b'\tcookieFile =\n\tsaveCookies = false\n\textraHeader =\n[fetch]\n\trecurseSubmodules = false\n'
           b'\tfsckObjects = true\n\tunpackLimit = 1\n[transfer]\n\tfsckObjects = true\n\tunpackLimit = 1\n'
           b'\tbundleURI = false\n[submodule]\n\trecurse = false\n[gc]\n\tauto = 0\n'
           b'[maintenance]\n\tauto = false\n[pack]\n\tthreads = 1\n[index]\n\tthreads = 1\n')


def _quarantine_scan(root, limits, *, stable=False):
    """Observe logical bytes and PACK counts during writes; never follow links."""
    pending, files, size, object_count, entry_count = [root], [], 0, 0, 0
    while pending:
        directory = pending.pop()
        _plain_directory(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                entry_count += 1
                _need(entry_count <= limits.max_quarantine_files, "source_quarantine_file_limit")
                path = directory / entry.name
                try:
                    info = path.lstat()
                    _need(not stat.S_ISLNK(info.st_mode) and not getattr(info, "st_file_attributes", 0) & 0x400,
                          "source_quarantine_link")
                    if stat.S_ISDIR(info.st_mode):
                        _need(len(pending) + len(files) < limits.max_quarantine_files, "source_quarantine_file_limit")
                        pending.append(path)
                        continue
                    _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "source_quarantine_link")
                    files.append((path.relative_to(root).as_posix(), info.st_size))
                    size += info.st_size
                    _need(len(files) <= limits.max_quarantine_files and size <= limits.max_disk_bytes,
                          "source_quarantine_disk_limit")
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                    with os.fdopen(os.open(path, flags), "rb") as stream:
                        opened = os.fstat(stream.fileno())
                        _need((opened.st_dev, opened.st_ino) == (info.st_dev, info.st_ino), "source_quarantine_changed")
                        header = stream.read(12)
                    if header.startswith(b"PACK") and len(header) == 12:
                        _, version, count = struct.unpack(">4sII", header)
                        _need(version in {2, 3}, "source_unsupported_pack")
                        object_count += count
                    elif re.fullmatch(r"objects/[0-9a-f]{2}/[0-9a-f]{38}", files[-1][0]):
                        object_count += 1
                    _need(object_count <= limits.max_objects, "source_object_count_limit")
                except FileNotFoundError:
                    # Git atomically renames its own temporary pack/index files.
                    # A stable final scan and object enumeration remain required.
                    if stable:
                        raise SourceAcquisitionError("source_quarantine_changed")
    return sorted(files), size


def _metadata(raw, limits):
    rows, total = {}, 0
    for line in raw.splitlines():
        match = re.fullmatch(rb"([0-9a-f]{40}) (commit|tree|blob|tag) (0|[1-9][0-9]{0,9})", line)
        _need(match is not None, "source_object_metadata_invalid")
        oid, kind, size = match[1].decode(), match[2].decode(), int(match[3])
        _need(oid not in rows and len(rows) < limits.max_objects, "source_object_count_limit")
        full_size = len(kind) + len(str(size)) + 2 + size
        total += full_size
        _need(full_size <= limits.max_object_bytes and total <= limits.max_total_object_bytes, "source_object_size_limit")
        rows[oid] = (kind, size)
    _need(rows, "source_objects_missing")
    return dict(sorted(rows.items()))


def _canonical_objects(raw, rows):
    result, position = {}, 0
    for oid, (kind, size) in rows.items():
        header = f"{oid} {kind} {size}\n".encode()
        _need(raw[position:position + len(header)] == header, "source_object_stream_invalid")
        position += len(header)
        payload = raw[position:position + size]
        _need(len(payload) == size and raw[position + size:position + size + 1] == b"\n", "source_object_stream_invalid")
        position += size + 1
        canonical = f"{kind} {size}".encode() + b"\0" + payload
        _need(hashlib.sha1(canonical, usedforsecurity=False).hexdigest() == oid, "source_object_identity_mismatch")
        result[oid] = canonical
    _need(position == len(raw), "source_object_stream_invalid")
    return result


def _reachable(objects, head, base):
    pending, selected = [(head, "commit"), (base, "commit")], {}
    while pending:
        oid, expected = pending.pop()
        _need(oid in objects, "source_closure_incomplete")
        raw = objects[oid]
        header, _, payload = raw.partition(b"\0")
        kind = header.split(b" ", 1)[0].decode()
        _need(kind == expected, "source_object_type_mismatch")
        if oid in selected:
            continue
        selected[oid] = raw
        if kind == "commit":
            tree, parents = _commit(payload)
            pending.append((tree, "tree"))
            pending.extend((parent, "commit") for parent in parents)
        elif kind == "tree":
            pending.extend((child, "tree" if mode == "40000" else "blob") for mode, _, child in _tree(payload))
    return selected


def acquire_public_source(destination, *, repository: dict, head_sha: str, base_tip_sha: str,
                          git_executable: str | Path, limits: SourceFetchLimits = SourceFetchLimits(),
                          token=None) -> GitHubSourceAcquisition:
    """Compatibility entry point that always refuses credentials before IO."""
    _need(token is None, "authenticated_source_unsupported")
    return acquire_source(destination, repository=repository, head_sha=head_sha, base_tip_sha=base_tip_sha,
                          git_executable=git_executable, limits=limits)


def acquire_source(destination, *, repository: dict, head_sha: str, base_tip_sha: str,
                   git_executable: str | Path, limits: SourceFetchLimits = SourceFetchLimits(),
                   token=None) -> GitHubSourceAcquisition:
    """Acquire exact objects; optional credentials reach only the fetch worker.

    API subject validation belongs to the caller. Failures leave diagnostics but
    never a successful receipt. No reviewed code or checkout filter is executed.
    """
    root, created, steps = None, False, []
    try:
        _need(_valid_token(token), "invalid_source_token")
        _need(type(limits) is SourceFetchLimits, "invalid_source_fetch_limits")
        hard = SourceFetchLimits()
        _need(all(type(getattr(limits, field.name)) is int and 0 < getattr(limits, field.name) <= getattr(hard, field.name)
                  for field in fields(limits)), "invalid_source_fetch_limits")
        _need(_repository_valid(repository), "invalid_source_repository")
        _need(_sha(head_sha) and _sha(base_tip_sha), "invalid_source_sha")
        repository = dict(repository)
        git = Path(git_executable)
        _need(git.is_absolute() and git.is_file(), "invalid_git_executable")
        root = _destination(destination)
        _native_parent(root.parent)
        deadline, started = time.monotonic() + limits.timeout_seconds, _now()
        root.mkdir(mode=0o700)
        created = True
        quarantine, home, raw_directory = root / "quarantine", root / "home", root / "raw"
        for path in (quarantine, home, raw_directory):
            path.mkdir(mode=0o700)
        for name in ("objects", "refs", "disabled-hooks", "tmp"):
            (quarantine / name).mkdir(mode=0o700)
        _write(quarantine / "HEAD", b"ref: refs/heads/unused\n")
        _write(quarantine / "config", _CONFIG)
        environment = _environment(git, home, quarantine / "tmp")
        def monitor():
            _quarantine_scan(quarantine, limits)
        def run(role, arguments, *, input_bytes=None, stdout_limit=None, retain=True):
            stdout_limit = limits.max_process_output_bytes if stdout_limit is None else stdout_limit
            command = _argv(git, quarantine, arguments)
            if role == "fetch" and token is not None:
                command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--fetch-worker"]
                input_bytes = canonical_bytes({"git": str(git), "quarantine": str(quarantine), "repository": repository,
                                               "head": head_sha, "base": base_tip_sha, "token": token})
                _need(len(input_bytes) <= _MAX_WORKER_INPUT, "invalid_source_worker_input")
            process, streams = _execute(command, cwd=quarantine, env=environment,
                deadline=deadline, limits=SimpleNamespace(max_stdout_bytes=stdout_limit,
                max_stderr_bytes=limits.max_process_output_bytes), stdin_bytes=input_bytes, monitor=monitor)
            sensitive = any(_contains_token(secret, body=raw, incomplete=process["termination"] != "exited")
                            for secret in _credential_forms(token) for raw in streams.values())
            if sensitive:
                # Refuse both streams before any original echo or its digest is
                # persisted. Native termination/exit remain diagnostic facts.
                streams = {"stdout": b"", "stderr": b"sensitive_source_response_refused\n"}
            entries = {}
            for name, raw in streams.items():
                entry = {"size_bytes": len(raw), "sha256": _digest(raw)}
                if retain or name == "stderr":
                    entry["path"] = f"raw/{role}.{name}"
                    _write(root / entry["path"], raw)
                entries[name] = entry
            step = {"role": role, "process": process, "streams": entries}
            if sensitive:
                step["stream_retention"] = "sanitized_sensitive_output"
            steps.append(step)
            _need(not sensitive, "sensitive_source_response_refused")
            _need(process["termination"] == "exited" and process["exit_code"] == 0, "source_git_process_failed")
            return streams["stdout"]
        git_version = run("git-version", ["--version"]).decode("ascii").strip()
        _need(re.fullmatch(r"git version [A-Za-z0-9_.()+ -]{1,128}", git_version), "source_git_version_invalid")
        url = "https://github.com/" + repository["full_name"] + ".git"
        run("fetch", _fetch_arguments(repository, head_sha, base_tip_sha))
        _need(not (quarantine / "shallow").exists(), "source_shallow_history_unsupported")
        rows = _metadata(run("object-metadata", ["cat-file", "--batch-all-objects", "--batch-check"],
                             stdout_limit=limits.max_objects * 80), limits)
        output_limit = sum(len(f"{oid} {kind} {size}\n".encode()) + size + 1 for oid, (kind, size) in rows.items())
        raw = run("object-bytes", ["cat-file", "--batch"], input_bytes=b"".join(oid.encode() + b"\n" for oid in rows),
                  stdout_limit=output_limit, retain=False)
        objects = _canonical_objects(raw, rows)
        selected = _reachable(objects, head_sha, base_tip_sha)
        remaining = int(deadline - time.monotonic())
        _need(remaining >= 1, "source_deadline_exceeded")
        snapshot_limits = replace(SnapshotLimits(), timeout_seconds=min(30, remaining), max_objects=limits.max_objects,
            max_object_bytes=limits.max_object_bytes, max_total_object_bytes=limits.max_total_object_bytes,
            max_object_disk_bytes=limits.max_disk_bytes)
        snapshot = materialize_snapshot(root / "snapshot", repository=repository["full_name"], head_sha=head_sha,
            base_tip_sha=base_tip_sha, objects=selected, git_executable=git, limits=snapshot_limits)
        _need(time.monotonic() < deadline, "source_deadline_exceeded")
        files, disk = _quarantine_scan(quarantine, limits, stable=True)
        quarantine_inventory = []
        for path, size in files:
            _need(time.monotonic() < deadline, "source_deadline_exceeded")
            content = _read(quarantine / path, limits.max_disk_bytes)
            _need(len(content) == size, "source_quarantine_changed")
            quarantine_inventory.append({"path": "quarantine/" + path, "size_bytes": size, "sha256": _digest(content)})
        evidence = {"kind": "delivery-review-github-source-acquisition", "acquisition_version": 1,
                    "mode": "anonymous_public" if token is None else "authenticated_https",
                    "api_identity": "supplied_not_reverified", "repository": dict(repository),
                    "requested_url": url, "head_sha": head_sha, "base_tip_sha": base_tip_sha,
                    "merge_base_sha": snapshot.merge_base_sha, "started_at": started, "completed_at": _now(),
                    "git_version": git_version, "processes": steps, "quarantine_bytes": disk,
                    "quarantine_files": quarantine_inventory, "captured_object_count": len(objects),
                    "reachable_object_count": len(selected), "excluded_object_count": len(objects) - len(selected),
                    "snapshot_inventory_sha256": _digest(snapshot.inventory_bytes)}
        evidence_bytes = canonical_bytes(evidence) + b"\n"
        _need(len(evidence_bytes) <= MAX_JSON_BYTES, "source_evidence_size_limit")
        _write(root / "acquisition.json", evidence_bytes)
        return GitHubSourceAcquisition(root, snapshot, evidence_bytes)
    except (ContractError, OSError, ValueError, TypeError, UnicodeError) as exc:
        reason = str(exc) if isinstance(exc, (SourceAcquisitionError, SnapshotError)) else "source_acquisition_failed"
        if created:
            try:
                raw = canonical_bytes({"kind": "delivery-review-github-source-acquisition-error", "version": 1,
                    "reason": reason, "processes": steps}) + b"\n"
                if len(raw) <= MAX_JSON_BYTES:
                    _write(root / "acquisition-error.json", raw)
            except (ContractError, OSError, ValueError):
                pass
        raise SourceAcquisitionError(reason) from exc


if __name__ == "__main__":
    if sys.argv[1:] != ["--fetch-worker"]:
        raise SystemExit(2)
    raise SystemExit(_fetch_worker_main())
