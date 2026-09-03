import unittest

from protolib.protobuf.proto_parser import parse_proto
from protolib.protobuf.proto_schema import build_schema
from protolib.protobuf.errors import (
    ProtoSyntaxError, ProtoSemanticError, UnsupportedProtoFeatureError,
)

ADDRESSBOOK_PROTO = """
syntax = "proto3";
package tutorial;

message Person {
  string name = 1;
  int32 id = 2;
  string email = 3;

  enum PhoneType {
    MOBILE = 0;
    HOME = 1;
    WORK = 2;
  }

  message PhoneNumber {
    string number = 1;
    PhoneType type = 2;
  }

  repeated PhoneNumber phones = 4;
}

message AddressBook {
  repeated Person people = 1;
}
"""


class TestBasicParsing(unittest.TestCase):
    def test_addressbook_structure(self):
        pf = parse_proto(ADDRESSBOOK_PROTO)
        self.assertEqual(pf.syntax, "proto3")
        self.assertEqual(pf.package, "tutorial")
        self.assertEqual([m.name for m in pf.messages], ["Person", "AddressBook"])

    def test_nested_message_and_enum(self):
        pf = parse_proto(ADDRESSBOOK_PROTO)
        person = pf.messages[0]
        self.assertEqual([m.name for m in person.nested_messages], ["PhoneNumber"])
        self.assertEqual([e.name for e in person.nested_enums], ["PhoneType"])
        phone_type = person.nested_enums[0]
        self.assertEqual([(v.name, v.number) for v in phone_type.values],
                          [("MOBILE", 0), ("HOME", 1), ("WORK", 2)])

    def test_field_labels_and_numbers(self):
        pf = parse_proto(ADDRESSBOOK_PROTO)
        person = pf.messages[0]
        by_name = {f.name: f for f in person.fields}
        self.assertEqual(by_name["name"].type_name, "string")
        self.assertEqual(by_name["name"].number, 1)
        self.assertEqual(by_name["name"].label, "optional")
        self.assertEqual(by_name["phones"].label, "repeated")


class TestOneofMapReserved(unittest.TestCase):
    def test_oneof(self):
        src = """
        syntax = "proto3";
        message Msg { oneof payload { string text = 1; int32 number = 2; } }
        """
        pf = parse_proto(src)
        fields = pf.messages[0].fields
        self.assertEqual([f.oneof_name for f in fields], ["payload", "payload"])

    def test_map_field(self):
        src = 'syntax = "proto3"; message Config { map<string, int32> counters = 1; }'
        pf = parse_proto(src)
        f = pf.messages[0].fields[0]
        self.assertEqual((f.map_key_type, f.map_value_type, f.label), ("string", "int32", "repeated"))

    def test_reserved_ranges_and_names(self):
        src = """
        syntax = "proto3";
        message M { reserved 2, 15, 9 to 11; reserved "old"; int32 x = 1; }
        """
        pf = parse_proto(src)
        self.assertEqual(sorted(pf.messages[0].reserved_numbers), [2, 9, 10, 11, 15])

    def test_field_options_are_parsed_and_discarded(self):
        src = 'syntax = "proto3"; message M { string x = 1 [deprecated = true]; }'
        pf = parse_proto(src)
        self.assertEqual(pf.messages[0].fields[0].name, "x")

    def test_file_level_option_is_skipped(self):
        src = 'syntax = "proto3"; option java_package = "com.example"; message M { int32 x = 1; }'
        pf = parse_proto(src)
        self.assertEqual(pf.messages[0].name, "M")

    def test_service_block_is_skipped(self):
        src = """
        syntax = "proto3";
        message Req {}
        message Res {}
        service Greeter { rpc SayHello (Req) returns (Res); }
        message After { int32 x = 1; }
        """
        pf = parse_proto(src)
        self.assertEqual([m.name for m in pf.messages], ["Req", "Res", "After"])


class TestRejectedConstructs(unittest.TestCase):
    def test_proto2_syntax_rejected(self):
        with self.assertRaises(UnsupportedProtoFeatureError):
            parse_proto('syntax = "proto2"; message M { required int32 x = 1; }')

    def test_required_field_rejected_in_proto3(self):
        with self.assertRaises(UnsupportedProtoFeatureError):
            parse_proto('syntax = "proto3"; message M { required int32 x = 1; }')

    def test_extend_rejected(self):
        with self.assertRaises(UnsupportedProtoFeatureError):
            parse_proto('syntax = "proto3"; extend Foo { int32 x = 1; }')

    def test_field_number_zero_rejected(self):
        with self.assertRaises(ProtoSemanticError):
            parse_proto('syntax = "proto3"; message M { int32 x = 0; }')

    def test_field_number_in_reserved_range_rejected(self):
        with self.assertRaises(ProtoSemanticError):
            parse_proto('syntax = "proto3"; message M { int32 x = 19500; }')

    def test_missing_semicolon_is_syntax_error(self):
        with self.assertRaises(ProtoSyntaxError):
            parse_proto('syntax = "proto3" message M {}')

    def test_enum_must_start_at_zero(self):
        with self.assertRaises(ProtoSemanticError):
            parse_proto('syntax = "proto3"; enum Color { RED = 1; }')

    def test_unterminated_string_is_syntax_error(self):
        with self.assertRaises(ProtoSyntaxError):
            parse_proto('syntax = "proto3')


class TestSchemaResolution(unittest.TestCase):
    def test_sibling_message_reference(self):
        pf = parse_proto(ADDRESSBOOK_PROTO)
        schema = build_schema(pf)
        addressbook = schema.message_by_name("AddressBook")
        people_field = addressbook.fields[0]
        resolved = schema.resolved_type_of(addressbook, people_field)
        self.assertEqual(resolved.name, "Person")

    def test_nested_enum_resolved_from_sibling_nested_message(self):
        pf = parse_proto(ADDRESSBOOK_PROTO)
        schema = build_schema(pf)
        phone_number = schema.message_by_name("tutorial.Person.PhoneNumber")
        type_field = [f for f in phone_number.fields if f.name == "type"][0]
        resolved = schema.resolved_type_of(phone_number, type_field)
        self.assertEqual(resolved.name, "PhoneType")

    def test_recursive_message_resolves_to_itself(self):
        src = """
        syntax = "proto3";
        message TreeNode { string value = 1; repeated TreeNode children = 2; }
        """
        pf = parse_proto(src)
        schema = build_schema(pf)
        node = schema.message_by_name("TreeNode")
        children_field = [f for f in node.fields if f.name == "children"][0]
        resolved = schema.resolved_type_of(node, children_field)
        self.assertIs(resolved, node)

    def test_undefined_type_reference_raises(self):
        src = 'syntax = "proto3"; message M { DoesNotExist x = 1; }'
        pf = parse_proto(src)
        with self.assertRaises(ProtoSemanticError):
            build_schema(pf)

    def test_duplicate_field_number_raises(self):
        src = 'syntax = "proto3"; message M { int32 a = 1; string b = 1; }'
        pf = parse_proto(src)
        with self.assertRaises(ProtoSemanticError):
            build_schema(pf)

    def test_field_using_reserved_number_raises(self):
        src = 'syntax = "proto3"; message M { reserved 5; int32 a = 5; }'
        pf = parse_proto(src)
        with self.assertRaises(ProtoSemanticError):
            build_schema(pf)


if __name__ == "__main__":
    unittest.main()
