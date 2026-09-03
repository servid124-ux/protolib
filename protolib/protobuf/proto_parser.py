"""
protolib/protobuf/proto_parser.py

A recursive-descent parser for proto3 .proto files, following the
grammar at https://protobuf.dev/reference/protobuf/proto3-spec/ .

Deliberately scoped to what real-world .proto files use most: syntax
declaration, package, import (recorded but not resolved -- see
UnsupportedProtoFeatureError below), message (with nesting, repeated,
optional, map<K,V>, oneof), enum, and field/message/enum options
(parsed and discarded -- they don't affect wire-format shape, which is
this port's whole purpose). proto2-only constructs (required fields,
extensions, groups) are explicitly rejected with a clear error rather
than silently mis-parsed, since guessing at proto2 semantics would be
worse than refusing.
"""

from __future__ import annotations

from .proto_ast import ProtoFile, ProtoMessage, ProtoField, ProtoEnum, ProtoEnumValue
from .proto_lexer import tokenize, Token, TokenType
from .errors import ProtoSyntaxError, ProtoSemanticError, UnsupportedProtoFeatureError

# Scalar types recognized directly by the wire format (wire.py) --
# anything else in a field's type position is assumed to be a
# message/enum reference, resolved later by proto_schema.py against
# what was actually declared in the file.
SCALAR_TYPES = frozenset({
    "double", "float", "int32", "int64", "uint32", "uint64",
    "sint32", "sint64", "fixed32", "fixed64", "sfixed32", "sfixed64",
    "bool", "string", "bytes",
})


class _TokenStream:
    """Thin cursor over a token list with lookahead, so the parser
    functions below read like the grammar they implement instead of
    manually juggling an index everywhere."""

    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0

    def peek(self, offset: int = 0) -> Token:
        idx = min(self._pos + offset, len(self._tokens) - 1)
        return self._tokens[idx]

    def advance(self) -> Token:
        tok = self._tokens[self._pos]
        if self._pos < len(self._tokens) - 1:
            self._pos += 1
        return tok

    def at_end(self) -> bool:
        return self.peek().type == TokenType.EOF

    def check_ident(self, *values: str) -> bool:
        tok = self.peek()
        return tok.type == TokenType.IDENT and tok.value in values

    def check_symbol(self, value: str) -> bool:
        tok = self.peek()
        return tok.type == TokenType.SYMBOL and tok.value == value

    def expect_symbol(self, value: str) -> Token:
        if not self.check_symbol(value):
            tok = self.peek()
            raise ProtoSyntaxError(
                f"expected {value!r}, got {tok.type.name} {tok.value!r}", tok.line, tok.column
            )
        return self.advance()

    def expect_ident(self, expected: str | None = None) -> Token:
        tok = self.peek()
        if tok.type != TokenType.IDENT or (expected is not None and tok.value != expected):
            what = f"identifier {expected!r}" if expected else "identifier"
            raise ProtoSyntaxError(f"expected {what}, got {tok.type.name} {tok.value!r}", tok.line, tok.column)
        return self.advance()

    def expect_any_name(self) -> Token:
        """A "name" in .proto grammar (message name, field name, etc.)
        is any identifier, including ones that are also keywords
        elsewhere (e.g. a field literally named `message` is legal)."""
        tok = self.peek()
        if tok.type != TokenType.IDENT:
            raise ProtoSyntaxError(f"expected a name, got {tok.type.name} {tok.value!r}", tok.line, tok.column)
        return self.advance()


