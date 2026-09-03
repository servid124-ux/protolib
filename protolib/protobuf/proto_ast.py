"""
protolib/protobuf/proto_ast.py

Plain dataclasses describing a parsed .proto file's structure. Kept
separate from proto_parser.py so the shape of the AST is easy to read
on its own, without the parsing logic alongside it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ProtoField:
    name: str
    type_name: str          # e.g. "int32", "string", "Person", "pkg.Address"
    number: int
    label: str = "optional"  # proto3: "optional" (default/implicit), "repeated"
    map_key_type: str | None = None    # set when this field is `map<K, V>`
    map_value_type: str | None = None
    oneof_name: str | None = None      # set when declared inside a `oneof` block


@dataclass
class ProtoEnumValue:
    name: str
    number: int


@dataclass
class ProtoEnum:
    name: str
    values: list[ProtoEnumValue] = field(default_factory=list)


@dataclass
class ProtoMessage:
    name: str
    fields: list[ProtoField] = field(default_factory=list)
    nested_messages: list["ProtoMessage"] = field(default_factory=list)
    nested_enums: list[ProtoEnum] = field(default_factory=list)
    reserved_numbers: list[int] = field(default_factory=list)


@dataclass
class ProtoFile:
    syntax: str = "proto3"
    package: str | None = None
    imports: list[str] = field(default_factory=list)
    messages: list[ProtoMessage] = field(default_factory=list)
    enums: list[ProtoEnum] = field(default_factory=list)
