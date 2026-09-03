# protolib (Python)

A from-scratch, pure-Python implementation of a
[node-protodef](https://github.com/ProtoDef-io/ProtoDef)-style system:
you describe the shape of **any** binary protocol's packets in a
declarative file (`.yml` or `.json`), and the library takes care of
parsing raw bytes into a Python `dict` and serializing a `dict` back
into bytes — without you having to hand-write a parser for every
single packet.

**This is a general-purpose binary protocol engine, not a game or
Minecraft library.** It has no idea what a "player" or a "block" is —
it only understands generic building blocks (`container`, `array`,
`switch`, `mapper`, `bitfield`, `bitflags`, `buffer`, `pstring`, fixed
and variable-length integers, etc.), the same way `node-protodef`
itself is used for all kinds of binary protocols, not just games.
You can describe a game protocol with it, but you can just as well
describe a custom IoT sensor stream, a binary file format, an
industrial fieldbus protocol, or a proprietary wire format for a
non-game app — anything with a byte layout you can put on paper.

Minecraft/ClassiCube shows up in this document because that's what
the primitives and the bundled teaching example happen to be modeled
after (that's the protocol family the author has reverse-engineered
the most) — it's the origin story of a few primitive *names*
(`nbt`, `fixedCoord`, `string64`), not a limitation of what the engine
can parse. `examples/` ships a single self-contained example,
`example_protocol.yml` / `.json`, built purely to demonstrate the four
trickiest patterns you're likely to need in *any* protocol
(parametrizable switch, relative `compareTo`, arrays of containers
with a switch inside, packed bitfield + switch) — it's a teaching
protocol, not a real one, see section 10. It's **not** a catalog of
real protocols, Minecraft or otherwise; bring your own `.yml` for
whatever binary format you're actually working with.

---

---

## 📚 Reference templates

| Format | File | Use |
|---|---|---|
| YAML | [`full_reference_template.yml`](examples/full_reference_template.yml) | Easier to read/write by hand |
| JSON | [`full_reference_template.json`](examples/full_reference_template.json) | protolib's native JSON |
| `.proto` | [`full_reference_template.proto`](examples/full_reference_template.proto) | Real Google Protocol Buffers, via `protolib.protobuf` (section 14) |

The YAML and JSON files describe the exact same (non-real) protocol for
protolib's own node-protodef-style engine (`protolib.core.Protocol`) --
open them side by side to see how each YAML block translates into
native JSON, no guessing required. The `.proto` file is a **separate**
catalog for the independent `protolib.protobuf` engine (section 14):
same idea (every construct that engine supports, cataloged in one
file, validated by a real roundtrip in
[`examples/demo_protobuf.py`](examples/demo_protobuf.py)), but it isn't
a translation of the other two -- protobuf and the YAML/JSON engine are
different schema languages with different wire formats, so there's no
line-for-line equivalence between them.

---

## 1. The idea in 30 seconds

```python
from protolib import Protocol

proto = Protocol("my_protocol.yml")

# bytes -> dict
parsed = proto.parse_packet("play", "toClient", raw_data)
print(parsed.name, parsed.params, parsed.bytes_read)

# dict -> bytes
data = proto.serialize_packet("play", "toClient", "spawn_player", {
    "playerId": 5, "x": 320, "y": 2080, "z": 320,
})
```

Everything else in this document is about how to declare
`my_protocol.yml` so that `parse_packet`/`serialize_packet` know which
fields to expect.

---

## 2. Install / use

No dependencies beyond `pyyaml` (for the `.yml` format). Used as a
local package, no need to install it:

```python
import sys
sys.path.insert(0, "path/to/the/folder/containing/protolib")
from protolib import Protocol
```

`Protocol(...)` accepts:
- a path to a `.json`, `.yml`, or `.yaml` file
- an in-memory string with JSON or YAML content
- an already-parsed `dict` (the "native" node-protodef format)

> **Heads up about relative paths:** a path like `"my_protocol.yml"` is
> resolved relative to your process's current working directory (cwd),
> **not** relative to the script file that calls `Protocol(...)`. If you
> run your script from a different folder than the one containing the
> `.yml`, the file won't be found there.
>
> If the file genuinely can't be found, `Protocol(...)` now raises a
> clear `LoaderError` telling you the path it looked for and the cwd it
> used — it does **not** silently try to parse the filename string as
> protocol content (that used to happen and produced a confusing crash
> deep inside `core.py` instead of a real "file not found" error).
>
> To make a script work no matter where it's run from, build the path
> relative to the script itself instead of relying on cwd:
> ```python
> import os
> here = os.path.dirname(os.path.abspath(__file__))
> proto = Protocol(os.path.join(here, "my_protocol.yml"))
> ```

---

## 3. Structure of a protocol file

```yaml
types:
  # types valid across the ENTIRE protocol, regardless of state/direction
  varint: native
  myCustomType:
    container:
    - name: x
      type: varint

<state_name>:        # e.g.: "play", "login", "handshaking"...
  toClient:
    types:
      # types that only exist for this direction/state, take
      # PRIORITY over the ones above if there's a name collision
      packet: ...
  toServer:
    types:
      packet: ...
```

- **global `types:`** — shared types (primitives, common structs).
- **`<state>.toClient.types` / `<state>.toServer.types`** — the real
  entry point to parse a packet is always a type named `packet` inside
  one of these blocks. `parse_packet(state, direction, data)` looks for
  that `packet`, reads it, and returns `{name, params}` based on how
  you built it (see the "packet container with mapper+switch" pattern
  in section 7).

> **Important:** `Protocol.parse_packet`/`serialize_packet` expect the
> `packet` type to ALWAYS have exactly two fields named `name` and
> `params` — that's what builds the `ParsedPacket(name=...,
> params=...)` you get back. The standard pattern (used in both
> example protocols) is:
>
> ```yaml
> packet:
>   container:
>   - name: name
>     type:
>       mapper:               # wire integer -> packet name
>         type: u8            # (or varint, depending on your protocol)
>         mappings:
>           '0x00': identification
>   - name: params
>     type:
>       switch:                # packet name -> its payload type
>         compareTo: name
>         fields:
>           identification: packet_identification
> ```
>
> If your `packet` doesn't follow this exact shape, `parse_packet` will
> fail with `KeyError: 'name'` — it's not a weird limitation of your
> protocol, it's literally how this library knows what to return.

`state` and `direction` don't have to be called that — they're simply
the two keys you'll later use to call
`parse_packet`/`serialize_packet`. A protocol without real "states"
(like ClassiCube) still declares a single `play:` just to have
somewhere to hang `toClient`/`toServer`.

---

## 4. The native `.json` format (without the YAML shorthand)

Everything in section 3 can be written directly in JSON, without going
through YAML — it's the format already used by `minecraft-data` and
the original `node-protodef`, and this library understands it with no
translation needed. The difference with the `.yml` shorthand is one of
**shape**, not semantics: each composite type (`container`, `switch`,
`array`, `bitfield`, etc.) is written as a 2-element list instead of a
single-key mapping:

```json
["<base_type_name>", <options>]
```

The same `switch` from section 6, in YAML shorthand:

```yaml
type:
  switch:
    compareTo: name
    fields:
      identification: packet_identification
