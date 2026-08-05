# protolib

Pure-Python, `node-protodef`-style declarative binary protocol
(de)serializer. Describe a protocol's packets in a `.yml`/`.json` file,
get `bytes -> dict` and `dict -> bytes` for free.

```python
from protolib import Protocol

proto = Protocol("my_protocol.yml")
parsed = proto.parse_packet("play", "toClient", raw_data)
data = proto.serialize_packet("play", "toClient", "spawn_player", {"x": 320})
```

This document covers three things: **(1)** how to write a protocol
file in YAML/JSON, **(2)** every `native` primitive type and how to use
it, **(3)** the full Python API.

---

## 1. Protocol files: YAML and JSON

### 1.1 Overall shape

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
      # PRIORITY over the global ones if there's a name collision
      packet: ...
  toServer:
    types:
      packet: ...
```

- **global `types:`** — shared types (primitives, common structs).
- **`<state>.toClient.types` / `<state>.toServer.types`** — the entry
  point to parse a packet is always a type named `packet` inside one
  of these blocks. `parse_packet(state, direction, data)` looks for
  that `packet`, reads it, and returns `{name, params}`.
- `state`/`direction` aren't special keywords — they're just the two
  strings you'll later pass to `parse_packet`/`serialize_packet`. A
  protocol without real "states" still declares a single block (e.g.
  `play:`) just to hang `toClient`/`toServer` off of it.

**The `packet` type must always have exactly two fields, `name` and
`params`** — that's what builds the `ParsedPacket(name=..., params=...)`
you get back. Standard pattern:

```yaml
packet:
  container:
  - name: name
    type:
      mapper:               # wire integer -> packet name
        type: u8            # (or varint, depending on your protocol)
        mappings:
          '0x00': identification
  - name: params
    type:
      switch:                # packet name -> its payload type
        compareTo: name
        fields:
          identification: packet_identification
```

If `packet` doesn't follow this exact shape, `parse_packet` fails with
`KeyError: 'name'` — that's how the library knows what to return, not
an arbitrary restriction.

### 1.2 YAML shorthand vs. native JSON

Internally, every composite type is a 2-element list:
`["baseType", options]` — that's the original `node-protodef`/
`minecraft-data` JSON format, understood as-is with no translation:

```json
"packet_identification": [
  "container",
  [
    { "name": "protocolVersion", "type": "u8" },
    { "name": "name", "type": "string64" }
  ]
]
```

The YAML loader (`protolib/loader.py`) auto-translates the more
readable single-key-mapping shorthand into that exact form:

```yaml
type:
  switch:
    compareTo: name
    fields:
      identification: packet_identification
```

`example_protocol.yml` and `example_protocol.json` are mechanically
equivalent — one is generated from the other, so they round-trip
byte-for-byte identically. Only mappings whose single key matches a
known composite type name (`container`, `array`, `switch`, `mapper`,
`option`, `bitfield`, `bitflags`, `buffer`, `pstring`, `count`,
`entityMetadataLoop`, `topBitSetTerminatedArray`, `cstring`, plus
anything passed via `extra_composite_names`) get this treatment —
everything else (plain type names, options that aren't composites) is
left as-is.

Use the raw `.json` form directly when: you already have a protocol
from `minecraft-data` in JSON and don't want to rewrite it; you're
generating the protocol from another language/script; or you want to
see exactly what the engine understands with zero ambiguity (useful
when debugging a `.yml` that isn't loading as expected).

Convert `.yml` → `.json` using the loader itself (guarantees fidelity,
since it's the same code path `Protocol` uses):

```python
import json
from protolib.loader import load_protocol_dict

d = load_protocol_dict("my_protocol.yml")
json.dump(d, open("my_protocol.json", "w"), indent=2, ensure_ascii=False)
```

Convert `.json` → `.yml` (best-effort, for migrating an existing
`minecraft-data`-style file):

```python
from protolib.loader import protocol_dict_to_yaml
import json
raw = json.load(open("protocol.json"))
open("protocol.yml", "w").write(protocol_dict_to_yaml(raw))
```

### 1.3 Composite types, one by one

**`container`** — groups named fields, in order:

```yaml
packet_login:
  container:
  - name: protocolVersion
    type: varint
  - name: username
    type: string
```
Reads as `{"protocolVersion": ..., "username": ...}`.
- `anon: true` on a field — instead of nesting under its own name, its
  sub-fields get merged directly into the parent `dict`. Used to
  "flatten" a bitfield or switch into loose sibling fields.
- `condition: <expr>` on a field — only read/written if the expression
  (same grammar as `eval_condition`, see §3.6) is true.

**`array`** — list of N elements of the same type:

```yaml
type:
  array:
    countType: varint    # reads a varint first, that's the length
    # count: otherField  # alternative: uses the VALUE of an already-read field
    type: myItemType