def parse_proto(source: str) -> ProtoFile:
    """Parses .proto source text into a ProtoFile AST."""
    ts = _TokenStream(tokenize(source))
    proto_file = ProtoFile()
    seen_syntax = False

    while not ts.at_end():
        if ts.check_symbol(";"):
            ts.advance()  # stray top-level semicolons are legal per grammar
            continue

        if ts.check_ident("syntax"):
            ts.advance()
            ts.expect_symbol("=")
            tok = ts.peek()
            if tok.type != TokenType.STRING:
                raise ProtoSyntaxError("expected a quoted syntax version", tok.line, tok.column)
            value = ts.advance().value
            if value != "proto3":
                raise UnsupportedProtoFeatureError(
                    f'syntax = "{value}" (only proto3 is supported; proto2 has different '
                    "field presence/required-field/extension semantics that this port "
                    "does not implement)",
                    tok.line,
                )
            proto_file.syntax = value
            seen_syntax = True
            ts.expect_symbol(";")
            continue

        if ts.check_ident("package"):
            ts.advance()
            parts = [ts.expect_any_name().value]
            while ts.check_symbol("."):
                ts.advance()
                parts.append(ts.expect_any_name().value)
            proto_file.package = ".".join(parts)
            ts.expect_symbol(";")
            continue

        if ts.check_ident("import"):
            ts.advance()
            if ts.check_ident("public", "weak"):
                ts.advance()
            tok = ts.peek()
            if tok.type != TokenType.STRING:
                raise ProtoSyntaxError("expected a quoted import path", tok.line, tok.column)
            proto_file.imports.append(ts.advance().value)
            ts.expect_symbol(";")
            continue

        if ts.check_ident("option"):
            _skip_option_statement(ts)
            continue

        if ts.check_ident("message"):
            proto_file.messages.append(_parse_message(ts))
            continue

        if ts.check_ident("enum"):
            proto_file.enums.append(_parse_enum(ts))
            continue

        if ts.check_ident("service"):
            _skip_service(ts)
            continue

        if ts.check_ident("extend"):
            tok = ts.peek()
            raise UnsupportedProtoFeatureError("extend (proto2 extensions)", tok.line)

        tok = ts.peek()
        raise ProtoSyntaxError(
            f"unexpected top-level token {tok.type.name} {tok.value!r}", tok.line, tok.column
        )

    return proto_file


def _skip_option_statement(ts: _TokenStream) -> None:
    """`option name = value;` at file/message/field scope. Options
    never change the wire-format shape this port cares about (they
    configure codegen behavior, deprecation flags, etc.), so their
    value is parsed just enough to skip past balanced brackets/braces
    correctly, then discarded."""
    ts.expect_ident("option")
    _skip_option_name(ts)
    ts.expect_symbol("=")
    _skip_option_value(ts)
    ts.expect_symbol(";")


def _skip_option_name(ts: _TokenStream) -> None:
    if ts.check_symbol("("):
        depth = 0
        while True:
            tok = ts.advance()
            if tok.type == TokenType.SYMBOL and tok.value == "(":
                depth += 1
            elif tok.type == TokenType.SYMBOL and tok.value == ")":
                depth -= 1
                if depth == 0:
                    break
    else:
        ts.expect_any_name()
    while ts.check_symbol("."):
        ts.advance()
        ts.expect_any_name()


def _skip_option_value(ts: _TokenStream) -> None:
    if ts.check_symbol("{"):
        depth = 0
        while True:
            tok = ts.advance()
            if tok.type == TokenType.SYMBOL and tok.value == "{":
                depth += 1
            elif tok.type == TokenType.SYMBOL and tok.value == "}":
                depth -= 1
                if depth == 0:
                    break
    else:
        ts.advance()  # a single literal token (string/int/float/ident like `true`)


def _skip_service(ts: _TokenStream) -> None:
    """`service Foo { rpc ... }` -- gRPC service definitions don't
    describe wire-format message SHAPE (this port's whole purpose),
    they describe RPC method names/streaming semantics on top of
    messages that are already fully defined elsewhere in the file. So
    the block is parsed only enough to skip it correctly (balanced
    braces), not translated into anything."""
    ts.expect_ident("service")
    ts.expect_any_name()
    ts.expect_symbol("{")
    depth = 1
    while depth > 0:
        tok = ts.advance()
        if tok.type == TokenType.EOF:
            raise ProtoSyntaxError("unexpected end of file inside service block", tok.line, tok.column)
        if tok.type == TokenType.SYMBOL and tok.value == "{":
            depth += 1
        elif tok.type == TokenType.SYMBOL and tok.value == "}":
            depth -= 1


