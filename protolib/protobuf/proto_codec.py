"""
protolib/protobuf/proto_codec.py

Reads/writes real protobuf wire-format bytes for a message defined in
a ResolvedSchema (proto_schema.py), producing/consuming plain Python
dicts the same way protolib's core.py does for its YAML/JSON schemas
-- so the two schema languages feel consistent to use even though
they're otherwise fully independent implementations.

Key wire-format behaviors this respects (see wire.py's own docstring
for the byte-level details):

  - PACKED repeated encoding is proto3's DEFAULT for repeated fields of
    a scalar numeric/bool/enum type (varint, fixed32, fixed64 wire
    types): all values are concatenated into ONE LEN-delimited run
    instead of one tag+value pair per element. `string`/`bytes`/message
    fields are NEVER packed (each element always gets its own tag).
    A decoder must ALSO accept the non-packed form for a field that's
    declared packed (older encoders, or an encoder from a different
    language, might emit it that way) -- this is a spec requirement
    for forward/backward compatibility, not an edge case to skip.
  - Unknown field numbers (present on the wire, absent from the
    schema -- e.g. reading data written by a newer .proto revision)
    are skipped, not treated as an error, per spec.
  - A message field is itself LEN-delimited (its own serialized bytes,
    length-prefixed) and, if the same field number appears more than
    once for a NON-repeated message field, the spec requires the
    LATEST occurrence to win (as if later-merged) -- unlike a scalar
    singular field, where in practice most decoders (including this
    one) also take the last occurrence, consistent with the "merge"
    semantics the spec describes for embedded messages.
  - proto3 has no wire-level way to tell "explicitly set to the
    default value" apart from "not set" for singular scalar fields
    (that's what proto3's `optional` keyword's presence-tracking
    exists for, in newer proto3 -- this port does not implement
    presence tracking; see the note in ProtoMessageEncoder below).
    Consequently, when ENCODING, a singular scalar field equal to its
    type's default (0, "", b"", False, enum value 0) is simply not
    written to the wire at all -- this is correct proto3 behavior, not
    data loss, since decoding an absent field back yields that same
    default.
"""

from __future__ import annotations

from typing import Any

from ..io import Reader, Writer, BufferUnderrun
from . import wire
from .proto_ast import ProtoMessage, ProtoEnum, ProtoField
from .proto_schema import ResolvedSchema
from .errors import ProtobufDecodeError, ProtoSemanticError

# Which wire type each scalar type uses, and whether it's eligible for
# packed encoding when repeated (per spec: only VARINT/I32/I64 scalar
# types pack; LEN-based types -- string, bytes, message -- never do).
_SCALAR_WIRETYPE: dict[str, int] = {
    "int32": wire.WIRETYPE_VARINT, "int64": wire.WIRETYPE_VARINT,
    "uint32": wire.WIRETYPE_VARINT, "uint64": wire.WIRETYPE_VARINT,
    "sint32": wire.WIRETYPE_VARINT, "sint64": wire.WIRETYPE_VARINT,
    "bool": wire.WIRETYPE_VARINT,
    "fixed32": wire.WIRETYPE_I32, "sfixed32": wire.WIRETYPE_I32, "float": wire.WIRETYPE_I32,
    "fixed64": wire.WIRETYPE_I64, "sfixed64": wire.WIRETYPE_I64, "double": wire.WIRETYPE_I64,
    "string": wire.WIRETYPE_LEN, "bytes": wire.WIRETYPE_LEN,
}

_SCALAR_READERS = {
    "int32": wire.read_int32, "int64": wire.read_int64,
    "uint32": wire.read_uint32, "uint64": wire.read_uint64,
    "sint32": wire.read_sint32, "sint64": wire.read_sint64,
    "bool": wire.read_bool,
    "fixed32": wire.read_fixed32, "sfixed32": wire.read_sfixed32, "float": wire.read_float,
    "fixed64": wire.read_fixed64, "sfixed64": wire.read_sfixed64, "double": wire.read_double,
    "string": wire.read_string, "bytes": wire.read_len_delimited,
}

_SCALAR_WRITERS = {
    "int32": wire.write_int32, "int64": wire.write_int64,
    "uint32": wire.write_uint32, "uint64": wire.write_uint64,
    "sint32": wire.write_sint32, "sint64": wire.write_sint64,
    "bool": wire.write_bool,
    "fixed32": wire.write_fixed32, "sfixed32": wire.write_sfixed32, "float": wire.write_float,
    "fixed64": wire.write_fixed64, "sfixed64": wire.write_sfixed64, "double": wire.write_double,
    "string": wire.write_string, "bytes": lambda v, w: wire.write_len_delimited(v, w),
}

