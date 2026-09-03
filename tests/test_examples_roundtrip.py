"""
tests/test_examples_roundtrip.py

A pesar de que todos los demás test_*.py usan protocolos sintéticos
chiquitos (por diseño, para aislar cada feature), este archivo es el
único que carga los protocolos REALES de examples/ y los ejercita de
punta a punta -- exactamente la garantía que el README y el CHANGELOG
dicen tener ("tested against two real protocols", "byte-exact
round-trips") pero que, antes de esta suite, no vivía en ningún lado
del repo como código ejecutable.

Los paquetes/params usados acá están tomados literalmente de
examples/demo.py y examples/demo_classicube.py (que ya corrían y
pasaban sus propios asserts antes de escribir esto), así que no son
datos inventados a mano.
"""
import os
import unittest

from tests import EXAMPLES_DIR
from protolib.core import Protocol
from protolib.loader import load_protocol_dict


class TestExampleProtocolRoundtrip(unittest.TestCase):
    """example_protocol.yml -- Minecraft Java 1.8.9-style, patrones 1/2/4 del README."""

    @classmethod
    def setUpClass(cls):
        cls.proto = Protocol(os.path.join(EXAMPLES_DIR, "example_protocol.yml"))

    def test_switch_with_parent_relative_path_pattern2(self):
        # El caso puntual que corrige el bug documentado de "../accion":
        # un array de items inline (container SIN nombre) donde cada item
        # usa un switch con compareTo="../accion" para llegar al campo del
        # container padre real, saltando el nivel del propio item.
        params = {
            "accion": "agregar",
            "jugadores": [
                {"uuid": "11111111-1111-1111-1111-111111111111", "nombre": "Pana_Bot"},
            ],
        }
        data = self.proto.serialize_packet("play", "toClient", "ejemplo_jugadores", params)
        parsed = self.proto.parse_packet("play", "toClient", data)
        self.assertEqual(parsed.name, "ejemplo_jugadores")
        self.assertEqual(parsed.params, params)
        self.assertEqual(parsed.bytes_read, len(data))

    def test_parametrized_type_plus_packed_bitfield_pattern1_and_4(self):
        params = {
            "entityId": 42,
            "metadata": [
                {"tipo": 0, "indice": 0, "valor": -1},
                {"tipo": 4, "indice": 1, "valor": "hola pana"},
                {"tipo": 5, "indice": 2, "valor": {"x": 10, "y": 64, "z": -5}},
            ],
        }
        data = self.proto.serialize_packet("play", "toClient", "ejemplo_metadata", params)
        parsed = self.proto.parse_packet("play", "toClient", data)
        self.assertEqual(parsed.params, params)
        self.assertEqual(parsed.bytes_read, len(data), "quedó basura sin leer al final del paquete")