```
`countType` prepends an integer that's read/written automatically.
`count` references a sibling field (typically built with `count`
below) — reads its existing value as the length, writes nothing extra.

**`switch`** — picks the field's type based on another field's value:

```yaml
type:
  switch:
    compareTo: name        # path to an already-read field
    fields:
      valueA: typeForA
      valueB: typeForB
    default: void           # optional, if nothing matches
```
`compareTo` accepts a plain field name, `../field` (parent container),
`/field` (absolute root), or a full boolean expression starting with
`fields.`. `compareToValue` can be used instead for a fixed literal.

**`mapper`** — integer ⟷ symbolic name:

```yaml
type:
  mapper:
    type: varint
    mappings:
      '0x00': handshake
      '0x01': status_request
```
A `0x00` on the wire becomes the string `"handshake"` when parsed (and
back when serializing) — lets a `switch` compare against readable
names instead of raw ids.

**`bitfield`** — several fields packed into less than 1 byte each:

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
Sub-fields take up `size` bits, MSB first, within the minimum number
of bytes needed (padded if they don't fill one exactly). Usually
combined with `anon: true` on the enclosing container.

**`bitflags`** — an integer as a named set of booleans:

```yaml
type:
  bitflags:
    type: u8
    flags: [air, water, lava, null, fire]   # position = bit number
    # or: {air: 0x01, water: 0x02, ...}
```
Returns `{"air": true, "water": false, ..., "_value": <raw integer>}`.

**`buffer`** — raw, uninterpreted bytes:

```yaml
type:
  buffer:
    count: 1024        # fixed size, or a reference to a field
    # countType: varint  # alternative: prefixed length
    # rest: true         # alternative: everything left in the frame
```

**`option`** — present only if a preceding boolean flag says so:

```yaml
type:
  option: myType
```
Reads a `bool` first; `true` → reads `myType` right after; `false` →
value is `None`, nothing else read.

**`pstring`** — string with a prefixed length:

```yaml
type:
  pstring:
    countType: varint     # how many length-bytes come first
    # encoding: utf-8      # default
```
The `string:` shortcut is this pre-built with `countType: varint`
(Minecraft Java-style). For fixed-size, unprefixed strings, see
`string64`/`utf16be64` in §2.

**`count`** — a length-prefix declared as its own sibling field (not
attached to the array/buffer it counts):

```yaml
type:
  count:
    type: u8              # or varint, etc — how the count itself is encoded
    countFor: ../items     # path to the field whose length this represents
```
On read, just reads an integer. On write, ignores whatever value was
passed in and writes `len(field at countFor)` instead.

**`entityMetadataLoop`** — list of entries until a terminator byte:

```yaml
type:
  entityMetadataLoop:
    endVal: 0xff        # default; raw byte marking "no more entries"
    endType: u8          # default; 1-byte type used only for the check
    type: entityMetadataItem
```
Reads entries until the next unread byte (peeked, not consumed) equals
`endVal`, then consumes that terminator and stops.

**`topBitSetTerminatedArray`** — list that ends when the high bit isn't set:

```yaml
type:
  topBitSetTerminatedArray:
    type: myVarintLikeItem
```
Reads entries while the high bit (`0x80`) of each entry's first byte
is set; stops right after the first entry whose first byte doesn't
have it set. When writing, the caller must set that bit correctly on
every item except the last — it's part of each item's own encoding,
not added by this wrapper.

**`registryEntryHolder`** — modern Minecraft's `IdOr<T>`:

```yaml
type:
  registryEntryHolder:
    idType: varint         # default
    otherwise:
      type: myInlineType    # read/written only when the raw id is 0
```
`id == 0` → inline `otherwise.type` value follows. `id != 0` (`id = n`)
→ reference to registry index `n - 1`. Python shape:
`{"type": "inline", "value": ...}` or `{"type": "reference", "id": N}`.

**`registryEntryHolderSet`** — modern Minecraft's `HolderSet<T>`:

```yaml
type:
  registryEntryHolderSet:
    idType: varint         # default
```
Wire: `[varint count]`; `count == 0` → followed by a `cstring` tag name
(e.g. `"#minecraft:trim_materials"`); `count > 0` → followed by exactly
`count` ids (no +1/-1 shift here). Python shape:
`{"type": "tag", "tagName": "..."}` or `{"type": "ids", "ids": [...]}`.

**`cstring` (composite form)** — same null-terminated string as the
native `cstring`, but with a configurable encoding:

```yaml
type:
  cstring:
    encoding: utf-8   # default
