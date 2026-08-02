import unittest

from protolib.io import Reader, Writer
from protolib.nbt import read_nbt, write_nbt, read_optional_nbt, write_optional_nbt
from protolib.primitives import PRIMITIVES


def roundtrip_nbt(tag):
    w = Writer()
    write_nbt(tag, w)
    data = w.result()
    return read_nbt(Reader(data)), data


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


if __name__ == "__main__":
    unittest.main()
