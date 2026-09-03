"""
tests/test_raknet_types.py

Covers the RakNet-specific types added for compatibility with
protocols beyond Minecraft (node-raknet, and by extension anything
built on RakNet, like Minecraft Bedrock's transport layer):

  - raknetMagic: the fixed 16-byte "magic" sequence marking OFFLINE
    RakNet packets.
  - raknetAddress: RakNet's SystemAddress, IPv4 (7 bytes, bit-inverted
    address) or IPv6 (29 bytes).

Reference: https://github.com/vp817/RakNetProtocolDoc ("magic" and
"Address DataType" sections).

Note: RakNet's `bit` datatype (1 bit, MSb-first, padded to a full
byte) is NOT a separate primitive here -- it's already covered
exactly by the existing `bitfield` composite (a single-field bitfield
with size=1 produces byte-identical output; see
TestBitCoveredByBitfield below).
"""

import random
import unittest

from protolib.core import Protocol
from protolib.errors import MagicMismatchError, InvalidTypeDefinition
from protolib.io import Reader, Writer
from protolib.primitives import PRIMITIVES

RAKNET_MAGIC_BYTES = bytes([
    0x00, 0xFF, 0xFF, 0x00, 0xFE, 0xFE, 0xFE, 0xFE,
    0xFD, 0xFD, 0xFD, 0xFD, 0x12, 0x34, 0x56, 0x78,
])

# Minimal protocol with just the natives raknetAddress' implementation
# calls into (u8/u16/u32/lu16/varint) -- no state, tests call
# read_type/write_type directly with scope=None.
_ADDR_PROTOCOL = {
    "types": {
        "varint": "native", "u8": "native", "u16": "native",
        "u32": "native", "lu16": "native",
    }
}


class TestRaknetMagic(unittest.TestCase):
    def setUp(self):
        self.magic = PRIMITIVES["raknetMagic"]

    def test_write_always_emits_the_constant(self):
        w = Writer()
        self.magic.write(None, w)  # value is ignored by design
        self.assertEqual(w.result(), RAKNET_MAGIC_BYTES)

    def test_write_ignores_whatever_value_is_passed(self):
        w = Writer()
        self.magic.write(b"anything, doesn't matter", w)
        self.assertEqual(w.result(), RAKNET_MAGIC_BYTES)

    def test_read_accepts_the_correct_bytes(self):
        r = Reader(RAKNET_MAGIC_BYTES)
        got = self.magic.read(r)
        self.assertEqual(got, RAKNET_MAGIC_BYTES)

    def test_read_rejects_wrong_bytes(self):
        r = Reader(b"\x00" * 16)
        with self.assertRaises(MagicMismatchError):
            self.magic.read(r)

    def test_read_rejects_off_by_one_byte(self):
        # last byte differs from the real magic (0x78 -> 0x79)
        almost = RAKNET_MAGIC_BYTES[:-1] + bytes([0x79])
        r = Reader(almost)
        with self.assertRaises(MagicMismatchError):
            self.magic.read(r)

    def test_size_of_is_16(self):
        self.assertEqual(self.magic.size_of(None), 16)

    def test_roundtrip(self):
        w = Writer()
        self.magic.write(None, w)
        data = w.result()
        r = Reader(data)
        got = self.magic.read(r)
        self.assertEqual(got, RAKNET_MAGIC_BYTES)
        self.assertEqual(len(data), 16)


class TestRaknetAddressIPv4(unittest.TestCase):
    def setUp(self):
        self.proto = Protocol(_ADDR_PROTOCOL)

    def _write(self, value):
        w = Writer()
        self.proto.write_type(["raknetAddress", {}], value, w, None, {})
        return w.result()

    def _read(self, data):
        r = Reader(data)
        return self.proto.read_type(["raknetAddress", {}], r, None, {})

    def test_known_value_localhost(self):
        # Verified by hand against the spec (bit-inverted u32 BE address
        # + u16 BE port), not just via self-roundtrip -- see the
        # conversation this was developed in for the manual byte
        # breakdown that matches this exact hex.
        value = {"version": 4, "address": "127.0.0.1", "port": 19132}
        data = self._write(value)
        self.assertEqual(data, bytes.fromhex("0480fffffe4abc"))
        self.assertEqual(len(data), 7)
        self.assertEqual(self._read(data), value)

    def test_all_zero_address(self):
        value = {"version": 4, "address": "0.0.0.0", "port": 0}
        data = self._write(value)
        self.assertEqual(len(data), 7)
        self.assertEqual(self._read(data), value)

    def test_broadcast_address_and_max_port(self):
        value = {"version": 4, "address": "255.255.255.255", "port": 65535}
        data = self._write(value)
        self.assertEqual(self._read(data), value)

    def test_bit_inversion_is_correct_not_just_self_consistent(self):
        # Manually compute the expected wire bytes independent of the
        # engine, to catch a bug that inverts on both write AND read
        # symmetrically (which a plain roundtrip test would miss).
        value = {"version": 4, "address": "192.168.1.42", "port": 25565}
        data = self._write(value)
        raw_u32 = int.from_bytes(data[1:5], "big")
        un_inverted = raw_u32 ^ 0xFFFFFFFF
        octets = [(un_inverted >> s) & 0xFF for s in (24, 16, 8, 0)]
        self.assertEqual(octets, [192, 168, 1, 42])
        port = int.from_bytes(data[5:7], "big")
        self.assertEqual(port, 25565)

    def test_fuzz_random_ipv4_addresses(self):
        random.seed(777)
        for _ in range(500):
            octets = [random.randint(0, 255) for _ in range(4)]
            value = {
                "version": 4,
                "address": ".".join(str(o) for o in octets),
                "port": random.randint(0, 65535),
            }
            data = self._write(value)
            self.assertEqual(len(data), 7)
            self.assertEqual(self._read(data), value)

    def test_malformed_address_string_raises(self):
        with self.assertRaises(InvalidTypeDefinition):
            self._write({"version": 4, "address": "1.2.3", "port": 0})