```

---

## 2. `native` types: the primitive catalog

`native` in the `.yml` means "resolve this by name in
`protolib/primitives.py`" instead of defining it inline. Every name
below is usable directly as `type: <name>` anywhere in a protocol
file, and is also importable from `protolib.PRIMITIVES` in Python.

### Fixed-width integers

Full family: `{u,i} × {8,16,24,32,40,48,56,64}`, big-endian by
default, plus an `l`-prefixed little-endian variant of each
(`lu8, li8, ..., lu64, li64`) — 32 names total.

| Name | Size | Notes |
|---|---|---|
| `u8`/`i8` | 1 byte | unsigned/signed |
| `u16`/`i16` | 2 bytes | big-endian |
| `u24`/`i24` | 3 bytes | big-endian; RakNet split-packet count, message index, etc. |
| `u32`/`i32` | 4 bytes | big-endian |
| `u40`/`i40` | 5 bytes | big-endian |
| `u48`/`i48` | 6 bytes | big-endian |
| `u56`/`i56` | 7 bytes | big-endian |
| `u64`/`i64` | 8 bytes | big-endian |
| `lu8`…`li64` | same sizes | `l` + any of the above = little-endian (e.g. `lu24`, `li48`) |

```yaml
- name: entityId
  type: i32
- name: sequenceNumber
  type: lu24    # little-endian, 3 bytes (RakNet-style)
```

### Floats

| Name | Size | Notes |
|---|---|---|
| `f16` | 2 bytes | IEEE 754 half-float, ~3 decimal digits precision |
| `f32` | 4 bytes | single precision |
| `f64` | 8 bytes | double precision |
| `lf16`/`lf32`/`lf64` | same | little-endian variants |

### Variable-length integers

| Name | Notes |
|---|---|
| `varint`/`varlong` | **signed** LEB128, no zigzag applied |
| `uvarint`/`uvarlong` | unsigned LEB128 |
| `varint128`/`uvarint128` | same LEB128 scheme extended to 128 bits (up to 19 bytes) — IDs too big for 64 bits (Snowflake-style IDs, truncated hashes) |
| `zigzag32`/`zigzag64` | true zigzag LEB128 (Protocol Buffers style: `0,-1,1,-2,2 -> 0,1,2,3,4`) |

```yaml
- name: keepAliveId
  type: varlong
- name: deltaX
  type: zigzag32
```

### Bool / void / strings

| Name | Size | Notes |
|---|---|---|
| `bool` | 1 byte | |
| `void` | 0 bytes | useful as an empty `switch` case |
| `cstring` | variable | `\0`-terminated, UTF-8 |
| `restBuffer` | variable | all remaining bytes in the buffer, no length prefix — put it last in a container |

### UUID

| Name | Size | Notes |
|---|---|---|
| `UUID` | 16 bytes | reads/writes as the dashed string form (`"1111...1111"`); on the wire it's just the 16 raw bytes |

### NBT

| Name | Notes |
|---|---|
| `nbt` | Minecraft's NBT format, **with** name prefix (`[u16 nameLen][name][payload]`) |
| `optionalNbt` | identical format/alias of `nbt`, semantically "may legitimately be absent" (`None` reads/writes as a lone `TAG_End`) |
| `anonymousNbt` | same payload, **without** the name prefix — modern Minecraft (1.20.2+) chat components, item `custom_data`, `block_entity_data` |
| `anonOptionalNbt` | alias of `anonymousNbt`, same "may be absent" semantics |

`nbt`/`optionalNbt` decode to `{"name": str, "type": "compound"|"int"|"list"|..., "value": ...}` (or `None` for `TAG_End`).
`anonymousNbt`/`anonOptionalNbt` decode to the same shape **without** the `"name"` key, so the two families can never be confused with each other.

```yaml
- name: entityData
  type: nbt
- name: customData
  type: anonymousNbt
```

### Fixed-size strings/buffers (ClassiCube-style, always fixed length, never prefixed)

| Name | Size | Notes |
|---|---|---|
| `string64` | 64 bytes | CP437-encoded, space-padded/truncated to exactly 64 bytes |
| `utf16be64` | 128 bytes | UTF-16BE, 64 **characters** (128 bytes on the wire), space-padded |
| `buffer1024` | 1024 bytes | raw bytes, `\x00`-padded/truncated |
| `buffer64` | 64 bytes | raw bytes, `\x00`-padded/truncated |

Need a different fixed length? Build your own with the factory
functions (not registered under a name, you assign one):

```python
from protolib.primitives import make_fixed_cp437_string, make_fixed_buffer
from protolib import make_fixed_utf16be_string

