"""
protolib/protobuf/wire.py

The actual byte-level wire format of Google Protocol Buffers, as
specified at https://protobuf.dev/programming-guides/encoding/ .

This is deliberately independent from protolib's existing varint
(protolib/primitives.py, `_varint_read`/`_varint_write`): that one
implements the Minecraft/node-protodef style LEB128 varint, which
truncates to a fixed `max_bits` and interprets the sign via two's
complement truncation. Protobuf's varint has different rules:

  - A plain `varint` wire-type value is unsigned at the wire level and,
    per the spec, is NEVER truncated to 32/64 bits while decoding raw
    bytes -- the spec explicitly allows (and real encoders/decoders
    must accept) up to 10 bytes for any varint, even one that will be
    interpreted as a 32-bit field, because a negative int32 is encoded
    as its 64-bit two's-complement form (see `_encode_int32`/
    `_encode_int64` below).
  - `sint32`/`sint64` use ZIGZAG encoding on top of the same raw
    varint bytes, which is a completely different mapping from
    negative numbers to bit patterns than two's-complement truncation.
  - `fixed32`/`fixed64`/`sfixed32`/`sfixed64`/`float`/`double` are
    fixed-width, LITTLE-endian (unlike protolib's existing fixed-width
    ints, which follow the protocol.yml's declared byte order and
    default to big-endian) -- protobuf's wire format is always
    little-endian for these, unconditionally, per spec.

Reusing protolib's Reader/Writer (protolib/io.py) since those are
already fully generic byte-cursor primitives with no protodef-specific
assumptions baked in.
"""

from __future__ import annotations

import struct

from ..io import Reader, Writer, BufferUnderrun
from .errors import ProtobufDecodeError, VarintTooLongError

# Wire types, per the encoding spec. GROUP_START/GROUP_END (3/4) are
# proto2-only and deprecated by Google; kept here only so a decoder
# encountering one in the wild raises a clear, specific error instead
# of miscategorizing it as an unknown/invalid wire type.
WIRETYPE_VARINT = 0   # int32, int64, uint32, uint64, sint32, sint64, bool, enum
WIRETYPE_I64 = 1      # fixed64, sfixed64, double
WIRETYPE_LEN = 2      # string, bytes, embedded messages, packed repeated fields
WIRETYPE_GROUP_START = 3   # deprecated (proto2 groups)
WIRETYPE_GROUP_END = 4     # deprecated (proto2 groups)
WIRETYPE_I32 = 5      # fixed32, sfixed32, float

_VALID_WIRETYPES = frozenset({
    WIRETYPE_VARINT, WIRETYPE_I64, WIRETYPE_LEN, WIRETYPE_I32,
})

# A raw varint is capped at 10 bytes: ceil(64 / 7) == 10. This is the
# spec's own limit for a 64-bit payload; anything longer is either
# malformed input or a decoder desync, never a legitimate value.
MAX_VARINT_BYTES = 10


# ---------------------------------------------------------------------------
# Raw (unsigned) varint -- the base encoding every other varint-based
# protobuf type builds on top of.
# ---------------------------------------------------------------------------

def read_raw_varint(r: Reader) -> int:
    """Reads a raw base-128 varint and returns its unsigned integer
    value exactly as encoded on the wire (no truncation to 32/64 bits,
    no sign interpretation -- callers apply that afterwards, since
    different protobuf types interpret the same raw bits differently)."""
    result = 0
    shift = 0
    count = 0
    while True:
        byte = r.read_bytes(1)[0]
        count += 1
        if count > MAX_VARINT_BYTES:
            raise VarintTooLongError(r.offset - count, count)
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return result


def write_raw_varint(value: int, w: Writer) -> None:
    """Writes a non-negative integer as a raw base-128 varint. Negative
    values must be pre-converted by the caller (int32/int64 sign-extend
    to 64 bits first; sint32/sint64 zigzag-encode first) -- this
    function only ever emits the unsigned wire bytes."""
    if value < 0:
        raise ValueError(
            f"write_raw_varint received a negative value ({value!r}); "
            "the caller must sign-extend (int32/int64) or zigzag-encode "
            "(sint32/sint64) before calling this"
        )
    out = bytearray()
    v = value
    while True:
        byte = v & 0x7F
        v >>= 7
        if v:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    w.write_bytes(bytes(out))


def raw_varint_size(value: int) -> int:
    """Byte length write_raw_varint(value, ...) would produce, without
    actually writing anything -- used to compute LEN-prefixed sizes."""
    if value < 0:
        raise ValueError(f"raw_varint_size received a negative value ({value!r})")
    size = 1
    v = value >> 7
    while v:
        size += 1
        v >>= 7
    return size


