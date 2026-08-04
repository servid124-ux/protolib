"""
demo.py — corre example_protocol.yml de punta a punta para mostrar los
4 patrones en acción. Poné esto junto a tu carpeta protolib/ y corré:

    python demo.py
"""
import os
import sys
sys.path.insert(0, "..")  # ajustá si tu protolib/ está en otro lado

from protolib.core import Protocol

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
