"""Load bundled, offline format definitions in wheels and source checkouts."""
from importlib.resources import files
from pathlib import Path


def definition_bytes(group: str, name: str) -> bytes:
    if group not in {"schemas", "formats", "locks"} or "/" in name or "\\" in name or not name.endswith(".json"):
        raise ValueError("invalid resource name")
    packaged = files("delivery_review").joinpath("data", group, name)
    if packaged.is_file():
        return packaged.read_bytes()
    # Only the repository layout is a development fallback; installed wheels
    # must carry their own definitions and must never look in the caller's cwd.
    root = Path(__file__).resolve().parents[2]
    if (root / "src/delivery_review/resources.py").resolve() == Path(__file__).resolve():
        return (root / group / name).read_bytes()
    raise FileNotFoundError("installed definition is missing")