# ---------------------------------------------------------------------------
# Tags: (field_number << 3) | wire_type
# ---------------------------------------------------------------------------

def read_tag(r: Reader) -> tuple[int, int]:
    """Reads a field tag and returns (field_number, wire_type)."""
    raw = read_raw_varint(r)
    wire_type = raw & 0x07
    field_number = raw >> 3
    if field_number == 0:
        # Field number 0 is reserved and never valid on the wire --
        # per spec, field numbers start at 1. A 0 here almost always
        # means we're not actually looking at the start of a tag
        # (buffer desync / wrong offset), not a legitimate empty message.
        raise ProtobufDecodeError(
            f"invalid tag at offset {r.offset - raw_varint_size(raw)}: "
            f"field number 0 is reserved and never appears on the wire "
            f"(raw tag byte(s) decoded to {raw})"
        )
    if wire_type not in _VALID_WIRETYPES:
        kind = "deprecated proto2 group" if wire_type in (3, 4) else "unrecognized"
        raise ProtobufDecodeError(
            f"invalid tag at offset {r.offset - raw_varint_size(raw)}: "
            f"field {field_number} has {kind} wire type {wire_type}"
        )
    return field_number, wire_type


def write_tag(field_number: int, wire_type: int, w: Writer) -> None:
    if field_number < 1:
        raise ValueError(f"field number must be >= 1, got {field_number}")
    if wire_type not in _VALID_WIRETYPES:
        raise ValueError(f"unsupported wire type {wire_type}")
    write_raw_varint((field_number << 3) | wire_type, w)


def tag_size(field_number: int, wire_type: int) -> int:
    return raw_varint_size((field_number << 3) | wire_type)


# ---------------------------------------------------------------------------
# Zigzag encoding, for sint32 / sint64
# ---------------------------------------------------------------------------

def zigzag_encode(value: int, bits: int) -> int:
    """Maps a signed integer to an unsigned one so small-magnitude
    negatives stay small on the wire: 0,-1,1,-2,2,... -> 0,1,2,3,4,...
    Per spec: (n << 1) ^ (n >> (bits-1)) using arithmetic (sign-
    extending) right shift, which Python's `>>` already is for ints."""
    return (value << 1) ^ (value >> (bits - 1))


def zigzag_decode(value: int) -> int:
    """Inverse of zigzag_encode: (n >> 1) ^ -(n & 1)."""
    return (value >> 1) ^ -(value & 1)


# ---------------------------------------------------------------------------
# int32 / int64 / uint32 / uint64 / sint32 / sint64 / bool / enum
# (all WIRETYPE_VARINT)
# ---------------------------------------------------------------------------

_U64_MASK = (1 << 64) - 1
_U32_MASK = (1 << 32) - 1


def read_int64(r: Reader) -> int:
    """int64: raw varint reinterpreted as a signed 64-bit two's
    complement value (NOT zigzag -- see read_sint64 for that)."""
    raw = read_raw_varint(r) & _U64_MASK
    return raw - (1 << 64) if raw & (1 << 63) else raw


def write_int64(value: int, w: Writer) -> None:
    # A negative int64 (or int32, per write_int32 below) is still
    # encoded as a FULL 10-byte varint of its 64-bit two's-complement
    # form -- this is intentional per spec (not a bug, however
    # wasteful): protobuf never truncates a varint's byte length based
    # on the declared field width, only sint32/sint64 (zigzag) are
    # efficient for negative numbers.
    write_raw_varint(value & _U64_MASK, w)


def read_int32(r: Reader) -> int:
    """int32: same wire representation as int64 (a negative value is
    STILL sign-extended to the full 64-bit varint on the wire per
    spec), truncated to the low 32 bits and reinterpreted as signed."""
    raw = read_raw_varint(r) & _U32_MASK
    return raw - (1 << 32) if raw & (1 << 31) else raw


def write_int32(value: int, w: Writer) -> None:
    # Sign-extend to 64 bits first, exactly as protoc-generated code
    # does, so a negative int32 round-trips through the full 10-byte
    # varint form instead of a truncated (and non-spec-compliant) one.
    write_raw_varint(value & _U64_MASK, w)


def read_uint32(r: Reader) -> int:
    return read_raw_varint(r) & _U32_MASK


def write_uint32(value: int, w: Writer) -> None:
    if not (0 <= value <= _U32_MASK):
        raise ValueError(f"uint32 value {value!r} out of range [0, {_U32_MASK}]")
    write_raw_varint(value, w)


def read_uint64(r: Reader) -> int:
    return read_raw_varint(r) & _U64_MASK