def _parse_message(ts: _TokenStream) -> ProtoMessage:
    ts.expect_ident("message")
    name = ts.expect_any_name().value
    msg = ProtoMessage(name=name)
    ts.expect_symbol("{")

    while not ts.check_symbol("}"):
        if ts.check_symbol(";"):
            ts.advance()
            continue

        if ts.check_ident("message"):
            msg.nested_messages.append(_parse_message(ts))
            continue

        if ts.check_ident("enum"):
            msg.nested_enums.append(_parse_enum(ts))
            continue

        if ts.check_ident("option"):
            _skip_option_statement(ts)
            continue

        if ts.check_ident("oneof"):
            _parse_oneof_into(ts, msg)
            continue

        if ts.check_ident("reserved"):
            _parse_reserved_into(ts, msg)
            continue

        if ts.check_ident("extensions"):
            tok = ts.peek()
            raise UnsupportedProtoFeatureError("extensions (proto2)", tok.line)

        if ts.check_ident("map"):
            msg.fields.append(_parse_map_field(ts))
            continue

        if ts.check_ident("required"):
            tok = ts.peek()
            raise UnsupportedProtoFeatureError(
                "required fields (proto2-only; proto3 has no field presence "
                "requirement keyword)", tok.line,
            )

        # optional field, or a bare `repeated`/type field
        msg.fields.append(_parse_field(ts))

    ts.expect_symbol("}")
    return msg


def _parse_field_number(ts: _TokenStream) -> int:
    tok = ts.peek()
    if tok.type != TokenType.INT:
        raise ProtoSyntaxError(f"expected a field number, got {tok.type.name} {tok.value!r}", tok.line, tok.column)
    number = ts.advance().value
    if number < 1:
        raise ProtoSemanticError(f"field number {number} must be >= 1")
    if 19000 <= number <= 19999:
        raise ProtoSemanticError(
            f"field number {number} is in the reserved range 19000-19999 "
            "(reserved for internal protobuf implementation use)"
        )
    if number > 536_870_911:  # 2**29 - 1, the spec's hard upper bound
        raise ProtoSemanticError(f"field number {number} exceeds the maximum of 536,870,911")
    return number


def _parse_type_name(ts: _TokenStream) -> str:
    """A field's type: a scalar keyword, or a (possibly dotted/
    leading-dot-qualified) message/enum name."""
    parts = []
    if ts.check_symbol("."):
        ts.advance()  # leading '.' means fully-qualified from the file's root; kept as-is
        parts.append(".")
    parts.append(ts.expect_any_name().value)
    while ts.check_symbol("."):
        ts.advance()
        parts.append(".")
        parts.append(ts.expect_any_name().value)
    return "".join(parts)


def _parse_field_options(ts: _TokenStream) -> None:
    """`[deprecated = true, ...]` after a field declaration. Discarded
    for the same reason as _skip_option_statement -- doesn't affect
    wire shape."""
    if not ts.check_symbol("["):
        return
    ts.advance()
    while not ts.check_symbol("]"):
        _skip_option_name(ts)
        ts.expect_symbol("=")
        _skip_option_value(ts)
        if ts.check_symbol(","):
            ts.advance()
    ts.expect_symbol("]")


def _parse_field(ts: _TokenStream, oneof_name: str | None = None) -> ProtoField:
    label = "optional"
    if ts.check_ident("repeated"):
        ts.advance()
        label = "repeated"
    elif ts.check_ident("optional"):
        # proto3 added explicit `optional` (distinct from the implicit
        # default) to support real field presence tracking. This port
        # doesn't distinguish "explicitly optional" from "implicit
        # default" at the wire level (both are absent-if-default on
        # the wire the same way) -- the label is accepted and recorded
        # but doesn't change encoding/decoding behavior here.
        ts.advance()

    type_name = _parse_type_name(ts)
    field_name = ts.expect_any_name().value
    ts.expect_symbol("=")
    number = _parse_field_number(ts)
    _parse_field_options(ts)
    ts.expect_symbol(";")
    return ProtoField(name=field_name, type_name=type_name, number=number,
                       label=label, oneof_name=oneof_name)


