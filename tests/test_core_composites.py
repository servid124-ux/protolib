import unittest

from protolib.core import Protocol
from protolib.errors import (
    SwitchCaseNotFound,
    InvalidTypeDefinition,
    UnknownTypeError,
    MapperValueNotFoundError,
)
from protolib.io import BufferUnderrun


def make_protocol(types: dict, global_types: dict | None = None) -> Protocol:
    """Builds a minimal one-state protocol dict so each test can register
    just the named type(s) it needs and read/write them via read_named/
    write_named -- the same helpers the library itself documents as
    'useful for tests' in core.py."""
    return Protocol({
        "types": global_types or {},
        "play": {"toClient": {"types": types}, "toServer": {"types": types}},
    })


class TestContainer(unittest.TestCase):
    def test_basic_fields_in_order(self):
        proto = make_protocol({
            "myType": ["container", [
                {"name": "a", "type": "u8"},
                {"name": "b", "type": "u16"},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"a": 5, "b": 300})
        self.assertEqual(data, b"\x05\x01\x2c")
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data), {"a": 5, "b": 300}
        )

    def test_anon_field_merges_into_parent(self):
        proto = make_protocol({
            "myType": ["container", [
                {"anon": True, "type": ["container", [
                    {"name": "a", "type": "u8"}, {"name": "b", "type": "u8"},
                ]]},
                {"name": "c", "type": "u8"},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"a": 1, "b": 2, "c": 3})
        self.assertEqual(data, b"\x01\x02\x03")
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data), {"a": 1, "b": 2, "c": 3}
        )

    def test_condition_field_present(self):
        proto = make_protocol({
            "myType": ["container", [
                {"name": "hasExtra", "type": "bool"},
                {"name": "extra", "type": "u8", "condition": "fields.hasExtra === true"},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"hasExtra": True, "extra": 9})
        self.assertEqual(data, b"\x01\x09")
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data),
            {"hasExtra": True, "extra": 9},
        )

    def test_condition_field_absent(self):
        proto = make_protocol({
            "myType": ["container", [
                {"name": "hasExtra", "type": "bool"},
                {"name": "extra", "type": "u8", "condition": "fields.hasExtra === true"},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"hasExtra": False})
        self.assertEqual(data, b"\x00")  # no 'extra' byte written at all
        parsed = proto.read_named("play", "toClient", "myType", data)
        self.assertEqual(parsed, {"hasExtra": False})
        self.assertNotIn("extra", parsed)


class TestArray(unittest.TestCase):
    def test_countType_prefixes_length_automatically(self):
        proto = make_protocol({"myType": ["array", {"countType": "u8", "type": "u16"}]})
        data = proto.write_named("play", "toClient", "myType", [1, 2, 3])
        self.assertEqual(data, b"\x03\x00\x01\x00\x02\x00\x03")
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), [1, 2, 3])

    def test_count_references_sibling_field(self):
        proto = make_protocol({
            "myType": ["container", [
                {"name": "n", "type": "u8"},
                {"name": "items", "type": ["array", {"count": "n", "type": "u8"}]},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"n": 3, "items": [7, 8, 9]})
        self.assertEqual(data, b"\x03\x07\x08\x09")
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data),
            {"n": 3, "items": [7, 8, 9]},
        )

    def test_empty_array_roundtrip(self):
        proto = make_protocol({"myType": ["array", {"countType": "u8", "type": "u16"}]})
        data = proto.write_named("play", "toClient", "myType", [])
        self.assertEqual(data, b"\x00")
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), [])

    def test_bad_count_reference_raises_invalid_type_definition_on_read(self):
        # 'count' reference validation only happens on the READ side
        # (_read_array calls resolve_field_path and checks the result);
        # _write_array never resolves 'count' at all -- it just writes
        # len(items) is NOT auto-derived here (unlike countType), so a
        # missing/bad reference can only ever surface while reading.
        proto = make_protocol({"myType": ["array", {"count": "doesNotExist", "type": "u8"}]})
        with self.assertRaises(InvalidTypeDefinition):
            proto.read_named("play", "toClient", "myType", b"\x01\x02")


