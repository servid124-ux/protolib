"""
protolib/framer.py

Minecraft (and many similar protocols) wrap each packet as:

    [varint: length][payload of `length` bytes]

This module knows NOTHING about protocol.json: it just splits a raw
byte stream (the kind arriving from a socket) into complete "frames",
and builds the length-prefix when sending. Parsing each frame into
{name, params} is done separately by `Protocol.parse_packet()`, at
another layer.

It does not implement compression (login threshold) or encryption: if
your protocol needs those, add them as an intermediate layer between
the socket and the framer (decompress/decrypt the frame before passing
it to Protocol.parse_packet).
"""

from __future__ import annotations

from .io import Reader, Writer
from .primitives import PRIMITIVES

_varint = PRIMITIVES["varint"]


class PacketFramer:
    """Accumulates raw bytes from a socket and returns complete frames
    as they become available."""

    def __init__(self):
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        """
        Feeds newly arrived bytes. Returns the list of complete frames
        that could be extracted (may be empty, one, or several if
        multiple packets arrived stuck together in the same socket
        chunk).
        """
        self._buffer += chunk
        frames: list[bytes] = []

        while True:
            r = Reader(self._buffer)
            try:
                length = _varint.read(r)
            except Exception:
                # not enough bytes have arrived yet even for the length varint
                break

            if length < 0:
                # Minecraft's varint length-prefix is read AS SIGNED
                # (to match the real protocol), so a value with the
                # high bit set decodes to negative. A peer sending that
                # is broken or adversarial -- if we let it through, the
                # comparison below (remaining buffer < length) would be
                # false for any negative value, so "missing bytes"
                # would never be detected and an empty or misaligned
                # frame would be cut, corrupting the framing of ALL
                # subsequent packets on the same connection. Better to
                # cut the connection here.
                raise ValueError(
                    f"negative length-prefix ({length}): peer is broken or "
                    f"malicious, cannot keep parsing this stream"
                )

            header_size = r.offset
            if len(self._buffer) - header_size < length:
                # the packet hasn't fully arrived yet
                break

            frame = self._buffer[header_size:header_size + length]
            frames.append(frame)
            self._buffer = self._buffer[header_size + length:]

        return frames

    @staticmethod
    def wrap(frame: bytes) -> bytes:
        """Wraps an already-serialized frame (without length-prefix) by
        adding the varint-length-prefix, ready for socket.send()/write()."""
        w = Writer()
        _varint.write(len(frame), w)
        w.write_bytes(frame)
        return w.result()
