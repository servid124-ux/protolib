"""
servidor.py -- servidor ClassiCube (protocolo 0x07) mínimo:

    1) acepta el login (Identification, 0x00)
    2) genera un mapa (cuevas o llano, a elección) y lo manda
       (Level Initialize -> Level Data Chunk x N -> Level Finalize)

No hace nada más: no procesa movimiento, chat, ni bloques. Es solo
para pararse, ver el mapa generado, y listo.

Uso:
    python servidor.py [puerto] [--modo cuevas|llano]   # default: 25565 cuevas
"""
from __future__ import annotations

import argparse
import gzip
import os
import random
import socket
import struct
import sys
import threading

sys.path.insert(0, "..")

from protolib.core import Protocol
from protolib.io import BufferUnderrun
from protolib.errors import ProtolibError

_here = os.path.dirname(os.path.abspath(__file__))
PROTO = Protocol(os.path.join(_here, "classicube_protocol.yml"))

# tamaño del mundo -- chico a propósito para que genere/transmita rápido
SIZE_X = 64
SIZE_Y = 64
SIZE_Z = 64

BLOCK_AIR = 0
BLOCK_STONE = 1
BLOCK_GRASS = 2
BLOCK_DIRT = 3
BLOCK_BEDROCK = 7


# =============================================================================
# Generador de cuevas -- cellular automata clásico:
#   1) rellenar todo de piedra, dejar 1 capa de bedrock en el fondo
#   2) "tallar" cuevas con random walk 3D (varios "mineros" caminando
#      al azar y vaciando un radio pequeño a su paso)
#   3) suavizar con unas pasadas de cellular automata (regla 4-5) para
#      que no queden bordes de un solo bloque sueltos
# =============================================================================

def generar_mapa_cuevas(size_x: int, size_y: int, size_z: int, seed: int | None = None) -> bytearray:
    rng = random.Random(seed)
    total = size_x * size_y * size_z
    # el formato ClassicWorld guarda los bloques en orden Y,Z,X (Y más
    # significativo), igual que Level Data Chunk los espera
    blocks = bytearray([BLOCK_STONE]) * total

    def idx(x: int, y: int, z: int) -> int:
        return (y * size_z + z) * size_x + x

    # bedrock en la capa 0
    for z in range(size_z):
        for x in range(size_x):
            blocks[idx(x, 0, z)] = BLOCK_BEDROCK

    # --- paso 1: random walk 3D tallando cuevas ---
    n_mineros = 6
    pasos_por_minero = 2000
    radio = 2

    # el random walk necesita margen de `radio` bloques en cada eje para
    # no salirse del mundo -- con un mundo mas chico que eso, randint()
    # recibiria low > high y explotaria. Si no hay margen suficiente no
    # tiene sentido tallar cuevas (el mundo es demasiado chico), asi que
    # devolvemos solo el bloque solido + bedrock generado arriba.
    cabe_x = size_x - 2 * radio - 1 >= 0
    cabe_y = size_y - 2 * radio - 2 >= 0
    cabe_z = size_z - 2 * radio - 1 >= 0
    if not (cabe_x and cabe_y and cabe_z):
        return blocks

    for _ in range(n_mineros):
        x = rng.randint(radio, size_x - radio - 1)
        y = rng.randint(radio + 1, size_y - radio - 1)
        z = rng.randint(radio, size_z - radio - 1)

        for _ in range(pasos_por_minero):
            for dx in range(-radio, radio + 1):
                for dy in range(-radio, radio + 1):
                    for dz in range(-radio, radio + 1):
                        if dx * dx + dy * dy + dz * dz > radio * radio:
                            continue
                        nx, ny, nz = x + dx, y + dy, z + dz
                        if 0 < nx < size_x - 1 and 1 < ny < size_y - 1 and 0 < nz < size_z - 1:
                            blocks[idx(nx, ny, nz)] = BLOCK_AIR

            # paso aleatorio, con leve sesgo horizontal para que las
            # cuevas se extiendan más de lo que suben/bajan
            x += rng.choice([-1, 0, 0, 1])
            z += rng.choice([-1, 0, 0, 1])
            y += rng.choice([-1, 0, 1])
            x = max(radio, min(size_x - radio - 1, x))
            y = max(radio + 1, min(size_y - radio - 1, y))
            z = max(radio, min(size_z - radio - 1, z))

    # --- paso 2: suavizado cellular automata (rellena bolsones de aire
    #     de 1 bloque totalmente rodeados, para que no queden huecos raros) ---
    def contar_vecinos_solidos(x, y, z):
        n = 0
        for dx, dy, dz in ((1,0,0),(-1,0,0),(0,1,0),(0,-1,0),(0,0,1),(0,0,-1)):
            nx, ny, nz = x + dx, y + dy, z + dz
            if 0 <= nx < size_x and 0 <= ny < size_y and 0 <= nz < size_z:
                if blocks[idx(nx, ny, nz)] != BLOCK_AIR:
                    n += 1
            else:
                n += 1  # borde del mundo cuenta como sólido
        return n

    for _ in range(2):
        nuevos = bytearray(blocks)
        for y in range(1, size_y - 1):
            for z in range(1, size_z - 1):
                for x in range(1, size_x - 1):
                    i = idx(x, y, z)
                    if blocks[i] == BLOCK_AIR and contar_vecinos_solidos(x, y, z) >= 6:
                        nuevos[i] = BLOCK_STONE
        blocks = nuevos

    return blocks


