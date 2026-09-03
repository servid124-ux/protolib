"""
protolib/nbt.py

Named Binary Tag (NBT) -- Notch's format used by Minecraft for tile
entities, item NBT, chunk data, etc. Big-endian, no compression here
(gzip/zlib compression, if applicable, is the responsibility of
whoever reads the full packet -- e.g. chunk data in 1.8.9 arrives
zlib-compressed *before* the NBT of each tile entity is reached).

Binary structure of a "named" tag (the normal case, except inside a
TAG_List, where elements do NOT carry a name):

    [u8 tagType][u16 nameLen][nameLen bytes utf-8][payload per tagType]

Tag IDs (standard, don't change between Minecraft versions):
    0  End
    1  Byte        (i8)
    2  Short       (i16)
    3  Int         (i32)
    4  Long        (i64)
    5  Float       (f32)
    6  Double      (f64)
    7  Byte_Array  ([i32 length][length bytes])
    8  String      ([u16 length][length bytes utf-8])
    9  List        ([u8 elementTagType][i32 count][count unnamed payloads])
    10 Compound    (named tags until a End is found)
    11 Int_Array   ([i32 length][length i32])
    12 Long_Array  ([i32 length][length i64])  -- doesn't exist in 1.8.9,
                    but supported in case this module gets reused for
                    newer versions.

Python representation: a dict shaped like
    {"type": "compound", "name": "...", "value": {...}}
for the root level, and recursively for each child tag. This preserves
the name and explicit type of each tag (necessary to be able to
serialize back without ambiguity, since an int and a float could both
be written as a plain Python number).
"""

from __future__ import annotations

from typing import Any

from .io import Reader, Writer
from .errors import ProtolibError


class NBTError(ProtolibError):
    pass


TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

_TYPE_NAME_BY_ID = {
    TAG_END: "end",
    TAG_BYTE: "byte",
    TAG_SHORT: "short",
    TAG_INT: "int",
    TAG_LONG: "long",
    TAG_FLOAT: "float",
    TAG_DOUBLE: "double",
    TAG_BYTE_ARRAY: "byteArray",
    TAG_STRING: "string",
    TAG_LIST: "list",
    TAG_COMPOUND: "compound",
    TAG_INT_ARRAY: "intArray",
    TAG_LONG_ARRAY: "longArray",
}
_TYPE_ID_BY_NAME = {v: k for k, v in _TYPE_NAME_BY_ID.items()}

import struct


def _read_u8(r: Reader) -> int:
    return r.read_bytes(1)[0]


def _write_u8(v: int, w: Writer) -> None:
    w.write_bytes(bytes([v & 0xFF]))


def _read_i16(r: Reader) -> int:
    return struct.unpack(">h", r.read_bytes(2))[0]


def _write_i16(v: int, w: Writer) -> None:
    w.write_bytes(struct.pack(">h", v))


def _read_u16(r: Reader) -> int:
    return struct.unpack(">H", r.read_bytes(2))[0]


def _write_u16(v: int, w: Writer) -> None:
    w.write_bytes(struct.pack(">H", v))


def _read_i32(r: Reader) -> int:
    return struct.unpack(">i", r.read_bytes(4))[0]


def _write_i32(v: int, w: Writer) -> None:
    w.write_bytes(struct.pack(">i", v))


def _read_i64(r: Reader) -> int:
    return struct.unpack(">q", r.read_bytes(8))[0]


def _write_i64(v: int, w: Writer) -> None:
    w.write_bytes(struct.pack(">q", v))


def _read_f32(r: Reader) -> float:
    return struct.unpack(">f", r.read_bytes(4))[0]


def _write_f32(v: float, w: Writer) -> None:
    w.write_bytes(struct.pack(">f", v))


def _read_f64(r: Reader) -> float:
    return struct.unpack(">d", r.read_bytes(8))[0]


def _write_f64(v: float, w: Writer) -> None:
    w.write_bytes(struct.pack(">d", v))


def _read_modified_utf8(r: Reader) -> str:
    length = _read_u16(r)
    return r.read_bytes(length).decode("utf-8")


def _write_modified_utf8(value: str, w: Writer) -> None:
    data = value.encode("utf-8")
    _write_u16(len(data), w)
    w.write_bytes(data)


