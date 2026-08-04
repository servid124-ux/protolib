"""
protolib/io.py

Raw byte read/write primitives. Knows nothing about protocol.json:
it's the lowest layer, on top of which the primitive types and then
the composite types are built.
"""

from __future__ import annotations


class BufferUnderrun(Exception):
    """Tried to read more bytes than are currently available."""

    def __init__(self, offset: int, needed: int, available: int):
        self.offset = offset
        self.needed = needed
        self.available = available
        super().__init__(
            f"buffer exhausted at offset={offset}: "
            f"needed {needed} bytes, {available} remaining"
        )


class Reader:
    """Read cursor over a bytes-like object."""

    __slots__ = ("buffer", "offset")

    def __init__(self, buffer: bytes | bytearray | memoryview, offset: int = 0):
        self.buffer = buffer
        self.offset = offset

    @property
    def remaining(self) -> int:
        return len(self.buffer) - self.offset

    def ensure(self, n: int) -> None:
        if n < 0:
            raise ValueError(f"number of bytes to read cannot be negative: {n}")
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
    """Accumulator of byte chunks, concatenated at the end via .result()."""

    __slots__ = ("_chunks",)

    def __init__(self):
        self._chunks: list[bytes] = []

    def write_bytes(self, data: bytes) -> None:
        self._chunks.append(data)

    def result(self) -> bytes:
        return b"".join(self._chunks)

    def __len__(self) -> int:
        return sum(len(c) for c in self._chunks)