def write_uint64(value: int, w: Writer) -> None:
    if not (0 <= value <= _U64_MASK):
        raise ValueError(f"uint64 value {value!r} out of range [0, {_U64_MASK}]")
    write_raw_varint(value, w)


def read_sint32(r: Reader) -> int:
    return zigzag_decode(read_raw_varint(r) & _U32_MASK)


def write_sint32(value: int, w: Writer) -> None:
    write_raw_varint(zigzag_encode(value, 32) & _U32_MASK, w)


def read_sint64(r: Reader) -> int:
    return zigzag_decode(read_raw_varint(r) & _U64_MASK)


def write_sint64(value: int, w: Writer) -> None:
    write_raw_varint(zigzag_encode(value, 64) & _U64_MASK, w)


def read_bool(r: Reader) -> bool:
    # Per spec, any non-zero varint decodes as true -- a decoder must
    # NOT reject a bool field encoded with a value other than exactly 1
    # (some encoders/languages may emit any non-zero value).
    return read_raw_varint(r) != 0


def write_bool(value: bool, w: Writer) -> None:
    write_raw_varint(1 if value else 0, w)


# ---------------------------------------------------------------------------
# fixed32 / sfixed32 / float / fixed64 / sfixed64 / double
# (WIRETYPE_I32 / WIRETYPE_I64, always little-endian per spec)
# ---------------------------------------------------------------------------

def read_fixed32(r: Reader) -> int:
    return struct.unpack("<I", r.read_bytes(4))[0]


def write_fixed32(value: int, w: Writer) -> None:
    w.write_bytes(struct.pack("<I", value))


def read_sfixed32(r: Reader) -> int:
    return struct.unpack("<i", r.read_bytes(4))[0]


def write_sfixed32(value: int, w: Writer) -> None:
    w.write_bytes(struct.pack("<i", value))


def read_float(r: Reader) -> float:
    return struct.unpack("<f", r.read_bytes(4))[0]


def write_float(value: float, w: Writer) -> None:
    w.write_bytes(struct.pack("<f", value))


def read_fixed64(r: Reader) -> int:
    return struct.unpack("<Q", r.read_bytes(8))[0]


def write_fixed64(value: int, w: Writer) -> None:
    w.write_bytes(struct.pack("<Q", value))


def read_sfixed64(r: Reader) -> int:
    return struct.unpack("<q", r.read_bytes(8))[0]


def write_sfixed64(value: int, w: Writer) -> None:
    w.write_bytes(struct.pack("<q", value))


def read_double(r: Reader) -> float:
    return struct.unpack("<d", r.read_bytes(8))[0]


def write_double(value: float, w: Writer) -> None:
    w.write_bytes(struct.pack("<d", value))


# ---------------------------------------------------------------------------
# LEN-prefixed values: string, bytes, embedded messages, packed repeated
# ---------------------------------------------------------------------------

def read_len_delimited(r: Reader) -> bytes:
    """Reads a LEN-prefixed value's raw bytes (the length varint
    followed by that many bytes) -- used as-is for `bytes`, decoded as
    UTF-8 for `string`, and recursively parsed for embedded messages
    and packed repeated fields."""
    length = read_raw_varint(r)
    if length < 0:
        raise ProtobufDecodeError(f"negative LEN length ({length}) at offset {r.offset}")
    return r.read_bytes(length)


def write_len_delimited(data: bytes, w: Writer) -> None:
    write_raw_varint(len(data), w)
    w.write_bytes(data)


def read_string(r: Reader) -> str:
    raw = read_len_delimited(r)
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtobufDecodeError(
            f"string field is not valid UTF-8 ({exc}); protobuf requires "
            "string fields to be valid UTF-8 -- use `bytes` instead if "
            "the field may carry arbitrary binary data"
        ) from exc


def write_string(value: str, w: Writer) -> None:
    write_len_delimited(value.encode("utf-8"), w)


# ---------------------------------------------------------------------------
# Skipping an unknown field -- required for forward compatibility
# (a message may contain fields the schema doesn't know about, e.g.
# when reading data written by a newer version of a .proto file: the
# spec requires these to be preserved/skipped, not treated as an error).
# ---------------------------------------------------------------------------

def skip_field(wire_type: int, r: Reader) -> None:
    if wire_type == WIRETYPE_VARINT:
        read_raw_varint(r)
    elif wire_type == WIRETYPE_I64:
        r.read_bytes(8)
    elif wire_type == WIRETYPE_LEN:
        read_len_delimited(r)
    elif wire_type == WIRETYPE_I32:
        r.read_bytes(4)
    else:
        raise ProtobufDecodeError(f"cannot skip unknown wire type {wire_type}")
