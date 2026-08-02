"""
protolib/primitives.py

Tipos primitivos: no dependen de protocol.json, son los bloques de bytes
más básicos (enteros de tamaño fijo, varints, floats, bool, strings).

Cada primitivo expone:
    read(reader: Reader) -> Any
    write(value: Any, writer: Writer) -> None
    size_of(value: Any) -> int

Se registran en un dict PRIMITIVES { nombre: Primitive } que después
el motor principal (core.py) usa como tipos base resolvibles por nombre.
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
# Enteros de tamaño fijo big-endian (estándar en protocolos Minecraft)
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

# little-endian (RakNet y algunos campos de protocolos viejos los usan)
# li8/lu8 son alias de i8/u8: 1 byte no tiene endianness, pero node-protodef
# los expone con estos nombres para que protocol.json pueda referenciarlos
# igual que cualquier otro par li*/lu* sin caso especial.
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


# ---------------------------------------------------------------------------
# Enteros de 3, 5, 6 y 7 bytes (u24/i24, u40/i40, u48/i48, u56/i56) -- struct
# no los soporta nativo, se arman a mano con int.from_bytes / int.to_bytes.
# u24 aparece en RakNet (split-packet count, message index, sequence number,
# etc.) y en varios protocolos viejos de Minecraft/Bedrock. u40/u48/u56
# completan la familia (junto con u8/u16/u24/u32/u64 ya cubiertos arriba)
# para cualquier protocolo que use un entero de ancho no potencia-de-2.
# ---------------------------------------------------------------------------

def _int_n_bytes_primitive(name, n_bytes, signed, little_endian=False):
    byteorder = "little" if little_endian else "big"

    def read(r):
        data = r.read_bytes(n_bytes)
        return int.from_bytes(data, byteorder=byteorder, signed=signed)

    def write(value, w):
        w.write_bytes(int(value).to_bytes(n_bytes, byteorder=byteorder, signed=signed))

    return Primitive(name=name, read=read, write=write, size_of=lambda v: n_bytes)


# big-endian (estándar Minecraft)
u24 = _int_n_bytes_primitive("u24", 3, signed=False)
i24 = _int_n_bytes_primitive("i24", 3, signed=True)
u40 = _int_n_bytes_primitive("u40", 5, signed=False)
i40 = _int_n_bytes_primitive("i40", 5, signed=True)
u48 = _int_n_bytes_primitive("u48", 6, signed=False)
i48 = _int_n_bytes_primitive("i48", 6, signed=True)
u56 = _int_n_bytes_primitive("u56", 7, signed=False)
i56 = _int_n_bytes_primitive("i56", 7, signed=True)

# little-endian (RakNet manda varios de estos campos en LE, ej. message
# index / sequence number / order index de 3 bytes)
lu24 = _int_n_bytes_primitive("lu24", 3, signed=False, little_endian=True)
li24 = _int_n_bytes_primitive("li24", 3, signed=True, little_endian=True)
lu40 = _int_n_bytes_primitive("lu40", 5, signed=False, little_endian=True)
li40 = _int_n_bytes_primitive("li40", 5, signed=True, little_endian=True)
lu48 = _int_n_bytes_primitive("lu48", 6, signed=False, little_endian=True)
li48 = _int_n_bytes_primitive("li48", 6, signed=True, little_endian=True)
lu56 = _int_n_bytes_primitive("lu56", 7, signed=False, little_endian=True)
li56 = _int_n_bytes_primitive("li56", 7, signed=True, little_endian=True)


# ---------------------------------------------------------------------------
# UUID (128 bits, formato estándar de Minecraft: 16 bytes crudos big-endian,
# sin guiones al leer/escribir binario -- el string con guiones es solo
# representación textual para humanos)
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
# restBuffer: consume todos los bytes que queden en el buffer actual.
# Típico como último campo de un container/packet (payload sin longitud
# prefijada, se asume que ocupa "todo lo que sobra").
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
# Varint / Varlong (LEB128, estilo Protocol Buffers / Minecraft)
# ---------------------------------------------------------------------------

def _varint_read_raw(r: Reader, max_bits: int) -> int:
    """Lee el valor crudo unsigned de hasta max_bits bits."""
    result = 0
    shift = 0
    while True:
        byte = r.read_bytes(1)[0]
        result |= (byte & 0x7F) << shift
        shift += 7
        if not (byte & 0x80):
            break
        if shift > max_bits + 7:
            raise ValueError(f"varint excede {max_bits} bits")
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

# variantes "unsigned" explícitas: nunca interpretan el bit de signo
# (útiles para protocolos que documentan varints siempre positivos, p.ej counts grandes)
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

# varint128 / uvarint128: LEB128 extendido a 128 bits. Útil para IDs
# grandes que no entran en 64 bits (p.ej. Snowflake IDs de 128 bits,
# hashes truncados, UUIDs codificados como entero variable en vez de
# los 16 bytes fijos de UUID). Reutiliza el mismo helper genérico de
# varint/varlong -- solo cambia max_bits a 128. Ocupa como máximo 19
# bytes en el peor caso (ceil(128/7) = 19).
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


# zigzag varint (protobuf-style: 0,-1,1,-2,2 -> 0,1,2,3,4 antes de LEB128)
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
# Strings de longitud terminada en null byte (C-style)
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

# Nota: NO se agrega acá un primitivo "string" ni "buffer" length-prefixed.
# core.py ya resuelve eso como tipos COMPUESTOS configurables:
#   - "pstring" con countType (varint, u16, u8, etc.) para strings
#   - "buffer" con countType/count para buffers de tamaño variable
# Duplicar el nombre "buffer" acá pisaría self._composite_handlers["buffer"]
# en core.py, que es más flexible porque el countType es configurable desde
# el .yml/.json en vez de estar hardcodeado a varint.


def make_fixed_utf16be_string(length: int) -> Primitive:
    """
    Minecraft Classic: strings de longitud fija en caracteres, codificados
    UTF-16BE y rellenados con espacios (' ') hasta completar `length`.
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
    Minecraft Classic (protocolo 0x07): strings de longitud fija en BYTES
    (no caracteres), codificados CP437 y rellenados con espacios (' ')
    hasta completar `length`. Se usa para username, verification key,
    server name/MOTD, mensajes de chat, nombre de jugador, etc.

    CP437 (no ASCII/UTF-8 puro) porque el cliente vainilla soporta los
    símbolos extendidos de esa codepage (incluye los códigos de color '&').
    """

    def read(r: Reader) -> str:
        data = r.read_bytes(length)
        return data.decode("cp437").rstrip(" ")

    def write(value: str, w: Writer) -> None:
        # errors="replace": un caracter que CP437 no puede representar
        # (emoji, etc.) se cambia por '?' en vez de tirar
        # UnicodeEncodeError y voltear el server -- esto va a recibir
        # texto arbitrario de usuarios (username, chat), así que no
        # puede confiar en que siempre venga en el charset esperado.
        padded = (value or "")[:length].ljust(length)
        w.write_bytes(padded.encode("cp437", errors="replace"))

    return Primitive(f"fixed_cp437_{length}", read, write, lambda v: length)


def make_fixed_buffer(length: int) -> Primitive:
    """
    Bytes crudos de longitud SIEMPRE fija (sin countType/count en el yml).
    Minecraft Classic lo usa para el chunk de 1024 bytes de Level Data
    Chunk -- si sobran menos de 1024 bytes reales, el resto viene/va
    relleno con 0x00, pero eso lo maneja quien arma el payload, no este
    primitivo (acá solo se garantiza leer/escribir exactamente `length`).
    """

    def read(r: Reader) -> bytes:
        return r.read_bytes(length)

    def write(value: bytes, w: Writer) -> None:
        data = (value or b"")[:length].ljust(length, b"\x00")
        w.write_bytes(data)

    return Primitive(f"fixed_buffer_{length}", read, write, lambda v: length)


from . import nbt as _nbt

nbt_ = Primitive("nbt", _nbt.read_nbt, _nbt.write_nbt)
optional_nbt = Primitive("optionalNbt", _nbt.read_optional_nbt, _nbt.write_optional_nbt)

# buffer1024: chunk de nivel de tamaño fijo (Level Data Chunk, 0x03)
buffer1024 = make_fixed_buffer(1024)
buffer1024 = Primitive("buffer1024", buffer1024.read, buffer1024.write, buffer1024.size_of)

# buffer64: bytes crudos de 64 fijos con padding \x00 (PluginMessage data,
# 0x35) -- misma semantica que write_byte_array(data, 64)/read_byte_array(64)
# del NetWriter/NetReader original: trunca si sobra, rellena con \x00 si falta.
buffer64 = make_fixed_buffer(64)
buffer64 = Primitive("buffer64", buffer64.read, buffer64.write, buffer64.size_of)

# instancia lista para usar por nombre en protocol.yml/json:
# type: string64  (siempre 64 bytes, no acepta parámetro ahí --
# si algún día necesitás otro largo, generá otra igual con
# make_fixed_cp437_string(N) y registrala con su propio nombre)
string64 = make_fixed_cp437_string(64)
string64 = Primitive("string64", string64.read, string64.write, string64.size_of)

# fixedCoord: coordenada fixed-point Q10.5 del protocolo Minecraft
# Classic/ClassiCube -- mismo byte-layout que i16 (big-endian, con
# signo), pero con nombre propio para que el .yml quede semántico:
# el valor crudo leído/escrito es valor_real * 32 (eso lo maneja quien
# arma el paquete, este primitivo solo lee/escribe el i16 tal cual).
fixed_coord = Primitive("fixedCoord", i16.read, i16.write, i16.size_of)

# fixedCoordDelta: mismo fixed-point *32, pero para los paquetes de
# posición COMPRIMIDA (0x09/0x0a), donde el delta entre updates viaja
# en 1 byte con signo en vez de 2 -- mismo byte-layout que i8.
fixed_coord_delta = Primitive("fixedCoordDelta", i8.read, i8.write, i8.size_of)


PRIMITIVES: dict[str, Primitive] = {
    p.name: p
    for p in [
        i8, u8, i16, u16, i32, u32, i64, u64, f32, f64,
        li8, lu8, li16, lu16, li32, lu32, li64, lu64, lf32, lf64,
        u24, i24, u40, i40, u48, i48, u56, i56,
        lu24, li24, lu40, li40, lu48, li48, lu56, li56,
        bool_, void,
        varint, varlong, uvarint, uvarlong, zigzag32, zigzag64,
        varint128, uvarint128,
        cstring,
        uuid_, rest_buffer,
        nbt_, optional_nbt,
        string64,
        buffer1024,
        buffer64,
        fixed_coord,
        fixed_coord_delta,
    ]
}
PRIMITIVES["bool"] = bool_
