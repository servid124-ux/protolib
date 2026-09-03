"""
demo.py — corre example_protocol.yml de punta a punta para mostrar los
4 patrones en acción. Poné esto junto a tu carpeta protolib/ y corré:

    python demo.py
"""
import os
import sys
sys.path.insert(0, "..")  # ajustá si tu protolib/ está en otro lado

from protolib import Protocol

# Ruta absoluta relativa a ESTE archivo, no al cwd del proceso -- así el
# demo funciona igual corriendo `python demo.py` desde adentro de examples/
# o `python examples/demo.py` desde la raíz del repo.
_here = os.path.dirname(os.path.abspath(__file__))
proto = Protocol(os.path.join(_here, "example_protocol.yml"))

print("=== Patrón 2: switch con ../accion (array de jugadores) ===")
params = {
    "accion": "agregar",
    "jugadores": [
        {"uuid": "11111111-1111-1111-1111-111111111111", "nombre": "Pana_Bot"},
    ],
}
data = proto.serialize_packet("play", "toClient", "ejemplo_jugadores", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "-> ", parsed.params)

print()
print("=== Patrones 1 + 4: tipo parametrizado + bitfield empaquetado ===")
params = {
    "entityId": 42,
    "metadata": [
        {"tipo": 0, "indice": 0, "valor": -1},                    # i8
        {"tipo": 4, "indice": 1, "valor": "hola pana"},            # string
        {"tipo": 5, "indice": 2, "valor": {"x": 10, "y": 64, "z": -5}},  # container
    ],
}
data = proto.serialize_packet("play", "toClient", "ejemplo_metadata", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "bytes_read:", parsed.bytes_read, "-> ", parsed.params)
assert parsed.bytes_read == len(data), "quedó desalineado!"
print("offset OK, no quedó basura sin leer")

print()
print("=== Patrón 5: catálogo de los 61 natives (packet_ejemplo_natives) ===")
params = {
    # -- enteros de ancho fijo, big-endian --
    "campo_u8": 200, "campo_i8": -100,
    "campo_u16": 60000, "campo_i16": -30000,
    "campo_u24": 16_000_000, "campo_i24": -8_000_000,
    "campo_u32": 4_000_000_000, "campo_i32": -2_000_000_000,
    "campo_u40": 500_000_000_000, "campo_i40": -500_000_000_000,
    "campo_u48": 100_000_000_000_000, "campo_i48": -100_000_000_000_000,
    "campo_u56": 2**55, "campo_i56": -(2**55),
    "campo_u64": 2**63, "campo_i64": -(2**62),
    # -- floats --
    "campo_f16": 3.5, "campo_f32": 3.14159, "campo_f64": 2.718281828,
    # -- misma familia, little-endian --
    "campo_lu8": 200, "campo_li8": -100,
    "campo_lu16": 60000, "campo_li16": -30000,
    "campo_lu24": 16_000_000, "campo_li24": -8_000_000,
    "campo_lu32": 4_000_000_000, "campo_li32": -2_000_000_000,
    "campo_lu40": 500_000_000_000, "campo_li40": -500_000_000_000,
    "campo_lu48": 100_000_000_000_000, "campo_li48": -100_000_000_000_000,
    "campo_lu56": 2**55, "campo_li56": -(2**55),
    "campo_lu64": 2**63, "campo_li64": -(2**62),
    "campo_lf16": 3.5, "campo_lf32": 3.14159, "campo_lf64": 2.718281828,
    # -- varints / zigzag --
    "campo_varint": -1, "campo_varlong": -123456789012,
    "campo_uvarint": 300, "campo_uvarlong": 123456789012,
    "campo_varint128": -(2**100), "campo_uvarint128": 2**100,
    "campo_zigzag32": -1, "campo_zigzag64": -1,
    # -- bool / void --
    "campo_bool": True, "campo_void": None,
    # -- strings --
    "campo_cstring": "hola pana",
    "campo_string64": "cabe en 64 bytes CP437",
    "campo_utf16be64": "héllo wörld, cabe en 64 chars UTF-16BE",
    # -- UUID --
    "campo_uuid": "11111111-1111-1111-1111-111111111111",
    # -- NBT (con nombre) --
    "campo_nbt": {"name": "root", "type": "compound",
                  "value": {"vida": {"type": "int", "value": 20}}},
    "campo_optionalNbt": None,
    # -- NBT anónimo (sin key "name") --
    "campo_anonymousNbt": {"type": "compound",
                            "value": {"nivel": {"type": "byte", "value": 5}}},
    "campo_anonOptionalNbt": None,
    # -- buffers fijos (ClassiCube) --
    "campo_buffer64": b"datos de plugin message",
    "campo_buffer1024": b"chunk de nivel" + b"\x00" * (1024 - 14),
    # -- fixed-point (ClassiCube): valor real * 32, ya empaquetado --
    "campo_fixedCoord": 32 * 10,       # posición real = 10.0
    "campo_fixedCoordDelta": 32 * 2,   # delta real = 2.0
    # -- restBuffer: se come todo lo que quede, va al final --
    "campo_restBuffer": b"el resto del paquete, sin limite de tamano",
}
data = proto.serialize_packet("play", "toClient", "ejemplo_natives", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "bytes_read:", parsed.bytes_read)
assert parsed.bytes_read == len(data), "quedó desalineado!"
print("offset OK, no quedó basura sin leer -- los 61 natives redondearon bien")
for campo in ("campo_f16", "campo_varint128", "campo_uuid", "campo_nbt", "campo_fixedCoord"):
    print(f"  {campo} -> {parsed.params[campo]!r}")