```

...is this in native JSON:

```json
{
  "type": [
    "switch",
    {
      "compareTo": "name",
      "fields": { "identification": "packet_identification" }
    }
  ]
}
```

And a `container` (which carries a list of fields instead of an
options-dict) follows the same `["container", [...]]` pattern:

```json
"packet_identification": [
  "container",
  [
    { "name": "protocolVersion", "type": "u8" },
    { "name": "name", "type": "string64" }
  ]
]
```

**Why would you still want to write `.yml`?** Because by hand, several
levels of nested 2-element lists are error-prone and hard to read (does
that `]` close the `container` or the outer `switch`?). The loader
(`protolib/loader.py`) automatically translates the `.yml` shorthand
into this native form before `Protocol` uses it — that's why
`example_protocol.json` and `example_protocol.yml` are **exactly
equivalent**: they serialize and parse byte-for-byte the same, one is
just more convenient to write/maintain by hand than the other.

**So when should you use the raw `.json` directly?**
- You already have a protocol from `minecraft-data` or another
  `node-protodef` project in JSON and want to reuse it as-is, without
  rewriting it.
- You want to generate the protocol programmatically from another
  language or script that has no business knowing the shorthand
  syntax.
- You want to see "what the engine actually understands internally"
  with no ambiguity — useful for debugging a `.yml` that isn't loading
  the way you expected: generate its `.json` (see below) and check if
  the translation came out as you thought.

**Generating `.json` from an existing `.yml`**, using the loader
itself (this guarantees it's faithful to what `Protocol` will actually
read, not a separately hand-made translation):

```python
import json
from protolib.loader import load_protocol_dict

