import unittest

from protolib.framer import PacketFramer


def framed(payload: bytes) -> bytes:
    return PacketFramer.wrap(payload)


class TestWrap(unittest.TestCase):
    def test_wrap_prefixes_varint_length(self):
        data = framed(b"hello")
        self.assertEqual(data, b"\x05hello")


class TestFeedSingleChunk(unittest.TestCase):
    def test_one_complete_frame(self):
        f = PacketFramer()
        frames = f.feed(framed(b"hello"))
        self.assertEqual(frames, [b"hello"])

    def test_multiple_frames_stuck_together(self):
        f = PacketFramer()
        frames = f.feed(framed(b"one") + framed(b"two") + framed(b"three"))
        self.assertEqual(frames, [b"one", b"two", b"three"])

    def test_empty_feed_returns_nothing(self):
        f = PacketFramer()
        self.assertEqual(f.feed(b""), [])


class TestFeedAcrossMultipleChunks(unittest.TestCase):
    def test_frame_split_byte_by_byte(self):
        f = PacketFramer()
        packet = framed(b"streamed slowly")
        collected: list[bytes] = []
        for i in range(len(packet)):
            collected.extend(f.feed(packet[i:i + 1]))
        self.assertEqual(collected, [b"streamed slowly"])

    def test_partial_frame_then_rest_arrives_later(self):
        f = PacketFramer()
        packet = framed(b"abcdefgh")
        self.assertEqual(f.feed(packet[:3]), [])   # not enough yet
        self.assertEqual(f.feed(packet[3:]), [b"abcdefgh"])

    def test_leftover_bytes_after_a_full_frame_stay_buffered(self):
        f = PacketFramer()
        first = framed(b"first")
        second = framed(b"second")
        # feed first frame plus a few leading bytes of the second one
        self.assertEqual(f.feed(first + second[:2]), [b"first"])
        self.assertEqual(f.feed(second[2:]), [b"second"])

    def test_length_prefix_itself_split_across_chunks(self):
        # varint(300) is 2 bytes (0xac 0x02); feed them one at a time
        # before any payload arrives at all.
        f = PacketFramer()
        packet = framed(b"x" * 300)
        self.assertEqual(f.feed(packet[:1]), [])  # only first length byte
        self.assertEqual(f.feed(packet[1:2]), [])  # second length byte, no payload yet
        frames = f.feed(packet[2:])
        self.assertEqual(frames, [b"x" * 300])


class TestMalformedInput(unittest.TestCase):
    def test_negative_length_prefix_raises(self):
        f = PacketFramer()
        # varint encoding of -1 (32-bit signed LEB128): 0xff 0xff 0xff 0xff 0x0f
        with self.assertRaises(ValueError):
            f.feed(b"\xff\xff\xff\xff\x0f")


if __name__ == "__main__":
    unittest.main()
