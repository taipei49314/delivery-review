"""Authored Snappy block controls based on Google's public format description."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from delivery_review.adapters.bundle_encoding import (
    BundleEncodingError, MAX_BUNDLE_BYTES, decode_bundle_body, decode_snappy_block,
)


def varint(value):
    result = bytearray()
    while value >= 128:
        result.append((value & 127) | 128)
        value >>= 7
    return bytes(result + bytes([value]))


def literal(raw):
    length = len(raw) - 1
    if length < 60:
        header = bytes([length << 2])
    else:
        width = max(1, (length.bit_length() + 7) // 8)
        header = bytes([(59 + width) << 2]) + length.to_bytes(width, "little")
    return varint(len(raw)) + header + raw


class BundleEncodingTests(unittest.TestCase):
    def test_literal_boundaries_return_identical_bytes(self):
        for size in (1, 60, 61, 256, 257, 65536, 65537):
            pattern = b"authored raw JSON is not rewritten\r\n"
            raw = (pattern * ((size + len(pattern) - 1) // len(pattern)))[:size]
            self.assertEqual(size, len(raw))
            self.assertEqual(raw, decode_snappy_block(literal(raw)))
        self.assertEqual(b"", decode_snappy_block(b"\0"))

    def test_all_copy_forms_preserve_overlap_semantics(self):
        # Literal 'xab', then offset2/length4, produces the specification's RLE.
        self.assertEqual(b"xababab", decode_snappy_block(b"\x07\x08xab\x01\x02"))
        self.assertEqual(b"xababab", decode_snappy_block(b"\x07\x08xab\x0e\x02\x00"))
        self.assertEqual(b"xababab", decode_snappy_block(b"\x07\x08xab\x0f\x02\x00\x00\x00"))
        raw = b"x" * 256 + b"yz"
        # Copy1 high offset bits: offset258/length4 -> 'xxxx'.
        encoded = literal(raw)
        payload = encoded[len(varint(len(raw))):]
        self.assertEqual(raw + b"xxxx", decode_snappy_block(varint(len(raw) + 4) + payload + b"\x21\x02"))

    def test_copy_lengths_larger_than_offsets_and_max_64_copy(self):
        self.assertEqual(b"x" * 65, decode_snappy_block(b"\x41\x00x\xfe\x01\0"))
        self.assertEqual(b"abc" + (b"abc" * 22)[:64], decode_snappy_block(b"\x43\x08abc\xfe\x03\0"))

    def test_truncated_header_literal_copy_and_invalid_offsets_refused(self):
        bad = [b"\x80", b"\x80\x80\x80\x80\x10", b"\x80\x80\x80\x80\x80\0",
               b"\x01\xf0", b"\x01\xf4\0", b"\x01\xf8\0\0", b"\x01\xfc\0\0\0",
               b"\x02\x04x", b"\x05\x00x\x01", b"\x05\x00x\x02\x01",
               b"\x05\x00x\x03\x01\0\0", b"\x05\x00x\x01\0", b"\x05\x00x\x01\x02"]
        for raw in bad:
            with self.subTest(raw=raw), self.assertRaises(BundleEncodingError):
                decode_snappy_block(raw)

    def test_exact_declared_length_and_no_trailing_frame_or_second_block(self):
        for raw in (b"\0x", b"\x02\x00x", b"\x01\x04xy", b"\x01\x00x\0",
                    literal(b"x") + literal(b"y"), b"\xff\x06\0\0sNaPpY"):
            with self.subTest(raw=raw), self.assertRaises(BundleEncodingError):
                decode_snappy_block(raw)

    def test_declared_output_cap_precedes_allocation_and_limits_cannot_rise(self):
        for raw, limit in ((varint(MAX_BUNDLE_BYTES + 1), MAX_BUNDLE_BYTES),
                           (varint(101), 100), (literal(b"ok"), 1)):
            with self.assertRaises(BundleEncodingError):
                decode_snappy_block(raw, max_output_bytes=limit)
        for limit in (True, 0, MAX_BUNDLE_BYTES + 1):
            with self.assertRaises(BundleEncodingError):
                decode_snappy_block(literal(b"x"), max_output_bytes=limit)
        with self.assertRaises(BundleEncodingError):
            decode_snappy_block(b"x" * (MAX_BUNDLE_BYTES + 1))

    def test_explicit_media_type_preserves_json_and_never_guesses_compression(self):
        raw = b'{"original":"whitespace"}\r\n'
        for content_type in (None, "application/json", "application/json; charset=utf-8"):
            self.assertEqual(raw, decode_bundle_body(raw, content_type))
        self.assertEqual(raw, decode_bundle_body(literal(raw), "application/x-snappy"))
        self.assertEqual(literal(raw), decode_bundle_body(literal(raw), None))
        for value in ("application/gzip", "application/x-snappy; unknown=1", "text/html", []):
            with self.assertRaises(BundleEncodingError):
                decode_bundle_body(raw, value)


if __name__ == "__main__":
    unittest.main()