my_string32 = make_fixed_cp437_string(32)   # CP437, 32 bytes
my_buffer256 = make_fixed_buffer(256)       # raw, 256 bytes
my_name16 = make_fixed_utf16be_string(16)   # UTF-16BE, 16 chars / 32 bytes
```

### Fixed-point (ClassiCube)

| Name | Size | Notes |
|---|---|---|
| `fixedCoord` | 2 bytes | signed, same layout as `i16`; the raw wire value is `real_value * 32` |
| `fixedCoordDelta` | 1 byte | signed, same layout as `i8`; used in compressed position packets (deltas between updates) |

### Adding your own native primitive

If you need a type that isn't a composition of the above (a
differently-encoded fixed string, a buffer of another fixed size not
worth a factory call every time, etc.), follow the pattern of
`make_fixed_cp437_string`/`make_fixed_buffer` in `primitives.py` and
register the result in the `PRIMITIVES` dict at the end of that file —
it becomes usable as `native`/by name in any `.yml` afterward.

---

## 3. Full API reference

### 3.1 `protolib.Protocol` — the main entry point

```python
from protolib import Protocol

Protocol(protocol_source: dict | str, *, fmt: str | None = None)
```
`protocol_source` accepts: a path to `.json`/`.yml`/`.yaml`; an
in-memory string with JSON or YAML content; or an already-parsed
`dict` in native `["type", opts]` form. `fmt` forces `"json"`/`"yaml"`
instead of autodetecting (useful for in-memory content with no
reliable extension).

> **Relative paths** resolve against the process's current working
> directory, not the calling script's location. If the file genuinely
> isn't found, `Protocol(...)` raises `LoaderError` naming the path it
> looked for and the cwd used. To make a script location-independent:
> ```python
> import os
> here = os.path.dirname(os.path.abspath(__file__))
> proto = Protocol(os.path.join(here, "my_protocol.yml"))
> ```

**High-level packet API:**

```python
proto.parse_packet(state: str, direction: str, data: bytes) -> ParsedPacket
proto.serialize_packet(state: str, direction: str, name: str, params: dict) -> bytes
```
`ParsedPacket` is a dataclass: `name: str`, `params: dict`,
`bytes_read: int` (how many bytes of `data` were actually consumed —
compare against `len(data)` to check for leftover/misaligned bytes).

**Direct type access** (bypassing the `packet` entry point — useful
for tests, or for parsing/serializing a standalone sub-structure like
a single slot or NBT tag):

```python
proto.read_named(state: str, direction: str, type_name: str, data: bytes) -> Any
proto.write_named(state: str, direction: str, type_name: str, value: Any) -> bytes
```

**Low-level, type-definition-level API** (what the above are built
on; useful when embedding a read/write inside your own code that
already holds a `Reader`/`Writer`):

```python
proto.read_type(type_def, reader, scope, fields, root=None, parent=None) -> Any
proto.write_type(type_def, value, writer, scope, fields, root=None, parent=None) -> None
proto.get_scope(state: str, direction: str) -> Scope
```

`Scope` is a dataclass wrapping `types: dict[str, TypeDef]` — the
`state.direction`-local type table, checked before falling back to the
protocol's global `types:`.

### 3.2 `protolib.Reader` / `protolib.Writer` — raw byte I/O

```python
from protolib import Reader, Writer, BufferUnderrun

r = Reader(buffer: bytes, offset: int = 0)
r.remaining          # property: len(buffer) - offset
r.ensure(n: int)      # raises BufferUnderrun if fewer than n bytes remain
r.read_bytes(n: int) -> bytes
r.peek_byte() -> int  # reads without advancing the cursor

w = Writer()
w.write_bytes(data: bytes)
w.result() -> bytes   # concatenates every chunk written so far
len(w)                 # total bytes written so far
```
`BufferUnderrun(offset, needed, available)` is raised by `Reader` when
asked to read more bytes than remain.

### 3.3 `protolib.PRIMITIVES` / `protolib.Primitive`

```python
from protolib import PRIMITIVES, Primitive

PRIMITIVES["varint"].read(reader) -> Any
PRIMITIVES["varint"].write(value, writer) -> None
PRIMITIVES["varint"].size_of(value) -> int | None
```
`Primitive` is `@dataclass(frozen=True)` with 4 fields: `name`,
`read(reader) -> Any`, `write(value, writer) -> None`, and optional
`size_of(value) -> int | None`.

### 3.4 `protolib.PacketFramer` — length-prefixed streaming

For protocols that wrap each packet as `[varint length][payload]`
(Minecraft-style). Knows nothing about the protocol file — just splits
a raw socket byte stream into complete frames. Doesn't implement
compression or encryption; add those as a layer between the socket and
the framer if your protocol needs them.

```python
from protolib import PacketFramer

