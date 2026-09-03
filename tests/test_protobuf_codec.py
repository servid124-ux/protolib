import unittest

from protolib.protobuf import ProtoFileSchema, wire
from protolib.protobuf.proto_codec import ProtoOneofViolationError
from protolib.protobuf.errors import ProtobufDecodeError
from protolib.io import Reader, Writer, BufferUnderrun

ADDRESSBOOK_PROTO = """
syntax = "proto3";
package tutorial;

message Person {
  string name = 1;
  int32 id = 2;
  string email = 3;

  enum PhoneType { MOBILE = 0; HOME = 1; WORK = 2; }
  message PhoneNumber { string number = 1; PhoneType type = 2; }

  repeated PhoneNumber phones = 4;
}

message AddressBook {
  repeated Person people = 1;
}
"""


class TestOfficialVectors(unittest.TestCase):
    def test_test1_a_150(self):
        schema = ProtoFileSchema.from_source('syntax = "proto3"; message Test1 { int32 a = 1; }')
        data = schema.encode("Test1", {"a": 150})
        self.assertEqual(data.hex(), "089601")
        self.assertEqual(schema.decode("Test1", data), {"a": 150})

    def test_test2_b_testing(self):
        schema = ProtoFileSchema.from_source('syntax = "proto3"; message Test2 { string b = 2; }')
        data = schema.encode("Test2", {"b": "testing"})
        self.assertEqual(data.hex(), "120774657374696e67")
        self.assertEqual(schema.decode("Test2", data), {"b": "testing"})


class TestNestedMessagesAndEnums(unittest.TestCase):
    def setUp(self):
        self.schema = ProtoFileSchema.from_source(ADDRESSBOOK_PROTO)

    def test_roundtrip_with_nested_message_and_enum(self):
        person = {
            "name": "Alice", "id": 1234, "email": "alice@example.com",
            "phones": [
                {"number": "555-1234", "type": "HOME"},
                {"number": "555-5678"},  # implicit MOBILE (the zero/default value)
            ],
        }
        data = self.schema.encode("Person", person)
        self.assertEqual(self.schema.decode("Person", data), person)

    def test_repeated_message_field(self):
        book = {"people": [
            {"name": "Alice", "id": 1, "phones": []},
            {"name": "Bob", "id": 2, "phones": []},
        ]}
        data = self.schema.encode("AddressBook", book)
        self.assertEqual(self.schema.decode("AddressBook", data), book)

    def test_empty_repeated_field_absent_writes_nothing_reads_as_empty_list(self):
        data = self.schema.encode("AddressBook", {})
        self.assertEqual(data, b"")
        self.assertEqual(self.schema.decode("AddressBook", data), {"people": []})

    def test_proto3_default_value_omitted_from_wire(self):
        # id=0 is int32's default -> must not be written to the wire at all.
        data = self.schema.encode("Person", {"name": "X", "id": 0, "phones": []})
        r = Reader(data)
        seen_fields = []
        while r.offset < len(data):
            fn, wt = wire.read_tag(r)
            seen_fields.append(fn)
            wire.skip_field(wt, r)
        self.assertNotIn(2, seen_fields)  # field 2 is `id`


class TestPackedRepeated(unittest.TestCase):
    def test_repeated_int32_is_packed_by_default(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message Numbers { repeated int32 values = 1; }'
        )
        data = schema.encode("Numbers", {"values": [1, 2, 3, 300]})
        r = Reader(data)
        fn, wt = wire.read_tag(r)
        # A single LEN-wiretype tag proves it's packed (not one
        # VARINT-wiretype tag per element).
        self.assertEqual(wt, wire.WIRETYPE_LEN)
        self.assertEqual(schema.decode("Numbers", data), {"values": [1, 2, 3, 300]})

    def test_decoder_accepts_unpacked_form_for_interop(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message Numbers { repeated int32 values = 1; }'
        )
        # Manually build the NON-packed wire form (legal per spec, must
        # still be accepted -- e.g. produced by a different encoder).
        w = Writer()
        for v in (10, 20, 30):
            wire.write_tag(1, wire.WIRETYPE_VARINT, w)
            wire.write_int32(v, w)
        parsed = schema.decode("Numbers", w.result())
        self.assertEqual(parsed, {"values": [10, 20, 30]})

    def test_repeated_string_is_never_packed(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message Strs { repeated string values = 1; }'
        )
        data = schema.encode("Strs", {"values": ["a", "b"]})
        r = Reader(data)
        tags = []
        while r.offset < len(data):
            fn, wt = wire.read_tag(r)
            tags.append((fn, wt))
            wire.skip_field(wt, r)
        self.assertEqual(len(tags), 2)  # one tag PER element, not packed


