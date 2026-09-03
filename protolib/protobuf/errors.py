"""
protolib/protobuf/errors.py

Exceptions specific to the .proto parser and the protobuf wire format,
kept separate from protolib/errors.py (the YAML/JSON protodef-style
engine's own exceptions) since the two schema languages are otherwise
fully independent -- an error in one should never be caught by code
written to handle the other.
"""

from __future__ import annotations


class ProtobufError(Exception):
    """Base error for everything under protolib.protobuf."""


# --- Wire format errors (wire.py) -------------------------------------

class ProtobufDecodeError(ProtobufError):
    """The bytes being read don't form valid protobuf wire format:
    a malformed tag, an invalid LEN length, a value with wrong UTF-8,
    an unrecognized wire type, etc."""


class VarintTooLongError(ProtobufDecodeError):
    """A varint exceeded the spec's 10-byte cap (ceil(64/7)) without
    its continuation bit clearing -- either malformed input or a
    decoder desync (e.g. reading a varint at the wrong offset because
    an earlier field was misinterpreted)."""

    def __init__(self, start_offset: int, bytes_read: int):
        self.start_offset = start_offset
        self.bytes_read = bytes_read
        super().__init__(
            f"varint starting at offset {start_offset} exceeds the 10-byte "
            f"limit (read {bytes_read} bytes with the continuation bit "
            "still set) -- this is either malformed input or a decoder "
            "desync from misreading an earlier field"
        )


# --- .proto parsing errors (proto_lexer.py / proto_parser.py) --------

class ProtoSyntaxError(ProtobufError):
    """The .proto source text doesn't parse: unexpected token, missing
    semicolon/brace, unsupported syntax, etc."""

    def __init__(self, message: str, line: int | None = None, column: int | None = None):
        self.line = line
        self.column = column
        location = f" at line {line}" + (f", column {column}" if column is not None else "") if line else ""
        super().__init__(f"{message}{location}")


class ProtoSemanticError(ProtobufError):
    """The .proto source parses fine syntactically, but is invalid in a
    way that would prevent generating a usable schema: a field number
    reused within the same message, a reference to an undefined
    message/enum type, a field number in the reserved range
    (19000-19999), etc."""


class UnsupportedProtoFeatureError(ProtobufError):
    """The .proto file uses a real, valid protobuf feature that this
    port doesn't implement (yet). Raised explicitly instead of silently
    ignoring the construct or misinterpreting it, so a schema gap is
    obvious immediately rather than producing a subtly wrong parse."""

    def __init__(self, feature: str, line: int | None = None):
        self.feature = feature
        location = f" (line {line})" if line else ""
        super().__init__(f"unsupported .proto feature: {feature}{location}")
