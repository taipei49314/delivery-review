"""Provision the reviewed public tool bytes without installing or executing them."""
from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile

from .adapters.github_attestation import GH_PINS, GH_VERSION, TRUSTED_ROOT_PINS
from .contracts import ContractError, canonical_bytes, loads_strict, require
from .evidence.store import _directories, _read, _write
from .resources import definition_bytes
from .toolchain_download import MAX_DOWNLOAD_BYTES, _url


_DIGEST = re.compile(r"[0-9a-f]{64}")
_PLATFORMS = {"linux-amd64": ("linux", "amd64"), "windows-amd64": ("win32", "amd64")}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _verified(raw, specification):
    require(type(raw) is bytes and len(raw) == specification["size_bytes"]
        and _sha(raw) == specification["sha256"], "tool_download_digest_mismatch")
    return raw


def load_lock():
    """Cross-check new acquisition metadata against existing engine/verifier pins."""
    value = loads_strict(definition_bytes("formats", "toolchain-lock.json"))
    require(type(value) is dict and set(value) == {"kind", "version", "engines", "gh", "trusted_root"}
        and value["kind"] == "delivery-review-toolchain-lock" and type(value["version"]) is int
        and value["version"] == 1, "invalid_toolchain_lock")
    frozen = loads_strict(definition_bytes("locks", "engine-candidates.json"))
    expected = {row["name"]: row["artifact"] for row in frozen["engines"]}
    require(value["engines"] == expected, "toolchain_engine_lock_mismatch")
    require(type(value["gh"]) is dict and set(value["gh"]) == set(_PLATFORMS), "invalid_toolchain_platforms")
    for name, entry in value["gh"].items():
        require(type(entry) is dict and set(entry) == {"filename", "url", "size_bytes", "sha256",
            "member", "binary_size_bytes", "binary_sha256"}, "invalid_toolchain_gh")
        suffix, member = (("windows_amd64.zip", "bin/gh.exe") if name == "windows-amd64" else
            ("linux_amd64.tar.gz", f"gh_{GH_VERSION}_linux_amd64/bin/gh"))
        filename = f"gh_{GH_VERSION}_{suffix}"
        require(entry["filename"] == filename and entry["url"] == f"https://github.com/cli/cli/releases/download/v{GH_VERSION}/{filename}"
            and entry["member"] == member and entry["binary_sha256"] == GH_PINS[_PLATFORMS[name]], "toolchain_gh_pin_mismatch")
        require(type(entry["binary_size_bytes"]) is int and 0 < entry["binary_size_bytes"] <= 128 * 1024 * 1024,
            "invalid_toolchain_binary_size")
    for entry in [*value["engines"].values(), *value["gh"].values()]:
        _url(entry["url"], origin=True)
        require(type(entry["size_bytes"]) is int and 0 < entry["size_bytes"] <= MAX_DOWNLOAD_BYTES
            and type(entry["sha256"]) is str and _DIGEST.fullmatch(entry["sha256"]), "invalid_toolchain_download")
    root = value["trusted_root"]
    require(type(root) is dict and set(root) == {"resource", "sha256", "size_bytes"}
        and root["resource"] == "github-trusted-root.json" and root["sha256"] in TRUSTED_ROOT_PINS
        and type(root["size_bytes"]) is int and 0 < root["size_bytes"] <= 1024 * 1024, "invalid_toolchain_root")
    return value


def _trusted_root(specification):
    export = loads_strict(definition_bytes("formats", specification["resource"]))
    require(type(export) is dict and set(export) == {"kind", "encoding", "origin", "bytes_base64"}
        and export["kind"] == "delivery-review-public-trusted-root-export"
        and export["encoding"] == "base64" and type(export["origin"]) is dict
        and type(export["bytes_base64"]) is str, "invalid_bundled_trusted_root")
    return _verified(base64.b64decode(export["bytes_base64"], validate=True), specification)


