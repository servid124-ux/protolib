import unittest

from protolib.io import Reader, Writer
from protolib.nbt import (
    read_nbt, write_nbt, read_optional_nbt, write_optional_nbt,
    read_anonymous_nbt, write_anonymous_nbt,
    read_anon_optional_nbt, write_anon_optional_nbt,
)
from protolib.primitives import PRIMITIVES


def roundtrip_nbt(tag):
    w = Writer()
    write_nbt(tag, w)
    data = w.result()
    return read_nbt(Reader(data)), data


def roundtrip_anonymous_nbt(tag):
    w = Writer()
    write_anonymous_nbt(tag, w)
    data = w.result()
    return read_anonymous_nbt(Reader(data)), data


class TestNbtRoundtrip(unittest.TestCase):
    def test_full_compound_with_every_tag_type(self):
        tag = {
            "name": "root",
            "type": "compound",
            "value": {
                "aByte": {"type": "byte", "value": -5},
                "aShort": {"type": "short", "value": 1234},
                "anInt": {"type": "int", "value": -100000},
                "aLong": {"type": "long", "value": 123456789012},
                "aFloat": {"type": "float", "value": 1.5},
                "aDouble": {"type": "double", "value": 2.25},
                "aString": {"type": "string", "value": "hola pana"},
                "aByteArray": {"type": "byteArray", "value": [-1, 0, 1, 127, -128]},
                "anIntArray": {"type": "intArray", "value": [1, -2, 3]},
                "aLongArray": {"type": "longArray", "value": [10, -20]},
                "aList": {"type": "list", "value": {"type": "int", "value": [1, 2, 3]}},
                "nested": {"type": "compound", "value": {"inner": {"type": "byte", "value": 7}}},
            },
        }
        got, _ = roundtrip_nbt(tag)
        self.assertEqual(got, tag)

    def test_tag_end_alone_means_absent(self):
        got, data = roundtrip_nbt(None)
        self.assertIsNone(got)
        self.assertEqual(data, b"\x00")

    def test_byte_array_elements_are_signed(self):
        tag = {"name": "", "type": "compound", "value": {
            "b": {"type": "byteArray", "value": [-128, -1, 0, 1, 127]},
        }}
        got, _ = roundtrip_nbt(tag)
        self.assertEqual(got["value"]["b"]["value"], [-128, -1, 0, 1, 127])

    def test_empty_string_and_empty_compound(self):
        tag = {"name": "", "type": "compound", "value": {}}
        got, data = roundtrip_nbt(tag)
        self.assertEqual(got, tag)
        # tag_type(1) + nameLen(2, =0) + immediately TAG_END(1) for the empty body
        self.assertEqual(data, b"\x0a\x00\x00\x00")

    def test_optional_nbt_is_the_same_wire_format_as_nbt(self):
        tag = {"name": "x", "type": "byte", "value": 9}
        w = Writer()
        write_optional_nbt(tag, w)
        data = w.result()
        self.assertEqual(read_optional_nbt(Reader(data)), tag)
        self.assertEqual(read_nbt(Reader(data)), tag)  # genuinely identical wire format


class TestNbtViaPrimitivesRegistry(unittest.TestCase):
    """Exercises nbt through PRIMITIVES['nbt'] / ['optionalNbt'] the way
    core.py actually reaches it (as a Primitive, not by importing nbt.py
    directly) -- this is the integration seam between the two modules."""

    def test_nbt_primitive_roundtrip(self):
        tag = {"name": "hp", "type": "short", "value": 20}
        w = Writer()
        PRIMITIVES["nbt"].write(tag, w)
        self.assertEqual(PRIMITIVES["nbt"].read(Reader(w.result())), tag)

    def test_optional_nbt_primitive_absent(self):
        w = Writer()
        PRIMITIVES["optionalNbt"].write(None, w)
        self.assertEqual(w.result(), b"\x00")
        self.assertIsNone(PRIMITIVES["optionalNbt"].read(Reader(w.result())))


class TestAnonymousNbtRoundtrip(unittest.TestCase):
    """anonymousNbt: mismo payload que nbt normal, pero sin el prefijo de
    nombre -- usado por Minecraft moderno en chat components, custom_data
    de items, block_entity_data, etc."""

    def test_compound_roundtrip_without_name_key(self):
        tag = {
            "type": "compound",
            "value": {
                "text": {"type": "string", "value": "hola pana"},
                "bold": {"type": "byte", "value": 1},
                "extra": {"type": "list", "value": {"type": "int", "value": [1, 2, 3]}},
            },
        }
        got, _ = roundtrip_anonymous_nbt(tag)
        self.assertEqual(got, tag)
        self.assertNotIn("name", got)  # a diferencia de read_nbt, no hay nombre que conservar

    def test_tag_end_alone_means_absent(self):
        got, data = roundtrip_anonymous_nbt(None)
        self.assertIsNone(got)
        self.assertEqual(data, b"\x00")

    def test_wire_format_is_exactly_two_bytes_shorter_than_named_nbt(self):
        # la única diferencia entre nbt y anonymousNbt es el [u16 nameLen]
        # que normalmente sigue al tagType -- con nombre vacío ("") ese
        # prefijo son 2 bytes (length=0) que anonymousNbt directamente no
        # escribe, así que el payload resultante debe ser idéntico salvo
        # por esos 2 bytes de menos.
        body = {"type": "byte", "value": 42}
        w_named = Writer()
        write_nbt({"name": "", **body}, w_named)
        data_named = w_named.result()

        w_anon = Writer()
        write_anonymous_nbt(body, w_anon)
        data_anon = w_anon.result()

        self.assertEqual(len(data_named) - len(data_anon), 2)
        # mismo tagType al inicio, mismo payload al final -- se saltea
        # exactamente el hueco del nameLen en el medio
        self.assertEqual(data_named[0], data_anon[0])
        self.assertEqual(data_named[3:], data_anon[1:])

    def test_nested_compound_inside_anonymous_root(self):
        tag = {
            "type": "compound",
            "value": {"nested": {"type": "compound", "value": {"inner": {"type": "byte", "value": 7}}}},
        }
        got, _ = roundtrip_anonymous_nbt(tag)
        self.assertEqual(got, tag)

    def test_anon_optional_nbt_is_the_same_wire_format_as_anonymous_nbt(self):
        tag = {"type": "byte", "value": 9}
        w = Writer()
        write_anon_optional_nbt(tag, w)
        data = w.result()
        self.assertEqual(read_anon_optional_nbt(Reader(data)), tag)
        self.assertEqual(read_anonymous_nbt(Reader(data)), tag)  # mismo wire format


class TestAnonymousNbtViaPrimitivesRegistry(unittest.TestCase):
    """Igual que TestNbtViaPrimitivesRegistry pero para los primitivos
    anonymousNbt/anonOptionalNbt -- el seam real que usa core.py."""

    def test_anonymous_nbt_primitive_roundtrip(self):
        tag = {"type": "short", "value": 20}
        w = Writer()
        PRIMITIVES["anonymousNbt"].write(tag, w)
        self.assertEqual(PRIMITIVES["anonymousNbt"].read(Reader(w.result())), tag)

    def test_anon_optional_nbt_primitive_absent(self):
        w = Writer()
        PRIMITIVES["anonOptionalNbt"].write(None, w)
        self.assertEqual(w.result(), b"\x00")
        self.assertIsNone(PRIMITIVES["anonOptionalNbt"].read(Reader(w.result())))


if __name__ == "__main__":
    unittest.main()
