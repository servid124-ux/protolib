# protolib

Pure-Python, dependency-free (core) declarative binary protocol (de)serializer, in the style of [node-protodef](https://github.com/ProtoDef-io/ProtoDef) / [node-minecraft-protocol](https://github.com/PrismarineJS/node-minecraft-protocol). It reads and writes binary packets from a **protocol definition** (JSON, in the original node-protodef format, or a more readable YAML shorthand) instead of hand-rolling struct-packing code for every packet type.

Originally built to reverse-engineer and re-implement the Minecraft protocol (Classic, Java 1.7/1.8, MCPE/Bedrock), but the engine itself is protocol-agnostic — it works for any binary format you can describe as a set of typed fields.

```python
from protolib import Protocol

proto = Protocol("protocol.yml")
pkt = proto.parse_packet("play", "toClient", raw_bytes)
print(pkt.name, pkt.params)

data = proto.serialize_packet("play", "toServer", "keep_alive", {"keepAliveId": 1})
```

- **Version:** 0.3.5
- **License:** MIT
- **Requires:** Python ≥ 3.9, no runtime dependencies (PyYAML only if you use `.yml`/`.yaml` files)

---

## Table of contents

1. [Install](#install)
2. [Core concepts](#core-concepts)
3. [Quick start](#quick-start)
4. [Protocol format (JSON, node-protodef style)](#protocol-format-json-node-protodef-style)
5. [YAML shorthand](#yaml-shorthand)
6. [Composite types](#composite-types)
7. [Primitive types catalog](#primitive-types-catalog)
8. [Conditions (`condition` fields)](#conditions-condition-fields)
9. [Field paths (`../field`, `/field`)](#field-paths-field-field)
10. [Parametrizable named types (`$arg`)](#parametrizable-named-types-arg)
11. [NBT](#nbt)
12. [PacketFramer (length-prefixed streams)](#packetframer-length-prefixed-streams)
13. [Errors](#errors)
14. [Public API reference](#public-api-reference)
15. [Extending the library](#extending-the-library)
16. [Project layout](#project-layout)
17. [Running the tests](#running-the-tests)

---

## Install

```bash
pip install protolib
# or, for YAML protocol files:
pip install protolib[yaml]
```

From source:

```bash
pip install .
```

## Core concepts

protolib is built in three layers:

1. **`io.py`** — the lowest layer. `Reader` (a cursor over a `bytes`-like buffer) and `Writer` (an accumulator of byte chunks). Knows nothing about protocols.
2. **`primitives.py`** — fixed byte-level types that don't depend on any protocol definition: `u8`, `i32`, `varint`, `bool`, `cstring`, `UUID`, NBT wrappers, etc. Each one is a `Primitive(name, read, write, size_of)`. Registered by name in the `PRIMITIVES` dict.
3. **`core.py`** — the engine. The `Protocol` class loads a protocol definition (a dict of named types + per-state/direction packet tables) and knows how to recursively read/write **composite types** (`container`, `array`, `switch`, `mapper`, `bitfield`, etc.), resolving primitives and named types along the way.

On top of that:

- **`loader.py`** translates a more readable YAML shorthand into the internal `["type", options]` form that the engine understands, or loads JSON directly (unmodified minecraft-data protocol.json files work as-is).
- **`framer.py`** implements the common `[varint length][payload]` stream framing used by Minecraft-like protocols, independent of packet *contents*.
- **`nbt.py`** implements Minecraft's NBT format, used as a primitive type inside packets.
- **`conditions.py`** implements a small, safe (no `eval`) expression evaluator for conditional fields.

## Quick start

```python
from protolib import Protocol

# Load from a file (format auto-detected from the extension)
proto = Protocol("protocol.yml")     # or "protocol.json"

# Or from an already-parsed dict / in-memory string
proto = Protocol({"types": {...}})
proto = Protocol(yaml_text, fmt="yaml")

# Parse a full packet (looks up scope by state/direction, decodes the
# "packet" type, which typically has "name" + "params" fields)
pkt = proto.parse_packet("play", "toServer", raw_bytes)
print(pkt.name, pkt.params, pkt.bytes_read)

# Serialize a packet by name
data = proto.serialize_packet("play", "toClient", "keep_alive", {"keepAliveId": 1})

# Read/write a standalone named type (bypassing "packet"), e.g. a slot or NBT
value = proto.read_named("play", "toClient", "slot", raw_bytes)
data = proto.write_named("play", "toClient", "slot", value)
```

Migrating an existing `protocol.json` (minecraft-data format) to the YAML shorthand:

```python
import json
from protolib.loader import protocol_dict_to_yaml

raw = json.load(open("protocol.json"))
open("protocol.yml", "w").write(protocol_dict_to_yaml(raw))
```

## Protocol format (JSON, node-protodef style)

A protocol definition is a dict with a top-level `"types"` key (globally available named types) plus one key per connection **state**, each with `"toClient"` / `"toServer"` sub-keys holding their own `"types"` (which take priority over global types for that state+direction):

```json
{
  "types": {
    "varint": "native",
    "string": ["pstring", {"countType": "varint"}]
  },
  "play": {
    "toClient": {
      "types": {
        "packet": ["container", [
          {"name": "name", "type": ["mapper", {"type": "varint", "mappings": {"0x00": "keep_alive"}}]},
          {"name": "params", "type": ["switch", {"compareTo": "name", "fields": {"keep_alive": "packet_keep_alive"}}]}
        ]],
        "packet_keep_alive": ["container", [
          {"name": "keepAliveId", "type": "varint"}
        ]]
      }
    }
  }
}
```

A composite type is always a 2-element list: `["baseType", options]`. This is exactly the format used by `minecraft-data`'s `protocol.json` files, so those load unmodified.

Type names are resolved first against the local scope (`state.direction.types`), then against the global `types`. Primitive names (`varint`, `i32`, ...) are checked first, before any named-type lookup.

## YAML shorthand

Hand-writing `["type", {...}]` two-element lists in YAML is error-prone. protolib's loader (`loader.py`) accepts a friendlier single-key-mapping form and translates it recursively into the internal list form:

```yaml
# YAML shorthand:
packet_keep_alive:
  container:
  - name: keepAliveId
    type: varint

# is equivalent to the JSON:
# "packet_keep_alive": ["container", [{"name": "keepAliveId", "type": "varint"}]]
```

Only single-key mappings whose key is a **known composite type name** are treated as shorthand (`container`, `array`, `switch`, `mapper`, `option`, `bitfield`, `bitflags`, `buffer`, `pstring`, `count`, `entityMetadataLoop`, `topBitSetTerminatedArray`, `cstring`). Anything else (a plain type name string, or a mapping that isn't a shorthand match) is left as-is. You can also mix explicit `[type, opts]` lists directly in the YAML if you prefer.

Loading is auto-detected from the file extension (`.json` vs `.yml`/`.yaml`), or forced with `Protocol(source, fmt="yaml")`. In-memory strings (not existing file paths) are auto-detected by content (`{`/`[` → JSON, otherwise YAML).

### `xxx: native` — what it actually does (and doesn't)

`examples/example_protocol.yml` starts with entries like:

```yaml
types:
  varint: native
  u8: native
  container: native
  switch: native
```

This is **purely documentation, not a declaration you need**. `varint`, `u8`, `container`, `switch`, etc. are resolved directly by `core.py` — primitives come straight from `PRIMITIVES` and composite base types from `_composite_handlers`, neither of which ever looks at `types:` to check whether a name was "registered" first. You can delete every `xxx: native` line from a protocol file and it will parse and run identically.

The value is for a human reading the file: seeing `varint: native` tells you "this name is a built-in, don't go looking for its definition somewhere else in this same file" — as opposed to a real named type like:

```yaml
types:
  string:
    pstring:
      countType: varint
```

Here `string` genuinely **is** defined in this file (as a `pstring` with a varint length prefix) — that's a real named type you're creating, not a native. The rule of thumb: if the right-hand side is the literal word `native`, it's just a label/comment for a type that already exists in the engine; if it's an actual type definition (`pstring`, `container`, `switch`, ...), you're defining a new named type that other parts of the protocol can then reference by name.

You only need to actually **write** a `types:` entry when either:
- you're defining a genuinely new named type (like `string` above), or
- you want a short alias for something you'll reuse in several places (e.g. `entityMetadataItem`, `itemByKind` in the [parametrizable types](#parametrizable-named-types-arg) section).

## Composite types

All composite types are declared as `["baseType", options]` (or the YAML shorthand equivalent). Read/write handlers live in `core.py`, registered in `Protocol._composite_handlers` / `_composite_write_handlers`.

| Type | Purpose |
|---|---|
| `container` | Ordered list of named fields, each with its own `type` (and optional `condition`). Fields can be `anon: true` to merge a sub-container's fields directly into the parent instead of nesting. |
| `array` | Homogeneous list. Length comes from a fixed `count` (int or field path), or a `countType` (a type read/written just before the items, e.g. `varint`). |
| `count` | A length-prefix declared as its own sibling field elsewhere in the container (instead of attached to the array via `countType`). On write, ignores the passed-in value and writes `len(field(countFor))`. |
| `switch` | Picks a type based on another field's value. `compareTo` (a field path) or `compareToValue` (a literal) selects a case from `fields`; `default` is used if no case matches. |
| `mapper` | Translates a raw integer to a symbolic name and back (e.g. packet IDs ↔ names). Unmatched values pass through as the raw integer. |
| `option` | Optional value, preceded by a 1-byte bool ("is it present?"). `None` ⇄ absent. |
| `bitfield` | Packs/unpacks several named sub-fields into N bits each, MSB-first, padded up to a whole byte if needed. Supports `signed: true` per sub-field. |
| `bitflags` | An integer interpreted as a named set of boolean flags — either a bitmask dict (`{name: mask}`, or `{name: bitPosition}` with `shift: true`) or a positional list (`["flagA", "flagB", ...]`, LSB-first unless `big: true`). The raw integer is always included under `_value`. |
| `buffer` | Raw bytes, with `count` (fixed or field-referencing), `countType` (length-prefixed), or `rest: true` (consumes everything remaining). |
| `pstring` | String with a configurable length prefix (`countType`, default `varint`) or explicit `count`, plus `encoding` (default `utf-8`). |
| `cstring` (composite form) | Like the `cstring` primitive, but with a configurable `encoding`. The plain `"cstring"` primitive is always UTF-8. |
| `entityMetadataLoop` | Reads entries until a 1-byte terminator (`endVal`, default `0xFF`) is *peeked* (not consumed as a separate read — it's part of each entry's own encoding, typically a bitfield). |
| `topBitSetTerminatedArray` | Reads entries while the high bit (`0x80`) of each entry's first byte is set — a LEB128-like "more items follow" pattern (used by RakNet-style formats). |
| `registryEntryHolder` | Modern Minecraft `IdOr<T>`: `id == 0` means an inline value follows (`otherwise.type`); `id != 0` is a registry reference (`id - 1`). Represented in Python as `{"type": "inline", "value": ...}` or `{"type": "reference", "id": ...}`. |
| `registryEntryHolderSet` | Modern Minecraft `HolderSet<T>`: either a tag reference (`{"type": "tag", "tagName": "#minecraft:..."}`) or an explicit list of ids (`{"type": "ids", "ids": [...]}`). |

### Conditional fields

Any field inside a `container` can have a `condition` (a string expression, see [Conditions](#conditions-condition-fields)) — if it evaluates to falsy, the field is skipped entirely on both read and write.

### Example: `switch` + `mapper` (typical packet dispatch table)

```yaml
packet:
  container:
  - name: name
    type:
      mapper:
        type: varint
        mappings:
          '0x00': keep_alive
          '0x01': login_success
  - name: params
    type:
      switch:
        compareTo: name
        fields:
          keep_alive: packet_keep_alive
          login_success: packet_login_success
```

### Example: `bitfield` + `switch` reading a packed type/index byte

```yaml
entry:
  container:
  - anon: true
    type:
      bitfield:
      - {name: kind, size: 3, signed: false}
      - {name: index, size: 5, signed: false}
  - name: value
    type:
    - itemByKind          # named, parametrizable type (see below)
    - compareTo: kind
```

## Primitive types catalog

Registered in `PRIMITIVES` (`primitives.py`), resolvable by name directly in a `type:` field — no `["type", {}]` wrapper needed.

**Fixed-width integers, big-endian:**
`i8` `u8` `i16` `u16` `i24` `u24` `i32` `u32` `i40` `u40` `i48` `u48` `i56` `u56` `i64` `u64`

**Same family, little-endian** (prefix `l`): `li8` `lu8` `li16` `lu16` `li24` `lu24` `li32` `lu32` `li40` `lu40` `li48` `lu48` `li56` `lu56` `li64` `lu64`
(`li8`/`lu8` are aliases of `i8`/`u8` — 1 byte has no endianness, but they're exposed under the `l*` name too for consistency with protocol.json files that reference them that way.)

**Floats:** `f16` (IEEE 754 half-float), `f32`, `f64`, and little-endian `lf16` `lf32` `lf64`.

**Varints (LEB128, Minecraft/protobuf style):** `varint` / `varlong` (signed, zigzag-free — same bit layout as unsigned but Python-side range depends on use), `uvarint` / `uvarlong` (unsigned), `varint128` / `uvarint128` (128-bit range, e.g. for big numeric IDs).

**Zigzag varints (protobuf style):** `zigzag32`, `zigzag64` — `0,-1,1,-2,2 → 0,1,2,3,4` before LEB128 encoding.

**Strings / buffers:** `cstring` (null-terminated, UTF-8), `restBuffer` (consumes all remaining bytes — must go last in a container), `string64` (64 bytes, CP437, space-padded, non-representable chars become `"?"`), `utf16be64` (64 *characters*, UTF-16BE, space-padded, 128 bytes on the wire — lossless for accents/most alphabets), `buffer1024` / `buffer64` (fixed-size raw byte blocks, zero-padded).

**Other:** `bool` (1 byte), `void` (0 bytes — reads `None`, writes nothing), `UUID` (16 raw bytes ⇄ dashed string), `fixedCoord` / `fixedCoordDelta` (Minecraft Classic Q10.5 fixed-point: same layout as `i16`/`i8`, semantic name only — the ×32 scaling is the caller's responsibility).

**NBT:** `nbt`, `optionalNbt`, `anonymousNbt`, `anonOptionalNbt` — see [NBT](#nbt).

Need a custom fixed-length string/buffer? Use the factory functions directly instead of a generic primitive:

```python
from protolib.primitives import make_fixed_cp437_string, make_fixed_utf16be_string, make_fixed_buffer

my_string32 = make_fixed_cp437_string(32)
my_buffer256 = make_fixed_buffer(256)
```

## Conditions (`condition` fields)

`conditions.py` implements a small, deliberately **`eval`-free** parser for a JS-like boolean expression subset, used in `condition` fields of container entries:

```
expr       := or_expr
or_expr    := and_expr ( '||' and_expr )*
and_expr   := comparison ( '&&' comparison )*
comparison := operand ( ('===' | '!==' | '==' | '!=' | '>=' | '<=' | '>' | '<') operand )?
operand    := path | literal | '(' expr ')'
path       := ('fields' | '$root' | '$parent') ('.' NAME | '[' INT ']')*
literal    := INT | FLOAT | STRING | 'true' | 'false' | 'null'
```

```yaml
container:
- name: hasName
  type: bool
- name: customName
  type: string
  condition: "fields.hasName === true"
```

`==`/`!=` use JS-style loose coercion, but scoped to only what a binary protocol realistically needs: comparing a number against a numeric string (`"0x1f"`, `"31"`). Everything else compares as-is. `===`/`!==` are always strict.

## Field paths (`../field`, `/field`)

Inside composite type options (`array.count`, `buffer.count`, `pstring.count`, `switch.compareTo`, `count.countFor`), paths follow node-protodef's convention (`resolve_field_path` in `core.py`):

- `"name"` → a field on the *current* container
- `"../name"` → one level up, to the parent container
- `"/name"` → absolute, from the root container

> **Note:** unlike node-protodef's full parent-chain, this port tracks only one explicit `parent` level. `"../../x"` and deeper fall back to root instead of walking further up — this covers every real-world protocol.json case encountered so far (at most a single `../`).

A `container` used **inline** as an array's item type does *not* push its own parent level (so `../field` inside an array item reaches the container that holds the array, not the item itself) — this matters if you need a switch inside an array item to compare against a sibling field of the array's *owner*, not of the item. A container referenced **by name**, on the other hand, always pushes its own level. See `entradaJugador` vs. the inline form in `examples/example_protocol.yml` for a worked example of this exact distinction.

## Parametrizable named types (`$arg`)

A named type can declare `"$name"` placeholders and be invoked with concrete values via `[typeName, {arg: value}]` — parity with node-protodef's `extendType`/`produceArgs`. This is exactly how `minecraft-data` defines `entityMetadataItem`.

```yaml
types:
  itemByKind:
    switch:
      compareTo: $compareTo    # placeholder
      fields:
        '0': i8
        '1': i32
        '2': string

  # used elsewhere with a concrete compareTo:
  entry:
    container:
    - name: kind
      type: varint
    - name: value
      type:
      - itemByKind
      - compareTo: kind        # $compareTo -> "kind" for this use site
```

Placeholders not provided at the use site are left as the literal string `"$name"` (same as node-protodef).

## NBT

`nbt.py` implements Minecraft's Named Binary Tag format (big-endian, no compression — gzip/zlib, if applicable, is the caller's responsibility before/after this layer). Four variants, matching real protocol.json usage:

| Function pair | Format | Use case |
|---|---|---|
| `read_nbt` / `write_nbt` | `[u8 type][u16 nameLen][name][payload]` | Normal named root tag |
| `read_optional_nbt` / `write_optional_nbt` | same as above | Semantically "may be absent" (a standalone `TAG_End` already means absent) |
| `read_anonymous_nbt` / `write_anonymous_nbt` | `[u8 type][payload]` (no name) | Modern Minecraft (1.20.2+) chat components, item `custom_data`, etc. — saves 2 bytes where the name is always `""` |
| `read_anon_optional_nbt` / `write_anon_optional_nbt` | same as anonymous | Optional anonymous variant |

Python representation preserves both the explicit type and (for named tags) the name, since a plain `int`/`float` can't otherwise round-trip unambiguously:

```python
{"name": "root", "type": "compound", "value": {
    "health": {"type": "int", "value": 20},
    "items": {"type": "list", "value": {"type": "int", "value": [1, 2, 3]}},
}}
```

All 12 standard tag types are supported (`byte`, `short`, `int`, `long`, `float`, `double`, `byteArray`, `string`, `list`, `compound`, `intArray`, `longArray`).

## PacketFramer (length-prefixed streams)

Minecraft (and many similar protocols) frame each packet on the wire as `[varint length][payload]`. `framer.py` handles only this outer framing — it knows nothing about packet *contents*; parsing `{name, params}` out of a frame is `Protocol.parse_packet()`'s job, a separate layer.

```python
from protolib import PacketFramer

framer = PacketFramer()

# feed() accumulates bytes from a socket and returns 0+ complete frames
# (handles partial reads and multiple packets arriving in one chunk)
for chunk in socket_reader():
    for frame in framer.feed(chunk):
        pkt = proto.parse_packet("play", "toServer", frame)
        handle(pkt)

# wrap() adds the length-prefix before sending
raw = proto.serialize_packet("play", "toClient", "keep_alive", {"keepAliveId": 1})
sock.sendall(PacketFramer.wrap(raw))
```

`PacketFramer` does **not** implement compression (post-login threshold) or encryption — if your protocol needs those, insert an intermediate layer between the socket and the framer (decompress/decrypt the frame's bytes before handing them to `parse_packet`).

A negative length-prefix (high bit set on a signed-varint read) raises `ValueError` immediately rather than silently mis-framing the rest of the stream.

## Errors

All engine-specific exceptions derive from `ProtolibError` (`errors.py`):

| Exception | Raised when |
|---|---|
| `UnknownTypeError` | A type name isn't a primitive and isn't found in the local or global scope |
| `InvalidTypeDefinition` | A malformed type definition (wrong shape, missing required option, `array`/`buffer`/`pstring` `count` resolving to a non-int, mismatched fixed `buffer` length, unrecognized `registryEntryHolder(Set)` value shape) |
| `SwitchCaseNotFound` | A `switch`'s `compareTo`/`compareToValue` doesn't match any case in `fields` and there's no `default` |
| `ConditionError` | A `condition` expression fails to parse or evaluate |

`BufferUnderrun` (`io.py`, not a `ProtolibError` subclass) is raised when a `Reader` is asked for more bytes than remain in the buffer — carries `.offset`, `.needed`, `.available`.

`LoaderError` (`loader.py`, subclass of `ProtolibError`) covers file-not-found, invalid JSON/YAML, missing PyYAML, or an unrecognized format string.

## Public API reference

Everything below is importable directly from `protolib`:

```python
from protolib import (
    Protocol, ParsedPacket, Scope,
    Reader, Writer, BufferUnderrun,
    PRIMITIVES, Primitive, make_fixed_utf16be_string,
    ProtolibError, UnknownTypeError, InvalidTypeDefinition,
    SwitchCaseNotFound, ConditionError,
    eval_condition,
    PacketFramer,
    load_protocol_dict, protocol_dict_to_yaml, LoaderError,
    read_nbt, write_nbt, NBTError,
    read_anonymous_nbt, write_anonymous_nbt,
    read_anon_optional_nbt, write_anon_optional_nbt,
)
```

### `Protocol`

```python
Protocol(protocol_source: dict | str, *, fmt: str | None = None)
```
`protocol_source` accepts an already-parsed dict, a path to `.json`/`.yml`/`.yaml`, or in-memory JSON/YAML text. `fmt` forces `"json"`/`"yaml"` instead of autodetecting.

| Method | Signature | Purpose |
|---|---|---|
| `parse_packet` | `(state, direction, data: bytes) -> ParsedPacket` | Decode a full frame using the state's `"packet"` type |
| `serialize_packet` | `(state, direction, name, params: dict) -> bytes` | Encode `{name, params}` back into bytes |
| `read_named` | `(state, direction, type_name, data: bytes) -> Any` | Decode a standalone named type, bypassing `"packet"` |
| `write_named` | `(state, direction, type_name, value) -> bytes` | Encode a standalone named type |
| `read_type` | `(type_def, reader, scope, fields, root=None, parent=None) -> Any` | Low-level recursive read, given a raw type definition |
| `write_type` | `(type_def, value, writer, scope, fields, root=None, parent=None) -> None` | Low-level recursive write |
| `get_scope` | `(state, direction) -> Scope` | Look up the `Scope` (local types) for a state/direction |

`ParsedPacket` is a dataclass: `name: str`, `params: dict`, `bytes_read: int` (useful to assert the whole buffer was consumed).

### `Reader` / `Writer` (`io.py`)

```python
r = Reader(buffer, offset=0)
r.remaining        # bytes left
r.ensure(n)         # raises BufferUnderrun if fewer than n bytes remain
r.read_bytes(n)     # -> bytes, advances offset
r.peek_byte()       # -> int, does NOT advance offset

w = Writer()
w.write_bytes(data)
w.result()          # -> bytes, all chunks concatenated
len(w)              # total bytes written so far
```

### `Primitive` (`primitives.py`)

```python
@dataclass(frozen=True)
class Primitive:
    name: str
    read: Callable[[Reader], Any]
    write: Callable[[Any, Writer], None]
    size_of: Callable[[Any], int] | None = None
```

## Extending the library

Following the project's established convention (see `CHANGELOG.md`): **`core.py` is only ever extended, never rewritten** — new composite handlers get added to `_composite_handlers` / `_composite_write_handlers` and as new methods at the end of the `Protocol` class; internal logic of existing handlers is only ever touched via targeted, surgical edits. A full rewrite previously broke `_resolve_compare` and switch fallback behavior — reverting to the original and only adding on top fixed it.

**To add a new primitive type:** register it in `PRIMITIVES` (`primitives.py`) via `_fixed_size_primitive`, `_int_n_bytes_primitive`, or a hand-written `Primitive(name, read, write, size_of)` — no changes to `core.py` needed, primitives are resolved by name lookup.

**To add a new composite type:**
1. Write `_read_<name>` / `_write_<name>` methods on `Protocol`.
2. Register them in `self._composite_handlers` / `self._composite_write_handlers` in `__init__`.
3. Add the name to `COMPOSITE_TYPE_NAMES` in `loader.py` so the YAML shorthand recognizes it (or pass it via `extra_composite_names` to `load_protocol_dict`).

## Project layout

```
protolib/
├── __init__.py       # public exports, __version__
├── io.py             # Reader, Writer, BufferUnderrun
├── primitives.py     # PRIMITIVES catalog, Primitive dataclass
├── core.py           # Protocol engine: composite types, read_type/write_type,
│                      # parse_packet/serialize_packet
├── loader.py          # JSON/YAML loading, YAML shorthand <-> protodef translation
├── framer.py          # PacketFramer (length-prefixed stream framing)
├── nbt.py             # NBT read/write (named, optional, anonymous variants)
├── conditions.py       # eval_condition: safe expression evaluator for `condition`
├── errors.py           # ProtolibError and subclasses
└── py.typed            # PEP 561 marker (type hints are part of the public API)

examples/
├── example_protocol.yml   # reference protocol: every composite pattern + all
│                            # 61 primitives, one field per native
├── example_protocol.json  # same protocol, plain node-protodef JSON form
├── demo.py                 # runs example_protocol.yml end-to-end
└── README.md                # walkthrough of each pattern in example_protocol.yml

tests/                # pytest suite (io, primitives, loader, conditions,
                       # core composites, NBT, framer, roundtrip against examples/)
```

## Running the tests

```bash
pip install -e .[dev]
pytest
```

The test suite covers `io.py`, every primitive, the loader (JSON/YAML/shorthand translation), `conditions.py`, all composite handlers in `core.py`, `nbt.py`, `framer.py`, and a full roundtrip test against `examples/example_protocol.yml`/`.json`.

---

## See also

- `examples/README.md` — walks through each pattern (`switch` with `../field`, arrays of containers with an inner switch, parametrizable named types, packed bitfields, and the full native-type catalog) with the exact YAML and expected Python values.
- `CHANGELOG.md` — full history of additions and bug fixes, including the reasoning behind non-obvious decisions (e.g. why `mapper` keys are normalized, why `entityMetadataLoop` peeks instead of reading the terminator byte separately).
 