class TestSwitch(unittest.TestCase):
    def setUp(self):
        self.proto = make_protocol({
            "myType": ["container", [
                {"name": "kind", "type": "u8"},
                {"name": "payload", "type": ["switch", {
                    "compareTo": "kind",
                    "fields": {"1": "u8", "2": "u16"},
                    "default": "void",
                }]},
            ]],
        })

    def test_matching_case_u8(self):
        data = self.proto.write_named("play", "toClient", "myType", {"kind": 1, "payload": 9})
        self.assertEqual(
            self.proto.read_named("play", "toClient", "myType", data),
            {"kind": 1, "payload": 9},
        )

    def test_matching_case_u16(self):
        data = self.proto.write_named("play", "toClient", "myType", {"kind": 2, "payload": 300})
        self.assertEqual(
            self.proto.read_named("play", "toClient", "myType", data),
            {"kind": 2, "payload": 300},
        )

    def test_unmatched_case_falls_back_to_default_void(self):
        data = self.proto.write_named("play", "toClient", "myType", {"kind": 99, "payload": None})
        self.assertEqual(
            self.proto.read_named("play", "toClient", "myType", data),
            {"kind": 99, "payload": None},
        )

    def test_no_default_raises_switch_case_not_found(self):
        proto = make_protocol({
            "myType": ["container", [
                {"name": "kind", "type": "u8"},
                {"name": "payload", "type": ["switch", {
                    "compareTo": "kind", "fields": {"1": "u8"},
                }]},
            ]],
        })
        with self.assertRaises(SwitchCaseNotFound):
            proto.read_named("play", "toClient", "myType", b"\x63\x00")

    def test_int_keyed_fields_already_worked_before_any_fix(self):
        # Unlike mapper, switch was already immune to the unquoted-YAML-key
        # footgun: _resolve_switch_case tries fields.get(case_key) (str)
        # and falls back to fields.get(compare_val) (raw int) either way.
        proto = make_protocol({
            "myType": ["container", [
                {"name": "kind", "type": "u8"},
                {"name": "payload", "type": ["switch", {"compareTo": "kind", "fields": {1: "u8"}}]},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"kind": 1, "payload": 9})
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data), {"kind": 1, "payload": 9}
        )


class TestMapper(unittest.TestCase):
    def setUp(self):
        self.proto = make_protocol({
            "myType": ["mapper", {
                "type": "u8",
                "mappings": {"0x00": "handshake", "0x01": "status"},
            }],
        })

    def test_write_symbolic_name_to_wire_int(self):
        data = self.proto.write_named("play", "toClient", "myType", "handshake")
        self.assertEqual(data, b"\x00")

    def test_read_wire_int_to_symbolic_name(self):
        parsed = self.proto.read_named("play", "toClient", "myType", b"\x01")
        self.assertEqual(parsed, "status")

    def test_unknown_raw_value_raises_on_read(self):
        # 0.3.8: parity with node-protodef (utils.js readMapper), which
        # throws when the raw value has no entry in mappings instead of
        # silently passing it through. A mapper models a closed set
        # (packet state, entity type, block face...), so an unmapped
        # value is a real protocol desync or a stale table, not
        # something safe to paper over.
        with self.assertRaises(MapperValueNotFoundError):
            self.proto.read_named("play", "toClient", "myType", b"\x05")

    def test_unknown_symbolic_name_raises_on_write(self):
        with self.assertRaises(MapperValueNotFoundError):
            self.proto.write_named("play", "toClient", "myType", "not_a_real_mapping")

    def test_int_keyed_mappings_work(self):
        # Simulates exactly what an UNQUOTED numeric/hex YAML key loads as
        # (a native int, not a string -- see test_loader.py's
        # test_yaml_unquoted_numeric_key_becomes_int_not_string). Before
        # the fix in Protocol._normalize_mapper_key this crashed with
        # AttributeError: 'int' object has no attribute 'lower' the moment
        # a packet using this mapper was actually read or written.
        proto = make_protocol({
            "myType": ["mapper", {"type": "u8", "mappings": {0x00: "handshake", 1: "status"}}],
        })
        self.assertEqual(proto.read_named("play", "toClient", "myType", b"\x00"), "handshake")
        self.assertEqual(proto.write_named("play", "toClient", "myType", "status"), b"\x01")

    def test_full_pipeline_from_unquoted_yaml_source(self):
        # End-to-end: real YAML text (as a user would actually type it,
        # without knowing to quote the key) -> loader -> Protocol -> a
        # real parse. This is the actual bug surface, not just the dict shape.
        from protolib.loader import load_protocol_dict

        yaml_text = "myType:\n  mapper:\n    type: u8\n    mappings:\n      0x01: status\n"
        types = load_protocol_dict(yaml_text, fmt="yaml")
        proto = make_protocol(types)
        self.assertEqual(proto.read_named("play", "toClient", "myType", b"\x01"), "status")