_SCALAR_DEFAULTS: dict[str, Any] = {
    "int32": 0, "int64": 0, "uint32": 0, "uint64": 0, "sint32": 0, "sint64": 0,
    "bool": False, "fixed32": 0, "sfixed32": 0, "float": 0.0,
    "fixed64": 0, "sfixed64": 0, "double": 0.0, "string": "", "bytes": b"",
}

_PACKABLE_WIRETYPES = frozenset({wire.WIRETYPE_VARINT, wire.WIRETYPE_I32, wire.WIRETYPE_I64})


def _is_scalar(resolved_type: object) -> bool:
    return isinstance(resolved_type, str)


def _read_packable_scalar_element(scalar_type: str, r: Reader) -> Any:
    return _SCALAR_READERS[scalar_type](r)


def _write_packable_scalar_element(scalar_type: str, value: Any, w: Writer) -> None:
    _SCALAR_WRITERS[scalar_type](value, w)


class ProtoOneofViolationError(ProtoSemanticError):
    """More than one field belonging to the same `oneof` group was
    present in the value dict being encoded. A oneof's whole point is
    mutual exclusivity -- at most one of its member fields may be set
    at a time -- so having two set simultaneously isn't something to
    silently resolve (e.g. by picking one and dropping the other); the
    caller's data itself is inconsistent with the schema."""

    def __init__(self, oneof_name: str, present_fields: list[str]):
        self.oneof_name = oneof_name
        self.present_fields = present_fields
        super().__init__(
            f"oneof {oneof_name!r}: more than one member field is present "
            f"in the value being encoded ({', '.join(present_fields)}); "
            "a oneof allows at most one of its fields to be set at a time"
        )


def encode_message(schema: ResolvedSchema, message: ProtoMessage, value: dict) -> bytes:
    w = Writer()
    _write_message_fields(schema, message, value or {}, w)
    return w.result()


def decode_message(schema: ResolvedSchema, message: ProtoMessage, data: bytes) -> dict:
    r = Reader(data)
    return _read_message_fields(schema, message, r, len(data))


def _write_message_fields(schema: ResolvedSchema, message: ProtoMessage, value: dict, w: Writer) -> None:
    fields_by_name = {f.name: f for f in message.fields}
    _check_oneof_exclusivity(message, value)
    for field_name, field in fields_by_name.items():
        if field_name not in value:
            continue
        raw_value = value[field_name]
        resolved = schema.resolved_type_of(message, field)

        if isinstance(resolved, tuple) and resolved[0] == "map":
            _write_map_field(schema, field, resolved, raw_value, w)
            continue

        if field.label == "repeated":
            _write_repeated_field(schema, field, resolved, raw_value, w)
            continue

        _write_singular_field(schema, field, resolved, raw_value, w, is_oneof_member=field.oneof_name is not None)


def _check_oneof_exclusivity(message: ProtoMessage, value: dict) -> None:
    groups: dict[str, list[str]] = {}
    for f in message.fields:
        if f.oneof_name is not None:
            groups.setdefault(f.oneof_name, []).append(f.name)
    for oneof_name, member_names in groups.items():
        present = [name for name in member_names if name in value]
        if len(present) > 1:
            raise ProtoOneofViolationError(oneof_name, present)