def _parse_map_field(ts: _TokenStream) -> ProtoField:
    ts.expect_ident("map")
    ts.expect_symbol("<")
    key_type = _parse_type_name(ts)
    ts.expect_symbol(",")
    value_type = _parse_type_name(ts)
    ts.expect_symbol(">")
    field_name = ts.expect_any_name().value
    ts.expect_symbol("=")
    number = _parse_field_number(ts)
    _parse_field_options(ts)
    ts.expect_symbol(";")
    return ProtoField(
        name=field_name, type_name=f"map<{key_type}, {value_type}>", number=number,
        label="repeated", map_key_type=key_type, map_value_type=value_type,
    )


def _parse_oneof_into(ts: _TokenStream, msg: ProtoMessage) -> None:
    ts.expect_ident("oneof")
    oneof_name = ts.expect_any_name().value
    ts.expect_symbol("{")
    while not ts.check_symbol("}"):
        if ts.check_symbol(";"):
            ts.advance()
            continue
        if ts.check_ident("option"):
            _skip_option_statement(ts)
            continue
        msg.fields.append(_parse_field(ts, oneof_name=oneof_name))
    ts.expect_symbol("}")


def _parse_reserved_into(ts: _TokenStream, msg: ProtoMessage) -> None:
    ts.expect_ident("reserved")
    # Either a list of field numbers/ranges ("reserved 2, 15, 9 to 11;")
    # or a list of names ("reserved 'foo', 'bar';") -- names don't
    # affect this port's wire-format concerns, only numeric ranges do
    # (to know a number is intentionally unavailable, not a typo).
    if ts.peek().type == TokenType.STRING:
        while True:
            ts.advance()
            if ts.check_symbol(","):
                ts.advance()
                continue
            break
    else:
        while True:
            start = _parse_field_number_allow_max(ts)
            if ts.check_ident("to"):
                ts.advance()
                end = _parse_field_number_allow_max(ts)
                msg.reserved_numbers.extend(range(start, end + 1))
            else:
                msg.reserved_numbers.append(start)
            if ts.check_symbol(","):
                ts.advance()
                continue
            break
    ts.expect_symbol(";")


def _parse_field_number_allow_max(ts: _TokenStream) -> int:
    if ts.check_ident("max"):
        ts.advance()
        return 536_870_911
    return _parse_field_number(ts)


def _parse_enum(ts: _TokenStream) -> ProtoEnum:
    ts.expect_ident("enum")
    name = ts.expect_any_name().value
    enum = ProtoEnum(name=name)
    ts.expect_symbol("{")
    while not ts.check_symbol("}"):
        if ts.check_symbol(";"):
            ts.advance()
            continue
        if ts.check_ident("option"):
            _skip_option_statement(ts)
            continue
        if ts.check_ident("reserved"):
            # enum reserved numbers/names: skip, same reasoning as message-level
            ts.advance()
            while not ts.check_symbol(";"):
                ts.advance()
            ts.advance()
            continue
        value_name = ts.expect_any_name().value
        ts.expect_symbol("=")
        negative = False
        if ts.check_symbol("-"):
            ts.advance()
            negative = True
        tok = ts.peek()
        if tok.type != TokenType.INT:
            raise ProtoSyntaxError(f"expected an enum value number, got {tok.value!r}", tok.line, tok.column)
        number = ts.advance().value
        if negative:
            number = -number
        _parse_field_options(ts)
        ts.expect_symbol(";")
        enum.values.append(ProtoEnumValue(name=value_name, number=number))
    ts.expect_symbol("}")
    if enum.values and enum.values[0].number != 0:
        raise ProtoSemanticError(
            f"enum {name!r}: proto3 requires the first value's number to be "
            f"0 (got {enum.values[0].name} = {enum.values[0].number}), so "
            "there's always a valid default when a field is unset"
        )
    return enum
