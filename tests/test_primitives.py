import unittest

from protolib.io import Reader, Writer
from protolib.primitives import (
    PRIMITIVES,
    make_fixed_utf16be_string,
    make_fixed_cp437_string,
    make_fixed_buffer,
)


def roundtrip(prim, value):
    w = Writer()
    prim.write(value, w)
    data = w.result()
    r = Reader(data)
    return prim.read(r), data


class TestFixedWidthIntFamily(unittest.TestCase):
    """u8..u64 / i8..i64 (incl. 24/40/48/56) big-endian, and their l-prefixed
    little-endian twins. Data-driven over the whole {u,i} x {8,16,24,32,40,48,56,64}
    family described in the README so a gap in any single width shows up here."""

    WIDTHS = (8, 16, 24, 32, 40, 48, 56, 64)

    def test_full_matrix_roundtrips_at_boundaries(self):
        for bits in self.WIDTHS:
            for signed in (False, True):
                base = ("i" if signed else "u") + str(bits)
                for name in (base, "l" + base):
                    with self.subTest(name=name):
                        prim = PRIMITIVES[name]
                        if signed:
                            lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
                        else:
                            lo, hi = 0, (1 << bits) - 1
                        for value in (lo, hi, (lo + hi) // 2):
                            got, data = roundtrip(prim, value)
                            self.assertEqual(got, value)
                            self.assertEqual(len(data), bits // 8)

    def test_big_vs_little_endian_byte_order_differs(self):
        w_be = Writer()
        PRIMITIVES["u16"].write(0x0102, w_be)
        self.assertEqual(w_be.result(), b"\x01\x02")

        w_le = Writer()
        PRIMITIVES["lu16"].write(0x0102, w_le)
        self.assertEqual(w_le.result(), b"\x02\x01")

    def test_li8_lu8_are_valid_aliases_with_no_real_endianness(self):
        # 1 byte has no endianness; li8/lu8 exist so protocol.json can
        # reference them uniformly alongside the other l* names.
        self.assertEqual(PRIMITIVES["li8"].read(Reader(b"\xff")), -1)
        self.assertEqual(PRIMITIVES["lu8"].read(Reader(b"\xff")), 255)

    def test_overflow_raises_instead_of_silently_wrapping(self):
        # u8/i8 go through struct.pack; u24/i24 (etc.) go through
        # int.to_bytes. Both should fail loudly on an out-of-range value
        # rather than silently truncating/wrapping bits.
        with self.assertRaises(Exception):
            PRIMITIVES["u8"].write(256, Writer())
        with self.assertRaises(Exception):
            PRIMITIVES["i8"].write(128, Writer())
        with self.assertRaises(Exception):
            PRIMITIVES["u24"].write(1 << 24, Writer())

    def test_floats(self):
        for name in ("f32", "f64", "lf32", "lf64"):
            got, _ = roundtrip(PRIMITIVES[name], 3.5)  # exact in binary fp
            self.assertAlmostEqual(got, 3.5, places=5)

    def test_f16_roundtrip_and_size(self):
        # 3.5 es exacto en binary16 igual que en binary32/64 (mantisa
        # simple), así que sirve para comparar directo con test_floats.
        for name in ("f16", "lf16"):
            with self.subTest(name=name):
                got, data = roundtrip(PRIMITIVES[name], 3.5)
                self.assertEqual(len(data), 2)
                self.assertAlmostEqual(got, 3.5, places=3)

    def test_f16_big_vs_little_endian_byte_order_differs(self):
        w_be = Writer()
        PRIMITIVES["f16"].write(1.5, w_be)
        self.assertEqual(w_be.result(), b"\x3e\x00")

        w_le = Writer()
        PRIMITIVES["lf16"].write(1.5, w_le)
        self.assertEqual(w_le.result(), b"\x00\x3e")

    def test_f16_precision_loss_vs_f32(self):
        # binary16 tiene solo 10 bits de mantisa: un valor que sí es
        # exacto en f32 puede perder precisión al pasar por f16. Esto
        # documenta el comportamiento (no es un bug), para que quede
        # claro por qué f16 no es un reemplazo drop-in de f32.
        value = 1.0 / 3.0
        got_f32, _ = roundtrip(PRIMITIVES["f32"], value)
        got_f16, _ = roundtrip(PRIMITIVES["f16"], value)
        self.assertNotEqual(got_f32, got_f16)
        self.assertAlmostEqual(got_f16, value, places=2)


class TestVarints(unittest.TestCase):
    def test_varint_known_byte_encoding_300(self):
        # Textbook Minecraft varint(300) == 0xAC 0x02 (wiki.vg reference value).
        w = Writer()
        PRIMITIVES["varint"].write(300, w)
        self.assertEqual(w.result(), b"\xac\x02")
        self.assertEqual(PRIMITIVES["varint"].read(Reader(b"\xac\x02")), 300)

    def test_varint_negative_one_is_five_bytes(self):
        # Signed varint(-1) == 0xff 0xff 0xff 0xff 0x0f (well-known reference value).
        w = Writer()
        PRIMITIVES["varint"].write(-1, w)
        self.assertEqual(w.result(), b"\xff\xff\xff\xff\x0f")
        self.assertEqual(PRIMITIVES["varint"].read(Reader(b"\xff\xff\xff\xff\x0f")), -1)

    def test_varint_single_byte_boundary(self):
        w0 = Writer(); PRIMITIVES["varint"].write(127, w0)
        self.assertEqual(w0.result(), b"\x7f")
        w1 = Writer(); PRIMITIVES["varint"].write(128, w1)
        self.assertEqual(w1.result(), b"\x80\x01")

    def test_varint_roundtrip_range(self):
        for value in (0, 1, -1, 127, 128, -128, 2**20, -(2**20), 2**31 - 1, -(2**31)):
            got, _ = roundtrip(PRIMITIVES["varint"], value)
            self.assertEqual(got, value)

    def test_varlong_roundtrip_64bit_range(self):
        for value in (0, 2**62, -(2**62), 2**63 - 1, -(2**63)):
            got, _ = roundtrip(PRIMITIVES["varlong"], value)
            self.assertEqual(got, value)

    def test_uvarint_never_goes_negative(self):
        got, _ = roundtrip(PRIMITIVES["uvarint"], 2**31 + 5)
        self.assertEqual(got, 2**31 + 5)

    def test_varint128_handles_values_above_64_bits(self):
        big = (1 << 100) + 12345
        got, data = roundtrip(PRIMITIVES["varint128"], big)
        self.assertEqual(got, big)
        self.assertLessEqual(len(data), 19)  # ceil(128/7) == 19 per the module's own docstring

    def test_uvarint128_zero_and_max_ish(self):
        got, _ = roundtrip(PRIMITIVES["uvarint128"], 0)
        self.assertEqual(got, 0)
        huge = (1 << 127)
        got, _ = roundtrip(PRIMITIVES["uvarint128"], huge)
        self.assertEqual(got, huge)

    def test_zigzag32_matches_protobuf_reference_mapping(self):
        # protobuf zigzag: 0,-1,1,-2,2 -> 0,1,2,3,4
        cases = {0: 0, -1: 1, 1: 2, -2: 3, 2: 4}
        for value, zigzag_raw in cases.items():
            w = Writer()
            PRIMITIVES["zigzag32"].write(value, w)
            data = w.result()
            self.assertEqual(PRIMITIVES["uvarint"].read(Reader(data)), zigzag_raw)
            self.assertEqual(PRIMITIVES["zigzag32"].read(Reader(data)), value)

    def test_zigzag64_roundtrip(self):
        for value in (0, -1, 1, 2**40, -(2**40)):
            got, _ = roundtrip(PRIMITIVES["zigzag64"], value)
            self.assertEqual(got, value)


class TestBoolVoidCstringUUID(unittest.TestCase):
    def test_bool(self):
        self.assertEqual(roundtrip(PRIMITIVES["bool"], True)[1], b"\x01")
        self.assertEqual(roundtrip(PRIMITIVES["bool"], False)[1], b"\x00")
        # anything nonzero reads back as True, mirroring real client behavior
        self.assertTrue(PRIMITIVES["bool"].read(Reader(b"\x2a")))

    def test_void_reads_nothing_writes_nothing(self):
        r = Reader(b"untouched")
        self.assertIsNone(PRIMITIVES["void"].read(r))
        self.assertEqual(r.offset, 0)
        w = Writer()
        PRIMITIVES["void"].write("anything", w)
        self.assertEqual(w.result(), b"")

    def test_cstring_roundtrip_and_null_termination(self):
        got, data = roundtrip(PRIMITIVES["cstring"], "hola")
        self.assertEqual(got, "hola")
        self.assertEqual(data, b"hola\x00")

    def test_uuid_roundtrip_and_no_dashes_on_wire(self):
        u = "11111111-1111-1111-1111-111111111111"
        got, data = roundtrip(PRIMITIVES["UUID"], u)
        self.assertEqual(got, u)
        self.assertEqual(len(data), 16)


class TestFixedWidthStringsAndBuffers(unittest.TestCase):
    def test_string64_pads_and_strips_spaces(self):
        got, data = roundtrip(PRIMITIVES["string64"], "hola")
        self.assertEqual(len(data), 64)
        self.assertEqual(got, "hola")
        self.assertEqual(data[:4], b"hola")
        self.assertEqual(data[4:], b" " * 60)

    def test_string64_truncates_overlong_input(self):
        long_name = "x" * 100
        _, data = roundtrip(PRIMITIVES["string64"], long_name)
        self.assertEqual(len(data), 64)

    def test_string64_replaces_chars_cp437_cannot_encode(self):
        # per primitives.py's own comment: errors="replace" so unencodable
        # chat text doesn't crash the server.
        got, _ = roundtrip(PRIMITIVES["string64"], "hola \U0001F600 pana")
        self.assertIn("?", got)

    def test_utf16be64_pads_and_strips_spaces(self):
        got, data = roundtrip(PRIMITIVES["utf16be64"], "hola")
        self.assertEqual(len(data), 128)  # 64 chars * 2 bytes (UTF-16BE)
        self.assertEqual(got, "hola")
        self.assertEqual(data[:8], "hola".encode("utf-16-be"))
        self.assertEqual(data[8:], (" " * 60).encode("utf-16-be"))

    def test_utf16be64_truncates_overlong_input(self):
        long_name = "x" * 100
        _, data = roundtrip(PRIMITIVES["utf16be64"], long_name)
        self.assertEqual(len(data), 128)

    def test_utf16be64_handles_non_latin_chars(self):
        # a diferencia de string64 (CP437, se come lo que no entra en la
        # codepage con "?"), UTF-16BE sí representa caracteres fuera de
        # ASCII sin pérdida -- este es justo el caso que motivó dejar
        # utf16be64 como instancia lista aparte de string64.
        got, _ = roundtrip(PRIMITIVES["utf16be64"], "héllo wörld")
        self.assertEqual(got, "héllo wörld")

    def test_buffer1024_pads_with_zero_bytes(self):
        _, data = roundtrip(PRIMITIVES["buffer1024"], b"\x01" * 10)
        self.assertEqual(len(data), 1024)
        self.assertEqual(data[:10], b"\x01" * 10)
        self.assertEqual(data[10:], b"\x00" * 1014)

    def test_buffer64_truncates_overlong_input(self):
        _, data = roundtrip(PRIMITIVES["buffer64"], b"\xff" * 200)
        self.assertEqual(len(data), 64)
        self.assertEqual(data, b"\xff" * 64)

    def test_fixed_coord_and_delta_are_i16_i8_under_the_hood(self):
        self.assertEqual(roundtrip(PRIMITIVES["fixedCoord"], 320)[1], roundtrip(PRIMITIVES["i16"], 320)[1])
        self.assertEqual(roundtrip(PRIMITIVES["fixedCoordDelta"], -5)[1], roundtrip(PRIMITIVES["i8"], -5)[1])

    def test_make_fixed_utf16be_string_helper(self):
        prim = make_fixed_utf16be_string(8)
        got, data = roundtrip(prim, "hi")
        self.assertEqual(got, "hi")
        self.assertEqual(len(data), 16)  # 8 chars * 2 bytes (UTF-16BE)

    def test_make_fixed_cp437_string_helper_independent_of_string64(self):
        prim = make_fixed_cp437_string(4)
        got, data = roundtrip(prim, "ok")
        self.assertEqual(got, "ok")
        self.assertEqual(len(data), 4)

    def test_make_fixed_buffer_helper(self):
        prim = make_fixed_buffer(8)
        _, data = roundtrip(prim, b"ab")
        self.assertEqual(data, b"ab\x00\x00\x00\x00\x00\x00")


if __name__ == "__main__":
    unittest.main()
