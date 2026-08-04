<div align="center">

# protolib

**A pure-Python, from-scratch, declarative binary protocol engine**

*Describe any binary protocol's packets in a `.yml`/`.json` file — get `bytes ⟷ dict` for free.*

[![PyPI](https://img.shields.io/pypi/v/protolib.svg)](https://pypi.org/project/protolib/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-156%20passing-brightgreen.svg)](tests/)
[![Style](https://img.shields.io/badge/dependencies-zero%20required-lightgrey.svg)](pyproject.toml)

[Quickstart](#quickstart) •
[Why protolib](#why-protolib) •
[Natives](#natives) •
[Docs](#full-documentation) •
[Changelog](#changelog)

</div>

---

## Why protolib

You have a binary protocol — a game, an IoT sensor stream, a
proprietary file format, an industrial fieldbus, whatever ships bytes
over a wire. Normally that means hand-writing a parser and a
serializer for every single packet type, and keeping both in sync by
hand forever.

`protolib` takes the [node-protodef](https://github.com/ProtoDef-io/ProtoDef)
approach instead: you describe the **shape** of your packets
declaratively, once, and the engine handles both directions.

```python
from protolib import Protocol

proto = Protocol("my_protocol.yml")

parsed = proto.parse_packet("play", "toClient", raw_bytes)
print(parsed.name, parsed.params)

data = proto.serialize_packet("play", "toClient", "spawn_player", {
    "playerId": 5, "x": 320, "y": 2080, "z": 320,
})
```

**This is a general-purpose engine, not a game or Minecraft library.**
It has no idea what a "player" is — it only understands generic
building blocks (`container`, `array`, `switch`, `mapper`, `bitfield`,
fixed/variable-length integers...). Minecraft/ClassiCube shows up in
this repo because that's the protocol family a few primitive *names*
(`nbt`, `fixedCoord`, `string64`) happen to be modeled after — describe
whatever binary format you're actually working with.

## Quickstart

```bash
pip install protolib
```

`pyyaml` is only needed if you're loading `.yml` files — install it
with the `yaml` extra if you want that (JSON and in-memory `dict`
protocols work with zero extra installs):

```bash
pip install "protolib[yaml]"
```

```python
from protolib import Protocol

proto = Protocol("my_protocol.yml")   # .yml, .json, or an in-memory dict/string — same API either way
```

Same protocol, two equivalent forms — pick whichever you prefer to
hand-write (`.yml` is the shorthand; `.json` is the exact format the
engine understands internally, and what `minecraft-data`/
`node-protodef` already ship).

**`my_protocol.yml`:**

```yaml
types:
  varint: native
  container: native
  string64: native

  packet_login:
    container:
    - name: protocolVersion
      type: varint
    - name: username
      type: string64

play:
  toClient:
    types:
      packet:
        container:
        - name: name
          type:
            mapper:
              type: varint
              mappings: { '0x00': login }
        - name: params
          type:
            switch:
              compareTo: name
              fields: { login: packet_login }
```

**The exact same protocol as `my_protocol.json`** — each composite
type becomes a `["name", options]` pair instead of a single-key
mapping:

```json
{
  "types": {
    "varint": "native",
    "container": "native",
    "string64": "native",
    "packet_login": [
      "container",
      [
        { "name": "protocolVersion", "type": "varint" },
        { "name": "username", "type": "string64" }
      ]
    ]
  },
  "play": {
    "toClient": {
      "types": {
        "packet": [
          "container",
          [
            {
              "name": "name",
              "type": [
                "mapper",
                {
                  "type": "varint",
                  "mappings": { "0x00": "login" }
                }
              ]
            },
            {
              "name": "params",
              "type": [
                "switch",
                {
                  "compareTo": "name",
                  "fields": { "login": "packet_login" }
                }
              ]
            }
          ]
        ]
      }
    }
  }
}
```

Both serialize/parse **byte-for-byte identically** — the `.json` above
was generated straight from the `.yml` via
`protolib.loader.load_protocol_dict`, never hand-translated, so they
can't drift apart. Confirmed: `Protocol("my_protocol.yml")` and
`Protocol("my_protocol.json")` produce the exact same bytes for the
same `serialize_packet(...)` call.

**Real switch, more than one case** — the `login`-only example above
has a single entry in `mappings`/`fields`. Here's the actual
`packet` block from `examples/example_protocol.yml`, three packet
types sharing one `switch`, same side-by-side:

```yaml
# .yml — the actual packet type from examples/example_protocol.yml
play:
  toClient:
    types:
      packet:
        container:
        - name: name
          type:
            mapper:
              type: varint
              mappings:
                '0x00': ejemplo_jugadores
                '0x01': ejemplo_metadata
                '0x02': ejemplo_natives
        - name: params
          type:
            switch:
              compareTo: name
              fields:
                ejemplo_jugadores: packet_ejemplo_jugadores
                ejemplo_metadata: packet_ejemplo_metadata
                ejemplo_natives: packet_ejemplo_natives
```

```json
{
  "play": {
    "toClient": {
      "types": {
        "packet": [
          "container",
          [
            {
              "name": "name",
              "type": [
                "mapper",
                {
                  "type": "varint",
                  "mappings": {
                    "0x00": "ejemplo_jugadores",
                    "0x01": "ejemplo_metadata",
                    "0x02": "ejemplo_natives"
                  }
                }
              ]
            },
            {
              "name": "params",
              "type": [
                "switch",
                {
                  "compareTo": "name",
                  "fields": {
                    "ejemplo_jugadores": "packet_ejemplo_jugadores",
                    "ejemplo_metadata": "packet_ejemplo_metadata",
                    "ejemplo_natives": "packet_ejemplo_natives"
                  }
                }
              ]
            }
          ]
        ]
      }
    }
  }
}
```

Same rule: `mappings` picks the packet name from the wire integer,
`switch.fields` picks its payload type from that name — add a fourth
packet type by adding one entry to each, no other code changes.

## Natives

61 primitive types ship out of the box — the full fixed-width integer
family (`u8`…`i64`, big- and little-endian), floats down to half
precision, LEB128 varints (signed, unsigned, zigzag, and 128-bit),
strings in three encodings, NBT (named and anonymous), and a handful
of fixed-size buffers/fixed-point types from the ClassiCube protocol.

| Category | Names |
|---|---|
| Integers | `{u,i}{8,16,24,32,40,48,56,64}` + little-endian `l` variants (32 names) |
| Floats | `f16`, `f32`, `f64` + `lf16`, `lf32`, `lf64` |
| Varints | `varint`/`varlong`, `uvarint`/`uvarlong`, `varint128`/`uvarint128`, `zigzag32`/`zigzag64` |
| Strings | `cstring` (`\0`-terminated), `string64` (CP437, fixed 64 bytes), `utf16be64` (UTF-16BE, fixed 64 chars) |
| NBT | `nbt`/`optionalNbt` (named), `anonymousNbt`/`anonOptionalNbt` (no name prefix) |
| Misc | `bool`, `void`, `UUID`, `restBuffer`, `buffer64`/`buffer1024`, `fixedCoord`/`fixedCoordDelta` |

Need one that isn't here? Register your own in `primitives.py` — see
`examples/example_protocol.yml` (`packet_ejemplo_natives`, Pattern 5)
for a live, runnable reference showing every single one used as an
actual field, with matching Python values in `examples/demo.py`.

```python
from protolib.primitives import PRIMITIVES
print(len(PRIMITIVES))   # 61
```

## Composites

Beyond primitives, the engine resolves these structural types:
`container`, `array`, `switch`, `mapper`, `bitfield`, `bitflags`,
`buffer`, `pstring`, `option`, `count`, `entityMetadataLoop`,
`topBitSetTerminatedArray`, `registryEntryHolder`,
`registryEntryHolderSet`. Full semantics and examples for each one are
in the main README (see below).

## Full documentation

This file is the GitHub-facing quick tour. The complete reference —
every composite type explained field-by-field, the native JSON format
without YAML shorthand, `condition`/`compareTo` expression grammar,
the `protolib.nbt`/`protolib.loader`/`protolib.conditions` module
APIs, and the full historical changelog — lives in
[`README.md`](README.md).

## Testing

```bash
python -m unittest discover -s tests
```

156 tests, plain `unittest` (also `pytest`-compatible), covering every
module and both bundled example protocols end-to-end, byte-for-byte.

## Changelog

Latest highlights — full history in [`CHANGELOG.md`](CHANGELOG.md):

- **`utf16be64`** — fixed 64-character UTF-16BE string, ready to use by
  name (the factory existed before, this is the registered instance).
- **`f16`/`lf16`** — half-float (IEEE 754 binary16), completing the
  float family alongside `f32`/`f64`.
- **`examples/example_protocol.yml`/`.json`** — Pattern 5: every single
  native used as a real field in one packet, for quick reference.
