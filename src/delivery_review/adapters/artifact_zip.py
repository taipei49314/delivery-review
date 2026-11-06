"""Read a deliberately narrow artifact ZIP dialect without filesystem writes.

Hashes describe retained bytes only. This reader does not authenticate an
artifact, interpret receipts, or establish producer/job/source associations.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, fields
import hashlib
import re
import stat
import struct
from types import MappingProxyType
import zlib

from delivery_review.contracts import ContractError

_END = struct.Struct("<4s4H2IH")
_CENTRAL = struct.Struct("<4s6H3I5H2I")
_LOCAL = struct.Struct("<4s5H3I2H")
_DESCRIPTOR = struct.Struct("<3I")
_POLICY_FIELDS = ("artifact_bytes", "expanded_bytes", "archive_members")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
    f"{prefix}{index}" for prefix in ("COM", "LPT") for index in range(1, 10)
}


class ArtifactZipError(ContractError):
    """Unsafe, unsupported, contradictory or resource-exceeding archive input."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArtifactZipLimits:
    """Local ceilings can be lowered; policy limits also apply independently."""

    max_archive_bytes: int = 25 * 1024 * 1024
    max_expanded_bytes: int = 100 * 1024 * 1024
    max_members: int = 100
    max_member_bytes: int = 25 * 1024 * 1024
    max_compression_ratio: int = 100
    max_path_chars: int = 512
    max_extra_bytes: int = 8192


@dataclass(frozen=True)
class ArtifactZipContents:
    archive_bytes: bytes
    archive_sha256: str
    files: Mapping[str, bytes]
    file_sha256: Mapping[str, str]

    @property
    def archive_size_bytes(self) -> int:
        return len(self.archive_bytes)


@dataclass(frozen=True)
class _Member:
    path: str
    name_bytes: bytes
    version: int
    flags: int
    compression: int
    time: int
    date: int
    crc: int
    compressed: int
    expanded: int
    offset: int


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ArtifactZipError(code)


def _bounds(policy: Mapping[str, int], limits: ArtifactZipLimits) -> tuple[int, int, int]:
    _require(type(limits) is ArtifactZipLimits, "invalid_zip_limits")
    hard = ArtifactZipLimits()
    for item in fields(limits):
        value = getattr(limits, item.name)
        _require(type(value) is int and 0 < value <= getattr(hard, item.name), "invalid_zip_limits")
    _require(isinstance(policy, Mapping), "invalid_zip_limits")
    for name in _POLICY_FIELDS:
        value = policy.get(name)
        _require(type(value) is int and 0 < value <= 2**53 - 1, "invalid_zip_limits")
    return (min(limits.max_archive_bytes, policy["artifact_bytes"]),
            min(limits.max_expanded_bytes, policy["expanded_bytes"]),
            min(limits.max_members, policy["archive_members"]))


def _path(path: str, limits: ArtifactZipLimits) -> None:
    _require(type(path) is str and 0 < len(path) <= limits.max_path_chars, "unsafe_zip_path")
    for segment in path.split("/"):
        _require(re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", segment) is not None
                 and not segment.endswith(".")
                 and segment.split(".", 1)[0].upper() not in _RESERVED,
                 "unsafe_zip_path")


def _extra(raw: bytes, limits: ArtifactZipLimits) -> None:
    _require(len(raw) <= limits.max_extra_bytes, "zip_resource_limit")
    position = 0
    seen = set()
    while position < len(raw):
        _require(len(raw) - position >= 4, "invalid_zip_header")
        kind, size = struct.unpack_from("<HH", raw, position)
        position += 4
        _require(size <= len(raw) - position and kind not in seen, "invalid_zip_header")
        # Only inert extended timestamps and NTFS timestamps are admitted.
        # ZIP64, Unicode path overrides, Unix link metadata and unknown extras
        # are unsupported, rather than being interpreted or extracted.
        _require(kind in {0x5455, 0x000A}, "unsupported_zip_format")
        seen.add(kind)
        position += size


