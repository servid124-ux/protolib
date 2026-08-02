"""
protolib/errors.py

Excepciones propias del motor, para distinguir errores de protocolo
(datos mal formados / definición de tipo inválida) de errores genéricos
de Python.
"""

from __future__ import annotations


class ProtolibError(Exception):
    """Error base de la librería."""


class UnknownTypeError(ProtolibError):
    def __init__(self, type_name: str):
        self.type_name = type_name
        super().__init__(f"tipo desconocido: \"{type_name}\"")


class InvalidTypeDefinition(ProtolibError):
    def __init__(self, definition: object):
        self.definition = definition
        super().__init__(f"definición de tipo inválida: {definition!r}")


class SwitchCaseNotFound(ProtolibError):
    def __init__(self, compare_to: str, value: object):
        self.compare_to = compare_to
        self.value = value
        super().__init__(
            f"switch sin caso para compareTo=\"{compare_to}\" valor={value!r}"
        )


class ConditionError(ProtolibError):
    def __init__(self, condition: str, reason: str = ""):
        self.condition = condition
        super().__init__(f"condición inválida \"{condition}\": {reason}")
