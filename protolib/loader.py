"""
protolib/loader.py

Loads protocol definitions from a file, string, or dict, supporting
both JSON (original node-protodef / minecraft-data format) and YAML
(own format, more comfortable to write/maintain by hand).

------------------------------------------------------------------------
Why does YAML need translation?
------------------------------------------------------------------------
node-protodef represents a composite type as a 2-element list:

    ["container", [ {"name": "x", "type": "varint"}, ... ]]
    ["switch", {"compareTo": "packetId", "fields": {...}}]

That's reasonable in JSON, but writing 2-element lists by hand in YAML
is error-prone and hard to read. This loader instead accepts a mapping
with a SINGLE key, which is the base type's name:

    container:
      - name: x
        type: varint

    switch:
      compareTo: packetId
      fields:
        0x00: handshake
        0x01: status_request

_yaml_to_protodef() recursively walks the structure loaded from YAML
and converts each single-key-mapping-named-after-a-composite-type into
the ["type", options] form that protodef.core.Protocol already
understands.

Simple types (strings like "varint", "bool", "i32", or the name of a
user-defined type) are left as-is: not every single-key mapping is a
composite type, so only mappings whose single key matches a known base
type name are translated (container, array, switch, mapper, option,
bitfield, bitflags, buffer, pstring, count, entityMetadataLoop,
topBitSetTerminatedArray) -- or any other the caller registers via
`extra_composite_names`.

If you ever add a new composite type in core.py, also add it to
COMPOSITE_TYPE_NAMES below so YAML recognizes it.
"""

from __future__ import annotations

import json
import os
from typing import Any

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None

from .errors import ProtolibError


class LoaderError(ProtolibError):
    """Error loading/parsing a protocol definition file."""


# Base composite type names recognized by Protocol._composite_handlers.
# If you add a new handler in core.py, add it here too.
COMPOSITE_TYPE_NAMES = frozenset({
    "container",
    "array",
    "switch",
    "mapper",
    "option",
    "bitfield",
    "bitflags",
    "buffer",
    "pstring",
    "count",
    "entityMetadataLoop",
    "topBitSetTerminatedArray",
    "cstring",
})


def _translate_opts(val: Any, composite_names: frozenset[str]) -> Any:
    """Translates the CONTENTS of a `val` that is itself already the
    options of an already-identified composite type (e.g. what follows
    `buffer:` or `switch:`) -- without re-checking whether `val` itself
    is ANOTHER single-key shorthand. Same criterion already applied by
    the explicit-form ["type", opts] branch a bit further down in this
    file.

    Real bug: without this, a nested shorthand like

        buffer:
          count: 16

    gets translated first to ["buffer", {"count": 16}] (correct), but
    since that {"count": 16} goes through the full _yaml_to_protodef
    again, and "count" is ALSO a known composite type name (the
    length-prefix-as-sibling-field `count` type), it gets reinterpreted
    as shorthand again and ends up as ["buffer", ["count", 16]] --
    exactly the same class of bug the comment further below already
    documents for the explicit list form, but without the equivalent
    protection here.
    """
    if isinstance(val, dict):
        return {k: _yaml_to_protodef(v, composite_names) for k, v in val.items()}
    if isinstance(val, list):
        return [_yaml_to_protodef(item, composite_names) for item in val]
    return val


def _yaml_to_protodef(node: Any, composite_names: frozenset[str]) -> Any:
    """Recursively translates natural YAML syntax -> ['type', opts] form."""
    if isinstance(node, dict):
        # Is this a "type shorthand"? -> single-key mapping, and that key
        # is a known composite type name.
        if len(node) == 1:
            (key, val), = node.items()
            if key in composite_names:
                return [key, _translate_opts(val, composite_names)]
        # normal mapping: translate values recursively, preserving keys.
        return {k: _yaml_to_protodef(v, composite_names) for k, v in node.items()}

    if isinstance(node, list):
        # Is this already an explicit ["type", opts] form (e.g. written
        # by hand in the YAML instead of using the shorthand)? If the
        # first element is a known composite type name, the options
        # (node[1]) must NOT go through the "single-key shorthand" check
        # again -- they're already in their final form, we just need to
        # translate whatever hangs inside them (without reinterpreting
        # the top key itself). Without this special case, something like
        # ["buffer", {"count": 16}] would get corrupted into
        # ["buffer", ["count", 16]] because {"count": 16} matched the
        # shorthand for the composite type "count".
        if (
            len(node) == 2
            and isinstance(node[0], str)
            and node[0] in composite_names
            and isinstance(node[1], dict)
        ):
            key, opts = node
            return [key, {k: _yaml_to_protodef(v, composite_names) for k, v in opts.items()}]
        return [_yaml_to_protodef(item, composite_names) for item in node]

    # str, int, float, bool, None -> as-is
    return node