def _directory(raw: bytes, max_members: int, expanded_limit: int,
               limits: ArtifactZipLimits) -> tuple[list[_Member], int]:
    _require(len(raw) >= _END.size, "invalid_zip_header")
    end_offset = len(raw) - _END.size
    signature, disk, central_disk, disk_count, count, central_size, central_offset, comment = _END.unpack_from(raw, end_offset)
    _require(signature == b"PK\x05\x06", "unsupported_zip_format")
    _require(disk == central_disk == 0 and disk_count == count and comment == 0,
             "unsupported_zip_format")
    _require(count != 0xFFFF and central_size != 0xFFFFFFFF and central_offset != 0xFFFFFFFF,
             "unsupported_zip_format")
    _require(0 < count <= max_members, "zip_resource_limit")
    _require(central_offset + central_size == end_offset, "invalid_zip_header")
    position = central_offset
    members = []
    names = set()
    total_expanded = 0
    for _ in range(count):
        _require(position + _CENTRAL.size <= end_offset, "invalid_zip_header")
        (signature, made_by, version, flags, compression, time, date, crc,
         compressed, expanded, name_size, extra_size, comment_size, start_disk,
         _internal_attributes, external_attributes, offset) = _CENTRAL.unpack_from(raw, position)
        _require(signature == b"PK\x01\x02", "invalid_zip_header")
        _require(start_disk == 0 and 10 <= version <= 20 and made_by >> 8 in {0, 3},
                 "unsupported_zip_format")
        _require(not flags & (0x0001 | 0x0040 | 0x2000), "encrypted_zip_member")
        _require(flags & ~(0x0002 | 0x0004 | 0x0008 | 0x0800) == 0
                 and compression in {0, 8} and (compression == 8 or not flags & 0x0006),
                 "unsupported_zip_format")
        _require(version == 20 or (compression == 0 and not flags & 0x0008),
                 "unsupported_zip_format")
        _require(comment_size == 0, "unsupported_zip_format")
        _require(compressed != 0xFFFFFFFF and expanded != 0xFFFFFFFF and offset != 0xFFFFFFFF,
                 "unsupported_zip_format")
        mode = external_attributes >> 16
        _require(stat.S_IFMT(mode) in {0, stat.S_IFREG}
                 and external_attributes & 0xFFFF & ~(0x01 | 0x02 | 0x04 | 0x20) == 0,
                 "non_regular_zip_member")
        position += _CENTRAL.size
        record_end = position + name_size + extra_size
        _require(record_end <= end_offset, "invalid_zip_header")
        name_bytes = raw[position:position + name_size]
        try:
            path = name_bytes.decode("ascii", errors="strict")
        except UnicodeError:
            raise ArtifactZipError("unsafe_zip_path") from None
        _path(path, limits)
        _require(path.casefold() not in names, "duplicate_zip_member")
        names.add(path.casefold())
        _extra(raw[position + name_size:record_end], limits)
        position = record_end
        total_expanded += expanded
        _require(expanded <= limits.max_member_bytes and total_expanded <= expanded_limit,
                 "zip_resource_limit")
        _require(expanded <= limits.max_compression_ratio * max(1, compressed),
                 "zip_compression_ratio")
        if compression == 0:
            _require(compressed == expanded, "invalid_zip_header")
        members.append(_Member(path, name_bytes, version, flags, compression,
                               time, date, crc, compressed, expanded, offset))
    _require(position == end_offset, "invalid_zip_header")
    return members, central_offset


