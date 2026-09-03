"""
protolib/protobuf/proto_schema.py

Takes a parsed ProtoFile (proto_ast.py) and resolves every field's
`type_name` (a bare string in the AST, e.g. "PhoneType" or
"tutorial.Person.PhoneNumber") into an actual ProtoMessage/ProtoEnum
definition, following proto3's own name resolution rules closely
enough for real-world files:

  - A message can reference a SIBLING type defined anywhere in the
    same file (order doesn't matter -- "message A { B b = 1; }
    message B {...}" is legal even though B is declared after A).
  - A message can reference its OWN nested types, or a nested type of
    an ancestor message, by short name (proto's C++-like nested scope
    lookup: search the current message's nested types, then its
    parent's, and so on up to the file's top-level types).
  - A dotted name (`tutorial.Person.PhoneNumber`) is resolved by
    walking that exact path from the (optionally package-qualified)
    root, and a LEADING dot (`.tutorial.Person`) means "start from the
    file's root, ignore lexical nesting" -- proto's own fully-qualified
    name syntax.
  - Recursive message definitions (a message containing, directly or
    indirectly, a field of its own type -- e.g. a tree node with
    repeated children of the same type) are valid and expected in
    protobuf (LEN-delimited encoding makes this natural, unlike a
    fixed-size C struct) -- this resolver does not treat a cycle in
    the type graph as an error, only an inability to find a name at
    all.

This module does NOT touch the wire format -- it only answers "what
kind of thing IS this field", so proto_codec.py can dispatch correctly
without re-deriving type resolution itself.
"""

from __future__ import annotations

from dataclasses import dataclass

from .proto_ast import ProtoFile, ProtoMessage, ProtoEnum, ProtoField
from .proto_parser import SCALAR_TYPES
from .errors import ProtoSemanticError


@dataclass
class ResolvedSchema:
    """A flat, name-indexed view over a ProtoFile's messages and enums,
    with every field's type already resolved to one of: a scalar type
    name (str, e.g. "int32"), a ProtoMessage, or a ProtoEnum."""

    proto_file: ProtoFile
    messages_by_fqname: dict[str, ProtoMessage]
    enums_by_fqname: dict[str, ProtoEnum]
    # For each ProtoMessage (by id(), since messages aren't hashable by
    # name alone when nested types repeat a name across branches),
    # field.type_name resolved to one of: str (scalar), ProtoMessage, ProtoEnum.
    resolved_field_types: dict[int, dict[str, object]]

    def fields_of(self, message: ProtoMessage) -> list[ProtoField]:
        return message.fields

    def resolved_type_of(self, message: ProtoMessage, field: ProtoField) -> object:
        return self.resolved_field_types[id(message)][field.name]

    def message_by_name(self, name: str) -> ProtoMessage:
        """Looks up a message by its plain name (e.g. "Person") or
        fully-qualified name (e.g. "tutorial.Person"). Raises
        ProtoSemanticError if not found or ambiguous."""
        if name in self.messages_by_fqname:
            return self.messages_by_fqname[name]
        # allow a bare short name if it uniquely identifies one message
        # across the whole file (convenience for the common case of a
        # flat, non-package-qualified file with no name collisions)
        candidates = [
            fq for fq in self.messages_by_fqname
            if fq == name or fq.endswith("." + name)
        ]
        if len(candidates) == 1:
            return self.messages_by_fqname[candidates[0]]
        if len(candidates) > 1:
            raise ProtoSemanticError(
                f"message name {name!r} is ambiguous; matches: {', '.join(sorted(candidates))}. "
                "Use a fully-qualified name instead."
            )
        raise ProtoSemanticError(f"no message named {name!r} in this schema")