class TestOption(unittest.TestCase):
    def setUp(self):
        self.proto = make_protocol({"myType": ["option", "u16"]})

    def test_present_value(self):
        data = self.proto.write_named("play", "toClient", "myType", 300)
        self.assertEqual(data, b"\x01\x01\x2c")
        self.assertEqual(self.proto.read_named("play", "toClient", "myType", data), 300)

    def test_absent_value(self):
        data = self.proto.write_named("play", "toClient", "myType", None)
        self.assertEqual(data, b"\x00")
        self.assertIsNone(self.proto.read_named("play", "toClient", "myType", data))


class TestBitfield(unittest.TestCase):
    def test_exact_byte_packing_matches_readme_example(self):
        # README section 6: {name: type, size: 3} + {name: index, size: 5}
        proto = make_protocol({
            "myType": ["bitfield", [
                {"name": "type", "size": 3, "signed": False},
                {"name": "index", "size": 5, "signed": False},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"type": 5, "index": 17})
        self.assertEqual(data, b"\xb1")  # 101 10001
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data), {"type": 5, "index": 17}
        )

    def test_signed_subfield_with_padding_to_next_byte(self):
        # total_bits=4 (not a multiple of 8): must pad up to 1 full byte.
        proto = make_protocol({"myType": ["bitfield", [{"name": "a", "size": 4, "signed": True}]]})
        data = proto.write_named("play", "toClient", "myType", {"a": -3})
        self.assertEqual(len(data), 1)
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), {"a": -3})


class TestBitflags(unittest.TestCase):
    def test_dict_form_with_shift(self):
        proto = make_protocol({
            "myType": ["bitflags", {
                "type": "u8",
                "flags": {"air": 0, "water": 1, "lava": 2},
                "shift": True,
            }],
        })
        data = proto.write_named(
            "play", "toClient", "myType", {"flags": {"air": True, "water": False, "lava": True}}
        )
        self.assertEqual(data, b"\x05")
        parsed = proto.read_named("play", "toClient", "myType", data)
        self.assertEqual(parsed, {"air": True, "water": False, "lava": True, "_value": 5})

    def test_list_form_positional_with_gap(self):
        # position 1 is a reserved/unused bit (None) per the README's own example.
        proto = make_protocol({"myType": ["bitflags", {"type": "u8", "flags": ["air", None, "lava"]}]})
        parsed = proto.read_named("play", "toClient", "myType", bytes([0b00000111]))
        self.assertEqual(parsed, {"air": True, "lava": True, "_value": 7})

    def test_unwrapped_dict_also_accepted_for_backcompat(self):
        proto = make_protocol({
            "myType": ["bitflags", {"type": "u8", "flags": {"air": 1, "water": 2}}],
        })
        data = proto.write_named("play", "toClient", "myType", {"air": True, "water": True})
        self.assertEqual(data, b"\x03")

    def test_list_form_big_does_not_reverse_order(self):
        # 0.3.8 regression test: `big` used to (incorrectly) reverse the
        # array so bit index N-1-i mapped to flag i -- a divergence from
        # node-protodef (utils.js readBitflags/writeBitflags), where
        # `big` only picks BigInt vs Number shifting for the mask, never
        # the bit each name maps to. "air" must always be bit 0 and
        # "lava" bit 2, with or without big=True.
        without_big = make_protocol({"myType": ["bitflags", {"type": "u8", "flags": ["air", "water", "lava"]}]})
        with_big = make_protocol({
            "myType": ["bitflags", {"type": "u8", "flags": ["air", "water", "lava"], "big": True}],
        })
        raw = bytes([0b00000101])  # bit0 (air) + bit2 (lava)
        expected = {"air": True, "water": False, "lava": True, "_value": 5}
        self.assertEqual(without_big.read_named("play", "toClient", "myType", raw), expected)
        self.assertEqual(with_big.read_named("play", "toClient", "myType", raw), expected)
        # and the same on write: both must produce identical bytes
        value = {"air": True, "water": False, "lava": True}
        self.assertEqual(
            without_big.write_named("play", "toClient", "myType", value),
            with_big.write_named("play", "toClient", "myType", value),
        )
        self.assertEqual(with_big.write_named("play", "toClient", "myType", value), raw)


