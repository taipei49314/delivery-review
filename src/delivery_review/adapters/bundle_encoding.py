"""Bounded decoding of GitHub's Snappy block attestation storage encoding.

No framing, compression detection or JSON rewriting is performed. The caller
keeps original transport bytes and content type; these decoded bytes are inputs
to the independent official signature verifier, never proof themselves.
"""
from __future__ import annotations

from ..contracts import ContractError

MAX_BUNDLE_BYTES = 2 * 1024 * 1024
DECODER_VERSION = 1


class BundleEncodingError(ContractError):
    pass


def _need(condition, code):
    if not condition:
        raise BundleEncodingError(code)


def decode_snappy_block(raw: bytes, *, max_output_bytes=MAX_BUNDLE_BYTES) -> bytes:
    """Decode the Google Snappy block format within declared and hard bounds."""
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_BUNDLE_BYTES, "bundle_encoded_size_limit")
    _need(type(max_output_bytes) is int and 1 <= max_output_bytes <= MAX_BUNDLE_BYTES, "invalid_bundle_decode_limit")
    position, declared = 0, 0
    for index in range(5):
        _need(position < len(raw), "snappy_header_truncated")
        value = raw[position]
        position += 1
        _need(index < 4 or value <= 15, "snappy_length_invalid")
        declared |= (value & 127) << (index * 7)
        if value < 128:
            break
    else:
        raise BundleEncodingError("snappy_length_invalid")
    _need(declared <= max_output_bytes, "bundle_decoded_size_limit")
    output = bytearray()
    while position < len(raw):
        _need(len(output) < declared, "snappy_trailing_data")
        tag = raw[position]
        position += 1
        kind = tag & 3
        if kind == 0:
            length = tag >> 2
            if length >= 60:
                width = length - 59
                _need(position + width <= len(raw), "snappy_literal_header_truncated")
                length = int.from_bytes(raw[position:position + width], "little")
                position += width
            length += 1
            _need(length <= declared - len(output), "snappy_output_length_mismatch")
            _need(position + length <= len(raw), "snappy_literal_truncated")
            output.extend(raw[position:position + length])
            position += length
            continue
        width = 1 if kind == 1 else 2 if kind == 2 else 4
        _need(position + width <= len(raw), "snappy_copy_truncated")
        offset = int.from_bytes(raw[position:position + width], "little")
        position += width
        if kind == 1:
            offset |= (tag & 224) << 3
            length = 4 + ((tag >> 2) & 7)
        else:
            length = 1 + (tag >> 2)
        _need(0 < offset <= len(output), "snappy_copy_offset_invalid")
        _need(length <= declared - len(output), "snappy_output_length_mismatch")
        # Copies are at most 64 bytes. Repeating the bounded suffix preserves
        # overlap semantics without a byte-by-byte loop or unbounded allocation.
        start = len(output) - offset
        pattern = output[start:start + min(offset, length)]
        output.extend((pattern * ((length + len(pattern) - 1) // len(pattern)))[:length])
    _need(len(output) == declared, "snappy_output_length_mismatch")
    return bytes(output)


def decode_bundle_body(raw: bytes, content_type: str | None, *, max_output_bytes=MAX_BUNDLE_BYTES) -> bytes:
    """Use an explicit qualified media type, never guess from body contents."""
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_BUNDLE_BYTES, "bundle_encoded_size_limit")
    _need(type(max_output_bytes) is int and 1 <= max_output_bytes <= MAX_BUNDLE_BYTES, "invalid_bundle_decode_limit")
    _need(content_type is None or type(content_type) is str and len(content_type) <= 1024,
          "unsupported_bundle_encoding")
    if content_type == "application/x-snappy":
        return decode_snappy_block(raw, max_output_bytes=max_output_bytes)
    _need(content_type in {None, "application/json", "application/json; charset=utf-8"}, "unsupported_bundle_encoding")
    _need(len(raw) <= max_output_bytes, "bundle_decoded_size_limit")
    return raw
