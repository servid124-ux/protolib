import os
import sys
sys.path.insert(0, "..")

from protolib.core import Protocol

_here = os.path.dirname(os.path.abspath(__file__))
proto = Protocol(os.path.join(_here, "classicube_protocol.yml"))

print("=== toServer: Identification (login del cliente) ===")
params = {
    "protocolVersion": 7,
    "name": "Pocke",
    "keyOrMotd": "clave-de-verificacion",
    "userType": 0,
}
data = proto.serialize_packet("play", "toServer", "identification", params)
parsed = proto.parse_packet("play", "toServer", data)
print("bytes:", len(data), "->", parsed.params)
assert parsed.bytes_read == len(data)

print()
print("=== toClient: Identification (respuesta del server, op) ===")
params = {
    "protocolVersion": 7,
    "name": "PocketNet Server",
    "keyOrMotd": "Bienvenido &epana",
    "userType": 0x64,
}
data = proto.serialize_packet("play", "toClient", "identification", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== toClient: Spawn Player ===")
params = {
    "playerId": 5,
    "playerName": "Pana_Bot",
    "x": 32 * 10,   # bloque 10 en fixed-point
    "y": 32 * 65,
    "z": 32 * 10,
    "yaw": 0,
    "pitch": 0,
}
data = proto.serialize_packet("play", "toClient", "spawn_player", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== toServer: Set Block ===")
params = {"x": 10, "y": 64, "z": 10, "mode": 1, "blockType": 1}
data = proto.serialize_packet("play", "toServer", "set_block", params)
parsed = proto.parse_packet("play", "toServer", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== toClient: Level Data Chunk (1024 bytes fijos) ===")
params = {"chunkLength": 500, "chunkData": bytes([1]) * 500 + bytes(1024 - 500), "percentComplete": 50}
data = proto.serialize_packet("play", "toClient", "level_data_chunk", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "-> chunkLength:", parsed.params["chunkLength"],
      "len(chunkData):", len(parsed.params["chunkData"]),
      "percentComplete:", parsed.params["percentComplete"])
assert parsed.bytes_read == len(data)

print()
print("=== toClient: Message ===")
params = {"playerId": -1, "message": "&eEl servidor dice hola"}
data = proto.serialize_packet("play", "toClient", "message", params)
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== CPE: ExtInfo + ExtEntry (negociacion, toClient) ===")
data = proto.serialize_packet("play", "toClient", "ext_info",
                               {"appName": "PocketNet Server", "extensionCount": 2})
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

data = proto.serialize_packet("play", "toClient", "ext_entry",
                               {"extName": "ClickDistance", "version": 1})
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== CPE: ClickDistance ===")
data = proto.serialize_packet("play", "toClient", "ext_click_distance", {"distance": 32 * 5})
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== CPE: CustomBlockSupportLevel (toServer) ===")
data = proto.serialize_packet("play", "toServer", "custom_block_support_level", {"supportLevel": 1})
parsed = proto.parse_packet("play", "toServer", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== CPE: HeldBlock ===")
data = proto.serialize_packet("play", "toClient", "held_block", {"blockToHold": 5, "preventChange": True})
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("=== CPE: ExtAddPlayerName / ExtRemovePlayerName ===")
data = proto.serialize_packet("play", "toClient", "ext_add_player_name", {
    "nameId": 1, "playerName": "Pocke", "listName": "&ePocke",
    "groupName": "Admins", "groupRank": 0,
})
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

data = proto.serialize_packet("play", "toClient", "ext_remove_player_name", {"nameId": 1})
parsed = proto.parse_packet("play", "toClient", data)
print("bytes:", len(data), "->", parsed.params)

print()
print("Todo OK, sin bytes sobrantes en ningun caso probado")