def _write_singular_field(schema: ResolvedSchema, field: ProtoField, resolved: object, raw_value: Any,
                            w: Writer, *, is_oneof_member: bool = False) -> None:
    if _is_scalar(resolved):
        # proto3: a singular scalar field at its type's default value
        # is simply omitted from the wire -- see this module's
        # docstring. This isn't data loss: decoding an absent field
        # yields the same default right back.
        #
        # EXCEPT for a oneof member: a oneof's whole purpose is
        # tracking WHICH member is active, which is a different fact
        # from "does it hold a non-default value" -- explicitly setting
        # member `a` to its own default (e.g. `{"a": 0}`) must still be
        # distinguishable on the wire from no member being set at all
        # (`{}`), or the oneof's presence information is silently lost.
        # A non-oneof singular field has no such "which one is active"
        # question to answer, so it keeps omitting its default value.
        if raw_value == _SCALAR_DEFAULTS[resolved] and not is_oneof_member:
            return
        wire_type = _SCALAR_WIRETYPE[resolved]
        wire.write_tag(field.number, wire_type, w)
        _SCALAR_WRITERS[resolved](raw_value, w)
        return

    if isinstance(resolved, ProtoEnum):
        if (raw_value == 0 or raw_value == _enum_zero_name(resolved)) and not is_oneof_member:
            return
        numeric = _enum_value_to_number(resolved, raw_value)
        wire.write_tag(field.number, wire.WIRETYPE_VARINT, w)
        wire.write_int32(numeric, w)
        return

    # Embedded message: always LEN-delimited, even if "empty" -- this
    # was already correct regardless of oneof membership (an
    # explicitly-set-but-empty sub-message is distinguishable on the
    # wire from an absent one; "absent" is the key simply missing from
    # the dict, which _write_message_fields already handles by
    # skipping it before we get here).
    sub_bytes = encode_message(schema, resolved, raw_value or {})
    wire.write_tag(field.number, wire.WIRETYPE_LEN, w)
    wire.write_len_delimited(sub_bytes, w)


def _write_repeated_field(schema: ResolvedSchema, field: ProtoField, resolved: object, values: list, w: Writer) -> None:
    if not values:
        return  # an empty repeated field writes nothing, same as an absent one

    if _is_scalar(resolved) and _SCALAR_WIRETYPE[resolved] in _PACKABLE_WIRETYPES:
        # PACKED encoding: one tag, then all values back-to-back inside
        # a single LEN block -- proto3's default for repeated numeric/
        # bool scalars. See this module's docstring for why.
        inner = Writer()
        for v in values:
            _write_packable_scalar_element(resolved, v, inner)
        wire.write_tag(field.number, wire.WIRETYPE_LEN, w)
        wire.write_len_delimited(inner.result(), w)
        return

    if _is_scalar(resolved):
        # string/bytes: never packed, one tag+LEN per element
        for v in values:
            wire.write_tag(field.number, wire.WIRETYPE_LEN, w)
            _SCALAR_WRITERS[resolved](v, w)
        return

    if isinstance(resolved, ProtoEnum):
        # Enums pack too (they're VARINT at the wire level).
        inner = Writer()
        for v in values:
            numeric = _enum_value_to_number(resolved, v)
            wire.write_int32(numeric, inner)
        wire.write_tag(field.number, wire.WIRETYPE_LEN, w)
        wire.write_len_delimited(inner.result(), w)
        return

    # repeated message: one tag+LEN per element, never packed
    for v in values:
        sub_bytes = encode_message(schema, resolved, v or {})
        wire.write_tag(field.number, wire.WIRETYPE_LEN, w)
        wire.write_len_delimited(sub_bytes, w)


def _write_map_field(schema: ResolvedSchema, field: ProtoField, resolved: tuple, mapping: dict, w: Writer) -> None:
    _, key_type, value_type = resolved
    if not mapping:
        return
    for k, v in mapping.items():
        # Per spec, `map<K, V> foo = N;` is wire-identical to:
        #   message FooEntry { K key = 1; V value = 2; }
        #   repeated FooEntry foo = N;
        # so each entry is encoded as its own tiny embedded message.
        entry_writer = Writer()
        if _is_scalar(key_type):
            if k != _SCALAR_DEFAULTS.get(key_type, None):
                wire.write_tag(1, _SCALAR_WIRETYPE[key_type], entry_writer)
                _SCALAR_WRITERS[key_type](k, entry_writer)
        else:
            raise ProtoSemanticError("map keys must be a scalar type per the protobuf spec")

        if _is_scalar(value_type):
            if v != _SCALAR_DEFAULTS.get(value_type, None):
                wire.write_tag(2, _SCALAR_WIRETYPE[value_type], entry_writer)
                _SCALAR_WRITERS[value_type](v, entry_writer)
        elif isinstance(value_type, ProtoEnum):
            numeric = _enum_value_to_number(value_type, v)
            if numeric != 0:
                wire.write_tag(2, wire.WIRETYPE_VARINT, entry_writer)
                wire.write_int32(numeric, entry_writer)
        else:
            sub_bytes = encode_message(schema, value_type, v or {})
            wire.write_tag(2, wire.WIRETYPE_LEN, entry_writer)
            wire.write_len_delimited(sub_bytes, entry_writer)

        wire.write_tag(field.number, wire.WIRETYPE_LEN, w)
        wire.write_len_delimited(entry_writer.result(), w)


