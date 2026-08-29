"""
protolib

An independent library (unofficial, not dependent on npm/PyPI's
`protodef`) for defining, reading, and writing binary protocols from a
protocol definition in JSON (node-minecraft-protocol / node-protodef
style) or in YAML (own syntax, more readable).

Basic usage (JSON, unmodified minecraft-data format):

    from protolib import Protocol

    proto = Protocol("protocol.json")
    pkt = proto.parse_packet("play", "toServer", raw_bytes)
    print(pkt.name, pkt.params)

    data = proto.serialize_packet("play", "toClient", "keep_alive", {"keepAliveId": 1})

Usage with YAML (shorthand syntax, see protolib/loader.py):

    proto = Protocol("protocol.yml")

Also accepts an already-parsed dict, or in-memory content as a string:

    proto = Protocol({"types": {...}})
    proto = Protocol(yaml_text, fmt="yaml")

Migrating an existing protocol.json to .yml:

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
from .nbt import (
    read_nbt, write_nbt, NBTError,
    read_anonymous_nbt, write_anonymous_nbt,
    read_anon_optional_nbt, write_anon_optional_nbt,
)

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
    "read_anonymous_nbt",
    "write_anonymous_nbt",
    "read_anon_optional_nbt",
    "write_anon_optional_nbt",
]

__version__ = "0.3.9"