def _read_payload(tag_type: int, r: Reader) -> Any:
    if tag_type == TAG_BYTE:
        raw = _read_u8(r)
        return raw - 256 if raw >= 128 else raw
    if tag_type == TAG_SHORT:
        return _read_i16(r)
    if tag_type == TAG_INT:
        return _read_i32(r)
    if tag_type == TAG_LONG:
        return _read_i64(r)
    if tag_type == TAG_FLOAT:
        return _read_f32(r)
    if tag_type == TAG_DOUBLE:
        return _read_f64(r)
    if tag_type == TAG_BYTE_ARRAY:
        length = _read_i32(r)
        raw_bytes = r.read_bytes(length)
        # Byte_Array elements are SIGNED bytes (same as a standalone
        # TAG_BYTE), not 0-255 values -- list(bytes) would return them
        # unsigned, which would be inconsistent with how a single
        # TAG_BYTE is read a few lines above.
        return [b - 256 if b >= 128 else b for b in raw_bytes]
    if tag_type == TAG_STRING:
        return _read_modified_utf8(r)
    if tag_type == TAG_LIST:
        element_type = _read_u8(r)
        count = _read_i32(r)
        return {
            "type": _TYPE_NAME_BY_ID.get(element_type, element_type),
            "value": [_read_payload(element_type, r) for _ in range(count)],
        }
    if tag_type == TAG_COMPOUND:
        result: dict[str, Any] = {}
        while True:
            child_type = _read_u8(r)
            if child_type == TAG_END:
                break
            name = _read_modified_utf8(r)
            value = _read_payload(child_type, r)
            result[name] = {
                "type": _TYPE_NAME_BY_ID.get(child_type, child_type),
                "value": value,
            }
        return result
    if tag_type == TAG_INT_ARRAY:
        length = _read_i32(r)
        return [_read_i32(r) for _ in range(length)]
    if tag_type == TAG_LONG_ARRAY:
        length = _read_i32(r)
        return [_read_i64(r) for _ in range(length)]
    raise NBTError(f"unknown NBT tag: {tag_type!r}")


def _write_payload(tag_type: int, value: Any, w: Writer) -> None:
    if tag_type == TAG_BYTE:
        _write_u8(value & 0xFF, w)
    elif tag_type == TAG_SHORT:
        _write_i16(value, w)
    elif tag_type == TAG_INT:
        _write_i32(value, w)
    elif tag_type == TAG_LONG:
        _write_i64(value, w)
    elif tag_type == TAG_FLOAT:
        _write_f32(value, w)
    elif tag_type == TAG_DOUBLE:
        _write_f64(value, w)
    elif tag_type == TAG_BYTE_ARRAY:
        data = bytes(b & 0xFF for b in value)
        _write_i32(len(data), w)
        w.write_bytes(data)
    elif tag_type == TAG_STRING:
        _write_modified_utf8(value, w)
    elif tag_type == TAG_LIST:
        element_type_name = value["type"]
        element_type = _TYPE_ID_BY_NAME.get(element_type_name, element_type_name)
        items = value["value"]
        _write_u8(element_type, w)
        _write_i32(len(items), w)
        for item in items:
            _write_payload(element_type, item, w)
    elif tag_type == TAG_COMPOUND:
        for name, tag in value.items():
            child_type_name = tag["type"]
            child_type = _TYPE_ID_BY_NAME.get(child_type_name, child_type_name)
            _write_u8(child_type, w)
            _write_modified_utf8(name, w)
            _write_payload(child_type, tag["value"], w)
        _write_u8(TAG_END, w)
    elif tag_type == TAG_INT_ARRAY:
        _write_i32(len(value), w)
        for v in value:
            _write_i32(v, w)
    elif tag_type == TAG_LONG_ARRAY:
        _write_i32(len(value), w)
        for v in value:
            _write_i64(v, w)
    else:
        raise NBTError(f"unknown NBT tag: {tag_type!r}")


def read_nbt(r: Reader) -> dict | None:
    """
    Reads a full named NBT tag from the root (the normal case: a named
    TAG_Compound, or TAG_End if the field is empty).

    Returns: {"name": str, "type": "compound", "value": {...}} or None
    if the first byte is TAG_End (equivalent to "no NBT here").
    """
    tag_type = _read_u8(r)
    if tag_type == TAG_END:
        return None
    name = _read_modified_utf8(r)
    value = _read_payload(tag_type, r)
    return {"name": name, "type": _TYPE_NAME_BY_ID.get(tag_type, tag_type), "value": value}


def write_nbt(tag: dict | None, w: Writer) -> None:
    """Writes back what read_nbt() produced. None -> just TAG_End."""
    if tag is None:
        _write_u8(TAG_END, w)
        return
    tag_type_name = tag["type"]
    tag_type = _TYPE_ID_BY_NAME.get(tag_type_name, tag_type_name)
    _write_u8(tag_type, w)
    _write_modified_utf8(tag["name"], w)
    _write_payload(tag_type, tag["value"], w)


