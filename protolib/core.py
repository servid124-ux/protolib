"""
protolib/core.py

The library's central engine. The Protocol class loads a protocol.json
(or an equivalent types dict) and knows:

    protocol.read_type(type_def, reader, scope, fields)   -> python value
    protocol.write_type(type_def, value, writer, scope, fields)

    protocol.parse_packet(state, direction, data: bytes) -> ParsedPacket
    protocol.serialize_packet(state, direction, name, params: dict) -> bytes

Supported composite types (defined as ["baseType", options]):
    container       - ordered list of named fields
    array           - homogeneous list, with fixed count, countType, or
                       count-referencing-another-field
    switch          - picks the type based on another field's value (compareTo)
    mapper          - translates a raw integer to a symbolic name (and back)
    option          - optional value preceded by a bool ("present?")
    bitfield        - packs/unpacks sub-fields of N bits each
    bitflags        - integer interpreted as a named set of flags
    buffer          - raw bytes, with fixed length or via countType/count
    pstring         - string with length prefixed by countType (varint, u16, etc)
    entityMetadataLoop - list of entries until a terminator is found
    topBitSetTerminatedArray - list that ends when the highest bit of the
                       first byte read in an entry is NOT set
                       (LEB128-like list pattern)

Primitive types (varint, i32, bool, cstring, etc.) live in primitives.py
and are resolved by name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .io import Reader, Writer
from .primitives import PRIMITIVES
from .conditions import eval_condition
from .errors import (
    UnknownTypeError,
    InvalidTypeDefinition,
    SwitchCaseNotFound,
    MapperValueNotFoundError,
    BitfieldOverflowError,
    InvalidMapperKeyError,
)
from .loader import load_protocol_dict

TypeDef = Any  # str | [str, dict] -- kept loosely typed for simplicity


class _ChainedView(dict):
    """
    A dict that mirrors a live `target` dict (same keys/values, always
    up to date -- see __missing__/etc. below: reads are delegated, so
    fields added to `target` AFTER this view was created still show up
    through it) while additionally remembering its own "one level up"
    ancestor.

    This exists instead of just reusing `target` directly as `parent`
    (which is what pre-0.4.0 did) so that resolve_field_path can climb
    "../../x" (or deeper): each level's _ChainedView remembers the
    level above it, chained, the same way node-protodef's real
    per-container '..' pointer does. Before 0.4.0, `parent` was always
    a bare dict with no memory of what came before it, so "../../x"
    and beyond had no way to climb more than one level and silently
    fell back to `root` (documented, but wrong for a container nested
    two or more levels under a switch/array).

    Implementation note: rather than copying `target`'s contents at
    construction time (which would go stale the moment a sibling field
    is read afterwards -- container fields are populated one at a time
    into the same live dict as parsing proceeds), this subclasses dict
    but overrides the read path to always delegate to `target` live.
    """
    __slots__ = ("_target", "_ancestor")

    def __init__(self, target: dict, ancestor: dict | None):
        super().__init__()
        self._target = target
        self._ancestor = ancestor

    def get(self, key, default=None):
        return self._target.get(key, default)

    def __getitem__(self, key):
        return self._target[key]

    def __contains__(self, key):
        return key in self._target

    def __iter__(self):
        return iter(self._target)

    def __len__(self):
        return len(self._target)

    def items(self):
        return self._target.items()

    def keys(self):
        return self._target.keys()

    def values(self):
        return self._target.values()


def _ancestor_of(d: dict | None) -> dict | None:
    """Returns the dict one level further up than `d`, if `d` is a
    _ChainedView remembering its own ancestor. Otherwise (a plain dict
    with no recorded ancestor, or None) there's no further chain to
    climb, matching pre-0.4.0 behavior exactly."""
    if isinstance(d, _ChainedView):
        return d._ancestor
    return None


def resolve_field_path(path: Any, fields: dict, root: dict | None, parent: dict | None) -> Any:
    """
    Resolves a node-protodef-style field path (utils.js:getField).

    Supports:
      "name"          -> fields["name"]                   (field on the current container)
      "../name"       -> parent["name"]                   (one level up)
      "../../name"    -> parent's own parent["name"]      (two levels up)
      "/name"         -> root["name"]                     (from the absolute root)

    Also, for parity with node-protodef: `count`/`countFor` can be a
    literal integer directly (fixed size), not just a field path -- e.g.
    ["array", {"count": 4, "type": "i32"}]. In that case it's returned
    as-is, without treating it as a string.

    0.4.0: real N-level "../../x" support. `parent` is climbed by
    following each level's own remembered ancestor (see _AncestorDict
    above), the same way node-protodef chains a '..' pointer per
    container. If the chain runs out before `up_count` is satisfied
    (e.g. a plain dict was passed in directly, as some direct read_type/
    write_type callers still do), climbing stops there instead of
    raising -- same permissive fallback as before, just no longer
    capped at exactly one level.
    """
    if isinstance(path, int):
        return path

    if path.startswith("/"):
        context = root if root is not None else fields
        segments = [s for s in path.split("/") if s != ""]
    elif path.startswith("../"):
        up_count = 0
        rest = path
        while rest.startswith("../"):
            up_count += 1
            rest = rest[3:]
        # A single "../" resolves directly against `parent` -- same as
        # pre-0.4.0. Each ADDITIONAL ".." beyond the first climbs one
        # more step via _ancestor_of (new in 0.4.0: real N-level
        # support instead of falling back to root after the first
        # hop). See the long comment on _ChainedView for why `parent`
        # itself already means "one level up" and doesn't need an
        # extra climb for the first "..".
        context = parent
        for _ in range(up_count - 1):
            nxt = _ancestor_of(context)
            if nxt is None:
                break
            context = nxt
        segments = [s for s in rest.split("/") if s != ""]
    else:
        context = fields
        segments = [s for s in path.split("/") if s != ""]

    current = context
    for seg in segments:
        if current is None:
            return None
        current = current.get(seg) if isinstance(current, dict) else None
    return current


def substitute_type_args(type_def: Any, type_args: dict) -> Any:
    """
    Substitutes "$name" placeholders inside a type definition with the
    corresponding value from `type_args`, recursively walking dicts and
    lists. Parity with node-protodef (protodef.js: findArgs/setField/
    produceArgs, used by extendType).

    This is what allows "parametrizable" named types like:

        entityMetadataItem:
          switch:
            compareTo: $compareTo
            fields: {...}

    invoked in the protocol as:

        type:
          - entityMetadataItem
          - compareTo: type

    When invoked this way, entityMetadataItem's default definition
    (which has the "$compareTo" placeholder) is cloned and the
    placeholder is replaced with the real value passed at the use site
    ("type" in this example) -- resulting in an effective switch with
    compareTo="type".

    A placeholder with no value provided in `type_args` (or
    `type_args=None`) is left as the literal string "$name" -- same as
    node-protodef, which only substitutes args that were actually
    present in typeArgs.
    """
    if isinstance(type_def, str):
        if type_def.startswith("$") and isinstance(type_args, dict):
            key = type_def[1:]
            if key in type_args:
                return type_args[key]
        return type_def
    if isinstance(type_def, list):
        return [substitute_type_args(item, type_args) for item in type_def]
    if isinstance(type_def, dict):
        return {k: substitute_type_args(v, type_args) for k, v in type_def.items()}
    return type_def


@dataclass
class ParsedPacket:
    name: str
    params: dict[str, Any]
    bytes_read: int


@dataclass
class Scope:
    """Represents a 'state.direction' (e.g. play.toClient) with its own
    types, which take priority over the protocol's global types."""

    types: dict[str, TypeDef] = field(default_factory=dict)