def _detect_format(path_or_text: str) -> str:
    """Guesses 'json' or 'yaml' from the file extension, or from the
    content if it's not a recognizable path (e.g. a string already in
    memory)."""
    lower = path_or_text.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".yml", ".yaml")):
        return "yaml"
    # Not a path with a known extension: look at the content.
    stripped = path_or_text.lstrip()
    if stripped.startswith(("{", "[")):
        return "json"
    return "yaml"


def load_protocol_dict(
    source: str | dict,
    *,
    fmt: str | None = None,
    extra_composite_names: frozenset[str] | None = None,
) -> dict:
    """
    Loads a protocol definition and returns the "raw" dict expected by
    `protodef.core.Protocol(...)`.

    `source` can be:
      - a path to a .json, .yml, or .yaml file
      - a string with JSON or YAML content already in memory
      - an already-parsed dict (returned as-is, without shorthand
        translation)

    `fmt`: forces "json" or "yaml" instead of autodetecting (useful if
    `source` is in-memory content with no reliable file extension).

    `extra_composite_names`: additional composite type names to
    recognize as YAML shorthand, in case you extend the engine with
    your own handlers outside of core.py.
    """
    if isinstance(source, dict):
        return source

    if not isinstance(source, str):
        raise LoaderError(f"unsupported source type: {type(source)!r}")

    composite_names = COMPOSITE_TYPE_NAMES | (extra_composite_names or frozenset())

    # Heuristic to distinguish "this is a file path" from "this is
    # already in-memory content": if it ends with a recognized
    # extension AND has no newline (real JSON/YAML content almost
    # always has at least one line break, a path never does), it's
    # treated as a path. Real bug fixed here: previously only
    # os.path.isfile(source) was used, which depends purely and
    # exclusively on the process's cwd at that moment. If the caller
    # ran the script from a different directory (e.g. `python
    # examples/demo.py` instead of `cd examples && python demo.py`),
    # the file DID exist on disk but not at that relative path --
    # isfile() silently returned False, and the string
    # "example_protocol.yml" ended up being treated as if it were the
    # yaml/json CONTENT to parse (falls through to yaml.safe_load,
    # which interprets it as a plain scalar string). That never raised
    # LoaderError: the load "succeeded" and returned the string as-is,
    # and the real crash only showed up later and somewhere else
    # (core.py: 'str' object has no attribute 'get'), with no hint that
    # the actual problem was a file not being found.
    looks_like_path = source.lower().endswith((".json", ".yml", ".yaml")) and "\n" not in source

    is_path = os.path.isfile(source)
    if is_path:
        with open(source, "r", encoding="utf-8") as fh:
            text = fh.read()
        detected = fmt or _detect_format(source)
    elif looks_like_path:
        raise LoaderError(
            f"file not found: {source!r} (current cwd: {os.getcwd()!r}). "
            f"If the path is relative to the script rather than to the "
            f"directory it's run from, use an absolute path, e.g.: "
            f"os.path.join(os.path.dirname(__file__), {os.path.basename(source)!r})"
        )
    else:
        text = source
        detected = fmt or _detect_format(source)

    if detected == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoaderError(f"invalid JSON: {exc}") from exc

    if detected == "yaml":
        if yaml is None:
            raise LoaderError(
                "PyYAML is not installed. Install it with: pip install pyyaml"
            )
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise LoaderError(f"invalid YAML: {exc}") from exc
        if raw is None:
            raise LoaderError("the YAML is empty")
        return _yaml_to_protodef(raw, composite_names)

    raise LoaderError(f"unknown format: {detected!r} (use 'json' or 'yaml')")


def protocol_dict_to_yaml(protocol_dict: dict) -> str:
    """
    "Best effort" inverse operation: takes a dict already in
    ['type', opts] form (the original node-protodef format, e.g. the
    protocol.json from minecraft-data) and converts it to the natural
    YAML shorthand syntax so it's readable/editable by hand.

    Useful for migrating an existing protocol.json to .yml once:

        import json
        from protolib.loader import protocol_dict_to_yaml
        raw = json.load(open("protocol.json"))
        open("protocol.yml", "w").write(protocol_dict_to_yaml(raw))
    """
    if yaml is None:
        raise LoaderError("PyYAML is not installed. Install it with: pip install pyyaml")

    def to_shorthand(node: Any) -> Any:
        if isinstance(node, list) and len(node) == 2 and isinstance(node[0], str):
            base, opts = node
            if base in COMPOSITE_TYPE_NAMES:
                return {base: to_shorthand(opts)}
            return [to_shorthand(x) for x in node]
        if isinstance(node, dict):
            return {k: to_shorthand(v) for k, v in node.items()}
        if isinstance(node, list):
            return [to_shorthand(x) for x in node]
        return node

    shorthand = to_shorthand(protocol_dict)
    return yaml.dump(
        shorthand,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
        width=100,
    )
