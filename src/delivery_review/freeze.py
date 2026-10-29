"""Check the reviewed M0 file baseline; this is not signature verification."""
import hashlib
from pathlib import Path

from .contracts import load_document, require, safe_relative_path


def frozen_paths(root: Path) -> set[str]:
    paths = {
        "docs/CONTRACT.md", "docs/ACCEPTANCE.md", "acceptance/manifest.json",
        "locks/engine-candidates.json", "fixtures/github/README.md",
    }
    for pattern in ("schemas/*.schema.json", "examples/*.json", "fixtures/github/*.json"):
        paths.update(p.relative_to(root).as_posix() for p in root.glob(pattern))
    return paths


def verify_freeze(root: Path) -> int:
    root = root.resolve()
    manifest = load_document(root / "acceptance/contract-freeze.json")
    require(manifest["contract_version"] == 1 and manifest["milestone"] == "M0",
            "unknown contract freeze")
    require(manifest["integration_status"] == "NOT_RUN", "M0 freeze cannot claim integration")
    files = manifest["files"]
    paths = [item["path"] for item in files]
    require(len(paths) == len(set(paths)), "duplicate frozen file")
    require(set(paths) == frozen_paths(root), "frozen file inventory changed")
    for item in files:
        relative = item["path"]
        require(safe_relative_path(relative), "unsafe frozen path")
        path = (root / relative).resolve()
        require(path.is_relative_to(root), "frozen file escapes repository")
        raw = path.read_bytes()
        require(len(raw) == item["size_bytes"] and hashlib.sha256(raw).hexdigest() == item["sha256"],
                f"frozen bytes changed: {relative}; record a reviewed contract amendment")
    return len(files)