def _read_message_fields(schema: ResolvedSchema, message: ProtoMessage, r: Reader, end_offset: int) -> dict:
    fields_by_number = {f.number: f for f in message.fields}
    result: dict[str, Any] = {}
    # Pre-seed repeated fields as empty lists / map fields as empty
    # dicts so a field with zero occurrences on the wire still comes
    # back as [] / {} rather than being absent from the result --
    # matches what real protobuf bindings do for repeated/map fields.
    for f in message.fields:
        resolved = schema.resolved_type_of(message, f)
        if isinstance(resolved, tuple) and resolved[0] == "map":
            result[f.name] = {}
        elif f.label == "repeated":
            result[f.name] = []

    while r.offset < end_offset:
        start_of_field = r.offset
        try:
            field_number, wire_type = wire.read_tag(r)
        except BufferUnderrun as exc:
            # The bytes remaining inside this message's own boundary
            # don't even form a complete tag -- almost always means an
            # earlier LEN length (this message's own, or an ancestor's)
            # didn't actually match what was encoded, leaving stray
            # bytes that aren't real protobuf here at all. Re-raised
            # with the message name and the exact offset where the
            # mismatch became visible, instead of surfacing a bare
            # BufferUnderrun from three stack frames down with no
            # indication of which message/boundary was involved.
            raise ProtobufDecodeError(
                f"message {message.name!r}: could not read a field tag at "
                f"offset {start_of_field} (within this message's own "
                f"{end_offset - start_of_field} remaining declared bytes) -- "
                "this message's LEN boundary likely doesn't actually match "
                "what's encoded inside it"
            ) from exc

        field = fields_by_number.get(field_number)

        if field is None:
            # Unknown field: skip per spec (forward compatibility --
            # see this module's docstring). Not an error.
            try:
                wire.skip_field(wire_type, r)
            except BufferUnderrun as exc:
                raise ProtobufDecodeError(
                    f"message {message.name!r}: unknown field {field_number} "
                    f"(wire type {wire_type}) at offset {start_of_field} "
                    "claims more bytes than remain inside this message's own "
                    "boundary -- malformed input or a boundary mismatch"
                ) from exc
            continue

        resolved = schema.resolved_type_of(message, field)

        if isinstance(resolved, tuple) and resolved[0] == "map":
            key, val = _read_map_entry(schema, resolved, r)
            result[field.name][key] = val
            continue

        if field.label == "repeated":
            _read_repeated_element(schema, field, resolved, wire_type, r, result)
            continue

        if field.oneof_name is not None:
            # Per spec, only one member of a oneof may be set at a
            # time; if the wire contains more than one (a different/
            # non-conforming encoder, corrupted data, or simply a
            # message written by an older/newer schema revision), the
            # LAST one encountered wins and any earlier member of the
            # SAME oneof group must be cleared -- otherwise the result
            # dict would end up with two "mutually exclusive" fields
            # both set, which the schema declares impossible.
            for sibling in fields_by_number.values():
                if sibling.oneof_name == field.oneof_name and sibling.name != field.name:
                    result.pop(sibling.name, None)

        result[field.name] = _read_singular_value(schema, resolved, wire_type, r)

        if r.offset > end_offset:
            # This field's own value (a nested message reading its own
            # LEN-delimited content, most commonly) consumed bytes past
            # what THIS message's boundary said was available -- i.e.
            # it read into bytes that actually belong to whatever comes
            # after this message ends. Caught immediately rather than
            # left for the loop-exit check below so the error points at
            # the actual field that overran, not just "somewhere in
            # this message".
            raise ProtobufDecodeError(
                f"message {message.name!r}: field {field.name!r} (number "
                f"{field.number}) read past this message's own boundary "
                f"(ended at offset {r.offset}, boundary was {end_offset}) -- "
                "a LEN length prefix likely didn't match what's actually "
                "encoded there"
            )

    if r.offset != end_offset:
        raise ProtobufDecodeError(
            f"message {message.name!r}: boundary mismatch -- expected to end "
            f"exactly at offset {end_offset}, last field read ended at "
            f"{r.offset} (a LEN length prefix likely didn't match the field "
            "actually encoded there)"
        )

    return result


