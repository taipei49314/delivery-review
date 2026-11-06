"""Authored ZIP controls; no downloaded/project data is executed or extracted."""
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import stat
import struct
import sys
import unittest
from unittest.mock import patch
import warnings
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from delivery_review.adapters.artifact_zip import (
    ArtifactZipError, ArtifactZipLimits, read_artifact_zip,
)
from delivery_review.contracts import ContractError

POLICY = {"artifact_bytes": 25 * 1024 * 1024, "expanded_bytes": 100 * 1024 * 1024,
          "archive_members": 100, "scanner_seconds": 120}
FILES = [("receipt.json", b'{"origin":"authored_zip_control"}\r\n'),
         ("junit.xml", b'<authored>data only, not a test result</authored>\r\n')]
CENTRAL = struct.Struct("<4s6H3I5H2I")
LOCAL = struct.Struct("<4s5H3I2H")
END = struct.Struct("<4s4H2IH")


class NonSeekable(io.BytesIO):
    def seek(self, *args):
        raise io.UnsupportedOperation("authored streaming writer")


def archive(files=None, *, method=zipfile.ZIP_DEFLATED, stream=False, extra=b"", mode=None, comment=b""):
    target = NonSeekable() if stream else io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(target, "w", compression=method) as output:
            output.comment = comment
            for name, content in FILES if files is None else files:
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 6, 0, 0, 0))
                info.compress_type = method
                info.extra = extra
                if mode is not None:
                    info.create_system = 3
                    info.external_attr = mode << 16
                output.writestr(info, content)
    return target.getvalue()


def central_records(raw):
    end = END.unpack_from(raw, len(raw) - END.size)
    position = end[6]
    result = []
    for _ in range(end[4]):
        entry = CENTRAL.unpack_from(raw, position)
        size = CENTRAL.size + entry[10] + entry[11] + entry[12]
        result.append((position, size, entry[-1]))
        position += size
    return result


def edit(raw, *, local=None, central=None, index=0):
    result = bytearray(raw)
    start, _size, offset = central_records(raw)[index]
    for location, fields in ((offset, local or {}), (start, central or {})):
        for relative, (kind, value) in fields.items():
            struct.pack_into("<" + kind, result, location + relative, value)
    return bytes(result)


def raw_member(payload, data, *, expanded=None, descriptor=0):
    """Authored single-member envelope, allowing adversarial DEFLATE streams."""
    name = b"receipt.json"
    expanded = len(data) if expanded is None else expanded
    crc = zlib.crc32(data) & 0xFFFFFFFF
    flags = 8 if descriptor else 0
    sizes = (0, 0, 0) if descriptor else (crc, len(payload), expanded)
    local = LOCAL.pack(b"PK\x03\x04", 20, flags, 8, 0, 0, *sizes, len(name), 0) + name + payload
    if descriptor:
        local += (b"PK\x07\x08" if descriptor == 16 else b"") + struct.pack("<3I", crc, len(payload), expanded)
    central = CENTRAL.pack(b"PK\x01\x02", 20, 20, flags, 8, 0, 0, crc, len(payload), expanded,
                           len(name), 0, 0, 0, 0, 0, 0) + name
    return local + central + END.pack(b"PK\x05\x06", 0, 0, 1, 1, len(central), len(local), 0)


def deflate(data):
    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(data) + compressor.flush()


