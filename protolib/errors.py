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
