"""
protolib/errors.py

Custom engine exceptions, to distinguish protocol errors (malformed
data / invalid type definition) from generic Python errors.
"""

from __future__ import annotations


class ProtolibError(Exception):
    """Base error for the library."""


class UnknownTypeError(ProtolibError):
    def __init__(self, type_name: str):
        self.type_name = type_name
        super().__init__(f"unknown type: \"{type_name}\"")


class InvalidTypeDefinition(ProtolibError):
    def __init__(self, definition: object):
        self.definition = definition
        super().__init__(f"invalid type definition: {definition!r}")


class SwitchCaseNotFound(ProtolibError):
    def __init__(self, compare_to: str, value: object):
        self.compare_to = compare_to
        self.value = value
        super().__init__(
            f"switch has no case for compareTo=\"{compare_to}\" value={value!r}"
        )


class ConditionError(ProtolibError):
    def __init__(self, condition: str, reason: str = ""):
        self.condition = condition
        super().__init__(f"invalid condition \"{condition}\": {reason}")


class InvalidMapperKeyError(ProtolibError):
    """
    Raised when a 'mapper' type's `mappings` dict has a key that can't
    be normalized to an integer (e.g. a typo like "0xZZ", or a stray
    non-numeric string). Before 0.4.0, this surfaced as a bare
    ValueError from int(key, 16) / int(key), raised the first time the
    mapper was actually used to read a packet -- not when the protocol
    definition was loaded -- with no indication of which mapper or
    which key was the culprit, making a simple YAML typo tedious to
    track down in a protocol with many mappers.
    """
    def __init__(self, key: object, mappings: dict, reason: str):
        self.key = key
        self.mappings = mappings
        super().__init__(
            f"mapper: key {key!r} in mappings {mappings!r} is not a valid "
            f"integer or hex string ({reason})"
        )


class MapperValueNotFoundError(ProtolibError):
    """
    Raised by 'mapper' when the raw value read has no entry in
    'mappings' (reading) or when the symbolic name being written has no
    inverse entry (writing). Parity with node-protodef's utils.js
    readMapper/writeMapper, which throw in both cases instead of
    silently passing the raw value through -- a mapper is usually an
    enum-like closed set (entity types, block faces, chat positions...)
    where an unknown value is a real protocol desync or a stale table,
    not something safe to paper over.
    """
    def __init__(self, value: object, mappings: dict, *, writing: bool = False):
        self.value = value
        self.mappings = mappings
        direction = "write" if writing else "read"
        super().__init__(
            f"mapper ({direction}): {value!r} is not in the mappings value"
        )


class NBTDecompressionError(ProtolibError):
    """
    Raised by the 'compressedNbt' primitive when the gzip payload
    can't be decompressed (corrupted/truncated bytes, or a segment
    that isn't actually gzip despite the length prefix matching).
    Parity with node-minecraft-protocol's compressedNbt reader, which
    wraps the zlib failure into the library's own error type instead
    of letting a raw zlib.error/OSError escape.
    """
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"compressedNbt: gzip decompression failed: {reason}")


class StatelessProtocolError(ProtolibError):
    """
    Raised by parse_packet/serialize_packet/read_named/write_named when
    the state/direction arguments don't match whether the loaded
    protocol actually declares states at all.

    Some real protocols (e.g. Minecraft Bedrock's protocol.json, unlike
    Java's) have NO top-level state keys (handshaking/status/login/...)
    -- every type, including the packet dispatcher, lives directly in
    the global `types:` block. For those, state/direction must be
    passed as None (there's nothing to select between). Passing a real
    state name to a stateless protocol, or None to a protocol that DOES
    declare states, is a caller mistake worth catching explicitly
    instead of surfacing as a confusing UnknownTypeError deep inside
    scope resolution.
    """
    def __init__(self, *, stateless: bool, state: object, direction: object):
        self.stateless = stateless
        self.state = state
        self.direction = direction
        if stateless:
            super().__init__(
                f"this protocol declares no states (e.g. Minecraft Bedrock-style "
                f"protocol.json, everything under a single 'types:' block) -- "
                f"call with state=None, direction=None instead of "
                f"state={state!r}, direction={direction!r}"
            )
        else:
            super().__init__(
                f"this protocol declares states (e.g. Minecraft Java-style "
                f"protocol.json, with handshaking/status/login/play blocks) -- "
                f"state and direction must be real state/direction names, "
                f"not state={state!r}, direction={direction!r}"
            )


class BitfieldOverflowError(ProtolibError):
    """
    Raised by 'bitfield' when writing a sub-field whose value doesn't
    fit in its declared 'size' bits. Before 0.4.0, an out-of-range
    value was silently masked (`value & ((1 << size) - 1)`), which
    quietly corrupts the packet instead of failing where the mistake
    actually is -- inconsistent with the rest of the library, where a
    fixed-width int (primitives.py, via struct.pack) or a fixed-count
    buffer (core.py: _write_buffer) already raise loudly on a
    size/value mismatch instead of truncating.
    """
    def __init__(self, field_name: str, value: int, size: int, *, signed: bool = False):
        self.field_name = field_name
        self.value = value
        self.size = size
        self.signed = signed
        if signed:
            lo, hi = -(1 << (size - 1)), (1 << (size - 1)) - 1
        else:
            lo, hi = 0, (1 << size) - 1
        super().__init__(
            f"bitfield: field \"{field_name}\" value {value!r} does not fit in "
            f"{size} bit{'s' if size != 1 else ''} "
            f"({'signed' if signed else 'unsigned'}, valid range [{lo}, {hi}])"
        )


class MagicMismatchError(ProtolibError):
    """
    Raised by the 'raknetMagic' primitive when the 16 bytes read from
    the wire don't match RakNet's fixed magic sequence
    (00 FF FF 00 FE FE FE FE FD FD FD FD 12 34 56 78). In the real
    RakNet protocol this constant marks OFFLINE packets (UnconnectedPing,
    OpenConnectionRequest1/2, etc.) and its whole purpose is to let an
    implementation immediately reject anything that isn't a genuine
    RakNet datagram -- silently accepting a mismatch here would defeat
    that check, so unlike a plain fixed-size buffer, this primitive
    validates on read instead of returning whatever bytes were present.
    """
    def __init__(self, expected: bytes, got: bytes):
        self.expected = expected
        self.got = got
        super().__init__(
            f"raknetMagic: expected {expected.hex()}, got {got.hex()} "
            f"-- not a valid RakNet packet (magic mismatch)"
        )