def read_optional_nbt(r: Reader) -> dict | None:
    """
    optionalNbt in node-minecraft-protocol: identical format to nbt,
    but semantically used where the value can legitimately be absent
    (e.g. a slot with no tag). In practice this is the same as read_nbt
    (a standalone TAG_End already means "absent"); it's exposed
    separately just so the type name matches the real protocol.json.
    """
    return read_nbt(r)


def write_optional_nbt(tag: dict | None, w: Writer) -> None:
    write_nbt(tag, w)


def read_anonymous_nbt(r: Reader) -> dict | None:
    """
    anonymousNbt: identical to a normal NBT tag, but WITHOUT the name
    prefix ([u16 nameLen][nameLen bytes]) that read_nbt/write_nbt do
    carry. Format: [u8 tagType][payload per tagType] -- the tagType is
    directly followed by the payload, with no string in between.

    Used by modern Minecraft (1.20.2+) in chat components, item
    custom_data, block_entity_data, and various other places in the
    protocol where the root tag's name would always be the empty
    string ("") -- instead of spending 2 bytes writing that zero
    length on every packet, those fields directly declare that the
    name doesn't exist.

    Just like read_nbt, a standalone first byte of TAG_End means "no
    NBT here" and returns None -- this isn't a special case for
    anonymousNbt, it's the same behavior TAG_End already has in any
    tagType position.

    Returns: {"type": <tag-name>, "value": <payload>} or None.
    Note that, unlike read_nbt, the resulting dict does NOT have a
    "name" key (there's no name to preserve).
    """
    tag_type = _read_u8(r)
    if tag_type == TAG_END:
        return None
    value = _read_payload(tag_type, r)
    return {"type": _TYPE_NAME_BY_ID.get(tag_type, tag_type), "value": value}


def write_anonymous_nbt(tag: dict | None, w: Writer) -> None:
    """Writes back what read_anonymous_nbt() produced. None -> just TAG_End."""
    if tag is None:
        _write_u8(TAG_END, w)
        return
    tag_type_name = tag["type"]
    tag_type = _TYPE_ID_BY_NAME.get(tag_type_name, tag_type_name)
    _write_u8(tag_type, w)
    _write_payload(tag_type, tag["value"], w)


def read_compressed_nbt(r: Reader) -> dict | None:
    """
    compressedNbt: length-prefixed, gzip-compressed NBT tag, as used
    for the (old, pre-1.20.2) Slot Data item-NBT field. Wire shape:

        [i16 length][length bytes of gzip data]

    length == -1 means "absent" (no NBT), matching
    node-minecraft-protocol's compressedNbt reader
    (src/datatypes/minecraft.js). Any other negative or corrupted
    length is a protocol error, not a silent absence.
    """
    import gzip

    from .errors import NBTDecompressionError

    length = _read_i16(r)
    if length == -1:
        return None
    if length < 0:
        raise NBTDecompressionError(f"invalid negative length {length}")

    compressed = r.read_bytes(length)
    try:
        raw = gzip.decompress(compressed)
    except OSError as exc:
        raise NBTDecompressionError(str(exc)) from exc

    inner = Reader(raw)
    return read_nbt(inner)


def write_compressed_nbt(tag: dict | None, w: Writer) -> None:
    """Writes back what read_compressed_nbt() produced. None -> length -1."""
    import gzip

    if tag is None:
        _write_i16(-1, w)
        return

    inner = Writer()
    write_nbt(tag, inner)
    compressed = gzip.compress(inner.result())
    # Match node-minecraft-protocol: clear the OS field (byte 9) so the
    # output is byte-identical to what real Minecraft/other clients
    # produce, instead of leaking the OS gzip was compiled/run on.
    compressed = bytearray(compressed)
    compressed[9] = 0
    compressed = bytes(compressed)

    _write_i16(len(compressed), w)
    w.write_bytes(compressed)


def read_anon_optional_nbt(r: Reader) -> dict | None:
    """
    anonOptionalNbt: same byte format as anonymousNbt (a standalone
    TAG_End already means "absent" in both), exposed separately just
    so the type name matches the real protocol.json -- same criterion
    as optionalNbt relative to nbt earlier in this file.
    """
    return read_anonymous_nbt(r)


def write_anon_optional_nbt(tag: dict | None, w: Writer) -> None:
    write_anonymous_nbt(tag, w)