class TestBuffer(unittest.TestCase):
    def test_countType_prefixed(self):
        proto = make_protocol({"myType": ["buffer", {"countType": "u8"}]})
        data = proto.write_named("play", "toClient", "myType", b"hi")
        self.assertEqual(data, b"\x02hi")
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), b"hi")

    def test_rest_consumes_everything_remaining(self):
        proto = make_protocol({"myType": ["buffer", {"rest": True}]})
        parsed = proto.read_named("play", "toClient", "myType", b"whatever is left")
        self.assertEqual(parsed, b"whatever is left")

    def test_fixed_count_exact_match(self):
        proto = make_protocol({"myType": ["buffer", {"count": 4}]})
        data = proto.write_named("play", "toClient", "myType", b"abcd")
        self.assertEqual(data, b"abcd")

    def test_fixed_count_mismatch_fails_loud(self):
        proto = make_protocol({"myType": ["buffer", {"count": 4}]})
        with self.assertRaises(InvalidTypeDefinition):
            proto.write_named("play", "toClient", "myType", b"ab")


class TestPstring(unittest.TestCase):
    def test_default_varint_length_utf8(self):
        proto = make_protocol({"myType": ["pstring", {"countType": "varint"}]})
        data = proto.write_named("play", "toClient", "myType", "hola")
        self.assertEqual(data, b"\x04hola")
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), "hola")

    def test_count_referencing_sibling_writes_no_extra_prefix(self):
        proto = make_protocol({
            "myType": ["container", [
                {"name": "n", "type": "u8"},
                {"name": "s", "type": ["pstring", {"count": "n"}]},
            ]],
        })
        data = proto.write_named("play", "toClient", "myType", {"n": 4, "s": "hola"})
        self.assertEqual(data, b"\x04hola")
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data), {"n": 4, "s": "hola"}
        )


class TestCount(unittest.TestCase):
    def test_count_type_ignores_given_value_and_derives_from_countFor(self):
        # README section 9, verbatim pattern.
        proto = make_protocol({
            "myType": ["container", [
                {"name": "itemCount", "type": ["count", {"type": "varint", "countFor": "items"}]},
                {"name": "items", "type": ["array", {"count": "itemCount", "type": "u8"}]},
            ]],
        })
        # Note: 'itemCount' is deliberately NOT supplied by the caller --
        # writeCount computes it from len(items) regardless of what's passed.
        data = proto.write_named("play", "toClient", "myType", {"items": [10, 20, 30]})
        self.assertEqual(data, b"\x03\x0a\x14\x1e")
        parsed = proto.read_named("play", "toClient", "myType", data)
        self.assertEqual(parsed, {"itemCount": 3, "items": [10, 20, 30]})


class TestEntityMetadataLoop(unittest.TestCase):
    def test_roundtrip_and_terminator(self):
        # Regression coverage for the documented fix at core.py's
        # _read_entity_metadata_loop ("Bug real corregido acá"): the
        # terminator byte must be peeked, not consumed, before deciding
        # whether an entry's own bitfield gets to read it.
        proto = make_protocol({
            "metaItem": ["container", [
                {"anon": True, "type": ["bitfield", [
                    {"name": "tipo", "size": 3, "signed": False},
                    {"name": "indice", "size": 5, "signed": False},
                ]]},
                {"name": "valor", "type": ["switch", {
                    "compareTo": "tipo", "fields": {"0": "i8", "1": "u16"},
                }]},
            ]],
            "myType": ["entityMetadataLoop", {"endVal": 0xFF, "type": "metaItem"}],
        })
        entries = [
            {"tipo": 0, "indice": 2, "valor": -5},
            {"tipo": 1, "indice": 3, "valor": 300},
        ]
        data = proto.write_named("play", "toClient", "myType", entries)
        self.assertEqual(data[-1], 0xFF)  # terminator present
        parsed = proto.read_named("play", "toClient", "myType", data)
        self.assertEqual(parsed, entries)

    def test_empty_list_is_just_the_terminator(self):
        proto = make_protocol({
            "metaItem": ["container", [{"name": "x", "type": "u8"}]],
            "myType": ["entityMetadataLoop", {"type": "metaItem"}],
        })
        data = proto.write_named("play", "toClient", "myType", [])
        self.assertEqual(data, b"\xff")
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), [])