class TestMapFields(unittest.TestCase):
    def test_string_to_int32_map_roundtrip(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message Config { map<string, int32> counters = 1; }'
        )
        data = schema.encode("Config", {"counters": {"a": 1, "b": 2}})
        self.assertEqual(schema.decode("Config", data), {"counters": {"a": 1, "b": 2}})

    def test_empty_map_roundtrip(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message Config { map<string, int32> counters = 1; }'
        )
        data = schema.encode("Config", {})
        self.assertEqual(schema.decode("Config", data), {"counters": {}})


class TestOneof(unittest.TestCase):
    def setUp(self):
        self.schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message Msg { oneof payload { string text = 1; int32 number = 2; } }'
        )

    def test_each_member_roundtrips_independently(self):
        self.assertEqual(self.schema.decode("Msg", self.schema.encode("Msg", {"text": "hi"})), {"text": "hi"})
        self.assertEqual(self.schema.decode("Msg", self.schema.encode("Msg", {"number": 5})), {"number": 5})

    def test_encoding_both_members_at_once_raises(self):
        with self.assertRaises(ProtoOneofViolationError):
            self.schema.encode("Msg", {"text": "hi", "number": 5})

    def test_wire_with_both_members_present_keeps_only_the_last(self):
        w = Writer()
        wire.write_tag(1, wire.WIRETYPE_LEN, w)
        wire.write_string("first", w)
        wire.write_tag(2, wire.WIRETYPE_VARINT, w)
        wire.write_int32(99, w)
        parsed = self.schema.decode("Msg", w.result())
        self.assertEqual(parsed, {"number": 99})

    def test_member_explicitly_set_to_its_own_default_is_still_present(self):
        # Regression test: a oneof tracks WHICH member is active, which
        # is different information from "does it hold a non-default
        # value". Before this fix, a member explicitly set to its own
        # default (e.g. {"number": 0}) was indistinguishable on the
        # wire from no member being set at all ({}) -- both encoded to
        # b"" and decoded back to {}, silently losing which member (if
        # any) was actually chosen.
        data = self.schema.encode("Msg", {"number": 0})
        self.assertNotEqual(data, b"")
        self.assertEqual(self.schema.decode("Msg", data), {"number": 0})

    def test_no_member_set_differs_from_member_set_to_default(self):
        data_none = self.schema.encode("Msg", {})
        data_zero = self.schema.encode("Msg", {"number": 0})
        self.assertEqual(data_none, b"")
        self.assertNotEqual(data_zero, data_none)
        self.assertEqual(self.schema.decode("Msg", data_none), {})
        self.assertEqual(self.schema.decode("Msg", data_zero), {"number": 0})

    def test_string_member_set_to_empty_string_is_still_present(self):
        # Same presence issue, on the string-typed member ("" is
        # string's own default).
        data = self.schema.encode("Msg", {"text": ""})
        self.assertNotEqual(data, b"")
        self.assertEqual(self.schema.decode("Msg", data), {"text": ""})


class TestMalformedInput(unittest.TestCase):
    """The decoder must reject corrupted/malformed bytes with a clear,
    specific error -- not a bare low-level exception, and never a
    silently-wrong parse."""

    def setUp(self):
        self.schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message PhoneNumber { string number = 1; }'
            ' message Person { repeated PhoneNumber phones = 4; }'
        )

    def test_garbage_bytes_inside_a_submessage_len_boundary_raises_clearly(self):
        # A valid PhoneNumber, followed by 2 stray bytes that don't
        # form a complete tag, all still inside the outer LEN length
        # that claims to bound this submessage -- simulates a LEN
        # prefix that doesn't actually match what's encoded.
        valid_phone = Writer()
        wire.write_tag(1, wire.WIRETYPE_LEN, valid_phone)
        wire.write_string("123", valid_phone)
        corrupted_inner = valid_phone.result() + b"\xff\xff"

        outer = Writer()
        wire.write_tag(4, wire.WIRETYPE_LEN, outer)
        wire.write_len_delimited(corrupted_inner, outer)

        with self.assertRaises(ProtobufDecodeError) as ctx:
            self.schema.decode("Person", outer.result())
        self.assertIn("PhoneNumber", str(ctx.exception))

    def test_truncated_buffer_is_rejected(self):
        data = self.schema.encode("Person", {"phones": [{"number": "12345"}]})
        with self.assertRaises((BufferUnderrun, ProtobufDecodeError)):
            self.schema.decode("Person", data[:-1])

    def test_len_length_longer_than_remaining_bytes_is_rejected(self):
        w = Writer()
        wire.write_tag(1, wire.WIRETYPE_LEN, w)
        wire.write_raw_varint(1000, w)  # claims 1000 bytes follow
        w.write_bytes(b"only a few bytes")
        with self.assertRaises(BufferUnderrun):
            self.schema.decode("PhoneNumber", w.result())


