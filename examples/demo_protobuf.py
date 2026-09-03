"""
demo_protobuf.py — corre full_reference_template.proto de punta a punta,
ejercitando cada construcción del catálogo (todos los escalares, repeated
empacado/no-empacado, mensajes anidados, enum, map, oneof, tipo recursivo,
reserved). Poné esto junto a tu carpeta protolib/ y corré:

    python demo_protobuf.py

Esto NO tiene nada que ver con demo.py (ese ejercita el motor YAML/JSON
node-protodef-style de protolib.core.Protocol) -- protolib.protobuf es un
motor de esquema completamente independiente, con su propio wire format
real de Google Protocol Buffers.
"""
import os
import sys
sys.path.insert(0, "..")  # ajustá si tu protolib/ está en otro lado

from protolib.protobuf import ProtoFileSchema, ProtoOneofViolationError

# Ruta absoluta relativa a ESTE archivo, no al cwd del proceso -- así el
# demo funciona igual corriendo `python demo_protobuf.py` desde adentro
# de examples/ o `python examples/demo_protobuf.py` desde la raíz del repo.
_here = os.path.dirname(os.path.abspath(__file__))
schema = ProtoFileSchema.from_file(os.path.join(_here, "full_reference_template.proto"))


def roundtrip(name: str, value: dict) -> None:
    data = schema.encode(name, value)
    parsed = schema.decode(name, data)
    assert parsed == value, f"{name}: mismatch\n  in:  {value}\n  out: {parsed}"
    print(f"  {name}: {len(data)} bytes -> roundtrip exacto")


print("=== ScalarCatalog: los 15 tipos escalares ===")
roundtrip("ScalarCatalog", {
    "campo_int32": -5,
    "campo_int64": 123456789012,
    "campo_uint32": 4000000000,
    "campo_uint64": 10,
    "campo_sint32": -1000,       # zigzag: compacto pese a ser negativo
    "campo_sint64": -999999999999,
    "campo_bool": True,
    "campo_fixed32": 42,
    "campo_sfixed32": -42,
    "campo_float": 1.5,
    "campo_fixed64": 42,
    "campo_sfixed64": -42,
    "campo_double": 3.14159,
    "campo_string": "hola mundo",
    "campo_bytes": b"\x00\x01\xff",
})

print()
print("=== RepeatedScalars: packed (numeric) vs never-packed (string) ===")
roundtrip("RepeatedScalars", {
    "numbers": [1, -2, 3, 1000000],
    "measurements": [1.1, 2.2, 3.3],
    "tags": ["a", "b", "c"],
})

print()
print("=== Person: mensaje + enum anidados, repeated de mensaje ===")
roundtrip("Person", {
    "name": "Alice",
    "id": 1,
    "email": "alice@example.com",
    "phones": [
        {"number": "555-1234", "type": "HOME"},
        {"number": "555-0000"},  # sin 'type' == MOBILE (el valor 0/default)
    ],
})

print()
print("=== AddressBook: referencia cruzada a otro mensaje del archivo ===")
roundtrip("AddressBook", {"people": [{"name": "Bob", "id": 2, "phones": []}]})

print()
print("=== Inventory: map<string, int32> ===")
roundtrip("Inventory", {"item_counts": {"sword": 1, "potion": 5}})

print()
print("=== Event: oneof, probando cada miembro por separado ===")
roundtrip("Event", {"text_message": "hola"})
roundtrip("Event", {"status_code": 200})
roundtrip("Event", {"joined": {"name": "Carl", "id": 3, "phones": []}})

print("  intentando poner DOS miembros del oneof a la vez (debe fallar)...")
try:
    schema.encode("Event", {"text_message": "x", "status_code": 1})
    raise AssertionError("debería haber lanzado ProtoOneofViolationError")
except ProtoOneofViolationError as e:
    print(f"  OK, rechazado correctamente: {e}")

print()
print("=== TreeNode: tipo recursivo (se referencia a sí mismo) ===")
roundtrip("TreeNode", {
    "value": "root",
    "children": [
        {"value": "child1", "children": []},
        {"value": "child2", "children": [{"value": "grandchild", "children": []}]},
    ],
})

print()
print("=== VersionedRecord: reserved (2, 4-6 y 'legacy_id' inutilizables) ===")
roundtrip("VersionedRecord", {"current_id": "abc", "revision": 3, "checksum": 999})

print()
print("Todo el catálogo de full_reference_template.proto pasa roundtrip exacto.")
