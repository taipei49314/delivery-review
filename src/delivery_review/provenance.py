"""Collector revision consistency; hashes are not publisher authentication."""
import hashlib
from pathlib import Path
import re

from . import __version__
from .contracts import ContractError, loads_strict, require
from .evidence.store import _read
from ._source_identity import SourceIdentityError, source_commit, source_paths


def _installed_identity(package: Path) -> dict:
    info = loads_strict(_read(package / "_build_info.json", 2 * 1024 * 1024))
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
        raw = _read(package / name, 4 * 1024 * 1024)
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
