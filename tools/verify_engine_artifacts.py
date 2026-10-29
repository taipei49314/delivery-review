"""Verify candidate wheel bytes without installing or executing their contents.

Offline by default. --download explicitly enables bounded requests to PyPI and
files.pythonhosted.org. The trusted input is the candidate's expected SHA-256.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import time
from email.parser import BytesParser
from email.policy import default as email_policy
import urllib.error
import urllib.parse
import urllib.request
import zipfile


MAX_DOWNLOAD = 50 * 1024 * 1024
MAX_METADATA = 4 * 1024 * 1024
MAX_MEMBER = 64 * 1024 * 1024
MAX_EXPANDED = 250 * 1024 * 1024
MAX_MEMBERS = 10000
TIMEOUT_SECONDS = 30
MAX_TRANSFER_SECONDS = 120


class VerificationError(ValueError):
    """The candidate bytes or their declared identity could not be verified."""


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not (
        path.is_absolute() or "\\" in name or ":" in name or "\x00" in name
        or any(part in {"", ".", ".."} for part in name.rstrip("/").split("/"))
    )


def verify_wheel_bytes(
    data: bytes,
    *,
    expected_name: str,
    expected_version: str,
    expected_sha256: str,
    expected_size: int | None = None,
) -> dict:
    """Pure verification over supplied bytes; never import or extract a wheel.

    The archive SHA-256 must match the separately approved candidate digest.
    Every regular wheel member must have a matching RECORD digest and size,
    except RECORD itself and optional wheel signature files.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256):
        raise VerificationError("Expected SHA-256 must be 64 lowercase hexadecimal characters")
    if len(data) > MAX_DOWNLOAD:
        raise VerificationError("Wheel exceeds the archive size limit")
    if expected_size is not None and len(data) != expected_size:
        raise VerificationError("Wheel byte length differs from the candidate metadata")
    if _sha256(data) != expected_sha256:
        raise VerificationError("Wheel SHA-256 differs from the approved candidate")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_MEMBERS:
                raise VerificationError("Wheel has too many archive members")
            names: set[str] = set()
            files: dict[str, bytes] = {}
            expanded = 0
            for entry in entries:
                if (not _safe_member(entry.orig_filename) or not _safe_member(entry.filename)
                        or entry.filename in names):
                    raise VerificationError("Wheel has an unsafe or duplicate archive member")
                names.add(entry.filename)
                mode = entry.external_attr >> 16
                if stat.S_ISLNK(mode) or entry.flag_bits & 1:
                    raise VerificationError("Wheel has a symbolic link or encrypted member")
                if entry.is_dir():
                    continue
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise VerificationError("Wheel has a non-regular archive member")
                expanded += entry.file_size
                if entry.file_size > MAX_MEMBER or expanded > MAX_EXPANDED:
                    raise VerificationError("Wheel exceeds expanded byte limits")
                with archive.open(entry) as stream:
                    value = stream.read(MAX_MEMBER + 1)
                if len(value) != entry.file_size or len(value) > MAX_MEMBER:
                    raise VerificationError("Wheel member length does not match its inventory")
                files[entry.filename] = value
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise VerificationError("Cannot read the candidate as a supported wheel archive") from exc

    metadata_paths = [name for name in files if re.fullmatch(r"[^/]+\.dist-info/METADATA", name)]
    if len(metadata_paths) != 1:
        raise VerificationError("Wheel must have exactly one top-level METADATA file")
    metadata_path = metadata_paths[0]
    dist_info = metadata_path.rsplit("/", 1)[0]
    metadata = BytesParser(policy=email_policy).parsebytes(files[metadata_path])
    if len(metadata.get_all("Name", [])) != 1 or len(metadata.get_all("Version", [])) != 1:
        raise VerificationError("METADATA must have one Name and one Version")
    name, version = str(metadata["Name"]), str(metadata["Version"])
    if _canonical_name(name) != _canonical_name(expected_name) or version != expected_version:
        raise VerificationError("METADATA identity differs from the candidate")
    record_path = f"{dist_info}/RECORD"
    if record_path not in files:
        raise VerificationError("Wheel is missing RECORD")
    try:
        rows = list(csv.reader(io.StringIO(files[record_path].decode("utf-8")), strict=True))
    except (UnicodeError, csv.Error) as exc:
        raise VerificationError("RECORD is not valid UTF-8 CSV") from exc
    seen: set[str] = set()
    verified = 0
    for row in rows:
        if len(row) != 3:
            raise VerificationError("RECORD row must have three columns")
        path, digest, size = row
        if path in seen or path not in files:
            raise VerificationError("RECORD contains a duplicate or absent file")
        seen.add(path)
        if path == record_path:
            if digest or size:
                raise VerificationError("RECORD must leave its own hash and size empty")
            continue
        try:
            algorithm, encoded = digest.split("=", 1)
            if algorithm not in {"sha256", "sha384", "sha512"}:
                raise ValueError("unsupported hash algorithm")
            expected = base64.urlsafe_b64encode(hashlib.new(algorithm, files[path]).digest()).rstrip(b"=").decode("ascii")
            if encoded != expected or not re.fullmatch(r"0|[1-9][0-9]*", size) or int(size) != len(files[path]):
                raise ValueError("hash or size mismatch")
        except (ValueError, TypeError) as exc:
            raise VerificationError("RECORD digest or size does not match member bytes") from exc
        verified += 1
    allowed_unlisted = {f"{dist_info}/RECORD.jws", f"{dist_info}/RECORD.p7s"}
    if record_path not in seen or (set(files) - seen) - allowed_unlisted:
        raise VerificationError("RECORD does not cover every required wheel file")
    inventory = [{"path": path, "size_bytes": len(value), "sha256": _sha256(value)}
                 for path, value in sorted(files.items())]
    inventory_bytes = json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return {
        "status": "candidate_byte_verified",
        "metadata": {"name": name, "version": version},
        "wheel": {"member_count": len(files), "inventory_sha256": _sha256(inventory_bytes),
                  "metadata_sha256": _sha256(files[metadata_path]),
                  "record_sha256": _sha256(files[record_path]), "record_verified_files": verified},
    }