def _worker_environment():
    # No tokens, HOME, config, proxy credentials, PATH, or Python injection vars.
    allowed = {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ"}
    return {name: value for name, value in os.environ.items() if name.upper() in allowed}


def _download(specification, *, deadline):
    remaining = min(60, deadline - time.monotonic())
    require(remaining > 0, "tool_download_timeout")
    with tempfile.TemporaryDirectory(prefix="delivery-review-download-") as temporary:
        destination = Path(temporary) / "download"
        payload = json.dumps({"url": specification["url"], "destination": str(destination),
            "size_bytes": specification["size_bytes"], "sha256": specification["sha256"],
            "timeout_seconds": remaining}, ensure_ascii=True).encode("ascii")
        process = None
        started = time.monotonic()
        try:
            process = subprocess.Popen([sys.executable, "-I", "-B", str(Path(__file__).with_name("toolchain_download.py").resolve())],
                cwd=temporary, env=_worker_environment(), stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, shell=False, close_fds=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            timeout = remaining - (time.monotonic() - started)
            if timeout <= 0:
                raise subprocess.TimeoutExpired("tool-download-worker", remaining)
            process.communicate(payload, timeout=timeout)
            require(process.returncode == 0, "tool_download_failed")
            return _verified(_read(destination, specification["size_bytes"]), specification)
        except subprocess.TimeoutExpired as exc:
            raise ContractError("tool_download_timeout") from exc
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)


def _extract_gh(raw, specification):
    _verified(raw, specification)  # Do not parse an unverified archive.
    size = specification["binary_size_bytes"]
    if specification["filename"].endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            require(len(members) <= 4096, "tool_archive_member_limit")
            selected = [member for member in members if member.filename == specification["member"]]
            require(len(selected) == 1, "tool_archive_member_missing_or_ambiguous")
            member = selected[0]
            kind = stat.S_IFMT(member.external_attr >> 16)
            require(not member.is_dir() and kind in {0, stat.S_IFREG} and not member.flag_bits & 1
                and member.file_size == size and member.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "unsafe_tool_archive_member")
            with archive.open(member) as stream:
                binary = stream.read(size + 1)
    else:
        # Bound expanded tar metadata as well as the selected executable.
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as compressed:
            expanded = compressed.read(128 * 1024 * 1024 + 1)
        require(len(expanded) <= 128 * 1024 * 1024, "tool_archive_expansion_limit")
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            members = []
            for member in archive:
                require(len(members) < 4096, "tool_archive_member_limit")
                members.append(member)
            selected = [member for member in members if member.name == specification["member"]]
            require(len(selected) == 1, "tool_archive_member_missing_or_ambiguous")
            member = selected[0]
            require(member.isreg() and not member.issparse() and member.size == size, "unsafe_tool_archive_member")
            with archive.extractfile(member) as stream:
                binary = stream.read(size + 1)
    return _verified(binary, {"size_bytes": size, "sha256": specification["binary_sha256"]})


def _operator_tools():
    require(sys.version_info[:2] in {(3, 11), (3, 12)}, "unsupported_toolchain_python")
    machine = platform.machine().lower()
    require(machine in {"amd64", "x86_64"} and struct.calcsize("P") == 8
        and sys.platform in {"linux", "win32"}, "unsupported_toolchain_platform")
    selected = "windows-amd64" if sys.platform == "win32" else "linux-amd64"
    git = shutil.which("git")
    require(git is not None, "git_executable_unavailable")
    git, python = Path(git).resolve(strict=True), Path(os.path.abspath(sys.executable))
    require(git.is_file() and python.is_file(), "operator_tool_unavailable")
    return selected, git, python


def install_tools(output):
    """Create a new operator tool directory; publish its usable configuration last."""
    selected, git, python = _operator_tools()
    lock = load_lock()
    root = _trusted_root(lock["trusted_root"])
    output = Path(os.path.abspath(output))
    _directories(output.parent)
    output.mkdir(mode=0o700)
    for name in ("wheels", "downloads", "bin"):
        (output / name).mkdir(mode=0o700)
    deadline = time.monotonic() + 180
    rows, wheels = [], {}
    for name, entry in lock["engines"].items():
        raw = _verified(_download(entry, deadline=deadline), entry)
        relative = "wheels/" + entry["filename"]
        _write(output / relative, raw)
        wheels[name] = relative
        rows.append({"path": relative, "url": entry["url"], "size_bytes": len(raw), "sha256": _sha(raw)})
    entry = lock["gh"][selected]
    archive = _verified(_download(entry, deadline=deadline), entry)
    relative = "downloads/" + entry["filename"]
    _write(output / relative, archive)
    rows.append({"path": relative, "url": entry["url"], "size_bytes": len(archive), "sha256": _sha(archive)})
    binary = _extract_gh(archive, entry)
    executable = "bin/gh.exe" if selected == "windows-amd64" else "bin/gh"
    _write(output / executable, binary)
    if selected == "linux-amd64":
        (output / executable).chmod(0o700)
    _write(output / "trusted-root.jsonl", root)
    _write(output / "download-receipt.json", canonical_bytes({"kind": "delivery-review-toolchain-receipt", "version": 1,
        "platform": selected, "downloads": rows, "gh_binary": {"path": executable, "sha256": _sha(binary), "size_bytes": len(binary)},
        "trusted_root": {"path": "trusted-root.jsonl", "sha256": _sha(root), "size_bytes": len(root)},
        "publisher_signature": "not_independently_qualified", "qualification": "not_established"}))
    config = output / "toolchain.json"
    _write(config, canonical_bytes({"git_executable": str(git), "python_executable": str(python),
        "engine_wheels": wheels, "gh_executable": executable, "trusted_root": "trusted-root.jsonl"}))
    return config