class ArtifactZipTests(unittest.TestCase):
    def read(self, raw, **kwargs):
        return read_artifact_zip(raw, policy_limits=kwargs.pop("policy_limits", POLICY), **kwargs)

    def refused(self, raw, code=None, **kwargs):
        with self.assertRaises(ArtifactZipError) as caught:
            self.read(raw, **kwargs)
        self.assertIsInstance(caught.exception, ContractError)
        self.assertEqual(caught.exception.code, str(caught.exception))
        if code:
            self.assertEqual(code, caught.exception.code)

    def test_stored_deflated_and_streamed_archives_preserve_exact_bytes(self):
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            for stream in (False, True):
                with self.subTest(method=method, stream=stream):
                    raw = archive(method=method, stream=stream)
                    observed = self.read(raw)
                    self.assertIs(raw, observed.archive_bytes)
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), observed.archive_sha256)
                    self.assertEqual(len(raw), observed.archive_size_bytes)
                    self.assertEqual(dict(FILES), observed.files)
                    self.assertEqual({name: hashlib.sha256(data).hexdigest() for name, data in FILES}, observed.file_sha256)
                    with self.assertRaises(TypeError):
                        observed.files["junit.xml"] = b"changed"
                    with self.assertRaises(TypeError):
                        observed.file_sha256["junit.xml"] = "changed"

    def test_reading_never_opens_a_file_or_executes_or_fetches(self):
        raw = archive()
        with patch("builtins.open", side_effect=AssertionError("filesystem access")), \
             patch("socket.create_connection", side_effect=AssertionError("network access")), \
             patch("subprocess.Popen", side_effect=AssertionError("execution")):
            self.assertEqual(dict(FILES), self.read(raw).files)

    def test_explicit_nested_paths_need_no_directory_members(self):
        files = [("proof/receipt.json", b"receipt"), ("reports/junit.xml", b"report")]
        observed = self.read(archive(files), expected_paths=tuple(name for name, _ in files))
        self.assertEqual(dict(files), observed.files)
        self.refused(archive(files), "zip_member_inventory_mismatch")

    def test_only_exact_approved_inventory_is_allowed(self):
        for files in (FILES[:1], FILES + [("extra.txt", b"extra")],
                      [("Receipt.json", FILES[0][1]), FILES[1]]):
            with self.subTest(files=files):
                self.refused(archive(files), "zip_member_inventory_mismatch")
        self.refused(archive(FILES + [("reports/", b"")]))

    def test_duplicate_and_case_aliases_are_not_last_one_wins(self):
        for name in ("receipt.json", "RECEIPT.JSON", "Receipt.Json"):
            with self.subTest(name=name):
                self.refused(archive(FILES + [(name, b"attacker replacement")]), "duplicate_zip_member")

    def test_unsafe_names_are_refused_even_if_declared_as_expected(self):
        for name in ("../receipt.json", "/receipt.json", "a/../receipt.json", "a//receipt.json",
                     "a\\receipt.json", "C:receipt.json", "receipt.json:stream", "receipt.json.",
                     "receipt.json ", "CON", "aux.xml", "folder/LPT1.txt", ".hidden", "a/./b",
                     "rece\u0131pt.json", "a" * 129, "a/" * 256 + "x"):
            with self.subTest(name=name):
                self.refused(archive([(name, b"data")]), "unsafe_zip_path", expected_paths=(name,))
        self.refused(archive().replace(b"receipt.json", b"\0eceipt.json"), "unsafe_zip_path")

    def test_links_devices_and_nonregular_attributes_are_refused(self):
        for kind in (stat.S_IFLNK, stat.S_IFDIR, stat.S_IFIFO, stat.S_IFCHR, stat.S_IFBLK, stat.S_IFSOCK):
            with self.subTest(kind=kind):
                self.refused(archive(mode=kind | 0o644), "non_regular_zip_member")
        for attributes in (0x10, 0x08, 0x400):
            self.refused(edit(archive(), central={38: ("I", attributes)}), "non_regular_zip_member")

    def test_encryption_and_unknown_compression_are_refused(self):
        for flag in (1, 0x40, 0x2000):
            self.refused(edit(archive(), central={8: ("H", flag)}), "encrypted_zip_member")
        for method in (zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA):
            self.refused(archive(method=method), "unsupported_zip_format")
        self.refused(edit(archive(), central={10: ("H", 99)}), "unsupported_zip_format")
        self.refused(edit(archive(), central={8: ("H", 0x10)}), "unsupported_zip_format")
        self.refused(edit(archive(), local={4: ("H", 10)}, central={6: ("H", 10)}), "unsupported_zip_format")

    def test_timestamp_extras_are_inert_but_link_path_zip64_extras_are_refused(self):
        for extra in (struct.pack("<HHBI", 0x5455, 5, 1, 1), struct.pack("<HHI", 0x000A, 4, 0)):
            self.assertEqual(dict(FILES), self.read(archive(extra=extra)).files)
        for kind in (1, 0x7075, 0x000D, 0xCAFE):
            self.refused(archive(extra=struct.pack("<HH", kind, 0)), "unsupported_zip_format")
        self.refused(archive(extra=b"\x55\x54\x10\0x"), "invalid_zip_header")
        self.refused(archive(extra=struct.pack("<HH", 0x5455, 0) * 2), "invalid_zip_header")

    def test_local_and_central_header_fields_must_match(self):
        raw = archive()
        changes = ({4: ("H", 10)}, {6: ("H", 8)}, {8: ("H", 0)}, {10: ("H", 1)},
                   {12: ("H", 1)}, {14: ("I", 1)}, {18: ("I", 1)}, {22: ("I", 1)})
        for mutation in changes:
            with self.subTest(mutation=mutation):
                self.refused(edit(raw, local=mutation), "zip_header_mismatch")
        mutated = bytearray(raw)
        mutated[LOCAL.size] = ord("R")
        self.refused(bytes(mutated), "zip_header_mismatch")

    def test_crc_checks_raw_uncompressed_bytes_not_just_headers(self):
        raw = archive(method=zipfile.ZIP_STORED)
        self.refused(edit(raw, local={14: ("I", 1)}, central={16: ("I", 1)}), "zip_crc_mismatch")
        mutated = bytearray(raw)
        mutated[LOCAL.size + len("receipt.json")] ^= 1
        self.refused(bytes(mutated), "zip_crc_mismatch")

    def test_deflate_truncation_trailing_stream_and_false_small_size_are_rejected(self):
        data = b"authored payload"
        payload = deflate(data)
        for compressed, plaintext, expanded in (
            (payload[:-1], data, len(data)),
            (payload + b"hidden trailing bytes", data, len(data)),
            (payload + payload, data, len(data)),
            (b"\x06", data, len(data)),
            # Matching prefix CRC and declared size must not hide expansion.
            (deflate(b"x" * (1024 * 1024)), b"x", 1),
        ):
            with self.subTest(compressed=compressed[:16]):
                self.refused(raw_member(compressed, plaintext, expanded=expanded),
                             "invalid_zip_compressed_data", expected_paths=("receipt.json",))

    def test_declared_expanded_size_cannot_exceed_or_undershoot_actual_bytes(self):
        data = b"authored payload"
        self.refused(raw_member(deflate(data), data, expanded=len(data) + 1),
                     "zip_size_mismatch", expected_paths=("receipt.json",))
        self.refused(raw_member(deflate(data), data, expanded=len(data) - 1),
                     "invalid_zip_compressed_data", expected_paths=("receipt.json",))

    def test_signature_and_signatureless_data_descriptors_are_validated(self):
        data = b"authored streamed payload"
        for size in (12, 16):
            raw = raw_member(deflate(data), data, descriptor=size)
            self.assertEqual(data, self.read(raw, expected_paths=("receipt.json",)).files["receipt.json"])
            central_offset = END.unpack_from(raw, len(raw) - END.size)[6]
            changed = bytearray(raw)
            changed[central_offset - 1] ^= 1
            self.refused(bytes(changed), "zip_header_mismatch", expected_paths=("receipt.json",))

    def test_ratio_limit_refuses_small_compressed_large_expansion(self):
        raw = archive([("receipt.json", b"x" * 10000)])
        self.refused(raw, "zip_compression_ratio", expected_paths=("receipt.json",))
        raw = archive([("receipt.json", b"x" * 1000)])
        self.refused(raw, "zip_compression_ratio", expected_paths=("receipt.json",),
                     limits=replace(ArtifactZipLimits(), max_compression_ratio=2))
        self.assertEqual(dict(FILES), self.read(archive(method=zipfile.ZIP_STORED),
                         limits=replace(ArtifactZipLimits(), max_compression_ratio=1)).files)

    def test_policy_and_local_size_limits_apply_at_exact_boundaries(self):
        raw = archive()
        expanded = sum(len(data) for _, data in FILES)
        policy = {"artifact_bytes": len(raw), "expanded_bytes": expanded, "archive_members": 2}
        limits = replace(ArtifactZipLimits(), max_archive_bytes=len(raw), max_expanded_bytes=expanded,
                         max_member_bytes=max(len(data) for _, data in FILES), max_members=2)
        self.assertEqual(dict(FILES), self.read(raw, policy_limits=policy, limits=limits).files)
        for name in ("artifact_bytes", "expanded_bytes"):
            with self.subTest(name=name):
                self.refused(raw, "zip_resource_limit", policy_limits={**policy, name: policy[name] - 1})
        for name in ("max_archive_bytes", "max_expanded_bytes", "max_member_bytes"):
            with self.subTest(name=name):
                self.refused(raw, "zip_resource_limit", limits=replace(limits, **{name: getattr(limits, name) - 1}))
        self.refused(raw, "zip_resource_limit", policy_limits={**policy, "archive_members": 1}, expected_paths=("receipt.json",))

    def test_invalid_limits_inputs_and_expected_paths_are_refused(self):
        raw = archive()
        for policy in (None, {}, {**POLICY, "artifact_bytes": True}, {**POLICY, "expanded_bytes": 0},
                       {**POLICY, "archive_members": 2**53}, {**POLICY, "artifact_bytes": "25"}):
            self.refused(raw, "invalid_zip_limits", policy_limits=policy)
        for limits in (None, {}, replace(ArtifactZipLimits(), max_archive_bytes=True),
                       replace(ArtifactZipLimits(), max_compression_ratio=101),
                       replace(ArtifactZipLimits(), max_member_bytes=26 * 1024 * 1024)):
            self.refused(raw, "invalid_zip_limits", limits=limits)
        for expected in (None, [], (), ("receipt.json", "RECEIPT.JSON")):
            self.refused(raw, "invalid_zip_expected_paths", expected_paths=expected)
        self.refused(raw.decode("latin1"), "invalid_zip_input")
        self.refused(bytearray(raw), "invalid_zip_input")

    def test_zip64_split_comments_and_self_extracting_layouts_are_unsupported(self):
        raw = archive()
        target = io.BytesIO()
        with zipfile.ZipFile(target, "w") as output:
            with output.open("receipt.json", "w", force_zip64=True) as member:
                member.write(b"authored")
        self.refused(target.getvalue(), "unsupported_zip_format", expected_paths=("receipt.json",))
        self.refused(archive(comment=b"authored comment"), "unsupported_zip_format")
        for offset, value in ((4, 1), (6, 1)):
            changed = bytearray(raw)
            struct.pack_into("<H", changed, len(raw) - END.size + offset, value)
            self.refused(bytes(changed), "unsupported_zip_format")
        for changed in (b"prefix" + raw, raw + b"trailing", raw + raw):
            self.refused(changed)

    def test_directory_offsets_overlaps_and_unreferenced_data_are_refused(self):
        raw = archive()
        self.refused(edit(raw, central={42: ("I", 1)}), "invalid_zip_header")
        self.refused(edit(raw, central={42: ("I", 0)}, index=1), "invalid_zip_header")
        # Out-of-range offsets must fail as domain errors, never struct.error.
        self.refused(edit(raw, central={42: ("I", 0xFFFFFF00)}, index=1), "invalid_zip_header")
        self.refused(edit(raw, central={20: ("I", 0xFFFF0000)}), "invalid_zip_header")
        changed = bytearray(raw)
        end_offset = len(raw) - END.size
        central_offset = END.unpack_from(raw, end_offset)[6]
        struct.pack_into("<I", changed, end_offset + 16, central_offset + 1)
        self.refused(bytes(changed), "invalid_zip_header")

    def test_central_order_does_not_change_member_identity(self):
        raw = archive()
        records = central_records(raw)
        start = records[0][0]
        reordered = raw[:start] + b"".join(raw[p:p + size] for p, size, _ in reversed(records)) + raw[-END.size:]
        self.assertEqual(dict(FILES), self.read(reordered).files)

    def test_extra_metadata_and_path_limits_are_bounded(self):
        raw = archive(extra=struct.pack("<HHBI", 0x5455, 5, 1, 1))
        self.refused(raw, "zip_resource_limit", limits=replace(ArtifactZipLimits(), max_extra_bytes=8))
        self.refused(archive(), "unsafe_zip_path", limits=replace(ArtifactZipLimits(), max_path_chars=11))

    def test_empty_regular_files_are_bytes_not_inferred_success(self):
        files = [("receipt.json", b""), ("junit.xml", b"")]
        for method in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            observed = self.read(archive(files, method=method))
            self.assertEqual(dict(files), observed.files)
            self.assertFalse(hasattr(observed, "binding"))
            self.assertFalse(hasattr(observed, "ready_for_review"))


if __name__ == "__main__":
    unittest.main()