def build_schema(proto_file: ProtoFile) -> ResolvedSchema:
    messages_by_fqname: dict[str, ProtoMessage] = {}
    enums_by_fqname: dict[str, ProtoEnum] = {}

    def register(msg_or_enum, fq_prefix: str, is_message: bool) -> None:
        fqname = f"{fq_prefix}.{msg_or_enum.name}" if fq_prefix else msg_or_enum.name
        if is_message:
            if fqname in messages_by_fqname:
                raise ProtoSemanticError(f"duplicate message name {fqname!r}")
            messages_by_fqname[fqname] = msg_or_enum
            for nested in msg_or_enum.nested_messages:
                register(nested, fqname, True)
            for nested_enum in msg_or_enum.nested_enums:
                register(nested_enum, fqname, False)
        else:
            if fqname in enums_by_fqname:
                raise ProtoSemanticError(f"duplicate enum name {fqname!r}")
            enums_by_fqname[fqname] = msg_or_enum

    package_prefix = proto_file.package or ""
    for m in proto_file.messages:
        register(m, package_prefix, True)
    for e in proto_file.enums:
        register(e, package_prefix, False)

    # Build a reverse index: for every message, what's its lexical
    # scope chain (itself -> parent -> ... -> file root)? Needed to
    # replicate proto's nested-scope name lookup.
    parent_of: dict[int, ProtoMessage | None] = {}

    def index_parents(msg: ProtoMessage, parent: ProtoMessage | None) -> None:
        parent_of[id(msg)] = parent
        for nested in msg.nested_messages:
            index_parents(nested, msg)

    for m in proto_file.messages:
        index_parents(m, None)

    def scope_chain(msg: ProtoMessage) -> list[ProtoMessage]:
        chain = [msg]
        current = parent_of.get(id(msg))
        while current is not None:
            chain.append(current)
            current = parent_of.get(id(current))
        return chain

    def fqname_of(msg_or_enum, is_message: bool) -> str:
        table = messages_by_fqname if is_message else enums_by_fqname
        for fq, candidate in table.items():
            if candidate is msg_or_enum:
                return fq
        raise AssertionError("internal error: type was registered but its fqname wasn't found")

    def resolve_name(type_name: str, containing_msg: ProtoMessage) -> object:
        if type_name in SCALAR_TYPES:
            return type_name

        if type_name.startswith("."):
            # Fully-qualified from the file root, per proto's own syntax.
            bare = type_name[1:]
            if bare in messages_by_fqname:
                return messages_by_fqname[bare]
            if bare in enums_by_fqname:
                return enums_by_fqname[bare]
            raise ProtoSemanticError(f"undefined type {type_name!r} (fully-qualified lookup failed)")

        # Nested-scope lookup: try resolving `type_name` (which may
        # itself be dotted, e.g. "Person.PhoneNumber") against each
        # enclosing scope from innermost to outermost, then the
        # package-qualified root, per proto3's own resolution order.
        chain = scope_chain(containing_msg)
        candidates: list[str] = []
        for scope_msg in chain:
            scope_fq = fqname_of(scope_msg, True)
            # try relative to this scope's own fqname (sibling/nested lookup)
            parent_fq = scope_fq.rsplit(".", 1)[0] if "." in scope_fq else ""
            candidates.append(f"{parent_fq}.{type_name}" if parent_fq else type_name)
            candidates.append(f"{scope_fq}.{type_name}")
        if package_prefix:
            candidates.append(f"{package_prefix}.{type_name}")
        candidates.append(type_name)

        for cand in candidates:
            if cand in messages_by_fqname:
                return messages_by_fqname[cand]
            if cand in enums_by_fqname:
                return enums_by_fqname[cand]

        raise ProtoSemanticError(
            f"undefined type {type_name!r} referenced from message "
            f"{fqname_of(containing_msg, True)!r} "
            f"(tried: {', '.join(dict.fromkeys(candidates))})"
        )

    resolved_field_types: dict[int, dict[str, object]] = {}

    def resolve_message_fields(msg: ProtoMessage) -> None:
        field_map: dict[str, object] = {}
        seen_numbers: dict[int, str] = {}
        for f in msg.fields:
            if f.number in seen_numbers:
                raise ProtoSemanticError(
                    f"message {fqname_of(msg, True)!r}: field number {f.number} used by "
                    f"both {seen_numbers[f.number]!r} and {f.name!r}"
                )
            if f.number in msg.reserved_numbers:
                raise ProtoSemanticError(
                    f"message {fqname_of(msg, True)!r}: field {f.name!r} uses reserved "
                    f"number {f.number}"
                )
            seen_numbers[f.number] = f.name
            if f.map_key_type is not None:
                # map<K, V> fields are handled specially by proto_codec.py
                # (protobuf actually desugars these into a synthetic
                # nested message with key=1/value=2 -- see proto_codec)
                key_resolved = resolve_name(f.map_key_type, msg) if f.map_key_type not in SCALAR_TYPES else f.map_key_type
                val_resolved = resolve_name(f.map_value_type, msg) if f.map_value_type not in SCALAR_TYPES else f.map_value_type
                field_map[f.name] = ("map", key_resolved, val_resolved)
            else:
                field_map[f.name] = resolve_name(f.type_name, msg)
        resolved_field_types[id(msg)] = field_map
        for nested in msg.nested_messages:
            resolve_message_fields(nested)

    for m in proto_file.messages:
        resolve_message_fields(m)

    return ResolvedSchema(
        proto_file=proto_file,
        messages_by_fqname=messages_by_fqname,
        enums_by_fqname=enums_by_fqname,
        resolved_field_types=resolved_field_types,
    )
