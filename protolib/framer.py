"""
protolib/framer.py

Minecraft (y muchos protocolos similares) envuelven cada paquete como:

    [varint: length][payload de `length` bytes]

Este módulo NO sabe nada de protocol.json: solo parte un stream de bytes
crudo (los que van llegando de un socket) en "frames" completos, y arma
el length-prefix al enviar. El parseo de cada frame en {name, params} lo
hace `Protocol.parse_packet()` por separado, en otra capa.

No implementa compresión (login threshold) ni encriptación: si tu
protocolo las necesita, se agregan como una capa intermedia entre el
socket y el framer (descomprimís/desencriptás el frame antes de
pasárselo a Protocol.parse_packet).
"""

from __future__ import annotations

from .io import Reader, Writer
from .primitives import PRIMITIVES

_varint = PRIMITIVES["varint"]


class PacketFramer:
    """Acumula bytes crudos de un socket y va devolviendo frames completos
    a medida que se completan."""

    def __init__(self):
        self._buffer = b""

    def feed(self, chunk: bytes) -> list[bytes]:
        """
        Alimenta bytes recién llegados. Devuelve la lista de frames
        completos que se pudieron extraer (puede ser vacía, uno, o varios
        si llegaron varios paquetes pegados en el mismo chunk de socket).
        """
        self._buffer += chunk
        frames: list[bytes] = []

        while True:
            r = Reader(self._buffer)
            try:
                length = _varint.read(r)
            except Exception:
                # no llegaron suficientes bytes ni para el varint de longitud
                break

            if length < 0:
                # El varint length-prefix de Minecraft se lee CON SIGNO
                # (paridad con el protocolo real), así que un valor con
                # el bit alto puesto decodifica a negativo. Un peer que
                # mande eso está roto o es adversarial -- si lo
                # dejáramos pasar, la comparación de abajo
                # (buffer restante < length) daría falso para cualquier
                # negativo, así que "faltan bytes" nunca se detectaría
                # y se cortaría un frame vacío o mal alineado, corrompiendo
                # el framing de TODOS los paquetes siguientes en la
                # misma conexión. Mejor cortar la conexión acá.
                raise ValueError(
                    f"length-prefix negativo ({length}): peer roto o "
                    f"malicioso, no se puede seguir parseando este stream"
                )

            header_size = r.offset
            if len(self._buffer) - header_size < length:
                # el paquete todavía no llegó completo
                break

            frame = self._buffer[header_size:header_size + length]
            frames.append(frame)
            self._buffer = self._buffer[header_size + length:]

        return frames

    @staticmethod
    def wrap(frame: bytes) -> bytes:
        """Envuelve un frame ya serializado (sin length-prefix) agregándole
        el varint-length-prefix, listo para socket.send()/write()."""
        w = Writer()
        _varint.write(len(frame), w)
        w.write_bytes(frame)
        return w.result()
