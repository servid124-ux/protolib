"""
protolib/primitives.py

Primitive types: don't depend on protocol.json, they're the most basic
byte blocks (fixed-size integers, varints, floats, bool, strings).

Each primitive exposes:
    read(reader: Reader) -> Any
    write(value: Any, writer: Writer) -> None
    size_of(value: Any) -> int

They're registered in a PRIMITIVES dict { name: Primitive } which the
main engine (core.py) then uses as base types resolvable by name.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Callable

from .io import Reader, Writer


@dataclass(frozen=True)
class Primitive:
    name: str
    read: Callable[[Reader], Any]
    write: Callable[[Any, Writer], None]
    size_of: Callable[[Any], int] | None = None


def _fixed_size_primitive(name: str, fmt: str, little_endian: bool = False) -> Primitive:
    size = struct.calcsize(fmt)
    full_fmt = ("<" if little_endian else ">") + fmt

    def read(r: Reader) -> Any:
        data = r.read_bytes(size)
        return struct.unpack(full_fmt, data)[0]

    def write(value: Any, w: Writer) -> None:
        w.write_bytes(struct.pack(full_fmt, value))

    return Primitive(name=name, read=read, write=write, size_of=lambda v: size)


# ---------------------------------------------------------------------------
# Fixed-size big-endian integers (standard in Minecraft protocols)
# ---------------------------------------------------------------------------

i8 = _fixed_size_primitive("i8", "b")
u8 = _fixed_size_primitive("u8", "B")
i16 = _fixed_size_primitive("i16", "h")
u16 = _fixed_size_primitive("u16", "H")
i32 = _fixed_size_primitive("i32", "i")
u32 = _fixed_size_primitive("u32", "I")
i64 = _fixed_size_primitive("i64", "q")
u64 = _fixed_size_primitive("u64", "Q")
f32 = _fixed_size_primitive("f32", "f")
f64 = _fixed_size_primitive("f64", "d")
# f16: IEEE 754 binary16 (half-float), 2 bytes. struct soporta 'e' de forma
# nativa desde Python 3.6 -- mismo helper genérico que f32/f64, sin código
# a mano. Usado en algunos protocolos modernos para posición/rotación
# compacta (p.ej. delta de rotación en paquetes de movimiento) donde f32
# desperdicia precisión que nunca se necesita. Rango ~6.1e-5 a 65504,
# ~3 dígitos decimales de precisión -- si el valor no entra o pierde
# demasiada precisión, struct redondea/satura silenciosamente al empaquetar
# (comportamiento nativo de 'e', no se agrega chequeo extra acá).
f16 = _fixed_size_primitive("f16", "e")

# little-endian (used by RakNet and some fields in older protocols)
# li8/lu8 are aliases of i8/u8: 1 byte has no endianness, but node-protodef
# exposes them under these names so protocol.json can reference them just
# like any other li*/lu* pair, with no special case.
li8 = _fixed_size_primitive("li8", "b")
lu8 = _fixed_size_primitive("lu8", "B")
li16 = _fixed_size_primitive("li16", "h", little_endian=True)
lu16 = _fixed_size_primitive("lu16", "H", little_endian=True)
li32 = _fixed_size_primitive("li32", "i", little_endian=True)
lu32 = _fixed_size_primitive("lu32", "I", little_endian=True)
li64 = _fixed_size_primitive("li64", "q", little_endian=True)
lu64 = _fixed_size_primitive("lu64", "Q", little_endian=True)
lf32 = _fixed_size_primitive("lf32", "f", little_endian=True)
lf64 = _fixed_size_primitive("lf64", "d", little_endian=True)
lf16 = _fixed_size_primitive("lf16", "e", little_endian=True)


# ---------------------------------------------------------------------------
# 3, 5, 6, and 7-byte integers (u24/i24, u40/i40, u48/i48, u56/i56) -- struct
# doesn't support these natively, so they're built by hand with
# int.from_bytes / int.to_bytes. u24 shows up in RakNet (split-packet
# count, message index, sequence number, etc.) and in several older
# Minecraft/Bedrock protocols. u40/u48/u56 round out the family (together
# with u8/u16/u24/u32/u64 already covered above) for any protocol that
# uses a non-power-of-2 integer width.
# ---------------------------------------------------------------------------

def _int_n_bytes_primitive(name, n_bytes, signed, little_endian=False):
    byteorder = "little" if little_endian else "big"

    def read(r):
        data = r.read_bytes(n_bytes)
        return int.from_bytes(data, byteorder=byteorder, signed=signed)

    def write(value, w):
        w.write_bytes(int(value).to_bytes(n_bytes, byteorder=byteorder, signed=signed))

    return Primitive(name=name, read=read, write=write, size_of=lambda v: n_bytes)


# big-endian (Minecraft standard)
u24 = _int_n_bytes_primitive("u24", 3, signed=False)
i24 = _int_n_bytes_primitive("i24", 3, signed=True)
u40 = _int_n_bytes_primitive("u40", 5, signed=False)
i40 = _int_n_bytes_primitive("i40", 5, signed=True)
u48 = _int_n_bytes_primitive("u48", 6, signed=False)
i48 = _int_n_bytes_primitive("i48", 6, signed=True)
u56 = _int_n_bytes_primitive("u56", 7, signed=False)
i56 = _int_n_bytes_primitive("i56", 7, signed=True)

# little-endian (RakNet sends several of these fields in LE, e.g. 3-byte
# message index / sequence number / order index)
lu24 = _int_n_bytes_primitive("lu24", 3, signed=False, little_endian=True)
li24 = _int_n_bytes_primitive("li24", 3, signed=True, little_endian=True)
lu40 = _int_n_bytes_primitive("lu40", 5, signed=False, little_endian=True)
li40 = _int_n_bytes_primitive("li40", 5, signed=True, little_endian=True)
lu48 = _int_n_bytes_primitive("lu48", 6, signed=False, little_endian=True)
li48 = _int_n_bytes_primitive("li48", 6, signed=True, little_endian=True)
lu56 = _int_n_bytes_primitive("lu56", 7, signed=False, little_endian=True)
li56 = _int_n_bytes_primitive("li56", 7, signed=True, little_endian=True)


# ---------------------------------------------------------------------------
# UUID (128 bits, standard Minecraft format: 16 raw big-endian bytes,
# no dashes when reading/writing binary -- the dashed string is just the
# human-readable textual representation)
# ---------------------------------------------------------------------------

import uuid as _uuid_module


def _uuid_read(r: Reader) -> str:
    data = r.read_bytes(16)
    return str(_uuid_module.UUID(bytes=data))


def _uuid_write(value: str, w: Writer) -> None:
    if isinstance(value, _uuid_module.UUID):
        parsed = value
    else:
        parsed = _uuid_module.UUID(str(value))
    w.write_bytes(parsed.bytes)


uuid_ = Primitive("UUID", _uuid_read, _uuid_write, lambda v: 16)


# ---------------------------------------------------------------------------
# restBuffer: consumes all remaining bytes in the current buffer.
# Typical as the last field of a container/packet (payload with no
# length prefix, assumed to occupy "everything that's left").
# ---------------------------------------------------------------------------

def _rest_buffer_read(r: Reader) -> bytes:
    return r.read_bytes(r.remaining)


def _rest_buffer_write(value: bytes, w: Writer) -> None:
    w.write_bytes(value or b"")


rest_buffer = Primitive("restBuffer", _rest_buffer_read, _rest_buffer_write,
                          lambda v: len(v or b""))


def _bool_read(r: Reader) -> bool:
    return r.read_bytes(1)[0] != 0


def _bool_write(value: bool, w: Writer) -> None:
    w.write_bytes(b"\x01" if value else b"\x00")


bool_ = Primitive("bool", _bool_read, _bool_write, lambda v: 1)


def _void_read(r: Reader) -> None:
    return None


def _void_write(value: Any, w: Writer) -> None:
    pass


void = Primitive("void", _void_read, _void_write, lambda v: 0)


# ---------------------------------------------------------------------------
# Varint / Varlong (LEB128, Protocol Buffers / Minecraft style)
# ---------------------------------------------------------------------------

def _varint_read_raw(r: Reader, max_bits: int) -> int:
    """Reads the raw unsigned value of up to max_bits bits."""
    result = 0
    shift = 0
    while True:
        byte = r.read_bytes(1)[0]
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            break
        if shift > max_bits + 7:
            raise ValueError(f"varint exceeds {max_bits} bits")
    return result & ((1 << max_bits) - 1)


def _to_signed(value: int, bits: int) -> int:
    sign_bit = 1 << (bits - 1)
    return value - (1 << bits) if value & sign_bit else value


def _varint_read(r: Reader, max_bits: int = 32) -> int:
    raw = _varint_read_raw(r, max_bits)
    return _to_signed(raw, max_bits)


def _varint_write(value: int, w: Writer, max_bits: int = 32) -> None:
    v = value & ((1 << max_bits) - 1)
    out = bytearray()
    while True:
        byte = v & 0x7F
        v >>= 7
        if v != 0:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            break
    w.write_bytes(bytes(out))


def _varint_size_of(value: int, max_bits: int = 32) -> int:
    v = value & ((1 << max_bits) - 1)
    size = 1
    v >>= 7
    while v != 0:
        size += 1
        v >>= 7
    return size


varint = Primitive(
    "varint",
    lambda r: _varint_read(r, 32),
    lambda v, w: _varint_write(v, w, 32),
    lambda v: _varint_size_of(v, 32),
)

varlong = Primitive(
    "varlong",
    lambda r: _varint_read(r, 64),
    lambda v, w: _varint_write(v, w, 64),
    lambda v: _varint_size_of(v, 64),
)

# explicit "unsigned" variants: never interpret the sign bit
# (useful for protocols that document always-positive varints, e.g. large counts)
uvarint = Primitive(
    "uvarint",
    lambda r: _varint_read_raw(r, 32),
    lambda v, w: _varint_write(v, w, 32),
    lambda v: _varint_size_of(v, 32),
)
uvarlong = Primitive(
    "uvarlong",
    lambda r: _varint_read_raw(r, 64),
    lambda v, w: _varint_write(v, w, 64),
    lambda v: _varint_size_of(v, 64),
)

# varint128 / uvarint128: LEB128 extended to 128 bits. Useful for large
# IDs that don't fit in 64 bits (e.g. 128-bit Snowflake IDs, truncated
# hashes, UUIDs encoded as a variable-length integer instead of the
# fixed 16 bytes of UUID). Reuses the same generic varint/varlong
# helper -- only max_bits changes to 128. Takes up at most 19 bytes in
# the worst case (ceil(128/7) = 19).
varint128 = Primitive(
    "varint128",
    lambda r: _varint_read(r, 128),
    lambda v, w: _varint_write(v, w, 128),
    lambda v: _varint_size_of(v, 128),
)
uvarint128 = Primitive(
    "uvarint128",
    lambda r: _varint_read_raw(r, 128),
    lambda v, w: _varint_write(v, w, 128),
    lambda v: _varint_size_of(v, 128),
)


# zigzag varint (protobuf-style: 0,-1,1,-2,2 -> 0,1,2,3,4 before LEB128)
def _zigzag_encode(value: int, bits: int) -> int:
    return ((value << 1) ^ (value >> (bits - 1))) & ((1 << bits) - 1)


def _zigzag_decode(value: int) -> int:
    return (value >> 1) ^ -(value & 1)


zigzag32 = Primitive(
    "zigzag32",
    lambda r: _zigzag_decode(_varint_read_raw(r, 32)),
    lambda v, w: _varint_write(_zigzag_encode(v, 32), w, 32),
    lambda v: _varint_size_of(_zigzag_encode(v, 32), 32),
)
zigzag64 = Primitive(
    "zigzag64",
    lambda r: _zigzag_decode(_varint_read_raw(r, 64)),
    lambda v, w: _varint_write(_zigzag_encode(v, 64), w, 64),
    lambda v: _varint_size_of(_zigzag_encode(v, 64), 64),
)


# ---------------------------------------------------------------------------
# Null-terminated strings (C-style)
# ---------------------------------------------------------------------------

def _cstring_read(r: Reader) -> str:
    out = bytearray()
    while True:
        b = r.read_bytes(1)
        if b == b"\x00":
            break
        out += b
    return out.decode("utf-8")


def _cstring_write(value: str, w: Writer) -> None:
    w.write_bytes(value.encode("utf-8") + b"\x00")


cstring = Primitive(
    "cstring",
    _cstring_read,
    _cstring_write,
    lambda v: len(v.encode("utf-8")) + 1,
)

# Note: a length-prefixed "string" or "buffer" primitive is deliberately
# NOT added here. core.py already handles that as configurable COMPOSITE
# types:
#   - "pstring" with countType (varint, u16, u8, etc.) for strings
#   - "buffer" with countType/count for variable-size buffers
# Duplicating the "buffer" name here would shadow
# self._composite_handlers["buffer"] in core.py, which is more flexible
# because countType is configurable from the .yml/.json instead of being
# hardcoded to varint.


def make_fixed_utf16be_string(length: int) -> Primitive:
    """
    Minecraft Classic: fixed-length strings in characters, UTF-16BE
    encoded and padded with spaces (' ') up to `length`.
    """

    def read(r: Reader) -> str:
        data = r.read_bytes(length * 2)
        return data.decode("utf-16-be").rstrip(" ")

    def write(value: str, w: Writer) -> None:
        padded = value[:length].ljust(length)
        w.write_bytes(padded.encode("utf-16-be"))

    return Primitive(f"fixed_utf16be_{length}", read, write, lambda v: length * 2)


def make_fixed_cp437_string(length: int = 64) -> Primitive:
    """
    Minecraft Classic (protocol 0x07): fixed-length strings in BYTES
    (not characters), CP437 encoded and padded with spaces (' ') up to
    `length`. Used for username, verification key, server name/MOTD,
    chat messages, player name, etc.

    CP437 (not plain ASCII/UTF-8) because the vanilla client supports
    that codepage's extended symbols (includes the '&' color codes).
    """

    def read(r: Reader) -> str:
        data = r.read_bytes(length)
        return data.decode("cp437").rstrip(" ")

    def write(value: str, w: Writer) -> None:
        # errors="replace": a character CP437 can't represent (emoji,
        # etc.) gets swapped for '?' instead of raising
        # UnicodeEncodeError and crashing the server -- this receives
        # arbitrary user text (username, chat), so it can't rely on it
        # always arriving in the expected charset.
        padded = (value or "")[:length].ljust(length)
        w.write_bytes(padded.encode("cp437", errors="replace"))

    return Primitive(f"fixed_cp437_{length}", read, write, lambda v: length)


def make_fixed_buffer(length: int) -> Primitive:
    """
    Raw bytes with an ALWAYS fixed length (no countType/count in the
    yml). Minecraft Classic uses this for the 1024-byte Level Data
    Chunk -- if there are fewer than 1024 real bytes left, the rest
    comes/goes padded with 0x00, but that's handled by whoever builds
    the payload, not this primitive (here we only guarantee reading/
    writing exactly `length`).
    """

    def read(r: Reader) -> bytes:
        return r.read_bytes(length)

    def write(value: bytes, w: Writer) -> None:
        data = (value or b"")[:length].ljust(length, b"\x00")
        w.write_bytes(data)

    return Primitive(f"fixed_buffer_{length}", read, write, lambda v: length)


# ---------------------------------------------------------------------------
# raknetMagic: RakNet's fixed 16-byte "magic" sequence used to mark
# OFFLINE packets (UnconnectedPing, OpenConnectionRequest1/2, etc.) --
# see e.g. https://github.com/vp817/RakNetProtocolDoc, "magic" datatype.
# Constant value, defined by the RakNet protocol itself (not
# configurable per-protocol.json):
#   00 FF FF 00 FE FE FE FE FD FD FD FD 12 34 56 78
#
# Unlike a plain fixed-size buffer, reading validates the bytes match
# and raises MagicMismatchError if they don't -- that check is the
# entire point of this field on the wire (a mismatch means "this isn't
# really a RakNet packet"), so silently accepting whatever 16 bytes
# happen to be there would defeat the field's purpose. The value
# passed to write() is ignored (there's only one valid value); write()
# always emits the constant, mirroring how a caller building a packet
# doesn't need to remember/supply the magic bytes by hand.
# ---------------------------------------------------------------------------

RAKNET_MAGIC = bytes([
    0x00, 0xFF, 0xFF, 0x00, 0xFE, 0xFE, 0xFE, 0xFE,
    0xFD, 0xFD, 0xFD, 0xFD, 0x12, 0x34, 0x56, 0x78,
])


def _raknet_magic_read(r: Reader) -> bytes:
    from .errors import MagicMismatchError

    got = r.read_bytes(16)
    if got != RAKNET_MAGIC:
        raise MagicMismatchError(RAKNET_MAGIC, got)
    return got


def _raknet_magic_write(value: bytes | None, w: Writer) -> None:
    # value is ignored on purpose -- see docstring above the constant.
    w.write_bytes(RAKNET_MAGIC)


raknet_magic = Primitive("raknetMagic", _raknet_magic_read, _raknet_magic_write,
                          lambda v: 16)


from . import nbt as _nbt

nbt_ = Primitive("nbt", _nbt.read_nbt, _nbt.write_nbt)
optional_nbt = Primitive("optionalNbt", _nbt.read_optional_nbt, _nbt.write_optional_nbt)
anonymous_nbt = Primitive("anonymousNbt", _nbt.read_anonymous_nbt, _nbt.write_anonymous_nbt)
anon_optional_nbt = Primitive(
    "anonOptionalNbt", _nbt.read_anon_optional_nbt, _nbt.write_anon_optional_nbt
)
compressed_nbt = Primitive(
    "compressedNbt", _nbt.read_compressed_nbt, _nbt.write_compressed_nbt
)

# ---------------------------------------------------------------------------
# lpVec3: "length-prefixed vec3" -- a quantized, variable-size encoding
# for a {x, y, z} float triple used by modern Minecraft for certain
# relative/delta position fields. Ported field-for-field from
# node-minecraft-protocol's src/datatypes/lpVec3.js so the bit-packing
# math matches exactly (same MAX_QUANTIZED_VALUE/scale/shift constants).
#
# Wire shape:
#   - all-zero vector (below ABS_MIN_VALUE): single 0x00 byte.
#   - otherwise: 6 bytes (1 marker/scale byte + 1 byte + u32BE, packed
#     as a 48-bit integer holding 3x 15-bit quantized components plus a
#     2-bit scale and a continuation flag), optionally followed by a
#     varint when the integer part of the scale needs more than 2 bits
#     (bit 4 of the first byte signals this "needs continuation" case).
# ---------------------------------------------------------------------------

_LPVEC3_MAX_QUANTIZED = 32766.0
_LPVEC3_ABS_MIN_VALUE = 3.051944088384301e-5
_LPVEC3_ABS_MAX_VALUE = 1.7179869183e10


def _lpvec3_sanitize(value: float) -> float:
    try:
        if value != value:  # NaN check without importing math
            return 0.0
    except TypeError:
        return 0.0
    return max(-_LPVEC3_ABS_MAX_VALUE, min(value, _LPVEC3_ABS_MAX_VALUE))


def _lpvec3_pack(value: float) -> int:
    return round((value * 0.5 + 0.5) * _LPVEC3_MAX_QUANTIZED)


def _lpvec3_unpack(packed: int, shift: int) -> float:
    quantized = min((packed // (2 ** shift)) % 0x8000, _LPVEC3_MAX_QUANTIZED)
    return (quantized * 2.0) / _LPVEC3_MAX_QUANTIZED - 1.0


def _lpvec3_read(r: Reader) -> dict:
    a = r.peek_byte()
    if a == 0:
        r.read_bytes(1)
        return {"x": 0.0, "y": 0.0, "z": 0.0}

    six = r.read_bytes(6)
    a, b = six[0], six[1]
    c = int.from_bytes(six[2:6], "big")

    packed = (c * 65536) + (b << 8) + a

    scale = a & 3
    if (a & 4) == 4:
        var_val = _varint_read_raw(r, 32)
        scale = (var_val * 4) + scale

    return {
        "x": _lpvec3_unpack(packed, 3) * scale,
        "y": _lpvec3_unpack(packed, 18) * scale,
        "z": _lpvec3_unpack(packed, 33) * scale,
    }


def _lpvec3_write(value: dict, w: Writer) -> None:
    x = _lpvec3_sanitize(value["x"])
    y = _lpvec3_sanitize(value["y"])
    z = _lpvec3_sanitize(value["z"])

    peak = max(abs(x), abs(y), abs(z))

    if peak < _LPVEC3_ABS_MIN_VALUE:
        w.write_bytes(b"\x00")
        return

    import math

    scale = math.ceil(peak)
    needs_continuation = scale > 3
    markers = ((scale % 4) | 4) if needs_continuation else scale
    packed = (
        markers
        + _lpvec3_pack(x / scale) * 0x8
        + _lpvec3_pack(y / scale) * 0x40000
        + _lpvec3_pack(z / scale) * 0x200000000
    )

    out = bytearray(6)
    out[0] = packed % 0x100
    out[1] = (packed // 0x100) % 0x100
    out[2:6] = ((packed // 0x10000) % 0x100000000).to_bytes(4, "big")
    w.write_bytes(bytes(out))

    if needs_continuation:
        _varint_write(scale // 4, w, 32)


def _lpvec3_size_of(value: dict) -> int:
    x = _lpvec3_sanitize(value["x"])
    y = _lpvec3_sanitize(value["y"])
    z = _lpvec3_sanitize(value["z"])
    peak = max(abs(x), abs(y), abs(z))
    if peak < _LPVEC3_ABS_MIN_VALUE:
        return 1

    import math

    scale = math.ceil(peak)
    if scale > 3:
        return 6 + _varint_size_of(scale // 4, 32)
    return 6


lp_vec3 = Primitive("lpVec3", _lpvec3_read, _lpvec3_write, _lpvec3_size_of)


# buffer1024: fixed-size level chunk (Level Data Chunk, 0x03)
buffer1024 = make_fixed_buffer(1024)
buffer1024 = Primitive("buffer1024", buffer1024.read, buffer1024.write, buffer1024.size_of)

# buffer64: fixed 64 raw bytes with \x00 padding (PluginMessage data,
# 0x35) -- same semantics as the original NetWriter/NetReader's
# write_byte_array(data, 64)/read_byte_array(64): truncates if there's
# extra, pads with \x00 if there's not enough.
buffer64 = make_fixed_buffer(64)
buffer64 = Primitive("buffer64", buffer64.read, buffer64.write, buffer64.size_of)

# ready-to-use instance for referencing by name in protocol.yml/json:
# type: string64  (always 64 bytes, doesn't take a parameter there --
# if you ever need a different length, generate another one the same
# way with make_fixed_cp437_string(N) and register it under its own
# name)
string64 = make_fixed_cp437_string(64)
string64 = Primitive("string64", string64.read, string64.write, string64.size_of)

# utf16be64: same idea as string64, but UTF-16BE (Minecraft Classic
# username field, some server-list/MOTD variants) -- 64 CHARACTERS,
# so 128 bytes on the wire (length * 2, see make_fixed_utf16be_string).
# The factory make_fixed_utf16be_string(N) already existed for a while
# but never had a ready-to-use instance registered in PRIMITIVES the
# way make_fixed_cp437_string already got with string64 -- this closes
# that gap following the exact same pattern. Need a different length?
# generate another one with make_fixed_utf16be_string(N) and register
# it under its own name, same as the note above for string64.
utf16be64 = make_fixed_utf16be_string(64)
utf16be64 = Primitive("utf16be64", utf16be64.read, utf16be64.write, utf16be64.size_of)

# fixedCoord: Q10.5 fixed-point coordinate from the Minecraft
# Classic/ClassiCube protocol -- same byte layout as i16 (signed,
# big-endian), but with its own name so the .yml stays semantic: the
# raw value read/written is real_value * 32 (that's handled by whoever
# builds the packet, this primitive just reads/writes the i16 as-is).
fixed_coord = Primitive("fixedCoord", i16.read, i16.write, i16.size_of)

# fixedCoordDelta: same *32 fixed-point, but for the COMPRESSED
# position packets (0x09/0x0a), where the delta between updates travels
# in 1 signed byte instead of 2 -- same byte layout as i8.
fixed_coord_delta = Primitive("fixedCoordDelta", i8.read, i8.write, i8.size_of)


PRIMITIVES: dict[str, Primitive] = {
    p.name: p
    for p in [
        i8, u8, i16, u16, i32, u32, i64, u64, f16, f32, f64,
        li8, lu8, li16, lu16, li32, lu32, li64, lu64, lf16, lf32, lf64,
        u24, i24, u40, i40, u48, i48, u56, i56,
        lu24, li24, lu40, li40, lu48, li48, lu56, li56,
        bool_, void,
        varint, varlong, uvarint, uvarlong, zigzag32, zigzag64,
        varint128, uvarint128,
        cstring,
        uuid_, rest_buffer,
        nbt_, optional_nbt,
        anonymous_nbt, anon_optional_nbt,
        compressed_nbt,
        string64,
        utf16be64,
        buffer1024,
        buffer64,
        fixed_coord,
        fixed_coord_delta,
        lp_vec3,
        raknet_magic,
    ]
}
PRIMITIVES["bool"] = bool_