class TestClassicubeProtocolRoundtrip(unittest.TestCase):
    """classicube_protocol.yml -- Minecraft Classic + CPE, todos los paquetes
    que examples/demo_classicube.py ejercita en la práctica."""

    @classmethod
    def setUpClass(cls):
        cls.proto = Protocol(os.path.join(EXAMPLES_DIR, "classicube_protocol.yml"))

    def _roundtrip(self, direction, name, params):
        data = self.proto.serialize_packet("play", direction, name, params)
        parsed = self.proto.parse_packet("play", direction, data)
        self.assertEqual(parsed.params, params, f"mismatch en {name}")
        return data, parsed

    def test_identification_toServer(self):
        self._roundtrip("toServer", "identification", {
            "protocolVersion": 7, "name": "Pocke",
            "keyOrMotd": "clave-de-verificacion", "userType": 0,
        })

    def test_identification_toClient_op(self):
        self._roundtrip("toClient", "identification", {
            "protocolVersion": 7, "name": "PocketNet Server",
            "keyOrMotd": "Bienvenido &epana", "userType": 0x64,
        })

    def test_spawn_player_fixed_point_coords(self):
        _, parsed = self._roundtrip("toClient", "spawn_player", {
            "playerId": 5, "playerName": "Pana_Bot",
            "x": 32 * 10, "y": 32 * 65, "z": 32 * 10, "yaw": 0, "pitch": 0,
        })
        self.assertEqual(parsed.bytes_read, len(self.proto.serialize_packet(
            "play", "toClient", "spawn_player",
            {"playerId": 5, "playerName": "Pana_Bot", "x": 320, "y": 2080, "z": 320, "yaw": 0, "pitch": 0},
        )))

    def test_set_block_toServer(self):
        self._roundtrip("toServer", "set_block", {"x": 10, "y": 64, "z": 10, "mode": 1, "blockType": 1})

    def test_level_data_chunk_fixed_1024_buffer(self):
        params = {
            "chunkLength": 500,
            "chunkData": bytes([1]) * 500 + bytes(1024 - 500),
            "percentComplete": 50,
        }
        data, parsed = self._roundtrip("toClient", "level_data_chunk", params)
        self.assertEqual(len(parsed.params["chunkData"]), 1024)
        self.assertEqual(parsed.bytes_read, len(data))

    def test_message_with_negative_player_id(self):
        self._roundtrip("toClient", "message", {"playerId": -1, "message": "&eEl servidor dice hola"})

    def test_cpe_ext_info_and_ext_entry(self):
        self._roundtrip("toClient", "ext_info", {"appName": "PocketNet Server", "extensionCount": 2})
        self._roundtrip("toClient", "ext_entry", {"extName": "ClickDistance", "version": 1})

    def test_cpe_click_distance(self):
        self._roundtrip("toClient", "ext_click_distance", {"distance": 32 * 5})

    def test_cpe_custom_block_support_level_toServer(self):
        self._roundtrip("toServer", "custom_block_support_level", {"supportLevel": 1})

    def test_cpe_held_block(self):
        self._roundtrip("toClient", "held_block", {"blockToHold": 5, "preventChange": True})

    def test_cpe_ext_add_and_remove_player_name(self):
        self._roundtrip("toClient", "ext_add_player_name", {
            "nameId": 1, "playerName": "Pocke", "listName": "&ePocke",
            "groupName": "Admins", "groupRank": 0,
        })
        self._roundtrip("toClient", "ext_remove_player_name", {"nameId": 1})


class TestYamlJsonEquivalence(unittest.TestCase):
    """El README promete que protocol.yml y protocol.json son equivalentes
    byte a byte. Esto lo prueba con los 2 pares que vienen en examples/,
    en vez de solo confiar en la afirmación del README."""

    def _assert_yml_json_equivalent(self, basename):
        yml_path = os.path.join(EXAMPLES_DIR, basename + ".yml")
        json_path = os.path.join(EXAMPLES_DIR, basename + ".json")
        from_yml = load_protocol_dict(yml_path)
        from_json = load_protocol_dict(json_path)
        self.assertEqual(
            from_yml, from_json,
            f"{basename}.yml y {basename}.json no producen el mismo dict tras cargar",
        )

    def test_example_protocol_yml_matches_json(self):
        self._assert_yml_json_equivalent("example_protocol")

    def test_classicube_protocol_yml_matches_json(self):
        self._assert_yml_json_equivalent("classicube_protocol")

    def test_protocol_built_from_json_produces_identical_bytes_to_yml(self):
        # No solo que los dicts sean iguales -- que USARLOS para serializar
        # el mismo paquete dé exactamente los mismos bytes en el wire.
        proto_yml = Protocol(os.path.join(EXAMPLES_DIR, "example_protocol.yml"))
        proto_json = Protocol(os.path.join(EXAMPLES_DIR, "example_protocol.json"))
        params = {"accion": "quitar"}
        data_yml = proto_yml.serialize_packet("play", "toClient", "ejemplo_jugadores", params)
        data_json = proto_json.serialize_packet("play", "toClient", "ejemplo_jugadores", params)
        self.assertEqual(data_yml, data_json)


if __name__ == "__main__":
    unittest.main()