d = load_protocol_dict("my_protocol.yml")
with open("my_protocol.json", "w", encoding="utf-8") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
```

This is exactly how `examples/example_protocol.json` was generated
from `example_protocol.yml` — the two files are guaranteed to be
byte-for-byte equivalent because one is mechanically derived from the
other, not hand-translated.

---

## 5. `native`: how primitive types get registered

`native` in the `.yml` means "don't define this here, look it up by
name in `protolib/primitives.py`". Already registered:

| Name        | Size          | Notes |
|---|---|---|
| `u8`/`i8`   | 1 byte        | unsigned/signed |
| `u16`/`i16` | 2 bytes BE    | |
| `u24`/`i24` | 3 bytes BE    | |
| `u32`/`i32` | 4 bytes BE    | |
| `u40`/`i40` | 5 bytes BE    | |
| `u48`/`i48` | 6 bytes BE    | |
| `u56`/`i56` | 7 bytes BE    | |
| `u64`/`i64` | 8 bytes BE    | |
| `f16`       | 2 bytes BE    | half-float (IEEE 754 binary16), ~3 decimal digits of precision |
| `f32`/`f64` | 4/8 bytes BE  | float/double |
| `lu8`…`lf64` | same, little-endian | `l` prefix (full family: `l` + any of the above, 8→64 bits, incl. `lf16`) |
| `varint`/`varlong` | variable | **signed** LEB128, no zigzag applied |
| `uvarint`/`uvarlong` | variable | unsigned LEB128 |
| `varint128`/`uvarint128` | variable | same as above, extended to 128 bits (up to 19 bytes) |
| `zigzag32`/`zigzag64` | variable | true zigzag LEB128 (Protocol Buffers style) |
| `bool`      | 1 byte        | |
| `void`      | 0 bytes       | useful as an empty case in a `switch` |
| `cstring`   | variable      | `\0`-terminated |
| `UUID`      | 16 bytes      | |
| `nbt`/`optionalNbt` | variable | Minecraft's NBT format (with name prefix) |
| `anonymousNbt`/`anonOptionalNbt` | variable | same NBT payload, no name prefix (modern Minecraft chat components, `custom_data`, etc.) |
| `string64`  | 64 fixed bytes | CP437, space-padded (ClassiCube) |
| `utf16be64` | 128 fixed bytes | UTF-16BE, 64 CHARACTERS (not bytes), space-padded (ClassiCube username field, etc.) |
| `buffer1024`| 1024 fixed bytes | raw, `0x00`-padded (ClassiCube) |
| `buffer64`  | 64 fixed bytes | raw, `0x00`-padded (ClassiCube) |
| `fixedCoord`/`fixedCoordDelta` | 2/1 bytes | fixed-point *32 (ClassiCube) |

**If your protocol needs a new type that isn't a composition of the
ones above** (say, a fixed-length string with a different encoding, or
a buffer of another fixed size), add it in Python following the
pattern of `make_fixed_cp437_string`/`make_fixed_buffer` in
`primitives.py`, and register it in the `PRIMITIVES` dict at the end
of the file. After that it's available as `native` in any `.yml`, for
you and for anyone else using this library.

Non-native types (`pstring`, `container`, `switch`, `array`,
`bitfield`, `bitflags`, `buffer`, `mapper`, `option`, `count`) are
resolved by the Python engine (`core.py`) according to their own logic
— explained below with examples.

---

## 6. Composite types, one by one

### `container` — groups named fields, in order

```yaml
packet_login:
  container:
  - name: protocolVersion
    type: varint
  - name: username
    type: string
```

Reads as `{"protocolVersion": ..., "username": ...}`.

**`anon: true` field**: instead of being stored under its own name, its
sub-fields get merged directly into the parent container's `dict`.
Useful for "breaking up" a bitfield or a switch into several loose
fields at the same level (see bitfield below).

**`condition` field**: the field is only read/written if the condition
(an expression over already-read fields) is true. Syntax: same path
language as `compareTo` (section 7).

### `array` — list of N elements of the same type

```yaml
type:
  array:
    countType: varint    # reads a varint first, that's the length
    # count: otherField  # alternative: uses the VALUE of another already-read field
    type: myItemType
```

`countType` prepends an integer that's read/written automatically.
`count`, on the other hand, references a sibling field already present
(typically built with the `count` type, see section 9) — it doesn't
write anything new, it just reads that existing value as the array's
length.

### `switch` — picks the field's type based on another field's value

```yaml
type:
  switch:
    compareTo: name        # path to an already-read field (see section 7)
    fields:
      valueA: typeForA
      valueB: typeForB
    default: void           # optional, if nothing matches
```

This is the central piece for "the `params` field depends on what the
`name` field says" — the pattern of every network packet with an
id/name.

### `mapper` — integer ⟷ symbolic name

```yaml
type:
  mapper:
    type: varint
    mappings:
      '0x00': handshake
      '0x01': status_request
```

When reading, a `0x00` in the stream becomes the string `"handshake"`
in the resulting `dict` (and vice versa when writing). This way the
`switch` above can compare against readable names (`compareTo: name`)
instead of the raw packet id integer.

A `mapper` models a closed set: if the raw value read has no entry in
`mappings` (or, when writing, the symbolic name has no inverse entry),
it raises `MapperValueNotFoundError` instead of silently passing the
raw value through — an unmapped value almost always means a stale
mappings table or a desynced stream, not something safe to ignore.

### `bitfield` — several fields packed into less than 1 byte each

```yaml
type:
  bitfield:
  - name: type
    size: 3
    signed: false
  - name: index
    size: 5
    signed: false
```

Declares sub-fields that each take up `size` bits, MSB first, within
the minimum necessary bytes (if they don't fill an exact byte, padding
is added). Almost always combined with `anon: true` on the container
that holds it, so that `type`/`index` end up as loose fields instead of
nested under an extra name.

### `bitflags` — an integer as a named set of booleans

```yaml
type:
  bitflags:
    type: u8
    flags: [air, water, lava, null, fire]   # position = bit number
    # or, as a dict: {air: 0x01, water: 0x02, ...}
```

Returns `{"air": true, "water": false, ..., "_value": <raw integer>}`.

### `buffer` — raw bytes (uninterpreted)

```yaml
type:
  buffer:
    count: 1024        # fixed size, or a reference to a field
    # countType: varint  # alternative: prefixed length
    # rest: true         # alternative: "whatever's left in the frame"
```

### `option` — present only if a preceding boolean flag says so

```yaml
type:
  option: myType