class TestTopBitSetTerminatedArray(unittest.TestCase):
    def test_stops_when_top_bit_clear(self):
        proto = make_protocol({"myType": ["topBitSetTerminatedArray", {"type": "u8"}]})
        raw = bytes([0x81, 0x82, 0x03])  # first two "announce more", third doesn't
        self.assertEqual(proto.read_named("play", "toClient", "myType", raw), [0x81, 0x82, 0x03])
        data = proto.write_named("play", "toClient", "myType", [0x81, 0x82, 0x03])
        self.assertEqual(data, raw)


class TestCstringComposite(unittest.TestCase):
    def test_custom_encoding_option(self):
        proto = make_protocol({"myType": ["cstring", {"encoding": "latin-1"}]})
        data = proto.write_named("play", "toClient", "myType", "café")
        self.assertTrue(data.endswith(b"\x00"))
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), "café")


class TestParametrizedArgTypes(unittest.TestCase):
    def test_dollar_arg_substitution(self):
        # README section 8, verbatim pattern.
        proto = make_protocol({
            "itemByType": ["switch", {
                "compareTo": "$compareTo",
                "fields": {"0": "i8", "1": "varint"},
            }],
            "myContainer": ["container", [
                {"name": "type", "type": "u8"},
                {"name": "value", "type": ["itemByType", {"compareTo": "type"}]},
            ]],
        })
        data0 = proto.write_named("play", "toClient", "myContainer", {"type": 0, "value": -1})
        self.assertEqual(
            proto.read_named("play", "toClient", "myContainer", data0), {"type": 0, "value": -1}
        )
        data1 = proto.write_named("play", "toClient", "myContainer", {"type": 1, "value": 300})
        self.assertEqual(
            proto.read_named("play", "toClient", "myContainer", data1), {"type": 1, "value": 300}
        )


class TestErrorPaths(unittest.TestCase):
    def test_unknown_type_name(self):
        proto = make_protocol({"myType": "thisTypeDoesNotExist"})
        with self.assertRaises(UnknownTypeError):
            proto.read_named("play", "toClient", "myType", b"\x00")

    def test_buffer_underrun_on_truncated_packet(self):
        proto = make_protocol({"myType": ["container", [{"name": "a", "type": "u32"}]]})
        with self.assertRaises(BufferUnderrun):
            proto.read_named("play", "toClient", "myType", b"\x01")

    def test_unknown_state_direction(self):
        proto = make_protocol({"myType": "u8"})
        with self.assertRaises(UnknownTypeError):
            proto.get_scope("login", "toClient")


