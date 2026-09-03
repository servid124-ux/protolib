"""
tests/test_stateless_protocol.py

Covers Protocol's support for protocols that declare NO states at all
(everything lives directly under the top-level `types:` block) -- the
shape of Minecraft Bedrock's protocol.json, as opposed to Java's
(handshaking/status/login/play, each with toClient/toServer).

Uses small synthetic protocol dicts (not real Minecraft data) so these
tests don't depend on any external file.
"""

import unittest

from protolib.core import Protocol
from protolib.errors import StatelessProtocolError, UnknownTypeError


# A minimal Bedrock-shaped protocol: no state keys, a "packet" dispatcher
# type living directly in the global `types:` block under a custom name
# (mcpe_packet, matching the real Bedrock protocol.json), same
# {name: mapper, params: switch} pattern as Java's "packet" type.
STATELESS_PROTOCOL = {
    "types": {
        "varint": "native",
        "bool": "native",
        "i32": "native",
        "string": ["pstring", {"countType": "varint"}],
        "packet_disconnect": ["container", [
            {"name": "message", "type": "string"},
        ]],
        "packet_status": ["container", [
            {"name": "status", "type": "i32"},
        ]],
        "mcpe_packet": ["container", [
            {"name": "name", "type": ["mapper", {
                "type": "varint",
                "mappings": {
                    "1": "status",
                    "2": "disconnect",
                },
            }]},
            {"name": "params", "type": ["switch", {
                "compareTo": "name",
                "fields": {
                    "status": "packet_status",
                    "disconnect": "packet_disconnect",
                },
            }]},
        ]],
    },
}

# A minimal Java-shaped protocol: real states, "packet" living under
# state.direction.types, exactly the pre-existing shape.
STATEFUL_PROTOCOL = {
    "types": {
        "varint": "native",
        "i32": "native",
    },
    "play": {
        "toClient": {
            "types": {
                "packet_ping": ["container", [
                    {"name": "id", "type": "i32"},
                ]],
                "packet": ["container", [
                    {"name": "name", "type": ["mapper", {
                        "type": "varint",
                        "mappings": {"1": "ping"},
                    }]},
                    {"name": "params", "type": ["switch", {
                        "compareTo": "name",
                        "fields": {"ping": "packet_ping"},
                    }]},
                ]],
            },
        },
    },
}


class TestStatelessProtocolRoundtrip(unittest.TestCase):
    """Bedrock-shaped protocol: state=None, direction=None throughout."""

    def setUp(self):
        self.proto = Protocol(STATELESS_PROTOCOL, packet_type_name="mcpe_packet")

    def test_serialize_then_parse_simple_packet(self):
        data = self.proto.serialize_packet(None, None, "status", {"status": 7})
        parsed = self.proto.parse_packet(None, None, data)
        self.assertEqual(parsed.name, "status")
        self.assertEqual(parsed.params, {"status": 7})

    def test_serialize_then_parse_string_field_packet(self):
        data = self.proto.serialize_packet(None, None, "disconnect", {"message": "adiós"})
        parsed = self.proto.parse_packet(None, None, data)
        self.assertEqual(parsed.name, "disconnect")
        self.assertEqual(parsed.params, {"message": "adiós"})

    def test_read_named_write_named_bypass_dispatcher(self):
        data = self.proto.write_named(None, None, "packet_status", {"status": 3})
        got = self.proto.read_named(None, None, "packet_status", data)
        self.assertEqual(got, {"status": 3})

    def test_default_packet_type_name_is_still_packet(self):
        # packet_type_name defaults to "packet" -- a stateless protocol
        # that (unusually) named its dispatcher "packet" instead of
        # something custom should still work with no extra argument.
        proto = Protocol({
            "types": {
                "varint": "native",
                "i32": "native",
                "packet_ping": ["container", [{"name": "id", "type": "i32"}]],
                "packet": ["container", [
                    {"name": "name", "type": ["mapper", {
                        "type": "varint", "mappings": {"1": "ping"},
                    }]},
                    {"name": "params", "type": ["switch", {
                        "compareTo": "name", "fields": {"ping": "packet_ping"},
                    }]},
                ]],
            },
        })
        data = proto.serialize_packet(None, None, "ping", {"id": 5})
        parsed = proto.parse_packet(None, None, data)
        self.assertEqual(parsed.name, "ping")
        self.assertEqual(parsed.params, {"id": 5})


class TestStatefulProtocolUnchanged(unittest.TestCase):
    """Java-shaped protocol: existing state/direction behavior must be
    completely unaffected by the stateless-support changes."""

    def setUp(self):
        self.proto = Protocol(STATEFUL_PROTOCOL)

    def test_serialize_then_parse_with_real_state_direction(self):
        data = self.proto.serialize_packet("play", "toClient", "ping", {"id": 99})
        parsed = self.proto.parse_packet("play", "toClient", data)
        self.assertEqual(parsed.name, "ping")
        self.assertEqual(parsed.params, {"id": 99})

    def test_get_scope_still_works_directly(self):
        scope = self.proto.get_scope("play", "toClient")
        self.assertIn("packet", scope.types)

    def test_unknown_state_direction_still_raises_unknown_type_error(self):
        with self.assertRaises(UnknownTypeError):
            self.proto.get_scope("play", "toServer")  # not defined in STATEFUL_PROTOCOL


class TestStatelessMismatchErrors(unittest.TestCase):
    """Calling with the wrong shape of state/direction for what the
    loaded protocol actually declares must fail clearly, not with a
    confusing low-level KeyError/UnknownTypeError."""

    def test_stateful_protocol_called_with_none_raises_stateless_error(self):
        proto = Protocol(STATEFUL_PROTOCOL)
        with self.assertRaises(StatelessProtocolError) as cm:
            proto.parse_packet(None, None, b"\x00")
        self.assertFalse(cm.exception.stateless)

    def test_stateless_protocol_called_with_real_state_raises_stateless_error(self):
        proto = Protocol(STATELESS_PROTOCOL, packet_type_name="mcpe_packet")
        with self.assertRaises(StatelessProtocolError) as cm:
            proto.parse_packet("play", "toClient", b"\x00")
        self.assertTrue(cm.exception.stateless)

    def test_stateless_protocol_partial_none_still_raises(self):
        # state=None but direction given (or vice versa) is still a
        # mismatch, not silently accepted.
        proto = Protocol(STATELESS_PROTOCOL, packet_type_name="mcpe_packet")
        with self.assertRaises(StatelessProtocolError):
            proto.parse_packet(None, "toClient", b"\x00")


if __name__ == "__main__":
    unittest.main()
