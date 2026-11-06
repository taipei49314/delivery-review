"""Collector revision consistency; hashes are not publisher authentication."""
import hashlib
import os
from pathlib import Path
import re
import stat

from . import __version__
from .contracts import ContractError, loads_strict, require
from .evidence.store import _directories, _identity, _not_link
from ._source_identity import SourceIdentityError, source_commit, source_paths


def _installed_read(path: Path, budget: int) -> bytes:
    """Read stable installed bytes, including package-manager cache hardlinks.

    Installed packages are consistency-checked inputs, not create-once evidence
    bundles. uv legitimately hardlinks them from its cache. Symlink/reparse
    traversal, non-regular files, concurrent changes and digest mismatches remain
    refused. This does not change the evidence store's single-link requirement.
    """
    _directories(path.parent)
    before = path.lstat()
    def regular(info):
        require(_not_link(info) and stat.S_ISREG(info.st_mode), "unsafe_installed_file")
    regular(before)
    require(before.st_size <= budget, "installed_file_limit")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        opened = os.fstat(stream.fileno())
        regular(opened)
        require(_identity(before) == _identity(opened), "collector_installation_changed")
        raw = stream.read(budget + 1)
        after = os.fstat(stream.fileno())
    final = path.lstat()
    regular(final)
    require(len(raw) <= budget and len(raw) == before.st_size and
            _identity(before) == _identity(after) == _identity(final) and
            opened.st_ctime_ns == after.st_ctime_ns and before.st_ctime_ns == final.st_ctime_ns,
            "collector_installation_changed")
    return raw


def _installed_identity(package: Path) -> dict:
    info = loads_strict(_installed_read(package / "_build_info.json", 2 * 1024 * 1024))
    require(type(info) is dict and set(info) == {"kind", "version", "package_version", "source_sha",
                                               "source_state", "files"}, "invalid_build_identity")
    require(info["kind"] == "delivery-review-build" and type(info["version"]) is int and info["version"] == 1,
            "invalid_build_identity")
    require(info["package_version"] == __version__ and info["source_state"] == "clean" and
            type(info["source_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", info["source_sha"]),
            "collector_source_unavailable")
    inventory = info["files"]
    require(type(inventory) is dict and 1 <= len(inventory) <= 4096, "invalid_build_inventory")
    actual = {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file() and
              p.suffix in {".py", ".json"} and p.name != "_build_info.json"}
    require(set(inventory) == actual, "collector_installation_changed")
    for name, expected in inventory.items():
        require(type(name) is str and name and "\\" not in name and not name.startswith("/") and
                all(part not in {"", ".", ".."} and ":" not in part for part in name.split("/")),
                "invalid_build_inventory")
        require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected), "invalid_build_inventory")
        raw = _installed_read(package / name, 4 * 1024 * 1024)
        require(hashlib.sha256(raw).hexdigest() == expected, "collector_installation_changed")
    return {"version": __version__, "source_sha": info["source_sha"]}


def collector_identity() -> dict:
    """Live provenance requires a clean checkout or consistent release build."""
    package = Path(__file__).resolve().parent
    root = package.parents[1]
    if (root / "src/delivery_review").resolve() == package:
        try:
            return {"version": __version__, "source_sha": source_commit(root, source_paths(root))}
        except SourceIdentityError as exc:
            raise ContractError(str(exc)) from exc
    if (package / "_build_info.json").exists():
        return _installed_identity(package)
    raise ContractError("collector_source_unavailable")
