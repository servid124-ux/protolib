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