def _read_singular_value(schema: ResolvedSchema, resolved: object, wire_type: int, r: Reader) -> Any:
    if _is_scalar(resolved):
        expected_wt = _SCALAR_WIRETYPE[resolved]
        if wire_type != expected_wt:
            raise ProtobufDecodeError(
                f"field type {resolved!r} expects wire type {expected_wt}, got {wire_type} "
                "(the .proto schema and the actual bytes disagree on this field's shape)"
            )
        return _SCALAR_READERS[resolved](r)

    if isinstance(resolved, ProtoEnum):
        if wire_type != wire.WIRETYPE_VARINT:
            raise ProtobufDecodeError(f"enum field expects VARINT wire type, got {wire_type}")
        numeric = wire.read_int32(r)
        return _enum_number_to_name(resolved, numeric)

    # embedded message
    if wire_type != wire.WIRETYPE_LEN:
        raise ProtobufDecodeError(f"message field expects LEN wire type, got {wire_type}")
    sub_bytes = wire.read_len_delimited(r)
    sub_reader = Reader(sub_bytes)
    return _read_message_fields(schema, resolved, sub_reader, len(sub_bytes))


def _read_repeated_element(schema: ResolvedSchema, field: ProtoField, resolved: object,
                             wire_type: int, r: Reader, result: dict) -> None:
    if _is_scalar(resolved) and _SCALAR_WIRETYPE[resolved] in _PACKABLE_WIRETYPES and wire_type == wire.WIRETYPE_LEN:
        # Packed form. Per spec, a decoder MUST also accept the
        # non-packed form (handled by the branches below) even for a
        # field that would normally be packed -- interoperability with
        # encoders that don't pack, or didn't in an older message.
        packed_bytes = wire.read_len_delimited(r)
        inner = Reader(packed_bytes)
        while inner.offset < len(packed_bytes):
            result[field.name].append(_read_packable_scalar_element(resolved, inner))
        return

    if isinstance(resolved, ProtoEnum) and wire_type == wire.WIRETYPE_LEN:
        packed_bytes = wire.read_len_delimited(r)
        inner = Reader(packed_bytes)
        while inner.offset < len(packed_bytes):
            numeric = wire.read_int32(inner)
            result[field.name].append(_enum_number_to_name(resolved, numeric))
        return

    # Non-packed: exactly one element per tag occurrence.
    result[field.name].append(_read_singular_value(schema, resolved, wire_type, r))


def _read_map_entry(schema: ResolvedSchema, resolved: tuple, r: Reader) -> tuple[Any, Any]:
    _, key_type, value_type = resolved
    entry_bytes = wire.read_len_delimited(r)
    entry_reader = Reader(entry_bytes)
    key = _SCALAR_DEFAULTS.get(key_type) if _is_scalar(key_type) else None
    if isinstance(value_type, ProtoEnum):
        value = _enum_number_to_name(value_type, 0)
    elif _is_scalar(value_type):
        value = _SCALAR_DEFAULTS.get(value_type)
    else:
        value = {}
    while entry_reader.offset < len(entry_bytes):
        entry_field_number, entry_wire_type = wire.read_tag(entry_reader)
        if entry_field_number == 1:
            key = _SCALAR_READERS[key_type](entry_reader)
        elif entry_field_number == 2:
            if isinstance(value_type, ProtoEnum):
                value = _enum_number_to_name(value_type, wire.read_int32(entry_reader))
            elif _is_scalar(value_type):
                value = _SCALAR_READERS[value_type](entry_reader)
            else:
                sub_bytes = wire.read_len_delimited(entry_reader)
                value = _read_message_fields(schema, value_type, Reader(sub_bytes), len(sub_bytes))
        else:
            wire.skip_field(entry_wire_type, entry_reader)
    return key, value


def _enum_zero_name(enum: ProtoEnum) -> str | None:
    for v in enum.values:
        if v.number == 0:
            return v.name
    return None


def _enum_value_to_number(enum: ProtoEnum, value: Any) -> int:
    if isinstance(value, int):
        return value
    for v in enum.values:
        if v.name == value:
            return v.number
    raise ProtoSemanticError(f"enum {enum.name!r} has no value named {value!r}")


def _enum_number_to_name(enum: ProtoEnum, number: int) -> str:
    for v in enum.values:
        if v.number == number:
            return v.name
    # Per spec, an unrecognized enum number (e.g. sent by a newer
    # binary with an enum value this schema doesn't know about yet)
    # must NOT be rejected -- it must round-trip as the raw number.
    # This is different from `mapper` in the YAML/JSON engine (where
    # an unmapped value IS an error): protobuf's own forward-
    # compatibility model explicitly requires tolerating unknown enum
    # numbers, since the enum is really "just an int32 with suggested
    # names" at the wire level.
    return number