class TestRaknetAddressIPv6(unittest.TestCase):
    def setUp(self):
        self.proto = Protocol(_ADDR_PROTOCOL)

    def _write(self, value):
        w = Writer()
        self.proto.write_type(["raknetAddress", {}], value, w, None, {})
        return w.result()

    def _read(self, data):
        r = Reader(data)
        return self.proto.read_type(["raknetAddress", {}], r, None, {})

    def test_known_value(self):
        value = {
            "version": 6, "address_family": 23, "port": 19133,
            "flow_info": 0, "address": bytes(range(16)), "scope_id": 0,
        }
        data = self._write(value)
        self.assertEqual(len(data), 29)
        self.assertEqual(self._read(data), value)

    def test_field_endianness_matches_spec(self):
        # address_family is little-endian; everything else (port,
        # flow_info, scope_id) is big-endian -- verified by hand.
        value = {
            "version": 6, "address_family": 0x1234, "port": 0x5678,
            "flow_info": 0xAABBCCDD, "address": b"\xff" * 16,
            "scope_id": 0x11223344,
        }
        data = self._write(value)
        self.assertEqual(data[0], 6)
        self.assertEqual(int.from_bytes(data[1:3], "little"), 0x1234)
        self.assertEqual(int.from_bytes(data[3:5], "big"), 0x5678)
        self.assertEqual(int.from_bytes(data[5:9], "big"), 0xAABBCCDD)
        self.assertEqual(data[9:25], b"\xff" * 16)
        self.assertEqual(int.from_bytes(data[25:29], "big"), 0x11223344)

    def test_wrong_address_length_raises(self):
        value = {
            "version": 6, "address_family": 23, "port": 0,
            "flow_info": 0, "address": b"\x00" * 15,  # 15, not 16
            "scope_id": 0,
        }
        with self.assertRaises(InvalidTypeDefinition):
            self._write(value)


class TestRaknetAddressErrors(unittest.TestCase):
    def setUp(self):
        self.proto = Protocol(_ADDR_PROTOCOL)

    def test_unsupported_version_on_write_raises(self):
        w = Writer()
        with self.assertRaises(InvalidTypeDefinition):
            self.proto.write_type(["raknetAddress", {}], {"version": 5}, w, None, {})

    def test_unsupported_version_on_read_raises(self):
        data = bytes([5]) + b"\x00" * 20
        r = Reader(data)
        with self.assertRaises(InvalidTypeDefinition):
            self.proto.read_type(["raknetAddress", {}], r, None, {})


class TestBitCoveredByBitfield(unittest.TestCase):
    """
    RakNet's `bit` datatype (1 bit, MSb-first, padded to a full byte
    with the remaining bits) doesn't need its own primitive -- a
    single-field `bitfield` with size=1 already produces the exact
    same bytes. This test documents/locks in that equivalence rather
    than adding a redundant type.
    """

    def setUp(self):
        self.proto = Protocol({
            "types": {
                "varint": "native",
                "is_valid": ["container", [
                    {"name": "is_valid", "type": ["bitfield", [
                        {"name": "is_valid", "size": 1, "signed": False},
                    ]]},
                ]],
                "three_flags": ["container", [
                    {"name": "flags", "type": ["bitfield", [
                        {"name": "is_valid", "size": 1, "signed": False},
                        {"name": "is_ack", "size": 1, "signed": False},
                        {"name": "is_nack", "size": 1, "signed": False},
                    ]]},
                ]],
            }
        })

    def test_single_bit_true_pads_to_full_byte_msb_first(self):
        w = Writer()
        self.proto.write_type("is_valid", {"is_valid": {"is_valid": 1}}, w, None, {})
        # 1 followed by 7 zero padding bits, MSb-first -> 0b10000000
        self.assertEqual(w.result(), bytes([0b10000000]))

    def test_three_bits_msb_first_with_padding(self):
        w = Writer()
        value = {"flags": {"is_valid": 1, "is_ack": 0, "is_nack": 1}}
        self.proto.write_type("three_flags", value, w, None, {})
        # 1,0,1 then 5 padding bits -> 0b10100000 = 0xA0
        self.assertEqual(w.result(), bytes([0xA0]))
        r = Reader(w.result())
        parsed = self.proto.read_type("three_flags", r, None, {})
        self.assertEqual(parsed, value)


if __name__ == "__main__":
    unittest.main()