```

Reads a `bool` first; if `true`, reads `myType` right after; if
`false`, the value is `None` and nothing else is read.

### `pstring` — string with a prefixed length

```yaml
type:
  pstring:
    countType: varint     # how many length-bytes come before it
    # encoding: utf-8      # default
```

The `string:` shortcut already ships built with this
(`countType: varint`, used in Minecraft Java). For ClassiCube,
`string64` is used instead (ALWAYS fixed size, no prefix — see
section 5), since that protocol doesn't prefix a length.

### `entityMetadataLoop` — list of entries until a terminator byte

```yaml
type:
  entityMetadataLoop:
    endVal: 0xff        # default; the raw byte that marks "no more entries"
    endType: u8          # default; 1-byte type used only for the terminator check
    type: entityMetadataItem   # full type of each entry
```

Reads entries of `type` one after another until the **next unread
byte** equals `endVal` (peeked without consuming it), then consumes
that terminator byte and stops. `type` gets the full raw byte as part
of its own read — this only peeks to decide whether to stop, it never
double-reads the byte that belongs to an entry. Used for Minecraft's
`entity_metadata` field (a variable-length list of `(type<<5)|key`
packed entries, see section 10).

### `topBitSetTerminatedArray` — list that ends when the high bit isn't set

```yaml
type:
  topBitSetTerminatedArray:
    type: myVarintLikeItem
```

Reads entries of `type` while the **highest bit (`0x80`) of each
entry's first byte** is set; stops right after reading the first entry
whose first byte does NOT have that bit set. A LEB128-like list
pattern used by RakNet and some custom formats, where each entry
"announces" whether another one follows. When writing, the caller is
responsible for setting that bit correctly on every item except the
last one — the continuation marker is part of each item's own encoded
data, not something this wrapper adds on top.

### `registryEntryHolder` — modern Minecraft's `IdOr<T>`

```yaml
type:
  registryEntryHolder:
    idType: varint         # default
    otherwise:
      type: myInlineType    # read/written only when the raw id is 0
```

"Numeric id → either a reference to a registry entry, or an inline
value" pattern (trims, biomes, sounds, banner patterns, etc. in modern
Minecraft Java). Wire format: `id == 0` means an inline `otherwise.type`
value follows; `id != 0` (say, `id = n`) means "reference to registry
index `n - 1`" (shifted by one so index `0` doesn't collide with the
inline case). Python representation, always explicit — never
ambiguous:
```python
{"type": "inline", "value": ...}
{"type": "reference", "id": N}
```

### `registryEntryHolderSet` — modern Minecraft's `HolderSet<T>`

```yaml
type:
  registryEntryHolderSet:
    idType: varint   # default; type of each id in the explicit-list case
```

Same idea as `registryEntryHolder`, but for **sets** of entries: either
a reference to a tag already known by both sides, or an explicit list
of ids. Wire format: `[varint count]`; if `count == 0`, a `cstring`
with the tag's name follows (conventionally prefixed with `#`, e.g.
`"#minecraft:trim_materials"`); if `count > 0`, exactly `count` ids
follow (`idType` each) — here `count` is literally "how many ids
there are," with none of `registryEntryHolder`'s index shifting.
Python representation:
```python
{"type": "tag", "tagName": "#minecraft:trim_materials"}
{"type": "ids", "ids": [3, 7, 12]}
```
Note: an empty `ids` list serializes identically to the `tag` case
(`count=0` either way) — that's true of the real vanilla protocol too,
not a limitation of this port.

---

## 7. `compareTo` and field paths (`../`, `/`)

`compareTo` (in a `switch`) and `condition` (on a `container` field)
use the same tiny path language to refer to "a field that's already
been read":

| Syntax | Means |
|---|---|
| `fieldName` | sibling field, same container |
| `../fieldName` | goes up one level: the container that holds this one |
| `../../fieldName` | goes up two levels |
| `/fieldName` | absolute, from the root of the whole packet |

**Important gotcha, already documented with a full example in
`examples/README.md`:** `../` only goes up one real level when the
container is written **inline** (literally inside `array.type` or
another container). If instead you reference a type **by name**
(`type: myNamedType`), that named type always adds its own nesting
level — so a `../field` inside it reaches, at most, the named
container itself, not the real parent further up. If your switch needs
to see a field higher up in the hierarchy, declare the container
inline at the point of use instead of moving it into a separately
named type.

---

## 8. Parametrizable types (`$arg`)

A named type can declare a `$`-prefixed placeholder instead of a fixed
value, and whoever uses it decides the real value at that point:

```yaml
types:
  itemByType:
    switch:
      compareTo: $compareTo    # placeholder -- the name after
      fields:                 # the $ is arbitrary, you choose it
        '0': i8
        '1': varint

  myContainer:
    container:
    - name: type
      type: u8
    - name: value
      type:
      - itemByType             # referenced as [name, options]
      - compareTo: type          # here the real $compareTo gets resolved
```

This defines a "generic" type once and reuses it with a different
comparison field depending on context — it's exactly how
`minecraft-data` defines `entityMetadataItem`.

---

## 9. `count` — a length-prefix declared apart from the array/buffer

For the (rare, but real in some protocols) case where an array's
length prefix isn't attached to that array but is instead its own
field elsewhere in the container:

```yaml
container:
- name: itemCount
  type:
    count:
      type: varint
      countFor: items      # when WRITING, ignores the given value and
- name: items               # writes len(items) automatically
  type:
    array:
      count: itemCount   # when READING, references that already-read field
      type: myItem
```

---

## 10. The 4 advanced patterns (teaching example)

These 4 patterns aren't specific to any one protocol — they're generic
engine features that tend to trip people up regardless of what you're
parsing. `examples/example_protocol.yml` is a made-up toy protocol
(loosely styled after Minecraft Java's packet shape, since that's a
familiar reference point) built solely to demonstrate them; it's not
meant to represent Minecraft itself or any other real protocol.
Covered with working, commented examples in
[`examples/README.md`](examples/README.md) +
[`examples/example_protocol.yml`](examples/example_protocol.yml)
(its `.json` equivalent, already translated to the native `["type",
opts]` form, lives in
[`examples/example_protocol.json`](examples/example_protocol.json)):

1. Parametrizable named type with `$arg` (section 8 of this doc).
2. `compareTo: ../field` and the inline-vs-named container gotcha
   (section 7).
3. Array of containers with a `switch` inside that looks at a field of
   the array's parent container.
4. `bitfield` packed into 1 byte + `switch` that uses that field to
   pick the type of another sibling field (a pattern common to any
   protocol that packs a type tag + value into a compact wire format —
   Minecraft's `entity_metadata` is just one real-world instance of it).

Run `python examples/demo.py` to see them in action.

---

## 11. Wiring this into a real socket loop

`protolib` itself doesn't touch sockets — it only turns bytes into
`dict`s and back. To use it against a real TCP/UDP connection, combine
`PacketFramer` (section 9) with your own `recv()` loop:

```python
sock_buffer = b""
while True:
    chunk = sock.recv(4096)
    if not chunk:
        break
    for frame in framer.feed(chunk):
        try:
            pkt = proto.parse_packet("play", "toServer", frame)
        except BufferUnderrun:
            # shouldn't happen here if you're using PacketFramer
            # correctly (feed() only returns COMPLETE frames), but if
            # you're parsing raw socket data directly without the
            # framer, this means "the full packet hasn't arrived yet"
            # -- keep buffering and retry once more bytes come in.
            continue
        handle(pkt)
```

If your protocol doesn't use a varint length-prefix (e.g. fixed-size
packets identified by their first byte, like Minecraft Classic), you
don't need `PacketFramer` at all — just call `parse_packet` directly
on whatever chunk boundaries make sense for your protocol, and catch
`BufferUnderrun` from `protolib.io` to know when to wait for more
bytes.

---

## 12. Errors and what they mean

All of them live in `protolib/errors.py`:

- **`UnknownTypeError`** — a type name isn't in `native`
  (`primitives.py`) nor defined in `types:` (global or scoped). Usually
  a typo, or you forgot to declare it.
- **`SwitchCaseNotFound`** — a `switch` has no `fields` entry for the
  value `compareTo` produced, and there's no `default` either. Check
  that the comparison value resolves to what you expect (does the
  `mapper` building that field convert it to a string? does
  `compareTo` point to the right field using the syntax from section
  7?).
- **`InvalidTypeDefinition`** — a composite type is missing a required
  option (`array` without `count`/`countType`, `buffer` without
  `count`/`countType`/`rest`, etc.).
- **`BufferUnderrun`** (in `protolib/io.py`, not `errors.py`) — an
  attempt was made to read more bytes than are available. In a real
  server this is NOT necessarily a protocol error: it means "the full
  packet hasn't arrived on the socket yet," and the correct pattern is
  to catch it and wait for more bytes (see the socket loop in section
  11) — or avoid it entirely by going through `PacketFramer`, which
  only ever hands you complete frames.

## 13. Full API (quick reference)

Everything `from protolib import ...` exposes (`protolib/__init__.py`),
with its real signature. What's already covered with examples above is
referenced, not repeated.

### `protolib.core` — the engine

```python
Protocol(protocol_source: dict | str, *, fmt: str | None = None)
```
See sections 1–2. `fmt` forces `"json"`/`"yaml"` when `protocol_source`
is in-memory content with no recognizable extension (passed straight
through to `load_protocol_dict`).

Public methods of `Protocol`:

| Method | Returns | Use |
|---|---|---|
| `parse_packet(state, direction, data: bytes)` | `ParsedPacket` | the usual one (section 1). |
| `serialize_packet(state, direction, name: str, params: dict)` | `bytes` | the usual one (section 1). |
| `read_named(state, direction, type_name: str, data: bytes)` | `Any` | reads a type **by name** that isn't necessarily the full `packet` — useful for testing/debugging a standalone type (e.g. an intermediate `container`) without going through the `name`/`params` wrapper. |
| `write_named(state, direction, type_name: str, value)` | `bytes` | the inverse of `read_named`. |
| `get_scope(state, direction)` | `Scope` | the internal object holding the resolved types for that state/direction, in case you need to inspect what got registered there. |