def generar_mapa_llano(size_x: int, size_y: int, size_z: int) -> bytearray:
    """
    Mundo plano clásico: bedrock en la capa 0, tierra hasta un poco
    antes de la mitad, pasto en la última capa sólida, aire arriba.
    Sin generación aleatoria -- siempre el mismo resultado.
    """
    total = size_x * size_y * size_z
    blocks = bytearray([BLOCK_AIR]) * total

    def idx(x: int, y: int, z: int) -> int:
        return (y * size_z + z) * size_x + x

    altura_suelo = size_y // 2  # capa de pasto (última sólida)

    for z in range(size_z):
        for x in range(size_x):
            blocks[idx(x, 0, z)] = BLOCK_BEDROCK
            for y in range(1, altura_suelo):
                blocks[idx(x, y, z)] = BLOCK_DIRT
            blocks[idx(x, altura_suelo, z)] = BLOCK_GRASS

    return blocks


def bloques_a_classicworld(blocks: bytearray) -> bytes:
    """
    Level Data Chunk manda: [i32 volumen big-endian][bloques...] comprimido
    con gzip (así lo espera el cliente vainilla, no protolib -- protolib
    solo define el paquete, la compresión es una capa aparte encima).
    """
    payload = struct.pack(">i", len(blocks)) + bytes(blocks)
    return gzip.compress(payload)


# =============================================================================
# Manejo de un cliente conectado
# =============================================================================

def manejar_cliente(conn: socket.socket, addr, modo: str):
    print(f"[+] conexion de {addr}")
    buffer = b""

    def recibir_paquete(direction: str):
        """Bloquea hasta tener un paquete completo de `direction`, lo
        parsea y devuelve el ParsedPacket. Usa bytes_read para saber
        cuanto sobra en el buffer para el proximo paquete."""
        nonlocal buffer
        while True:
            try:
                parsed = PROTO.parse_packet("play", direction, buffer)
                buffer = buffer[parsed.bytes_read:]
                return parsed
            except BufferUnderrun:
                chunk = conn.recv(4096)
                if not chunk:
                    return None
                buffer += chunk

    def enviar(direction: str, name: str, params: dict):
        data = PROTO.serialize_packet("play", direction, name, params)
        conn.sendall(data)

    try:
        # --- 1) login ---
        login = recibir_paquete("toServer")
        if login is None or login.name != "identification":
            print("[-] no llego un Identification valido, cierro")
            return

        username = login.params["name"]
        print(f"[+] login de '{username}' (protocolVersion={login.params['protocolVersion']})")

        enviar("toClient", "identification", {
            "protocolVersion": 7,
            "name": "Servidor de Cuevas",
            "keyOrMotd": "&aSolo cuevas &7-&f generado al conectar",
            "userType": 0x00,
        })

        # --- 2) generar y mandar el mapa ---
        print(f"[+] generando mapa ({modo})...")
        if modo == "llano":
            blocks = generar_mapa_llano(SIZE_X, SIZE_Y, SIZE_Z)
        else:
            blocks = generar_mapa_cuevas(SIZE_X, SIZE_Y, SIZE_Z, seed=random.randint(0, 2**31))
        comprimido = bloques_a_classicworld(blocks)
        print(f"[+] mapa generado: {SIZE_X}x{SIZE_Y}x{SIZE_Z}, "
              f"{len(comprimido)} bytes comprimidos")

        enviar("toClient", "level_initialize", {})

        CHUNK = 1024
        total = len(comprimido)
        for offset in range(0, total, CHUNK):
            trozo = comprimido[offset:offset + CHUNK]
            percent = min(100, int((offset + len(trozo)) * 100 / total))
            enviar("toClient", "level_data_chunk", {
                "chunkLength": len(trozo),
                "chunkData": trozo,   # el primitivo hace pad con 0x00 solo
                "percentComplete": percent,
            })

        enviar("toClient", "level_finalize", {
            "sizeX": SIZE_X, "sizeY": SIZE_Y, "sizeZ": SIZE_Z,
        })

        # --- 3) spawnear al jugador en el medio, arriba del terreno ---
        spawn_x = SIZE_X // 2
        spawn_z = SIZE_Z // 2
        spawn_y = (SIZE_Y // 2) + 1 if modo == "llano" else SIZE_Y - 2
        enviar("toClient", "spawn_player", {
            "playerId": -1,   # -1 = el propio jugador
            "playerName": username,
            "x": spawn_x * 32, "y": spawn_y * 32, "z": spawn_z * 32,
            "yaw": 0, "pitch": 0,
        })

        print(f"[+] '{username}' spawneado, mapa entregado. Sin mas logica implementada.")

        # mantener la conexion viva respondiendo pings, nada mas
        while True:
            pkt = recibir_paquete("toServer")
            if pkt is None:
                break
            # no procesamos nada mas (set_block, position, message) --
            # este servidor es solo "cuevas", no hay mundo persistente

    except (ConnectionResetError, BrokenPipeError):
        pass
    except ProtolibError as e:
        # cliente mando algo que no matchea el protocolo (packet id
        # desconocido, etc.) -- cerramos ESTA conexion nada mas, no
        # tumbamos el servidor ni el hilo con un traceback crudo
        print(f"[-] {addr} mando datos invalidos, cierro conexion: {e}")
    finally:
        conn.close()
        print(f"[-] {addr} desconectado")


def main():
    parser = argparse.ArgumentParser(description="Servidor ClassiCube minimo (cuevas o llano)")
    parser.add_argument("puerto", nargs="?", type=int, default=25565)
    parser.add_argument("--modo", choices=["cuevas", "llano"], default="cuevas")
    args = parser.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.puerto))
    srv.listen(5)
    print(f"[+] servidor ({args.modo}) escuchando en 0.0.0.0:{args.puerto}")

    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(
                target=manejar_cliente, args=(conn, addr, args.modo), daemon=True
            ).start()
    except KeyboardInterrupt:
        print("\n[+] cerrando servidor")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
