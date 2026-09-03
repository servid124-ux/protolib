"""
protolib.protobuf -- real Google Protocol Buffers (.proto) support.

This is a SEPARATE schema language from protolib's original YAML/JSON
protodef-style engine (protolib.core.Protocol): that one describes any
byte layout via generic building blocks (container, array, switch,
mapper, bitfield, ...); this one specifically speaks the .proto syntax
and wire format documented at https://protobuf.dev/ . The two don't
share schema representations -- a .proto file compiles to its own
ResolvedSchema, independent of protolib.core.Protocol.

Quick start:

    >>> from protolib.protobuf import ProtoFileSchema
    >>> schema = ProtoFileSchema.from_file("addressbook.proto")
    >>> data = schema.encode("Person", {"name": "Alice", "id": 1})
    >>> schema.decode("Person", data)
    {'name': 'Alice', 'id': 1}

Submodules, if lower-level access is needed:
  - wire.py: byte-level varint/zigzag/fixed/tag primitives
  - proto_lexer.py / proto_parser.py / proto_ast.py: .proto -> AST
  - proto_schema.py: AST -> name-resolved ResolvedSchema
  - proto_codec.py: ResolvedSchema + bytes <-> dict
"""

from __future__ import annotations

from .proto_ast import ProtoFile, ProtoMessage, ProtoEnum, ProtoField, ProtoEnumValue
from .proto_parser import parse_proto
from .proto_schema import ResolvedSchema, build_schema
from .proto_codec import encode_message, decode_message, ProtoOneofViolationError
from .errors import (
    ProtobufError,
    ProtobufDecodeError,
    VarintTooLongError,
    ProtoSyntaxError,
    ProtoSemanticError,
    UnsupportedProtoFeatureError,
)


class ProtoFileSchema:
    """
    High-level convenience wrapper: parses a .proto file (or source
    string) once, and exposes encode/decode by message name -- the
    protobuf equivalent of protolib.core.Protocol's
    read_named/write_named, so both schema languages feel similar to
    use day-to-day even though they're independently implemented.
    """

    def __init__(self, resolved_schema: ResolvedSchema):
        self._schema = resolved_schema

    @classmethod
    def from_source(cls, proto_source: str) -> "ProtoFileSchema":
        proto_file = parse_proto(proto_source)
        return cls(build_schema(proto_file))

    @classmethod
    def from_file(cls, path: str) -> "ProtoFileSchema":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_source(f.read())

    @property
    def resolved(self) -> ResolvedSchema:
        """Escape hatch to the underlying ResolvedSchema, for callers
        that need direct access to the parsed message/enum definitions
        (e.g. introspecting fields, building tooling on top)."""
        return self._schema

    def encode(self, message_name: str, value: dict) -> bytes:
        """Serializes `value` (a dict of field name -> Python value) as
        the named message type's real protobuf wire-format bytes."""
        message = self._schema.message_by_name(message_name)
        return encode_message(self._schema, message, value)

    def decode(self, message_name: str, data: bytes) -> dict:
        """Parses real protobuf wire-format `data` bytes as the named
        message type, returning a plain dict of field name -> value."""
        message = self._schema.message_by_name(message_name)
        return decode_message(self._schema, message, data)


__all__ = [
    "ProtoFileSchema",
    "ProtoFile",
    "ProtoMessage",
    "ProtoEnum",
    "ProtoField",
    "ProtoEnumValue",
    "parse_proto",
    "ResolvedSchema",
    "build_schema",
    "encode_message",
    "decode_message",
    "ProtoOneofViolationError",
    "ProtobufError",
    "ProtobufDecodeError",
    "VarintTooLongError",
    "ProtoSyntaxError",
    "ProtoSemanticError",
    "UnsupportedProtoFeatureError",
]
