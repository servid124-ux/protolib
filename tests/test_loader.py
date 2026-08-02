import json
import os
import tempfile
import unittest

from protolib.loader import load_protocol_dict, protocol_dict_to_yaml, LoaderError


class TestShorthandTranslation(unittest.TestCase):
    def test_dict_source_passes_through_untouched(self):
        # load_protocol_dict explicitly does NOT translate an already-parsed
        # dict (per its own docstring) -- only string sources (file/text) go
        # through _yaml_to_protodef.
        raw = {"container": [{"name": "a", "type": "varint"}]}
        self.assertEqual(load_protocol_dict(raw), raw)

    def test_yaml_container_shorthand_becomes_native_form(self):
        yaml_text = """
myType:
  container:
    - name: a
      type: varint
"""
        got = load_protocol_dict(yaml_text, fmt="yaml")
        self.assertEqual(got, {"myType": ["container", [{"name": "a", "type": "varint"}]]})

    def test_yaml_switch_shorthand_with_quoted_keys(self):
        yaml_text = """
myType:
  switch:
    compareTo: packetId
    fields:
      "0": handshake
"""
        got = load_protocol_dict(yaml_text, fmt="yaml")
        self.assertEqual(
            got,
            {"myType": ["switch", {"compareTo": "packetId", "fields": {"0": "handshake"}}]},
        )

    def test_yaml_unquoted_numeric_key_becomes_int_not_string(self):
        # Easy footgun, confirmed empirically against PyYAML: an UNQUOTED
        # numeric/hex mapping key resolves to a native int, not the string
        # every example protocol shipped in examples/ always uses ('0x00':
        # with quotes, everywhere, with no comment explaining why). See the
        # int-keyed regression tests in test_core_composites.py::TestMapper
        # for why this matters at runtime, not just here at load time.
        yaml_text = """
myType:
  mapper:
    type: u8
    mappings:
      0x00: handshake
      1: status
"""
        got = load_protocol_dict(yaml_text, fmt="yaml")
        mappings = got["myType"][1]["mappings"]
        self.assertEqual(mappings, {0: "handshake", 1: "status"})
        self.assertIsInstance(list(mappings.keys())[0], int)

    def test_buffer_count_shorthand_is_not_corrupted(self):
        # Regression test for the exact bug documented in loader.py: a
        # literal {"count": 16} option inside buffer must NOT be
        # re-interpreted as the 'count' composite-type shorthand, because
        # 'count' also happens to be a recognized composite type name.
        yaml_text = """
myType:
  buffer:
    count: 16
"""
        got = load_protocol_dict(yaml_text, fmt="yaml")
        self.assertEqual(got, {"myType": ["buffer", {"count": 16}]})

    def test_already_explicit_form_in_yaml_is_preserved(self):
        yaml_text = """
myType:
  - buffer
  - count: 16
"""
        got = load_protocol_dict(yaml_text, fmt="yaml")
        self.assertEqual(got, {"myType": ["buffer", {"count": 16}]})

    def test_simple_string_type_left_alone(self):
        yaml_text = "myType: varint\n"
        got = load_protocol_dict(yaml_text, fmt="yaml")
        self.assertEqual(got, {"myType": "varint"})


class TestFormatDetectionAndErrors(unittest.TestCase):
    def test_json_string_content(self):
        got = load_protocol_dict('{"myType": "varint"}')
        self.assertEqual(got, {"myType": "varint"})

    def test_invalid_json_raises_loader_error(self):
        with self.assertRaises(LoaderError):
            load_protocol_dict("{not valid json", fmt="json")

    def test_invalid_yaml_raises_loader_error(self):
        with self.assertRaises(LoaderError):
            load_protocol_dict("myType: [unclosed", fmt="yaml")

    def test_empty_yaml_raises_loader_error(self):
        with self.assertRaises(LoaderError):
            load_protocol_dict("   \n", fmt="yaml")

    def test_unsupported_source_type_raises_loader_error(self):
        with self.assertRaises(LoaderError):
            load_protocol_dict(12345)

    def test_missing_file_with_path_like_name_gives_helpful_error(self):
        # Regression test for the documented cwd-relative-path bug: a
        # string that LOOKS like a path (.yml/.yaml/.json, no newline) but
        # doesn't exist must raise LoaderError with the cwd in the message
        # -- NOT silently fall through to being parsed as YAML/JSON content.
        with self.assertRaises(LoaderError) as ctx:
            load_protocol_dict("this_file_does_not_exist.yml")
        self.assertIn("this_file_does_not_exist.yml", str(ctx.exception))

    def test_loads_from_real_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "proto.yml")
            with open(path, "w") as fh:
                fh.write("myType: varint\n")
            self.assertEqual(load_protocol_dict(path), {"myType": "varint"})

    def test_json_extension_content_without_newline_is_still_content_not_path(self):
        # Content that happens to look like a filename-ish string but isn't
        # a real path AND doesn't end in a recognized extension should just
        # be parsed as inline text, not raise a missing-file error.
        got = load_protocol_dict('{"a": 1}', fmt="json")
        self.assertEqual(got, {"a": 1})


class TestReverseConversion(unittest.TestCase):
    def test_protocol_dict_to_yaml_is_reversible_via_loader(self):
        original = {
            "myType": ["container", [
                {"name": "count", "type": "varint"},
                {"name": "items", "type": ["array", {"countType": "varint", "type": "u8"}]},
            ]],
        }
        yaml_text = protocol_dict_to_yaml(original)
        self.assertIsInstance(yaml_text, str)
        roundtripped = load_protocol_dict(yaml_text, fmt="yaml")
        self.assertEqual(roundtripped, original)


if __name__ == "__main__":
    unittest.main()