`ParsedPacket` (dataclass, what `parse_packet` returns):
```python
ParsedPacket(name: str, params: dict, bytes_read: int)
```
`bytes_read` tells you how many bytes of the original buffer the
packet consumed — useful if you're parsing several packets stuck
together in the same buffer without going through `PacketFramer`.

`Scope` (dataclass): `Scope(types: dict[str, TypeDef])` — types local
to a `state.direction`, taking priority over the global ones.

### `protolib.io` — raw byte layer

```python
Reader(buffer: bytes | bytearray | memoryview, offset: int = 0)
Reader.remaining -> int          # property
Reader.ensure(n: int) -> None    # raises BufferUnderrun if not enough bytes
Reader.read_bytes(n: int) -> bytes
Reader.peek_byte() -> int        # does not advance the offset

Writer()
Writer.write_bytes(data: bytes) -> None
Writer.result() -> bytes         # concatenates all chunks
len(writer)                      # total bytes accumulated
```

`BufferUnderrun(offset, needed, available)` — see section 12.

### `protolib.framer` — splitting a socket stream into packets

Not mentioned in the sections above; it's the missing piece between
"bytes are arriving from `recv()`" and "I have a complete frame to
hand to `parse_packet`". Assumes the Minecraft format
`[varint length][payload]`.

```python
from protolib import PacketFramer

framer = PacketFramer()

# in your recv() loop:
frames = framer.feed(chunk)     # list[bytes], can come back empty, or
for frame in frames:             # with several frames if multiple packets
    pkt = proto.parse_packet("play", "toServer", frame)  # arrived stuck together

# when sending (after serialize_packet, which does NOT add the length-prefix):
raw = proto.serialize_packet("play", "toClient", "keep_alive", {...})
sock.send(PacketFramer.wrap(raw))
```
`PacketFramer.wrap(frame: bytes) -> bytes` is a `@staticmethod`, no
instance needed. It doesn't compress or encrypt — that goes in your
own intermediate layer if your protocol needs it (see the module's
docstring).

### `protolib.nbt` — Named Binary Tag

Not mentioned above except as a row in the primitives table
(`nbt`/`optionalNbt`). If you need to read/write standalone NBT,
outside of a protocol declared in `.yml`:

```python
from protolib import read_nbt, write_nbt
from protolib.io import Reader, Writer

tag = read_nbt(Reader(raw_bytes))
# -> {"name": str, "type": "compound"|"int"|"list"|..., "value": ...} | None
#    (None if the first byte is TAG_End)

w = Writer()
write_nbt(tag, w)
raw_bytes_back = w.result()
```
`read_optional_nbt`/`write_optional_nbt` are identical aliases,
exposed only so the name matches `optionalNbt` from
`node-minecraft-protocol` when generating/reading someone else's
protocol.json. `NBTError` is the module's own exception (unknown tag,
etc).

**`anonymousNbt` / `anonOptionalNbt`** — same tag payload format, but
**without** the `[u16 nameLen][nameLen bytes]` name prefix that
`read_nbt`/`write_nbt` carry: just `[u8 tagType][payload]`, directly.
Used by modern Minecraft (1.20.2+) in chat components, an item's
`custom_data`, `block_entity_data`, and other places where the root
tag's name would always be the empty string — no point spending 2
bytes writing that zero length on every packet.

```python
from protolib import read_anonymous_nbt, write_anonymous_nbt
from protolib.io import Reader, Writer

tag = read_anonymous_nbt(Reader(raw_bytes))
# -> {"type": "compound"|"int"|"list"|..., "value": ...} | None
#    (no "name" key — unlike read_nbt's result, so the two can never
#    be confused with each other)

w = Writer()
write_anonymous_nbt(tag, w)
```
`read_anon_optional_nbt`/`write_anon_optional_nbt` are aliases, same
relationship to `anonymousNbt` as `optionalNbt` has to `nbt`.

### `protolib.conditions` — the `condition` field on a container

Section 3 mentions `condition` but says "same path language as
`compareTo`" — it's actually a separate expression grammar, closer to
boolean JS, **without** `eval()`/`exec()`:

```python
eval_condition(expr: str, fields: dict, root: dict | None = None,
                parent: dict | None = None) -> bool
```

Grammar supported in `condition:`:
```
fields.someField === 1
fields.type !== 0 && fields.flag == true
$root.version >= 47
$parent.hasData
(fields.a > 0) || (fields.b < 10)
```
Operators: `=== !== == != >= <= > < && ||`. Paths: `fields.x`,
`$root.x`, `$parent.x`, with `[N]` index support. If the expression is
a single operand with no comparison, its truthiness gets evaluated
(same as JS/Python). Raises `ConditionError` if the expression isn't
valid.

### `protolib.loader` — loading and converting formats

```python
load_protocol_dict(source: str | dict, *, fmt: str | None = None,
                    extra_composite_names: frozenset[str] | None = None) -> dict
```
See section 4. `extra_composite_names` is for when you extend
`core.py` with your own composite types (beyond `container`, `switch`,
`array`, etc.) and want the YAML shorthand to also recognize them as a
single-key mapping instead of a `[name, opts]` list.