def _payload(raw: bytes, member: _Member, region_end: int,
             limits: ArtifactZipLimits) -> bytes:
    _require(member.offset + _LOCAL.size <= region_end, "invalid_zip_header")
    (signature, version, flags, compression, time, date, crc, compressed,
     expanded, name_size, extra_size) = _LOCAL.unpack_from(raw, member.offset)
    _require(signature == b"PK\x03\x04" and (version, flags, compression, time, date) ==
             (member.version, member.flags, member.compression, member.time, member.date),
             "zip_header_mismatch")
    position = member.offset + _LOCAL.size
    payload_start = position + name_size + extra_size
    payload_end = payload_start + member.compressed
    _require(payload_end <= region_end, "invalid_zip_header")
    _require(raw[position:position + name_size] == member.name_bytes, "zip_header_mismatch")
    _extra(raw[position + name_size:payload_start], limits)
    expected = (member.crc, member.compressed, member.expanded)
    if flags & 0x0008:
        _require((crc, compressed, expanded) in {(0, 0, 0), expected}, "zip_header_mismatch")
        size = region_end - payload_end
        _require(size in {12, 16}, "invalid_zip_header")
        descriptor_start = payload_end
        if size == 16:
            _require(raw[descriptor_start:descriptor_start + 4] == b"PK\x07\x08", "invalid_zip_header")
            descriptor_start += 4
        _require(_DESCRIPTOR.unpack_from(raw, descriptor_start) == expected, "zip_header_mismatch")
    else:
        _require((crc, compressed, expanded) == expected, "zip_header_mismatch")
        _require(payload_end == region_end, "invalid_zip_header")
    return raw[payload_start:payload_end]


def _expand(payload: bytes, member: _Member) -> bytes:
    if member.compression == 0:
        output = payload
    else:
        decoder = zlib.decompressobj(-15)
        try:
            # The extra byte detects false declared sizes. Unlike a general ZIP
            # reader, this also requires the complete compressed stream to end
            # exactly here, with no discarded trailing data or hidden expansion.
            output = decoder.decompress(payload, member.expanded + 1)
        except zlib.error:
            raise ArtifactZipError("invalid_zip_compressed_data") from None
        _require(len(output) <= member.expanded and decoder.eof and
                 not decoder.unconsumed_tail and not decoder.unused_data,
                 "invalid_zip_compressed_data")
    _require(len(output) == member.expanded, "zip_size_mismatch")
    _require(zlib.crc32(output) & 0xFFFFFFFF == member.crc, "zip_crc_mismatch")
    return output


def read_artifact_zip(raw: bytes, *, policy_limits: Mapping[str, int],
                      expected_paths: tuple[str, ...] = ("receipt.json", "junit.xml"),
                      limits: ArtifactZipLimits = ArtifactZipLimits()) -> ArtifactZipContents:
    """Read exactly the approved members of a bounded classic ZIP archive.

    Stored and raw-DEFLATE members, including 32-bit data descriptors, are
    supported. ZIP64, split/self-extracting archives, comments, directory entries
    and unknown extras are not in this dialect. All offsets/header relationships
    are checked; no filesystem or subprocess operation is performed.
    """
    archive_limit, expanded_limit, member_limit = _bounds(policy_limits, limits)
    _require(type(raw) is bytes, "invalid_zip_input")
    _require(len(raw) <= archive_limit, "zip_resource_limit")
    _require(type(expected_paths) is tuple and 0 < len(expected_paths) <= member_limit,
             "invalid_zip_expected_paths")
    expected_names = set()
    for path in expected_paths:
        _path(path, limits)
        _require(path.casefold() not in expected_names, "invalid_zip_expected_paths")
        expected_names.add(path.casefold())
    members, central_offset = _directory(raw, member_limit, expanded_limit, limits)
    _require({item.path for item in members} == set(expected_paths), "zip_member_inventory_mismatch")
    physical = sorted(members, key=lambda member: member.offset)
    _require(physical[0].offset == 0 and all(item.offset < central_offset for item in physical)
             and len({item.offset for item in physical}) == len(physical),
             "invalid_zip_header")
    # Validate every member's physical region before expanding any member.
    payloads = []
    for index, member in enumerate(physical):
        end = physical[index + 1].offset if index + 1 < len(physical) else central_offset
        payloads.append((member, _payload(raw, member, end, limits)))
    files = {member.path: _expand(payload, member) for member, payload in payloads}
    return ArtifactZipContents(raw, hashlib.sha256(raw).hexdigest(),
                               MappingProxyType(files), MappingProxyType({
                                   path: hashlib.sha256(data).hexdigest() for path, data in files.items()
                               }))
