"""Standard-library-only build inventory and source-consistency checks."""
from hashlib import sha256
import json
import importlib.util
import os
from pathlib import Path
import re

_spec = importlib.util.spec_from_file_location("delivery_review_source_identity",
    Path(__file__).resolve().parent / "src/delivery_review/_source_identity.py")
_source = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_source)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def source_files(root):
    root = Path(root).resolve()
    return {path.relative_to(root).as_posix(): digest(path) for path in _source.source_paths(root)}


def source_identity(root):
    root = Path(root).resolve()
    inventory = source_files(root)
    unverified = {"source_state": "unverified", "source_sha": None, "files": inventory}
    # A checkout (including a worktree's .git file) must use its actual Git state.
    # A stale archive record cannot override dirty, broken or unavailable Git.
    if os.path.lexists(root / ".git"):
        try:
            commit = _source.source_commit(root, _source.source_paths(root))
            return {"source_state": "clean", "source_sha": commit, "files": inventory}
        except _source.SourceIdentityError:
            pass
        return unverified
    recorded = root / "_source_provenance.json"
    try:
        if recorded.is_file():
            value = json.loads(recorded.read_text(encoding="utf-8"))
            if (type(value) is dict and set(value) == {"source_state", "source_sha", "files"}
                    and value["files"] == inventory and value["source_state"] == "clean"
                    and type(value["source_sha"]) is str and re.fullmatch(r"[0-9a-f]{40}", value["source_sha"])):
                return value
    except (OSError, ValueError, UnicodeError):
        pass
    return unverified


def expected_package_files(root):
    """Map every shipped module/resource to its original source file."""
    root = Path(root).resolve()
    package = root / "src/delivery_review"
    expected = {path.relative_to(package).as_posix(): path for path in package.rglob("*.py")
                if "__pycache__" not in path.parts}
    for group in ("schemas", "formats", "locks"):
        for source in (root / group).rglob("*.json"):
            expected["data/" + group + "/" + source.relative_to(root / group).as_posix()] = source
    return expected


def verify_build_package(package, expected, *, complete):
    """Refuse stale build output instead of blessing extra bytes as this source."""
    package = Path(package)
    actual = {}
    if package.is_symlink():
        raise ValueError("build package is a symlink")
    if package.exists():
        for path in package.rglob("*"):
            if path.is_symlink():
                raise ValueError("build package contains a symlink")
            if path.is_file():
                if path.stat().st_nlink != 1:
                    raise ValueError("build package contains a hardlink")
                name = path.relative_to(package).as_posix()
                if name != "_build_info.json":
                    actual[name] = path
    if not set(actual) <= set(expected) or complete and set(actual) != set(expected):
        raise ValueError("build package contains unexpected or missing files")
    if complete and any(digest(path) != digest(expected[name]) for name, path in actual.items()):
        raise ValueError("build package bytes differ from source")
    return {name: digest(path) for name, path in sorted(actual.items())}
