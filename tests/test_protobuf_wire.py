import unittest

from protolib.protobuf import wire
from protolib.protobuf.errors import ProtobufDecodeError, VarintTooLongError
from protolib.io import Reader, Writer


class TestRawVarint(unittest.TestCase):
    def test_official_spec_vector_150(self):
        # https://protobuf.dev/programming-guides/encoding/ : the
        # canonical worked example, 150 encodes as 0x96 0x01.
        w = Writer()
        wire.write_raw_varint(150, w)
        self.assertEqual(w.result(), b"\x96\x01")
        r = Reader(w.result())
        self.assertEqual(wire.read_raw_varint(r), 150)

    def test_single_byte_roundtrip(self):
        for v in (0, 1, 127):
            w = Writer()
            wire.write_raw_varint(v, w)
            self.assertEqual(len(w.result()), 1)
            self.assertEqual(wire.read_raw_varint(Reader(w.result())), v)

    def test_negative_raises(self):
        with self.assertRaises(ValueError):
            wire.write_raw_varint(-1, Writer())

    def test_overlong_varint_raises(self):
        # 10 continuation bytes with the continuation bit still set on
        # the 10th -- exceeds the spec's own cap.
        malformed = bytes([0x80] * 10 + [0x01])
        with self.assertRaises(VarintTooLongError):
            wire.read_raw_varint(Reader(malformed))


class TestTags(unittest.TestCase):
    def test_field_1_varint_tag_is_0x08(self):
        w = Writer()
        wire.write_tag(1, wire.WIRETYPE_VARINT, w)
        self.assertEqual(w.result(), b"\x08")

    def test_roundtrip(self):
        w = Writer()
        wire.write_tag(31, wire.WIRETYPE_LEN, w)
        fn, wt = wire.read_tag(Reader(w.result()))
        self.assertEqual((fn, wt), (31, wire.WIRETYPE_LEN))

    def test_field_number_zero_rejected_on_write(self):
        with self.assertRaises(ValueError):
            wire.write_tag(0, wire.WIRETYPE_VARINT, Writer())

    def test_field_number_zero_rejected_on_read(self):
        # tag byte 0x00 == (field 0 << 3) | wiretype 0
        with self.assertRaises(ProtobufDecodeError):
            wire.read_tag(Reader(b"\x00"))

    def test_deprecated_group_wiretype_rejected(self):
        # wire type 3 (GROUP_START), field 1 -> tag byte 0x0B
        with self.assertRaises(ProtobufDecodeError):
            wire.read_tag(Reader(b"\x0b"))


class TestZigzag(unittest.TestCase):
    def test_official_spec_table_32bit(self):
        # https://protobuf.dev/programming-guides/encoding/#signed-ints
        table = [(0, 0), (-1, 1), (1, 2), (-2, 3), (0x7FFFFFFF, 0xFFFFFFFE), (-0x80000000, 0xFFFFFFFF)]
        for signed, expected in table:
            with self.subTest(signed=signed):
                self.assertEqual(wire.zigzag_encode(signed, 32), expected)
                self.assertEqual(wire.zigzag_decode(expected), signed)


class TestVarintScalars(unittest.TestCase):
    def test_int32_official_vector(self):
        w = Writer()
        wire.write_int32(150, w)
        self.assertEqual(w.result(), b"\x96\x01")

    def test_negative_int32_is_ten_bytes(self):
        # Spec-mandated (if wasteful) behavior: a negative int32 is
        # sign-extended to the full 64-bit varint form on the wire.
        w = Writer()
        wire.write_int32(-1, w)
        self.assertEqual(len(w.result()), 10)
        self.assertEqual(wire.read_int32(Reader(w.result())), -1)

    def test_sint32_uses_zigzag_and_is_compact(self):
        w = Writer()
        wire.write_sint32(-1, w)
        self.assertEqual(len(w.result()), 1)  # zigzag(-1) == 1, one byte
        self.assertEqual(wire.read_sint32(Reader(w.result())), -1)

    def test_uint32_range_enforced(self):
        with self.assertRaises(ValueError):
            wire.write_uint32(-1, Writer())
        with self.assertRaises(ValueError):
            wire.write_uint32(2**32, Writer())

    def test_bool_any_nonzero_is_true(self):
        # Per spec, decoders must accept any nonzero varint as true,
        # not just the canonical 1.
        r = Reader(bytes([42]))
        self.assertTrue(wire.read_bool(r))


class TestFixedWidth(unittest.TestCase):
    def test_fixed32_little_endian(self):
        w = Writer()
        wire.write_fixed32(1, w)
        self.assertEqual(w.result(), b"\x01\x00\x00\x00")

    def test_fixed64_little_endian(self):
        w = Writer()
        wire.write_fixed64(1, w)
        self.assertEqual(w.result(), b"\x01\x00\x00\x00\x00\x00\x00\x00")

    def test_float_roundtrip(self):
        w = Writer()
        wire.write_float(1.5, w)
        self.assertAlmostEqual(wire.read_float(Reader(w.result())), 1.5, places=5)

    def test_double_roundtrip(self):
        w = Writer()
        wire.write_double(3.14159265358979, w)
        self.assertAlmostEqual(wire.read_double(Reader(w.result())), 3.14159265358979, places=12)

    def test_sfixed32_negative(self):
        w = Writer()
        wire.write_sfixed32(-1, w)
        r = Reader(w.result())
        self.assertEqual(wire.read_sfixed32(r), -1)


class TestLenDelimited(unittest.TestCase):
    def test_string_official_vector(self):
        # https://protobuf.dev/programming-guides/encoding/ : b = "testing"
        w = Writer()
        wire.write_string("testing", w)
        self.assertEqual(w.result(), bytes.fromhex("0774657374696e67"))

    def test_invalid_utf8_raises(self):
        w = Writer()
        wire.write_len_delimited(b"\xff\xfe", w)
        with self.assertRaises(ProtobufDecodeError):
            wire.read_string(Reader(w.result()))

    def test_bytes_are_not_decoded(self):
        w = Writer()
        wire.write_len_delimited(b"\xff\xfe\x00", w)
        self.assertEqual(wire.read_len_delimited(Reader(w.result())), b"\xff\xfe\x00")


class TestSkipField(unittest.TestCase):
    def test_skips_each_wire_type_correctly(self):
        w = Writer()
        wire.write_raw_varint(999, w)
        r = Reader(w.result())
        wire.skip_field(wire.WIRETYPE_VARINT, r)
        self.assertEqual(r.offset, len(w.result()))

        r2 = Reader(b"\x00" * 8)
        wire.skip_field(wire.WIRETYPE_I64, r2)
        self.assertEqual(r2.offset, 8)

        r3 = Reader(b"\x00" * 4)
        wire.skip_field(wire.WIRETYPE_I32, r3)
        self.assertEqual(r3.offset, 4)


if __name__ == "__main__":
    unittest.main()