def _approved_url(url: str, allowed_host: str) -> None:
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != allowed_host or parsed.username
            or parsed.password or parsed.port not in (None, 443) or parsed.fragment):
        raise VerificationError("Download URL is outside the approved HTTPS host")


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _approved_url(newurl, urllib.parse.urlsplit(req.full_url).hostname)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch(url: str, *, host: str, limit: int) -> bytes:
    _approved_url(url, host)
    request = urllib.request.Request(url, headers={"User-Agent": "delivery-review-engine-verifier/0.1"})
    deadline = time.monotonic() + MAX_TRANSFER_SECONDS
    try:
        with urllib.request.build_opener(_SameHostRedirect()).open(request, timeout=TIMEOUT_SECONDS) as response:
            _approved_url(response.url, host)
            declared = response.headers.get("Content-Length")
            if declared is not None and (not declared.isdigit() or int(declared) > limit):
                raise VerificationError("Download Content-Length exceeds the allowed bound")
            chunks: list[bytes] = []
            received = 0
            while True:
                if time.monotonic() > deadline:
                    raise VerificationError("Download exceeds the transfer deadline")
                # read1 limits each operation to one underlying read. The
                # socket timeout bounds the final read beyond the deadline.
                chunk = response.read1(min(65536, limit - received + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                received += len(chunk)
                if received > limit:
                    raise VerificationError("Download exceeds the allowed byte bound")
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VerificationError("Official package download failed") from exc


def _match_pypi(engine: dict) -> dict:
    name, version = engine["name"], engine["version"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not re.fullmatch(r"[A-Za-z0-9_.+-]+", version):
        raise VerificationError("Invalid package identifier")
    url = f"https://pypi.org/pypi/{name}/{version}/json"
    try:
        metadata = json.loads(_fetch(url, host="pypi.org", limit=MAX_METADATA))
        candidates = [item for item in metadata["urls"]
                      if item.get("packagetype") == "bdist_wheel"
                      and item.get("digests", {}).get("sha256") == engine["artifact"]["sha256"]]
        if len(candidates) != 1:
            raise VerificationError("PyPI does not name one matching approved wheel")
        candidate = candidates[0]
        expected = engine["artifact"]
        if (candidate.get("yanked") or candidate["filename"] != expected["filename"]
                or candidate["size"] != expected["size_bytes"] or candidate["url"] != expected["url"]):
            raise VerificationError("PyPI candidate identity differs from the lock")
        return candidate
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, VerificationError):
            raise
        raise VerificationError("Official PyPI metadata is malformed") from exc


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".engine-lock-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path(__file__).resolve().parents[1] / "locks" / "engine-candidates.json")
    parser.add_argument("--wheel-dir", required=True, type=Path, help="Existing wheels, or download destination outside the repository")
    parser.add_argument("--download", action="store_true", help="Explicitly fetch official PyPI metadata and candidate wheels")
    parser.add_argument("--receipt", type=Path, help="Write a verified lock only after every candidate passes; otherwise print JSON")
    options = parser.parse_args(argv)
    try:
        lock = json.loads(options.lock.read_text(encoding="utf-8"))
        if lock.get("schema_version") != 1 or lock.get("lock_kind") != "engine_candidates":
            raise VerificationError("Unsupported engine candidate lock")
        if not isinstance(lock.get("engines"), list) or not lock["engines"]:
            raise VerificationError("Engine candidate list is empty or malformed")
        wheel_dir = options.wheel_dir.resolve()
        if options.download:
            repository = Path(__file__).resolve().parents[1]
            if wheel_dir == repository or repository in wheel_dir.parents:
                raise VerificationError("Downloaded wheels must be stored outside the repository")
            wheel_dir.mkdir(parents=True, exist_ok=True)
        for engine in lock["engines"]:
            artifact = engine["artifact"]
            filename = artifact["filename"]
            if Path(filename).name != filename or not _safe_member(filename) or not filename.endswith(".whl"):
                raise VerificationError("Invalid wheel filename")
            if options.download:
                _match_pypi(engine)
                data = _fetch(artifact["url"], host="files.pythonhosted.org", limit=MAX_DOWNLOAD)
            else:
                with (wheel_dir / filename).open("rb") as stream:
                    data = stream.read(MAX_DOWNLOAD + 1)
            verification = verify_wheel_bytes(data, expected_name=engine["name"], expected_version=engine["version"],
                                              expected_sha256=artifact["sha256"], expected_size=artifact["size_bytes"])
            if options.download:
                destination = wheel_dir / filename
                if destination.exists():
                    with destination.open("rb") as stream:
                        if stream.read(MAX_DOWNLOAD + 1) != data:
                            raise VerificationError("Existing download differs; refusing to overwrite it")
                else:
                    with destination.open("xb") as stream:
                        stream.write(data)
            engine["verification"] = verification
            engine["integration_status"] = "NOT_RUN"
        if options.receipt:
            _write_json(options.receipt, lock)
            print("All candidate wheel bytes verified; installation and integration remain NOT_RUN.")
        else:
            print(json.dumps(lock, ensure_ascii=True, indent=2))
        return 0
    except (VerificationError, OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"Engine byte verification failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
