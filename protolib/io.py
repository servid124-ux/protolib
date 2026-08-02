"""
protolib/io.py

Primitivas de lectura/escritura de bytes crudos. No conoce nada de
protocol.json: es la capa más baja, sobre la que se construyen los
tipos primitivos y luego los tipos compuestos.
"""

from __future__ import annotations


class BufferUnderrun(Exception):
    """Se intentó leer más bytes de los que quedan disponibles."""

    def __init__(self, offset: int, needed: int, available: int):
        self.offset = offset
        self.needed = needed
        self.available = available
        super().__init__(
            f"buffer agotado en offset={offset}: "
            f"se necesitaban {needed} bytes, quedan {available}"
        )


class Reader:
    """Cursor de lectura sobre un objeto bytes-like."""

    __slots__ = ("buffer", "offset")

    def __init__(self, buffer: bytes | bytearray | memoryview, offset: int = 0):
        self.buffer = buffer
        self.offset = offset

    @property
    def remaining(self) -> int:
        return len(self.buffer) - self.offset

    def ensure(self, n: int) -> None:
        if n < 0:
            raise ValueError(f"cantidad de bytes a leer no puede ser negativa: {n}")
        if self.remaining < n:
            raise BufferUnderrun(self.offset, n, self.remaining)

    def read_bytes(self, n: int) -> bytes:
        self.ensure(n)
        start = self.offset
        self.offset += n
        data = self.buffer[start:start + n]
        return bytes(data) if not isinstance(data, bytes) else data

    def peek_byte(self) -> int:
        self.ensure(1)
        return self.buffer[self.offset]


class Writer:
    """Acumulador de chunks de bytes, concatenados al final con .result()."""

    __slots__ = ("_chunks",)

    def __init__(self):
        self._chunks: list[bytes] = []

    def write_bytes(self, data: bytes) -> None:
        self._chunks.append(data)

    def result(self) -> bytes:
        return b"".join(self._chunks)

    def __len__(self) -> int:
        return sum(len(c) for c in self._chunks)