class TestForwardCompatibility(unittest.TestCase):
    def test_unknown_field_number_is_skipped_not_an_error(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message M { int32 a = 1; }'
        )
        known = schema.encode("M", {"a": 1})
        w = Writer()
        wire.write_tag(99, wire.WIRETYPE_VARINT, w)
        wire.write_int32(42, w)
        combined = known + w.result()
        self.assertEqual(schema.decode("M", combined), {"a": 1})

    def test_unrecognized_enum_number_round_trips_as_raw_int(self):
        schema = ProtoFileSchema.from_source(
            'syntax = "proto3"; message M { enum E { A = 0; B = 1; } E e = 1; }'
        )
        w = Writer()
        wire.write_tag(1, wire.WIRETYPE_VARINT, w)
        wire.write_int32(77, w)  # not a value declared in enum E
        parsed = schema.decode("M", w.result())
        self.assertEqual(parsed, {"e": 77})


class TestFullReferenceTemplate(unittest.TestCase):
    """Exercises examples/full_reference_template.proto end to end --
    the .proto-language counterpart to full_reference_template.yml/.json
    (the YAML/JSON engine's own reference catalog). Every message in
    that file is round-tripped here so the file stays a living,
    CI-checked reference instead of prose that can silently drift out
    of sync with what the codec actually supports."""

    @classmethod
    def setUpClass(cls):
        import os
        here = os.path.dirname(os.path.abspath(__file__))
        proto_path = os.path.join(here, "..", "examples", "full_reference_template.proto")
        cls.schema = ProtoFileSchema.from_file(proto_path)

    def _roundtrip(self, message_name, value):
        data = self.schema.encode(message_name, value)
        self.assertEqual(self.schema.decode(message_name, data), value)

    def test_scalar_catalog_all_15_types(self):
        self._roundtrip("ScalarCatalog", {
            "campo_int32": -5, "campo_int64": 123456789012,
            "campo_uint32": 4000000000, "campo_uint64": 10,
            "campo_sint32": -1000, "campo_sint64": -999999999999,
            "campo_bool": True,
            "campo_fixed32": 42, "campo_sfixed32": -42, "campo_float": 1.5,
            "campo_fixed64": 42, "campo_sfixed64": -42, "campo_double": 3.14159,
            "campo_string": "hola mundo", "campo_bytes": b"\x00\x01\xff",
        })

    def test_repeated_scalars_packed_and_unpacked(self):
        self._roundtrip("RepeatedScalars", {
            "numbers": [1, -2, 3, 1000000],
            "measurements": [1.1, 2.2, 3.3],
            "tags": ["a", "b", "c"],
        })

    def test_person_nested_message_and_enum(self):
        self._roundtrip("Person", {
            "name": "Alice", "id": 1, "email": "alice@example.com",
            "phones": [{"number": "555-1234", "type": "HOME"}, {"number": "555-0000"}],
        })

    def test_addressbook_cross_reference(self):
        self._roundtrip("AddressBook", {"people": [{"name": "Bob", "id": 2, "phones": []}]})

    def test_inventory_map(self):
        self._roundtrip("Inventory", {"item_counts": {"sword": 1, "potion": 5}})

    def test_event_oneof_each_member(self):
        self._roundtrip("Event", {"text_message": "hola"})
        self._roundtrip("Event", {"status_code": 200})
        self._roundtrip("Event", {"joined": {"name": "Carl", "id": 3, "phones": []}})

    def test_event_oneof_violation_rejected(self):
        with self.assertRaises(ProtoOneofViolationError):
            self.schema.encode("Event", {"text_message": "x", "status_code": 1})

    def test_treenode_recursive_type(self):
        self._roundtrip("TreeNode", {
            "value": "root",
            "children": [
                {"value": "child1", "children": []},
                {"value": "child2", "children": [{"value": "grandchild", "children": []}]},
            ],
        })

    def test_versioned_record_reserved_numbers_not_reused(self):
        message = self.schema.resolved.message_by_name("VersionedRecord")
        used_numbers = {f.number for f in message.fields}
        self.assertTrue(used_numbers.isdisjoint({2, 4, 5, 6}))
        self._roundtrip("VersionedRecord", {"current_id": "abc", "revision": 3, "checksum": 999})


if __name__ == "__main__":
    unittest.main()
