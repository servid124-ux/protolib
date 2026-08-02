"""
protolib/nbt.py

Named Binary Tag (NBT) -- el formato de Notch usado por Minecraft para
tile entities, item NBT, chunk data, etc. Big-endian, sin compresión acá
(la compresión gzip/zlib, si aplica, es responsabilidad de quien lea el
paquete completo -- p.ej. chunk data en 1.8.9 va zlib-comprimido *antes*
de que llegue el NBT de cada tile entity).

Estructura binaria de un tag "con nombre" (el caso normal, salvo dentro
de TAG_List donde los elementos NO llevan nombre):

    [u8 tagType][u16 nameLen][nameLen bytes utf-8][payload según tagType]

IDs de tag (estándar, no cambian entre versiones de Minecraft):
    0  End
    1  Byte        (i8)
    2  Short       (i16)
    3  Int         (i32)
    4  Long        (i64)
    5  Float       (f32)
    6  Double      (f64)
    7  Byte_Array  ([i32 length][length bytes])
    8  String      ([u16 length][length bytes utf-8])
    9  List        ([u8 elementTagType][i32 count][count payloads sin nombre])
    10 Compound    (tags con nombre hasta encontrar un End)
    11 Int_Array   ([i32 length][length i32])
    12 Long_Array  ([i32 length][length i64])  -- no existe en 1.8.9, pero
                    se soporta por si se reutiliza este módulo en versiones
                    más nuevas.

Representación en Python: un dict con la forma
    {"type": "compound", "name": "...", "value": {...}}
para el nivel raíz, y recursivamente para cada tag hijo. Esto conserva
el nombre y el tipo explícito de cada tag (necesario para poder
serializar de vuelta sin ambigüedad, ya que un int y un float ambos
podrían escribirse como número en Python puro).
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
        # Byte_Array son bytes CON SIGNO (igual que un TAG_BYTE suelto),
        # no valores 0-255 -- list(bytes) los devolvería sin signo, lo
        # cual es inconsistente con como se lee un TAG_BYTE individual
        # unas líneas más arriba.
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
    raise NBTError(f"tag NBT desconocido: {tag_type!r}")


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
        raise NBTError(f"tag NBT desconocido: {tag_type!r}")


def read_nbt(r: Reader) -> dict | None:
    """
    Lee un tag NBT completo con nombre desde la raíz (el caso normal:
    un TAG_Compound nombrado, o TAG_End si el campo está vacío).

    Devuelve: {"name": str, "type": "compound", "value": {...}} o None
    si el primer byte es TAG_End (equivalente a "no hay NBT aquí").
    """
    tag_type = _read_u8(r)
    if tag_type == TAG_END:
        return None
    name = _read_modified_utf8(r)
    value = _read_payload(tag_type, r)
    return {"name": name, "type": _TYPE_NAME_BY_ID.get(tag_type, tag_type), "value": value}


def write_nbt(tag: dict | None, w: Writer) -> None:
    """Escribe de vuelta lo que produjo read_nbt(). None -> solo TAG_End."""
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
    optionalNbt en node-minecraft-protocol: idéntico formato a nbt, pero
    semánticamente se usa donde el valor puede legítimamente estar
    ausente (p.ej. slot sin tag). En la práctica es lo mismo que read_nbt
    (un TAG_End suelto ya significa "ausente"), se expone aparte solo
    para que el nombre de tipo coincida con el protocol.json real.
    """
    return read_nbt(r)


def write_optional_nbt(tag: dict | None, w: Writer) -> None:
    write_nbt(tag, w)