framer = PacketFramer()
frames: list[bytes] = framer.feed(chunk: bytes)   # 0, 1, or several complete frames
raw = PacketFramer.wrap(frame: bytes) -> bytes     # adds the varint length-prefix
```
`feed()` raises `ValueError` if a negative length-prefix is decoded
(broken or adversarial peer) — the connection should be dropped in
that case, since the framing of all subsequent packets can no longer
be trusted.

### 3.5 `protolib.loader` — loading and format conversion

```python
from protolib import load_protocol_dict, protocol_dict_to_yaml, LoaderError

load_protocol_dict(
    source: str | dict,
    *,
    fmt: str | None = None,
    extra_composite_names: frozenset[str] | None = None,
) -> dict
```
Same loading rules as `Protocol(...)` (§3.1); this is what `Protocol`
calls internally. `extra_composite_names` lets the YAML shorthand
recognize composite types you added yourself beyond the built-in set.

```python
protocol_dict_to_yaml(protocol_dict: dict) -> str
```
Inverse, best-effort operation: native `["type", opts]` dict → readable
YAML shorthand text. Meant to kick off a JSON→YAML migration, not
guaranteed to be byte-for-byte invertible.

`LoaderError` (subclass of `ProtolibError`) — file not found, invalid
JSON/YAML syntax, or PyYAML not installed when a `.yml` is requested.

### 3.6 `protolib.eval_condition` — the `condition`/expression grammar

```python
from protolib import eval_condition

eval_condition(expr: str, fields: dict, root: dict | None = None,
                parent: dict | None = None) -> bool
```
Deliberately **not** `eval()`/`exec()` — a small parser for a safe
subset of JS-like boolean expressions:

```
fields.someField === 1
fields.type !== 0 && fields.flag == true
$root.version >= 47
$parent.hasData
(fields.a > 0) || (fields.b < 10)
```
Operators: `=== !== == != >= <= > < && ||`. Paths: `fields.x`,
`$root.x`, `$parent.x`, with `[N]` index support. A single operand with
no comparison is evaluated for truthiness (JS/Python semantics).
Raises `ConditionError` on an invalid expression.

### 3.7 `protolib.nbt` — NBT read/write functions

```python
from protolib import read_nbt, write_nbt, NBTError
from protolib.io import Reader, Writer

tag = read_nbt(Reader(raw_bytes))
# -> {"name": str, "type": "compound"|"int"|"list"|..., "value": ...} | None
#    (None only if the first byte is TAG_End)

w = Writer()
write_nbt(tag, w)
raw_bytes_back = w.result()
```
`read_optional_nbt`/`write_optional_nbt` — identical aliases, named to
match `optionalNbt` from `node-minecraft-protocol`.

```python
from protolib import read_anonymous_nbt, write_anonymous_nbt

tag = read_anonymous_nbt(Reader(raw_bytes))
# -> {"type": "compound"|"int"|"list"|..., "value": ...} | None  (no "name" key)

w = Writer()
write_anonymous_nbt(tag, w)
```
`read_anon_optional_nbt`/`write_anon_optional_nbt` — aliases, same
relationship to `anonymousNbt` as `optionalNbt` has to `nbt`.
`NBTError` is raised on an unknown tag type, etc.

### 3.8 `protolib.errors` — every exception

All inherit from `ProtolibError` (itself an `Exception`):

| Exception | Raised when |
|---|---|
| `UnknownTypeError(type_name)` | a type name isn't a primitive, and isn't found in the local scope or global `types:` |
| `InvalidTypeDefinition(definition)` | a type definition isn't a recognized string or 2-element list |
| `SwitchCaseNotFound(compare_to, value)` | a `switch` has no matching `fields` entry and no `default` |
| `ConditionError(condition, reason)` | a `condition`/`eval_condition` expression is malformed |
| `LoaderError` | protocol file not found, invalid JSON/YAML, or PyYAML missing (`protolib.loader`) |
| `NBTError` | malformed NBT data (`protolib.nbt`) |

```python
from protolib import (
    ProtolibError, UnknownTypeError, InvalidTypeDefinition,
    SwitchCaseNotFound, ConditionError, LoaderError, NBTError,
)
```

---

## Install

```bash
pip install protolib          # core, JSON only
pip install protolib[yaml]    # + YAML support
```
Python 3.9+, zero required dependencies.