```python
protocol_dict_to_yaml(protocol_dict: dict) -> str
```
The **inverse** operation from section 4: takes a dict already in
native `["type", opts]` format (e.g. a `minecraft-data` `protocol.json`
as-is, untouched) and returns the equivalent YAML shorthand, readable
by hand. It's "best effort" — meant to kick off a JSON→YAML migration,
not to guarantee a byte-for-byte identical result in the generated
YAML (the YAML→JSON encoding from section 4 is exact, the inverse
can't always be 1:1).

`LoaderError` — see section 12 (path not found, invalid JSON/YAML,
PyYAML not installed).

### `protolib.primitives` — everything `native` available

The table in section 5 doesn't list every combination. The full set of
fixed-width integers is `{u,i} × {8,16,24,32,40,48,56,64}` plus their
little-endian variants (`l` + the same), 32 names total
(`u8, i8, u16, i16, ..., u64, i64, lu8, li8, ..., lu64, li64`). Besides
that:

| Name | What it is |
|---|---|
| `varint`/`varlong` | **signed** LEB128 (no zigzag applied — see `zigzag32/64` below if you need that) |
| `uvarint`/`uvarlong` | unsigned LEB128 |
| `varint128`/`uvarint128` | same as above, extended to 128 bits (up to 19 bytes) — for IDs that don't fit in 64 bits (large Snowflake-style IDs, truncated hashes, etc.) |
| `zigzag32`/`zigzag64` | true zigzag LEB128 (Protocol Buffers style) |
| `rest_buffer` | all remaining bytes in the buffer, no length prefix |
| `buffer64` | 1024→64 fixed bytes, `\x00`-padded (added in 0.2.1, see Changelog) |

```python
from protolib import PRIMITIVES, Primitive, make_fixed_utf16be_string

PRIMITIVES["varint"].read(reader)          # -> int
PRIMITIVES["varint"].write(123, writer)    # -> None, writes to writer
PRIMITIVES["varint"].size_of(123)          # -> int, bytes it'll take up

fixed_name = make_fixed_utf16be_string(16)  # new Primitive, not registered
```
`Primitive` is a `@dataclass(frozen=True)` with 4 fields: `name`,
`read(reader) -> Any`, `write(value, writer) -> None`, and an optional
`size_of(value) -> int | None`. `make_fixed_cp437_string` and
`make_fixed_buffer` (mentioned in section 5) follow the same pattern
if you need to build your own fixed primitive.

### `protolib.errors` — every exception

`ProtolibError` is the base for all of them. See section 12 for
`UnknownTypeError`, `InvalidTypeDefinition`, `SwitchCaseNotFound`.
`ConditionError` is explained above, under `conditions`.

---

## 14. `protolib.protobuf` — real Google Protocol Buffers (`.proto`)

Everything above (sections 1-13) is protolib's own YAML/JSON,
node-protodef-style engine — a generic set of building blocks
(`container`, `array`, `switch`, ...) for describing *any* binary
protocol.

`protolib.protobuf` is a **separate, independent** schema language
living alongside it: it reads real `.proto` files (proto3 syntax) and
speaks the actual Google Protocol Buffers wire format
(https://protobuf.dev/programming-guides/encoding/). The two don't
share a schema representation — pick whichever fits the protocol
you're describing. If you already have a `.proto` file (or need to be
wire-compatible with something written in Go/Java/C++/etc. using real
protobuf), use this section instead of YAML/JSON.

```python
from protolib.protobuf import ProtoFileSchema

schema = ProtoFileSchema.from_file("examples/addressbook.proto")

data = schema.encode("Person", {
    "name": "Alice",
    "id": 1234,
    "email": "alice@example.com",
    "phones": [{"number": "555-1234", "type": "HOME"}],
})
# `data` is now real protobuf wire-format bytes -- byte-for-byte
# compatible with what `protoc`-generated code in any other language
# would produce for the same message and values.

schema.decode("Person", data)
# -> {'name': 'Alice', 'id': 1234, 'email': 'alice@example.com',
#     'phones': [{'number': '555-1234', 'type': 'HOME'}]}
```

`ProtoFileSchema.from_source(text)` works the same way if the `.proto`
content is already a string rather than a file on disk.

### What's supported

- `message`, arbitrarily nested, with cross-references between
  messages (forward or backward in the file) and recursive types
  (e.g. a tree node referencing itself via `repeated`).
- `enum` (top-level or nested); an unrecognized enum number read from
  the wire round-trips as the raw int instead of failing, per spec.
- `repeated`, with correct **packed encoding** by default for numeric/
  bool/enum scalars (and correct acceptance of the non-packed form too,
  for interop with encoders that don't pack).
- `map<K, V>` (desugared to its real wire form, a synthetic
  `key=1`/`value=2` message per entry, exactly like `protoc` does).
- `oneof`, with mutual exclusivity enforced: encoding a dict with more
  than one member of the same `oneof` present raises
  `ProtoOneofViolationError` rather than silently writing both.
- `reserved` (both number ranges and names), field options (parsed and
  discarded — they don't affect wire shape), file-level `option`,
  `package`-qualified and dotted/nested type names.
- Forward compatibility: an unknown field number on the wire is
  skipped, not an error (so data from a newer `.proto` revision than
  the one loaded here doesn't crash).

### What's explicitly rejected (not silently mishandled)

`syntax = "proto2"`, `required` fields, and `extend` (proto2
extensions) all raise `UnsupportedProtoFeatureError` immediately at
parse time. proto2 has different field-presence and extension
semantics that this port doesn't implement — refusing clearly beats
guessing and producing a subtly wrong parse.

### Lower-level pieces, if you need them

`ProtoFileSchema` is a thin convenience wrapper. The actual pipeline
underneath, if you need to inspect it directly:

```python
from protolib.protobuf import parse_proto, build_schema, encode_message, decode_message

proto_file = parse_proto(source_text)      # .proto text -> AST (proto_ast.py)
schema = build_schema(proto_file)           # AST -> name-resolved ResolvedSchema
message = schema.message_by_name("Person")  # -> ProtoMessage
data = encode_message(schema, message, {"name": "Alice"})
decode_message(schema, message, data)
```

`protolib.protobuf.wire` also exposes the raw byte-level primitives
(`read_raw_varint`, `write_tag`, `zigzag_encode`, `read_fixed32`, ...)
directly, in case you're building something lower-level than a full
`.proto`-driven schema.

---

## Changelog

### Unreleased
- **README fixes**: corrected `from protolib.core import Protocol`

  import examples to the real public import (`from protolib import
  Protocol`, exposed via `protolib/__init__.py`); completed the
  primitives table (was missing `u24/i24`, `u40/i40`, `u48/i48`,
  `u56/i56`, `varint128/uvarint128`, `anonymousNbt/anonOptionalNbt`,
  `buffer64`); removed a stray leftover sentence on the `varint` row;
  fixed dead references to files no longer in `examples/`.
- **`examples/` trimmed** to a single self-contained teaching protocol
  (`example_protocol.yml`/`.json` + `demo.py`), which is what it was
  meant to be from the start — it's there to show the four trickiest
  patterns (section 10), not to double as a library of real,
  ready-to-use Minecraft/ClassiCube protocols. The ClassiCube example,
  `servidor.py`, and other real-project protocol files that had
  accumulated there were removed; **if `tests/test_examples_roundtrip.py`
  still references any of them (e.g. `classicube_protocol.yml`), that
  test file needs a matching update** — it wasn't touched as part of
  this cleanup.

### 0.3.4
- New **`anonymousNbt` / `anonOptionalNbt`** primitives — same as
  `nbt`/`optionalNbt` but without the name prefix (modern Minecraft's
  chat components, item `custom_data`, `block_entity_data`).
- New **`registryEntryHolder`** / **`registryEntryHolderSet`**
  composites — modern Minecraft's `IdOr<T>` / `HolderSet<T>` patterns
  (trims, biomes, sounds, banner patterns, tags).

### 0.3.3
- **`tests/`**: full test suite (160+ tests, plain `unittest`, also
  `pytest`-compatible) covering every module and both example
  protocols end-to-end.
- Added the missing `LICENSE` file and `protolib/py.typed` (PEP 561).
- Fixed `conditions.py` letting a raw `ValueError` escape instead of
  the documented `ConditionError`.
- Fixed `mapper` crashing with `AttributeError` when a YAML mapping key
  was written unquoted and numeric/hex (e.g. `0x00:` instead of
  `'0x00':`) — PyYAML loads that as an `int`, not a `str`.
- Fixed the YAML shorthand loader corrupting `buffer:\n  count: 16`
  because `count` is itself a valid composite type name.

### 0.3.2
- New **`varint128` / `uvarint128`** primitives — LEB128 extended to
  128 bits, for IDs too large for 64 bits.
- Fixed a version mismatch between `pyproject.toml` and
  `protolib/__init__.py`.

### 0.3.0
- Fixed `Protocol("file.yml")` failing silently and confusingly when
  the process wasn't run from that file's exact directory.
- Added the missing fixed-width primitives to complete the
  8/16/24/32/40/48/56/64-bit family: `u40`, `i40`, `u56`, `i56` and
  their little-endian variants.

### 0.2.1
- New primitive `buffer64`: fixed 64-byte raw bytes with automatic
  `\x00` padding (same semantics as `buffer1024`, built with the same
  `make_fixed_buffer` mold). Added while migrating `PocketNet` to a
  single `protocol.yml` — the `data` field of `PluginMessagePacket`
  (0x35) needed exactly this behavior, and the generic `buffer` with a
  fixed `count` is strict (fails if the value isn't exactly N bytes
  long instead of truncating/padding).
- New example at the time: a complete Minecraft Classic 0.30 + CPE
  protocol (55 packets) migrated from the "one class per packet" style
  into a single declarative file, tested with byte-exact round-trips
  against the original `encode()`/`decode()`. Later removed from
  `examples/` (see "Examples" note below) — it lived in a real project
  outside this package, not as a maintained part of it.

### 0.2.0
- Base version: primitives, composites (`container`, `array`,
  `switch`, `mapper`, `option`, `bitfield`, `bitflags`, `buffer`,
  `pstring`, `count`, `entityMetadataLoop`,
  `topBitSetTerminatedArray`), JSON and YAML support, native NBT and
  UUID.
