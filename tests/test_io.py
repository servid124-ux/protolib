import unittest

from protolib.io import Reader, Writer, BufferUnderrun


class TestReader(unittest.TestCase):
    def test_read_bytes_advances_offset(self):
        r = Reader(b"hello world")
        self.assertEqual(r.read_bytes(5), b"hello")
        self.assertEqual(r.offset, 5)
        self.assertEqual(r.read_bytes(6), b" world")
        self.assertEqual(r.remaining, 0)

    def test_remaining(self):
        r = Reader(b"abcdef", offset=2)
        self.assertEqual(r.remaining, 4)

    def test_peek_byte_does_not_advance(self):
        r = Reader(b"\x05rest")
        self.assertEqual(r.peek_byte(), 0x05)
        self.assertEqual(r.offset, 0)
        self.assertEqual(r.read_bytes(1), b"\x05")

    def test_underrun_on_read(self):
        r = Reader(b"ab")
        with self.assertRaises(BufferUnderrun) as ctx:
            r.read_bytes(5)
        self.assertEqual(ctx.exception.offset, 0)
        self.assertEqual(ctx.exception.needed, 5)
        self.assertEqual(ctx.exception.available, 2)

    def test_underrun_on_peek_empty(self):
        r = Reader(b"")
        with self.assertRaises(BufferUnderrun):
            r.peek_byte()

    def test_negative_read_raises_value_error(self):
        r = Reader(b"abc")
        with self.assertRaises(ValueError):
            r.read_bytes(-1)

    def test_accepts_bytearray_and_memoryview(self):
        self.assertEqual(Reader(bytearray(b"xy")).read_bytes(2), b"xy")
        self.assertEqual(Reader(memoryview(b"xy")).read_bytes(2), b"xy")

    def test_partial_buffer_still_reports_correct_remaining_after_reads(self):
        r = Reader(b"0123456789")
        r.read_bytes(3)
        r.read_bytes(4)
        self.assertEqual(r.remaining, 3)
        self.assertEqual(r.offset, 7)


class TestWriter(unittest.TestCase):
    def test_accumulates_and_joins(self):
        w = Writer()
        w.write_bytes(b"foo")
        w.write_bytes(b"bar")
        self.assertEqual(w.result(), b"foobar")

    def test_len_counts_total_bytes_written(self):
        w = Writer()
        self.assertEqual(len(w), 0)
        w.write_bytes(b"12345")
        w.write_bytes(b"67")
        self.assertEqual(len(w), 7)

    def test_empty_writer_result_is_empty_bytes(self):
        self.assertEqual(Writer().result(), b"")


if __name__ == "__main__":
    unittest.main()