class Protocol:
    """
    Loads and represents a complete protocol.json.

    Expected structure of the input dict (same format as
    node-minecraft-protocol / node-protodef):

        {
          "types": { "<name>": <typeDef>, ... },
          "<state>": {
             "toClient": { "types": { ... } },
             "toServer": { "types": { ... } }
          },
          ...
        }

    Type names are resolved first against the local scope
    (state.direction.types) and, if not found there, against the
    global types.

    `protocol_source` accepts:
      - an already-parsed dict (classic node-protodef format)
      - a path to a .json, .yml, or .yaml file
      - a string with JSON or YAML content in memory

    .yml/.yaml files support a more readable "shorthand" syntax (see
    protodef/loader.py), which is automatically translated to the
    internal ["type", options] format. minecraft-data's JSON works
    unchanged.
    """

    def __init__(self, protocol_source: dict[str, Any] | str, *, fmt: str | None = None):
        protocol_json = load_protocol_dict(protocol_source, fmt=fmt)
        self.raw = protocol_json
        self.global_types: dict[str, TypeDef] = protocol_json.get("types", {})
        self._scopes: dict[tuple[str, str], Scope] = {}

        for state_name, state_val in protocol_json.items():
            if state_name == "types" or not isinstance(state_val, dict):
                continue
            for direction in ("toClient", "toServer"):
                if direction in state_val:
                    self._scopes[(state_name, direction)] = Scope(
                        types=state_val[direction].get("types", {})
                    )

        self._composite_handlers: dict[str, Callable] = {
            "container": self._read_container,
            "array": self._read_array,
            "count": self._read_count,
            "switch": self._read_switch,
            "mapper": self._read_mapper,
            "option": self._read_option,
            "bitfield": self._read_bitfield,
            "bitflags": self._read_bitflags,
            "buffer": self._read_buffer,
            "pstring": self._read_pstring,
            "entityMetadataLoop": self._read_entity_metadata_loop,
            "topBitSetTerminatedArray": self._read_top_bit_set_terminated_array,
            "cstring": self._read_cstring_encoded,
            "registryEntryHolder": self._read_registry_entry_holder,
            "registryEntryHolderSet": self._read_registry_entry_holder_set,
        }
        self._composite_write_handlers: dict[str, Callable] = {
            "container": self._write_container,
            "array": self._write_array,
            "count": self._write_count,
            "switch": self._write_switch,
            "mapper": self._write_mapper,
            "option": self._write_option,
            "bitfield": self._write_bitfield,
            "bitflags": self._write_bitflags,
            "buffer": self._write_buffer,
            "pstring": self._write_pstring,
            "entityMetadataLoop": self._write_entity_metadata_loop,
            "topBitSetTerminatedArray": self._write_top_bit_set_terminated_array,
            "cstring": self._write_cstring_encoded,
            "registryEntryHolder": self._write_registry_entry_holder,
            "registryEntryHolderSet": self._write_registry_entry_holder_set,
        }

    # -------------------------------------------------------------------
    # Type name resolution
    # -------------------------------------------------------------------

    def get_scope(self, state: str, direction: str) -> Scope:
        try:
            return self._scopes[(state, direction)]
        except KeyError:
            raise UnknownTypeError(f"{state}.{direction} (state/direction not defined)")

    def _resolve_named_type(self, name: str, scope: Scope | None) -> TypeDef | None:
        if scope is not None and name in scope.types:
            return scope.types[name]
        if name in self.global_types:
            return self.global_types[name]
        return None

    # -------------------------------------------------------------------
    # Reading
    # -------------------------------------------------------------------

    def read_type(self, type_def: TypeDef, r: Reader, scope: Scope | None,
                   fields: dict[str, Any], root: dict | None = None,
                   parent: dict | None = None) -> Any:
        if isinstance(type_def, str):
            if type_def in PRIMITIVES:
                return PRIMITIVES[type_def].read(r)
            resolved = self._resolve_named_type(type_def, scope)
            if resolved is None:
                raise UnknownTypeError(type_def)
            return self.read_type(resolved, r, scope, fields, root, parent)

        if isinstance(type_def, list) and len(type_def) == 2:
            base, opts = type_def
            handler = self._composite_handlers.get(base)
            if handler is not None:
                return handler(opts, r, scope, fields, root, parent)
            # `base` is not a composite base type (container/switch/array/...)
            # -- it might be a parametrizable NAMED type, invoked as
            # [name, typeArgs], e.g. [entityMetadataItem, {compareTo: type}].
            # Parity with node-protodef (protodef.js: extendType/produceArgs):
            # the named type's default definition is resolved and the
            # "$arg" placeholders are substituted with the typeArgs given
            # here.
            named = self._resolve_named_type(base, scope)
            if named is None:
                raise UnknownTypeError(f"(composite base type) {base}")
            substituted = substitute_type_args(named, opts if isinstance(opts, dict) else None)
            return self.read_type(substituted, r, scope, fields, root, parent)

        raise InvalidTypeDefinition(type_def)

    # ---- container ------------------------------------------------------

    def _read_container(self, opts: list[dict], r: Reader, scope: Scope,
                          fields: dict, root: dict | None, parent: dict | None,
                          *, push_level: bool = True) -> dict:
        result: dict[str, Any] = {}
        effective_root = root if root is not None else result
        # `push_level` decides whether THIS container becomes the
        # `parent` ("..") seen by its own sub-fields. A normal container
        # (resolved by name, named field, etc.) does push a level --
        # that's how node-protodef works. The exception is a container
        # used directly as an array's inline item_type: there,
        # node-protodef does NOT push context (the array is transparent
        # to ".."), so "../field" inside an array item must skip that
        # item and resolve against the array's own real parent (e.g. the
        # container that holds the array, not the item with `uuid`).
        #
        # 0.4.0: when push_level=True, child_parent is a live
        # _ChainedView over `result` (remembering `parent` as its own
        # ancestor) instead of the bare `result` dict. This is what
        # lets resolve_field_path climb "../../x" (or deeper) for
        # real: each level remembers the level above it, chained,
        # instead of the chain stopping after one hop. Reads through
        # it are always delegated live to `result`, so fields added
        # to `result` by later sibling fields still show up correctly.
        child_parent = _ChainedView(result, parent) if push_level else parent
        for f in opts:
            if "condition" in f:
                if not eval_condition(f["condition"], result, effective_root, fields):
                    continue
            value = self.read_type(f["type"], r, scope, result, effective_root, child_parent)
            if f.get("anon"):
                if isinstance(value, dict):
                    result.update(value)
            else:
                result[f["name"]] = value
        return result

    def _write_container(self, opts: list[dict], value: dict, w: Writer, scope: Scope,
                           fields: dict, root: dict | None, parent: dict | None,
                           *, push_level: bool = True) -> None:
        data = value or {}
        effective_root = root if root is not None else data
        # See the matching comment in _read_container above.
        child_parent = _ChainedView(data, parent) if push_level else parent
        for f in opts:
            if "condition" in f:
                if not eval_condition(f["condition"], data, effective_root, fields):
                    continue
            if f.get("anon"):
                self.write_type(f["type"], data, w, scope, data, effective_root, child_parent)
            else:
                self.write_type(f["type"], data.get(f["name"]), w, scope, data, effective_root, child_parent)

    # ---- array ------------------------------------------------------------

    def _read_array(self, opts: dict, r: Reader, scope: Scope,
                      fields: dict, root, parent) -> list:
        if "count" in opts:
            count = resolve_field_path(opts["count"], fields, root, parent)
            if not isinstance(count, int) or count < 0:
                raise InvalidTypeDefinition(
                    f"array: 'count' ({opts['count']!r}) resolved to {count!r}, "
                    f"expected an integer >= 0 -- check that the field exists "
                    f"and that the path (../, /) points to the correct level"
                )
        elif "countType" in opts:
            count = self.read_type(opts["countType"], r, scope, fields, root, parent)
        else:
            raise InvalidTypeDefinition("array requires 'count' or 'countType'")

        item_type = opts["type"]
        return [self._read_array_item(item_type, r, scope, fields, root, parent) for _ in range(count)]

    def _read_array_item(self, item_type, r: Reader, scope: Scope,
                           fields: dict, root, parent) -> Any:
        # An INLINE container (["container", opts], literal right here
        # in array.type) must not push its own parent level: see the
        # comment in _read_container. This is deliberately NOT
        # generalized to named types that happen to resolve to a
        # container -- a named type used as an array's item type
        # (e.g. "type: player_info") usually represents a real,
        # independently-meaningful record with its own fields and its
        # own descendants, and THOSE descendants legitimately need the
        # item to be their own level (a "../" inside one of the item's
        # own sub-containers must land on the item, not skip past it
        # to the array's parent). Only a literal inline shape --
        # deliberately anonymous, with no identity beyond "whatever the
        # array/switch decided to read here" -- carries the "this
        # isn't really its own thing" signal unambiguously. Any other
        # item_type (named type, switch, primitive) behaves exactly as
        # before.
        #
        # KNOWN LIMITATION (investigated for 0.4.0, not fixed): a
        # NAMED container used directly as an array's item type, with
        # a "../x" INSIDE that named container's own top-level fields
        # meant to skip the item and reach the array's parent, does
        # NOT work -- the named container always pushes its own level
        # (same as any other named type would, anywhere else), so
        # "../x" lands on the array's own (usually empty/irrelevant)
        # level instead. Generalizing push_level=False to any
        # container-shaped named type was tried and reverted: it fixes
        # that case but breaks the much more common one immediately
        # above (a named item with its OWN nested sub-containers that
        # legitimately need "../" to resolve back to the item, not
        # past it). The two needs are structurally indistinguishable
        # from `item_type` alone. Workaround: write the item's shape
        # inline (`type: {container: [...]}`) instead of via a name if
        # you need "../" to skip it and reach the array's parent --
        # that form's transparency is unambiguous and already works.
        if isinstance(item_type, list) and len(item_type) == 2 and item_type[0] == "container":
            return self._read_container(item_type[1], r, scope, fields, root, parent, push_level=False)
        return self.read_type(item_type, r, scope, fields, root, parent)

    def _write_array(self, opts: dict, value: list, w: Writer, scope: Scope,
                       fields: dict, root, parent) -> None:
        items = value or []
        if "countType" in opts:
            self.write_type(opts["countType"], len(items), w, scope, fields, root, parent)
        # if it uses "count" (a reference to another field), the caller
        # must have already written that field (the responsibility of
        # the container assembling 'fields')
        item_type = opts["type"]
        for item in items:
            self._write_array_item(item_type, item, w, scope, fields, root, parent)

    def _write_array_item(self, item_type, item, w: Writer, scope: Scope,
                            fields: dict, root, parent) -> None:
        # See the matching comment in _read_array_item above.
        if isinstance(item_type, list) and len(item_type) == 2 and item_type[0] == "container":
            self._write_container(item_type[1], item, w, scope, fields, root, parent, push_level=False)
            return
        self.write_type(item_type, item, w, scope, fields, root, parent)

    # ---- count ("separate" length-prefix, declared as a sibling field) -------
    # Typical use: a container where an array/buffer's length prefix is
    # NOT attached to that array (the normal countType case), but is
    # instead its own field elsewhere in the container, and the array
    # references that field by name via its own "count". The `count`
    # type itself, when reading, simply reads an integer (typeArgs.type);
    # when writing, it IGNORES the value passed to it and writes
    # len(getField(countFor)) instead -- same as node-protodef
    # structures.js: readCount/writeCount.
    #
    # opts: {"type": "u8"|"varint"|..., "countFor": "<path to the field, supports ../ and />"}

    def _read_count(self, opts: dict, r: Reader, scope: Scope,
                      fields: dict, root, parent) -> Any:
        return self.read_type(opts["type"], r, scope, fields, root, parent)

    def _write_count(self, opts: dict, value: Any, w: Writer, scope: Scope,
                       fields: dict, root, parent) -> None:
        target = resolve_field_path(opts["countFor"], fields, root, parent)
        length = len(target) if target is not None else 0
        self.write_type(opts["type"], length, w, scope, fields, root, parent)

    # ---- switch -------------------------------------------------------------

    def _resolve_compare_value(self, opts: dict, fields: dict, root, parent) -> Any:
        # node-protodef (compiler-conditional.js): the switch compares
        # against `compareTo` (a path to an already read/written field)
        # OR against `compareToValue` (a fixed literal, no indirection --
        # e.g. useful for choosing a type based on the packet's own name,
        # which is already computed from the outside). Only one of the
        # two should be present.
        if "compareToValue" in opts:
            return opts["compareToValue"]
        compare_to = opts["compareTo"]
        if compare_to.startswith("fields."):
            return eval_condition(compare_to, fields, root, parent)
        if compare_to in fields:
            return fields[compare_to]
        # node-protodef-style paths (utils.js:getField): "../field" goes
        # up to the parent container, "/field" is absolute from the root.
        # Previously this
        # falls through to eval_condition, which only understands
        # "$parent.field" syntax -- never "../field" -- so a compareTo
        # with ".." always resolved to None (real bug behind
        # SwitchCaseNotFound with compareTo="../something").
        # resolve_field_path is the one that knows how to navigate
        # "../" and "/", so we use it first for those cases.
        if compare_to.startswith("../") or compare_to.startswith("/"):
            return resolve_field_path(compare_to, fields, root, parent)
        # A relative path with "/" in the middle (e.g. "flags/kind", no
        # leading "/" or "../") is typical for indexing a sub-field of an
        # already-read/written sibling bitfield. The "compare_to in fields"
        # check above only matches the literal key, so this never reached
        # resolve_field_path (which does know how to walk it) and instead
        # fell through to eval_condition inside the try/except below, which
        # silently swallowed the failure and returned None. We check "in
        # fields" first on purpose, in case some protocol has a literal
        # field name containing "/" rather than using it as a path separator.
        if "/" in compare_to:
            return resolve_field_path(compare_to, fields, root, parent)
        try:
            return eval_condition(compare_to, fields, root, parent)
        except Exception:
            return None

    def _resolve_switch_case(self, opts: dict, compare_val: Any, root) -> TypeDef | None:
        # bool must be checked before str/other, since in Python bool is a
        # subclass of int (isinstance(True, int) == True) -- and minecraft-data
        # (node-protodef / JSON) always uses lowercase "true"/"false" as switch
        # keys (the JSON literal, not Python's str(True) == "True"). Without
        # this, a switch on a bool field never matches (SwitchCaseNotFound).
        if isinstance(compare_val, bool):
            case_key = "true" if compare_val else "false"
        elif isinstance(compare_val, str):
            case_key = compare_val
        else:
            case_key = str(compare_val)
        case_type = opts["fields"].get(case_key, opts["fields"].get(compare_val))
        if case_type is not None:
            return case_type
        # Keys starting with "/" are resolved against the root context,
        # not as a string comparison -- parity with
        # compiler-conditional.js: `if (val.startsWith('/')) val = 'ctx.' + val.slice(1)`.
        # We walk the switch's keys looking for one whose
        # resolved-against-root value matches.
        for key, type_for_key in opts["fields"].items():
            if key.startswith("/"):
                root_val = resolve_field_path(key, {}, root, None)
                if root_val == compare_val:
                    return type_for_key
        return None

    def _identify_compare_ref(self, opts: dict) -> str:
        return opts.get("compareToValue", opts.get("compareTo"))

    def _read_switch(self, opts: dict, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> Any:
        compare_val = self._resolve_compare_value(opts, fields, root, parent)
        case_type = self._resolve_switch_case(opts, compare_val, root)
        if case_type is None:
            if "default" in opts:
                case_type = opts["default"]
            else:
                raise SwitchCaseNotFound(self._identify_compare_ref(opts), compare_val)
        # An INLINE container case (["container", opts], literal right
        # here in switch.fields) must not push its own parent level:
        # same reasoning as the array-item case (see
        # _resolve_to_container_opts). Conceptually an inline case is
        # "part of" the same level as the field holding the switch, not
        # a new sub-level, so "../" inside it must skip past it.
        #
        # Deliberately NOT generalized to named types here (unlike
        # array items): a switch case that's a NAMED type (e.g. "packet_foo")
        # represents a real, independently-meaningful structure (a
        # whole packet body, a whole sub-record) that legitimately has
        # its own level -- collapsing it into the switch's level would
        # be wrong the moment two different switches (nested at
        # different depths) both happened to resolve to a container by
        # name, since only ONE of those nesting depths is "transparent"
        # by convention (the literal-inline one) and the other is a
        # real level boundary. Only a literal inline shape carries the
        # "this isn't really its own thing" signal unambiguously.
        if isinstance(case_type, list) and len(case_type) == 2 and case_type[0] == "container":
            return self._read_container(case_type[1], r, scope, fields, root, parent, push_level=False)
        return self.read_type(case_type, r, scope, fields, root, parent)

    def _write_switch(self, opts: dict, value: Any, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        compare_val = self._resolve_compare_value(opts, fields, root, parent)
        case_type = self._resolve_switch_case(opts, compare_val, root)
        if case_type is None:
            if "default" in opts:
                case_type = opts["default"]
            else:
                raise SwitchCaseNotFound(self._identify_compare_ref(opts), compare_val)
        # Mirror of _read_switch above -- see that comment.
        if isinstance(case_type, list) and len(case_type) == 2 and case_type[0] == "container":
            self._write_container(case_type[1], value, w, scope, fields, root, parent, push_level=False)
            return
        return self.write_type(case_type, value, w, scope, fields, root, parent)

    # ---- mapper (integer <-> symbolic name) --------------------------------

    @staticmethod
    def _normalize_mapper_key(key: Any, mappings: dict | None = None) -> int:
        """
        Real bug: the mapping keys 'can arrive as "0x00", "0x1f",
        "31", etc.' per the original comment -- but that assumes they
        ALWAYS arrive as a string. A mapper.mappings written in YAML
        with an UNQUOTED numeric/hex key (0x00: identification, instead
        of '0x00': identification) is visually indistinguishable from
        the quoted form, but PyYAML resolves that key as a native int,
        not a string -- confirmed empirically (yaml.safe_load({0x00: x})
        gives {0: 'x'}, with an int key). The previous code called
        key.lower() unconditionally and blew up with AttributeError:
        'int' object has no attribute 'lower' as soon as that mapper
        was used to read/write a real packet -- not while loading the
        protocol, so the error showed up far from the real cause and
        with no hint at all. The protocols in examples/ never triggered
        this because they ALWAYS quote the keys by hand ('0x00': ...)
        -- a convention that isn't documented anywhere, so anyone
        writing a new protocol.yml without knowing it steps on this
        bug. `switch` was already immune to this (_resolve_switch_case
        tries both the string key and the raw value), so `mapper` now
        follows the same criterion.

        0.4.0: a key that's neither an int nor a numeric/hex string
        (a genuine typo, e.g. "0xZZ") used to raise a bare ValueError
        from int(..., 16)/int(...), with no hint of which mapper or
        which key was at fault -- and only the first time that mapper
        was actually exercised while reading a packet, far from the
        protocol.yml line that caused it. Now raises
        InvalidMapperKeyError with the offending key and the full
        mappings dict it came from, so the typo is obvious immediately.
        """
        if isinstance(key, int):
            return key
        try:
            return int(key, 16) if key.lower().startswith("0x") else int(key)
        except (ValueError, AttributeError) as exc:
            raise InvalidMapperKeyError(key, mappings or {}, str(exc)) from exc

    def _read_mapper(self, opts: dict, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> Any:
        raw = self.read_type(opts["type"], r, scope, fields, root, parent)
        mappings = opts["mappings"]
        # normalize the mapping's keys (which can arrive as "0x00",
        # "0x1f", "31", unquoted 0x1f, etc.) to integer, so we don't
        # depend on the exact format the protocol.json/yml was written in
        for key, mapped_name in mappings.items():
            if self._normalize_mapper_key(key, mappings) == raw:
                return mapped_name
        # Parity with node-protodef (utils.js readMapper): an
        # unmapped value is an error, not a silent passthrough -- a
        # mapper models a closed set (entity type, block face, chat
        # position...), so a raw value with no entry usually means the
        # mapping table is stale or the stream desynced upstream.
        raise MapperValueNotFoundError(raw, mappings, writing=False)

    def _write_mapper(self, opts: dict, value: Any, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        mappings = opts["mappings"]
        if isinstance(value, str):
            numeric = None
            for k, v in mappings.items():
                if v == value:
                    numeric = self._normalize_mapper_key(k, mappings)
                    break
            if numeric is None:
                raise MapperValueNotFoundError(value, mappings, writing=True)
        else:
            numeric = value
        self.write_type(opts["type"], numeric, w, scope, fields, root, parent)


    # ---- option (present if a preceding bool is true) -------------------------

    def _read_option(self, opts: TypeDef, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> Any:
        present = PRIMITIVES["bool"].read(r)
        if not present:
            return None
        return self.read_type(opts, r, scope, fields, root, parent)

    def _write_option(self, opts: TypeDef, value: Any, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        present = value is not None
        PRIMITIVES["bool"].write(present, w)
        if present:
            self.write_type(opts, value, w, scope, fields, root, parent)

    # ---- bitfield (sub-fields packed into N bits) -------------------------

    def _read_bitfield(self, opts: list[dict], r: Reader, scope: Scope,
                         fields: dict, root, parent) -> dict:
        # node-protodef (structures.js:readBitField) does NOT require the
        # bit sum to be a multiple of 8: if bits are left over at the
        # end, they're padded until the last byte is filled. We no
        # longer raise an error here; we simply round up to the next byte.
        total_bits = sum(f["size"] for f in opts)
        num_bytes = (total_bits + 7) // 8
        pad_bits = num_bytes * 8 - total_bits
        raw_bytes = r.read_bytes(num_bytes)
        big = int.from_bytes(raw_bytes, "big")

        result: dict[str, Any] = {}
        bits_left = total_bits + pad_bits
        for f in opts:
            bits_left -= f["size"]
            mask = (1 << f["size"]) - 1
            val = (big >> bits_left) & mask
            if f.get("signed") and val >= (1 << (f["size"] - 1)):
                val -= 1 << f["size"]
            result[f["name"]] = val
        return result

    def _write_bitfield(self, opts: list[dict], value: dict, w: Writer, scope: Scope,
                          fields: dict, root, parent) -> None:
        # Same criterion as _read_bitfield: if the total bits don't fill
        # a whole byte, padding is added by left-shifting the leftover
        # -- parity with node-protodef: `buffer[offset++] = toWrite << (8 - bits)`
        # on the last byte when fewer than 8 bits remain unused.
        total_bits = sum(f["size"] for f in opts)
        num_bytes = (total_bits + 7) // 8
        pad_bits = num_bytes * 8 - total_bits
        big = 0
        for f in opts:
            size = f["size"]
            signed = bool(f.get("signed"))
            raw_value = value.get(f["name"], 0)
            # 0.4.0: an out-of-range value used to be silently masked
            # here (`value & ((1 << size) - 1)`), quietly corrupting the
            # packet instead of failing at the actual mistake --
            # inconsistent with fixed-width ints (struct.pack raises on
            # overflow) and fixed-count buffers (_write_buffer raises on
            # a size mismatch). Now it raises BitfieldOverflowError,
            # checked against the same range a fixed-width int of that
            # bit size would allow.
            if signed:
                lo, hi = -(1 << (size - 1)), (1 << (size - 1)) - 1
            else:
                lo, hi = 0, (1 << size) - 1
            if not (lo <= raw_value <= hi):
                raise BitfieldOverflowError(f["name"], raw_value, size, signed=signed)
            v = raw_value & ((1 << size) - 1)
            big = (big << size) | v
        big <<= pad_bits
        w.write_bytes(big.to_bytes(num_bytes, "big"))

    # ---- bitflags (integer as a named set of flags) ------------------------

    def _read_bitflags(self, opts: dict, r: Reader, scope: Scope,
                         fields: dict, root, parent) -> dict[str, Any]:
        raw = self.read_type(opts["type"], r, scope, fields, root, parent)
        flags_def = opts["flags"]
        shift = opts.get("shift", False)

        # flags as a dict: {"name": bitmask, ...} or, if shift=True,
        # {"name": bitPosition, ...} -- parity with the official spec
        # (utils.md: "shift: Specify if flags is an object and holds bit
        # positions as values opposed to a bitmask").
        if isinstance(flags_def, dict):
            result: dict[str, Any] = {}
            for flag_name, raw_mask in flags_def.items():
                mask = (1 << raw_mask) if shift else raw_mask
                result[flag_name] = bool(raw & mask)
            result["_value"] = raw
            return result

        # flags as a positional list: each entry occupies bit `i` (its
        # own index), always LSB-first. Fixed in 0.3.8 -- this used to
        # reverse the list when `big=True`, but that doesn't match
        # node-protodef (utils.js readBitflags): there, `big` only
        # picks BigInt vs Number shifting for the mask (1n << BigInt(k)
        # vs 1 << k), it never changes which bit index a name maps to.
        # Since Python ints don't need that distinction, `big` is now a
        # no-op here for arrays -- kept accepted (not rejected) so
        # existing protocol.json/yml files that set it don't break, but
        # it no longer alters behavior.
        flag_names: list[str] = flags_def
        result: dict[str, Any] = {}
        for i, flag_name in enumerate(flag_names):
            if flag_name is None:
                continue
            result[flag_name] = bool((raw >> i) & 1)
        # Parity with node-protodef: the raw value always travels in
        # '_value', in addition to each individual flag under its own name.
        result["_value"] = raw
        return result

    def _write_bitflags(self, opts: dict, value: dict, w: Writer, scope: Scope,
                          fields: dict, root, parent) -> None:
        flags_def = opts["flags"]
        shift = opts.get("shift", False)

        # Official spec: when writing, the expected value comes wrapped
        # as {"flags": {...}}. For backward compatibility with protocols
        # that already depended on this port taking the "bare" flags
        # dict (unwrapped), both forms are accepted.
        flag_values = value.get("flags", value) if isinstance(value, dict) else {}

        if isinstance(flags_def, dict):
            raw = 0
            for flag_name, raw_mask in flags_def.items():
                mask = (1 << raw_mask) if shift else raw_mask
                if flag_values.get(flag_name):
                    raw |= mask
            self.write_type(opts["type"], raw, w, scope, fields, root, parent)
            return

        # See the matching comment in _read_bitflags: `big` no longer
        # reverses the array order here either, for the same reason --
        # that reversal never existed in node-protodef, it was a
        # divergence in this port. Bit index is always the flag's own
        # position in the list.
        flag_names: list[str] = flags_def
        raw = 0
        for i, flag_name in enumerate(flag_names):
            if flag_name is None:
                continue
            if flag_values.get(flag_name):
                raw |= (1 << i)
        self.write_type(opts["type"], raw, w, scope, fields, root, parent)

    # ---- buffer (raw bytes) ------------------------------------------------

    def _read_buffer(self, opts: dict, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> bytes:
        if "count" in opts:
            count = resolve_field_path(opts["count"], fields, root, parent)
            if not isinstance(count, int) or count < 0:
                raise InvalidTypeDefinition(
                    f"buffer: 'count' ({opts['count']!r}) resolved to {count!r}, "
                    f"expected an integer >= 0 -- check that the field exists "
                    f"and that the path (../, /) points to the correct level"
                )
        elif "countType" in opts:
            count = self.read_type(opts["countType"], r, scope, fields, root, parent)
        elif opts.get("rest"):
            count = r.remaining
        else:
            raise InvalidTypeDefinition("buffer requires 'count', 'countType', or 'rest'")
        return r.read_bytes(count)

    def _write_buffer(self, opts: dict, value: bytes, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        data = value or b""
        if "countType" in opts:
            self.write_type(opts["countType"], len(data), w, scope, fields, root, parent)
        elif "count" in opts and isinstance(opts["count"], int):
            # fixed count (literal integer, not a path to another
            # field): unlike countType, here NOBODY writes a length
            # beforehand -- the protocol's framing assumes this field
            # occupies EXACTLY that many bytes. If `data` comes in
            # shorter or longer, writing it as-is would leave the next
            # field misaligned for whoever receives the packet, with no
            # visible error here. Better to fail loudly than corrupt
            # the framing.
            if len(data) != opts["count"]:
                raise InvalidTypeDefinition(
                    f"buffer: expected exactly {opts['count']} bytes "
                    f"(fixed count), but the value has {len(data)} -- if the "
                    f"size can vary, generate a dedicated primitive with "
                    f"explicit padding (see make_fixed_buffer in "
                    f"primitives.py) instead of a generic 'buffer'"
                )
        w.write_bytes(data)

    # ---- pstring (string with configurable length prefix) -----------------

    def _read_pstring(self, opts: dict, r: Reader, scope: Scope,
                        fields: dict, root, parent) -> str:
        count_type = opts.get("countType", "varint")
        encoding = opts.get("encoding", "utf-8")
        if "count" in opts:
            length = resolve_field_path(opts["count"], fields, root, parent)
            if not isinstance(length, int) or length < 0:
                raise InvalidTypeDefinition(
                    f"pstring: 'count' ({opts['count']!r}) resolved to {length!r}, "
                    f"expected an integer >= 0 -- check that the field exists "
                    f"and that the path (../, /) points to the correct level"
                )
        else:
            length = self.read_type(count_type, r, scope, fields, root, parent)
        data = r.read_bytes(length)
        return data.decode(encoding)

    def _write_pstring(self, opts: dict, value: str, w: Writer, scope: Scope,
                         fields: dict, root, parent) -> None:
        count_type = opts.get("countType", "varint")
        encoding = opts.get("encoding", "utf-8")
        data = value.encode(encoding)
        if "count" not in opts:
            self.write_type(count_type, len(data), w, scope, fields, root, parent)
        w.write_bytes(data)

    # ---- entityMetadataLoop (list until a terminator is found) -----------------

    def _read_entity_metadata_loop(self, opts: dict, r: Reader, scope: Scope,
                                      fields: dict, root, parent) -> list:
        """
        opts:
          endVal: raw value (byte) that marks the end of the list.
          endType: 1-byte primitive type used only for the terminator
                   check (default 'u8'); NOT used to decode each real
                   entry.
          type: full type of each entry (typically a container that
                already includes, as an anon field, Minecraft's real
                type/key bitfield -- see entityMetadata in the yml).

        Real bug fixed here: the previous version read each entry's
        first byte with `end_type` and used it directly as an "index"
        to decide the switch case -- but that byte is actually a packed
        bitfield `(type<<5)|key`, already declared as such inside
        `item_type` (the container in the yml). Reading it twice with
        different semantics (once here as a plain, discarded integer,
        and again inside the container as a bitfield) left the reader's
        offset misaligned with the rest of the packet, producing a
        BufferUnderrun further along in the same read.

        The correct approach (and generic, without hardcoding bits
        here) is: peek at the byte with peek_byte() WITHOUT consuming
        it to check whether it's the terminator; if it isn't, let
        `item_type` (the real container, defined in the yml) read the
        whole entry -- including that same byte as part of its own
        bitfield.
        """
        end_val = opts.get("endVal", 0xFF)
        item_type = opts["type"]

        result = []
        while True:
            if r.peek_byte() == end_val:
                r.read_bytes(1)  # consume the terminator
                break
            entry = self.read_type(item_type, r, scope, fields, root, parent)
            result.append(entry)
        return result

    def _resolve_to_container_opts(self, type_def: TypeDef, scope: Scope) -> list[dict] | None:
        """
        Follows named-type indirection (WITHOUT reading/writing any
        bytes) to find out whether `type_def` ultimately resolves to a
        `["container", opts]` -- and if so, returns those `opts`.
        Returns None for anything else (a primitive, a switch, an
        array, an unresolvable/parametrized type, ...).

        Used by _is_container_type (entityMetadataLoop's own need: is
        an entry's `type` a container, so it should be exposed as a
        full dict rather than unwrapped into a bare 'value'?).

        NOTE for anyone tempted to also use this for array-item/switch-
        case push_level decisions (0.4.0 explored this and reverted
        it): whether a container should push its own level is NOT the
        same question as whether it's *reachable* as a container.
        Generalizing push_level=False to any named type that resolves
        to a container breaks the moment that container has its OWN
        meaningful sub-structure (descendants that need "../" to land
        on it, not skip past it) -- which is the common case for a
        named record type, and different from a literal inline
        container (deliberately anonymous, with no identity beyond
        "whatever the array/switch decided to read here"). Only the
        inline-literal shape carries the "this isn't really its own
        thing" signal unambiguously; see the comments in
        _read_array_item and _read_switch for the two directions this
        was tried and reverted.
        """
        current = type_def
        seen: set[str] = set()
        while isinstance(current, str):
            if current in PRIMITIVES:
                return None
            if current in seen:
                return None  # cyclical named type, bail out rather than loop forever
            seen.add(current)
            resolved = self._resolve_named_type(current, scope)
            if resolved is None:
                return None
            current = resolved
        if isinstance(current, list) and len(current) == 2 and current[0] == "container":
            return current[1]
        return None

    def _is_container_type(self, type_def: TypeDef, scope: Scope) -> bool:
        """Resolves (without reading/writing bytes) whether a type is,
        ultimately, a container -- to know whether an entityMetadataLoop
        entry should be passed as a full dict or unwrapped into 'value'."""
        return self._resolve_to_container_opts(type_def, scope) is not None

    def _write_entity_metadata_loop(self, opts: dict, value: list, w: Writer, scope: Scope,
                                       fields: dict, root, parent) -> None:
        """
        Symmetric to the _read_entity_metadata_loop fix: each `entry` in
        `value` is the full dict exactly as returned by the read
        (typically {'type':.., 'key':.., 'value':..}, produced by the
        real container declared in `item_type` -- anon type/key
        bitfield + named value/switch field). It's written using
        item_type directly, letting the internal container/bitfield/
        switch pack the type/key byte and the payload as appropriate.
        Finally, the raw terminator byte (end_val) is written with
        end_type.
        """
        end_val = opts.get("endVal", 0xFF)
        end_type = opts.get("endType", "u8")
        item_type = opts["type"]

        for entry in (value or []):
            self.write_type(item_type, entry, w, scope, fields, root, parent)
        self.write_type(end_type, end_val, w, scope, fields, root, parent)

    # ---- topBitSetTerminatedArray ------------------------------------------------

    def _read_top_bit_set_terminated_array(self, opts: dict, r: Reader, scope: Scope,
                                              fields: dict, root, parent) -> list:
        """
        Reads `type` entries while the highest bit (0x80) of each
        entry's FIRST byte is set. A typical pattern for LEB128-like
        lists in RakNet / custom formats (each entry "announces"
        whether there's another one after it).
        """
        item_type = opts["type"]
        result = []
        while True:
            start_offset = r.offset
            item = self.read_type(item_type, r, scope, fields, root, parent)
            result.append(item)
            # 0.4.0: this used to index r.buffer[start_offset] directly,
            # with no bounds check. If `item_type` ever consumes zero
            # bytes (e.g. an empty inline container) right at the end of
            # the buffer, start_offset == len(r.buffer) and this raised
            # a bare IndexError with no context. r.ensure() gives the
            # same guarantee the rest of the library relies on: a clear
            # BufferUnderrun instead of a raw Python exception.
            r.ensure_at(start_offset, 1)
            first_byte = r.buffer[start_offset]
            if not (first_byte & 0x80):
                break
        return result

    def _write_top_bit_set_terminated_array(self, opts: dict, value: list, w: Writer, scope: Scope,
                                               fields: dict, root, parent) -> None:
        """
        When writing, the caller is responsible for each item already
        carrying the high bit set except for the last one (this
        reflects the real protocol: the continuation marker is usually
        part of the item's own data, not something this wrapper can
        invent).
        """
        item_type = opts["type"]
        items = value or []
        for item in items:
            self.write_type(item_type, item, w, scope, fields, root, parent)

    # -------------------------------------------------------------------
    # Writing
    # -------------------------------------------------------------------

    def write_type(self, type_def: TypeDef, value: Any, w: Writer, scope: Scope | None,
                    fields: dict[str, Any], root: dict | None = None,
                    parent: dict | None = None) -> None:
        if isinstance(type_def, str):
            if type_def in PRIMITIVES:
                PRIMITIVES[type_def].write(value, w)
                return
            resolved = self._resolve_named_type(type_def, scope)
            if resolved is None:
                raise UnknownTypeError(type_def)
            return self.write_type(resolved, value, w, scope, fields, root, parent)

        if isinstance(type_def, list) and len(type_def) == 2:
            base, opts = type_def
            handler = self._composite_write_handlers.get(base)
            if handler is not None:
                return handler(opts, value, w, scope, fields, root, parent)
            named = self._resolve_named_type(base, scope)
            if named is None:
                raise UnknownTypeError(f"(composite base type) {base}")
            substituted = substitute_type_args(named, opts if isinstance(opts, dict) else None)
            return self.write_type(substituted, value, w, scope, fields, root, parent)

        raise InvalidTypeDefinition(type_def)

    # ---- composite cstring (["cstring", {"encoding": "..."}]) -----------------
    # The "cstring" primitive (primitives.py) still exists as-is, forces
    # utf-8 and is left untouched. This is an additional COMPOSITE type,
    # only triggered if protocol.json uses the ["cstring", {opts}] form
    # instead of the plain "cstring" string -- so it's 100% additive,
    # doesn't change existing behavior. Parity with node-protodef
    # (src/datatypes/utils.js: readCString/writeCString accept
    # typeArgs.encoding, default 'utf8').

    def _read_cstring_encoded(self, opts: dict, r: Reader, scope: Scope,
                                fields: dict, root, parent) -> str:
        encoding = (opts or {}).get("encoding", "utf-8")
        out = bytearray()
        while True:
            b = r.read_bytes(1)
            if b == b"\x00":
                break
            out += b
        return out.decode(encoding)

    def _write_cstring_encoded(self, opts: dict, value: str, w: Writer, scope: Scope,
                                 fields: dict, root, parent) -> None:
        encoding = (opts or {}).get("encoding", "utf-8")
        w.write_bytes(value.encode(encoding) + b"\x00")

    # ---- registryEntryHolder (modern Minecraft IdOr<T>) --------------------
    #
    # "numeric id -> either a reference to a server registry entry, or
    # an inline value" pattern. Used for trims, biomes, sounds, banner
    # patterns, etc. where the data can arrive pre-loaded via registry
    # index (id > 0, the normal case) or, more rarely, sent in full
    # inside the packet itself when the client doesn't yet have that
    # entry registered (id == 0). Parity with vanilla Minecraft's real
    # `IdOr<T>` (net.minecraft.network.codec.IdOr /
    # RegistryFriendlyByteBuf.readIdOrPayload in the vanilla protocol)
    # and with node-minecraft-protocol's `registryEntryHolder`.
    #
    # opts:
    #   idType:     primitive type of the id (default "varint")
    #   otherwise:  {"type": <TypeDef>} -- the inline type to read/write
    #               when the raw id is 0.
    #
    # Wire format:
    #   id == 0            -> [id=0][otherwise.type's value, inline]
    #   id != 0 (id = n)    -> [id=n]   (reference to the registry, index n-1;
    #                          the -1/+1 is because 0 is already taken by
    #                          the inline case, so the real registry
    #                          index is shifted by one to avoid clashing)
    #
    # Python representation (so both cases are distinguishable without
    # ambiguity, same as the rest of the library does with NBT/mapper):
    #   inline:    {"type": "inline", "value": <decoded value>}
    #   reference: {"type": "reference", "id": <registry index>}

    def _read_registry_entry_holder(self, opts: dict, r: Reader, scope: Scope,
                                       fields: dict, root, parent) -> dict:
        id_type = opts.get("idType", "varint")
        raw_id = self.read_type(id_type, r, scope, fields, root, parent)
        if raw_id == 0:
            otherwise_type = opts["otherwise"]["type"]
            value = self.read_type(otherwise_type, r, scope, fields, root, parent)
            return {"type": "inline", "value": value}
        return {"type": "reference", "id": raw_id - 1}

    def _write_registry_entry_holder(self, opts: dict, value: dict, w: Writer, scope: Scope,
                                        fields: dict, root, parent) -> None:
        id_type = opts.get("idType", "varint")
        kind = value.get("type")
        if kind == "inline":
            self.write_type(id_type, 0, w, scope, fields, root, parent)
            otherwise_type = opts["otherwise"]["type"]
            self.write_type(otherwise_type, value.get("value"), w, scope, fields, root, parent)
            return
        if kind == "reference":
            self.write_type(id_type, value["id"] + 1, w, scope, fields, root, parent)
            return
        raise InvalidTypeDefinition(
            f"registryEntryHolder: expected value['type'] to be one of ('inline', 'reference'), "
            f"got {kind!r} -- see the format in the docstring of "
            f"_read_registry_entry_holder"
        )

    # ---- registryEntryHolderSet (modern Minecraft HolderSet<T>) -----------
    #
    # A registryEntryHolder variant for SETS of entries: it can be a
    # reference to a tag already known to both sides (e.g.
    # "#minecraft:trim_materials", resolved against the tag registry the
    # client already has) or an explicit list of inline ids. Parity with
    # vanilla `HolderSet<T>` (net.minecraft.core.HolderSet /
    # RegistryFriendlyByteBuf.readHolderSet) and with
    # node-minecraft-protocol's `registryEntryHolderSet`.
    #
    # opts:
    #   idType: primitive type of each id in the explicit list (default "varint")
    #
    # Wire format (parity with the real vanilla codec):
    #   [varint count]
    #     count == 0  -> followed by a string (cstring/pstring -- here the
    #                    "cstring" type is used as-is, already registered
    #                    in primitives.py) with the tag's name, typically
    #                    prefixed with "#" (e.g. "#minecraft:trim_materials")
    #     count  > 0  -> followed by exactly `count` ids (idType each),
    #                    WITHOUT registryEntryHolder's +1/-1 -- here count already
    #                    is already "how many ids there are", not an id-or-reference
    #
    # Python representation:
    #   by tag:   {"type": "tag", "tagName": "#minecraft:trim_materials"}
    #   inline:   {"type": "ids", "ids": [3, 7, 12]}

    def _read_registry_entry_holder_set(self, opts: dict, r: Reader, scope: Scope,
                                           fields: dict, root, parent) -> dict:
        id_type = opts.get("idType", "varint")
        count = self.read_type("varint", r, scope, fields, root, parent)
        if count == 0:
            tag_name = self.read_type("cstring", r, scope, fields, root, parent)
            return {"type": "tag", "tagName": tag_name}
        ids = [self.read_type(id_type, r, scope, fields, root, parent) for _ in range(count)]
        return {"type": "ids", "ids": ids}

    def _write_registry_entry_holder_set(self, opts: dict, value: dict, w: Writer, scope: Scope,
                                            fields: dict, root, parent) -> None:
        id_type = opts.get("idType", "varint")
        kind = value.get("type")
        if kind == "tag":
            self.write_type("varint", 0, w, scope, fields, root, parent)
            self.write_type("cstring", value["tagName"], w, scope, fields, root, parent)
            return
        if kind == "ids":
            ids = value.get("ids") or []
            self.write_type("varint", len(ids), w, scope, fields, root, parent)
            for entry_id in ids:
                self.write_type(id_type, entry_id, w, scope, fields, root, parent)
            return
        raise InvalidTypeDefinition(
            f"registryEntryHolderSet: expected value['type'] to be one of ('tag', 'ids'), "
            f"got {kind!r} -- see the format in the docstring of "
            f"_read_registry_entry_holder_set"
        )

    # -------------------------------------------------------------------
    # High-level API: complete packets
    # -------------------------------------------------------------------

    def parse_packet(self, state: str, direction: str, data: bytes) -> ParsedPacket:
        scope = self.get_scope(state, direction)
        r = Reader(data)
        packet = self.read_type("packet", r, scope, {})
        return ParsedPacket(name=packet["name"], params=packet["params"], bytes_read=r.offset)

    def serialize_packet(self, state: str, direction: str, name: str, params: dict) -> bytes:
        scope = self.get_scope(state, direction)
        w = Writer()
        self.write_type("packet", {"name": name, "params": params}, w, scope, {})
        return w.result()

    # direct access to a named type (bypassing "packet"), useful for
    # tests and for parsing/serializing standalone sub-structures
    # (e.g. a slot, an NBT)
    def read_named(self, state: str, direction: str, type_name: str, data: bytes) -> Any:
        scope = self.get_scope(state, direction)
        r = Reader(data)
        return self.read_type(type_name, r, scope, {})

    def write_named(self, state: str, direction: str, type_name: str, value: Any) -> bytes:
        scope = self.get_scope(state, direction)
        w = Writer()
        self.write_type(type_name, value, w, scope, {})
        return w.result()