class TestRegistryEntryHolder(unittest.TestCase):
    """IdOr<T> de Minecraft moderno: id==0 -> valor inline de 'otherwise',
    id!=0 -> referencia a un registro por índice (raw_id - 1)."""

    def _proto(self):
        return make_protocol({
            "myType": ["registryEntryHolder", {
                "idType": "varint",
                "otherwise": {"type": ["container", [
                    {"name": "suffix", "type": "cstring"},
                    {"name": "color", "type": "i32"},
                ]]},
            }],
        })

    def test_reference_case_writes_id_plus_one(self):
        proto = self._proto()
        data = proto.write_named("play", "toClient", "myType", {"type": "reference", "id": 0})
        # raw id en el wire es id+1 -> varint(1) = 0x01 (índice 0 de registro,
        # distinguible del 0x00 que significa "viene inline")
        self.assertEqual(data, b"\x01")
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data),
            {"type": "reference", "id": 0},
        )

    def test_reference_case_roundtrip_nonzero_index(self):
        proto = self._proto()
        data = proto.write_named("play", "toClient", "myType", {"type": "reference", "id": 41})
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data),
            {"type": "reference", "id": 41},
        )

    def test_inline_case_reads_otherwise_type_after_zero_id(self):
        proto = self._proto()
        data = proto.write_named("play", "toClient", "myType", {
            "type": "inline",
            "value": {"suffix": "custom", "color": -1},
        })
        self.assertEqual(data[0], 0x00)  # raw id == 0 marca el caso inline
        self.assertEqual(
            proto.read_named("play", "toClient", "myType", data),
            {"type": "inline", "value": {"suffix": "custom", "color": -1}},
        )

    def test_default_id_type_is_varint(self):
        proto = make_protocol({
            "myType": ["registryEntryHolder", {"otherwise": {"type": "u8"}}],
        })
        data = proto.write_named("play", "toClient", "myType", {"type": "reference", "id": 5})
        self.assertEqual(data, b"\x06")  # varint(5+1), no se pasó idType explícito

    def test_invalid_value_kind_raises(self):
        proto = self._proto()
        with self.assertRaises(InvalidTypeDefinition):
            proto.write_named("play", "toClient", "myType", {"type": "bogus"})

    def test_used_inside_container_field(self):
        # caso de uso real: un item con trim que puede venir precargado por
        # el registro del servidor (lo normal) o completo inline (cuando el
        # cliente aún no tiene ese trim registrado)
        proto = make_protocol({
            "myType": ["container", [
                {"name": "itemId", "type": "varint"},
                {"name": "trim", "type": ["registryEntryHolder", {
                    "otherwise": {"type": "cstring"},
                }]},
            ]],
        })
        value = {"itemId": 7, "trim": {"type": "reference", "id": 2}}
        data = proto.write_named("play", "toClient", "myType", value)
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), value)


class TestRegistryEntryHolderSet(unittest.TestCase):
    """HolderSet<T>: count==0 -> sigue el nombre de una tag (string, típicamente
    con prefijo '#'), count>0 -> siguen exactamente count ids inline."""

    def _proto(self, id_type=None):
        opts = {"idType": id_type} if id_type else {}
        return make_protocol({"myType": ["registryEntryHolderSet", opts]})

    def test_tag_reference_case(self):
        proto = self._proto()
        value = {"type": "tag", "tagName": "#minecraft:trim_materials"}
        data = proto.write_named("play", "toClient", "myType", value)
        self.assertEqual(data[0], 0x00)  # count==0 marca "sigue una tag"
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), value)

    def test_explicit_ids_case(self):
        proto = self._proto()
        value = {"type": "ids", "ids": [3, 7, 12]}
        data = proto.write_named("play", "toClient", "myType", value)
        self.assertEqual(data[0], 0x03)  # count==3, sin +1/-1 (a diferencia del holder simple)
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), value)

    def test_empty_ids_list_is_not_confused_with_tag_case(self):
        # una lista vacía de ids también serializa count=0 en el wire --
        # en la práctica es indistinguible byte a byte del caso 'tag' (el
        # protocolo vanilla real tampoco lo distingue), así que leerla de
        # vuelta cae del lado de 'tag' esperando un cstring a continuación.
        # Documentamos el comportamiento acá para que no sorprenda a nadie
        # que lo use: si se necesita "conjunto vacío inline" real, conviene
        # no usar count=0 como caso límite en el protocolo que se declare.
        proto = self._proto()
        w_ids = proto.write_named("play", "toClient", "myType", {"type": "ids", "ids": []})
        self.assertEqual(w_ids, b"\x00")

    def test_custom_id_type(self):
        proto = self._proto(id_type="u8")
        value = {"type": "ids", "ids": [200, 255]}
        data = proto.write_named("play", "toClient", "myType", value)
        self.assertEqual(data, b"\x02\xc8\xff")  # count varint(2) + dos u8 crudos
        self.assertEqual(proto.read_named("play", "toClient", "myType", data), value)

    def test_invalid_value_kind_raises(self):
        proto = self._proto()
        with self.assertRaises(InvalidTypeDefinition):
            proto.write_named("play", "toClient", "myType", {"type": "bogus"})


if __name__ == "__main__":
    unittest.main()
