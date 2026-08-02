"""
protolib

Libreria propia (no oficial, no depende de la `protodef` de npm/PyPI)
para definir, leer y escribir protocolos binarios a partir de una
definicion de protocolo en JSON (estilo node-minecraft-protocol /
node-protodef) o en YAML (sintaxis propia, mas legible).

Uso basico (JSON, formato minecraft-data sin modificar):

    from protolib import Protocol

    proto = Protocol("protocol.json")
    pkt = proto.parse_packet("play", "toServer", raw_bytes)
    print(pkt.name, pkt.params)

    data = proto.serialize_packet("play", "toClient", "keep_alive", {"keepAliveId": 1})

Uso con YAML (sintaxis shorthand, ver protolib/loader.py):

    proto = Protocol("protocol.yml")

Tambien aceptan dict ya parseado, o el contenido en memoria como string:

    proto = Protocol({"types": {...}})
    proto = Protocol(yaml_text, fmt="yaml")

Migrar un protocol.json existente a .yml:

    from protolib.loader import protocol_dict_to_yaml
    import json
    raw = json.load(open("protocol.json"))
    open("protocol.yml", "w").write(protocol_dict_to_yaml(raw))
"""

from .core import Protocol, ParsedPacket, Scope
from .io import Reader, Writer, BufferUnderrun
from .primitives import PRIMITIVES, Primitive, make_fixed_utf16be_string
from .errors import (
    ProtolibError,
    UnknownTypeError,
    InvalidTypeDefinition,
    SwitchCaseNotFound,
    ConditionError,
)
from .conditions import eval_condition
from .framer import PacketFramer
from .loader import load_protocol_dict, protocol_dict_to_yaml, LoaderError
from .nbt import read_nbt, write_nbt, NBTError

__all__ = [
    "Protocol",
    "ParsedPacket",
    "Scope",
    "Reader",
    "Writer",
    "BufferUnderrun",
    "PRIMITIVES",
    "Primitive",
    "make_fixed_utf16be_string",
    "ProtolibError",
    "UnknownTypeError",
    "InvalidTypeDefinition",
    "SwitchCaseNotFound",
    "ConditionError",
    "eval_condition",
    "PacketFramer",
    "load_protocol_dict",
    "protocol_dict_to_yaml",
    "LoaderError",
    "read_nbt",
    "write_nbt",
    "NBTError",
]

__version__ = "0.3.3"
