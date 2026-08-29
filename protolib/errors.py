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
