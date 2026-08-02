"""
protolib/loader.py

Carga de definiciones de protocolo desde archivo, string o dict,
soportando tanto JSON (formato original node-protodef / minecraft-data)
como YAML (formato propio, más cómodo de escribir/mantener a mano).

------------------------------------------------------------------------
¿Por qué YAML necesita traducción?
------------------------------------------------------------------------
node-protodef representa un tipo compuesto como una lista de 2 elementos:

    ["container", [ {"name": "x", "type": "varint"}, ... ]]
    ["switch", {"compareTo": "packetId", "fields": {...}}]

Eso en JSON es razonable, pero en YAML escribir listas-de-2-elementos
a mano es propenso a error y poco legible. Este loader acepta en su
lugar un mapping con UNA sola clave, que es el nombre del tipo base:

    container:
      - name: x
        type: varint

    switch:
      compareTo: packetId
      fields:
        0x00: handshake
        0x01: status_request

_yaml_to_protodef() recorre recursivamente la estructura cargada del
YAML y convierte cada mapping-de-una-clave-con-nombre-de-tipo-compuesto
a la forma ["tipo", opciones] que ya entiende protodef.core.Protocol.

Los tipos simples (strings como "varint", "bool", "i32", o el nombre
de un tipo definido por el usuario) se dejan tal cual: no todo mapping
de una clave es un tipo compuesto, así que solo se traducen los
mappings cuya única clave coincide con un nombre de tipo base conocido
(container, array, switch, mapper, option, bitfield, bitflags, buffer,
pstring, count, entityMetadataLoop, topBitSetTerminatedArray) — o
cualquier otro que el caller registre vía `extra_composite_names`.

Si algún día agregás un tipo compuesto nuevo en core.py, agregalo
también a COMPOSITE_TYPE_NAMES acá abajo para que el YAML lo reconozca.
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
    """Error al cargar/parsear un archivo de definición de protocolo."""


# Nombres de tipo base compuestos reconocidos por Protocol._composite_handlers.
# Si agregás un handler nuevo en core.py, sumalo acá también.
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
    """Traduce el CONTENIDO de un `val` que ya es, en sí mismo, las opciones
    de un tipo compuesto ya identificado (p.ej. lo que sigue a `buffer:` o
    a `switch:`) -- sin volver a chequear si `val` mismo es OTRO shorthand
    de una sola clave. Mismo criterio que ya aplicaba la rama de forma
    explícita ["tipo", opts] un poco más abajo en este archivo.

    Bug real: sin esto, un shorthand anidado como

        buffer:
          count: 16

    se traduce primero a ["buffer", {"count": 16}] (correcto) pero como
    ese {"count": 16} vuelve a pasar por _yaml_to_protodef completo, y
    "count" TAMBIÉN es un nombre de tipo compuesto conocido (el tipo
    `count` de length-prefix-como-campo-hermano), se lo reinterpreta como
    shorthand de vuelta y termina en ["buffer", ["count", 16]] -- exactamente
    la misma clase de bug que el comentario de más abajo ya documenta para
    la forma explícita de lista, pero sin la protección equivalente acá.
    """
    if isinstance(val, dict):
        return {k: _yaml_to_protodef(v, composite_names) for k, v in val.items()}
    if isinstance(val, list):
        return [_yaml_to_protodef(item, composite_names) for item in val]
    return val


def _yaml_to_protodef(node: Any, composite_names: frozenset[str]) -> Any:
    """Traduce recursivamente sintaxis YAML natural -> forma ['tipo', opts]."""
    if isinstance(node, dict):
        # ¿Es un "type shorthand"? -> mapping de una sola clave, y esa clave
        # es un nombre de tipo compuesto conocido.
        if len(node) == 1:
            (key, val), = node.items()
            if key in composite_names:
                return [key, _translate_opts(val, composite_names)]
        # mapping normal: traducir valores recursivamente, preservando claves.
        return {k: _yaml_to_protodef(v, composite_names) for k, v in node.items()}

    if isinstance(node, list):
        # ¿Ya es una forma explícita ["tipo", opts] (p.ej. escrita a mano
        # en el YAML en vez de usar el shorthand)? Si el primer elemento es
        # un nombre de tipo compuesto conocido, las opciones (node[1]) NO
        # deben pasar de nuevo por el chequeo de "shorthand de 1 clave" --
        # ya están en su forma final, solo hay que traducir lo que cuelga
        # adentro (sin reinterpretar la clave superior misma). Sin este
        # caso especial, algo como ["buffer", {"count": 16}] se corrompía
        # a ["buffer", ["count", 16]] porque {"count": 16} coincidía con
        # el shorthand del tipo compuesto "count".
        if (
            len(node) == 2
            and isinstance(node[0], str)
            and node[0] in composite_names
            and isinstance(node[1], dict)
        ):
            key, opts = node
            return [key, {k: _yaml_to_protodef(v, composite_names) for k, v in opts.items()}]
        return [_yaml_to_protodef(item, composite_names) for item in node]

    # str, int, float, bool, None -> tal cual
    return node


def _detect_format(path_or_text: str) -> str:
    """Adivina 'json' o 'yaml' por extensión de archivo, o por contenido
    si no es una ruta reconocible (p.ej. un string ya en memoria)."""
    lower = path_or_text.lower()
    if lower.endswith(".json"):
        return "json"
    if lower.endswith((".yml", ".yaml")):
        return "yaml"
    # No es una ruta con extensión conocida: mirar el contenido.
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
    Carga una definición de protocolo y devuelve el dict "crudo" que
    espera `protodef.core.Protocol(...)`.

    `source` puede ser:
      - ruta a un archivo .json, .yml o .yaml
      - un string con contenido JSON o YAML ya en memoria
      - un dict ya parseado (se devuelve tal cual, sin traducir shorthand)

    `fmt`: fuerza "json" o "yaml" en vez de autodetectar (útil si `source`
    es contenido en memoria sin extensión de archivo confiable).

    `extra_composite_names`: nombres de tipo compuesto adicionales a
    reconocer como shorthand YAML, por si extendés el motor con handlers
    propios fuera de core.py.
    """
    if isinstance(source, dict):
        return source

    if not isinstance(source, str):
        raise LoaderError(f"tipo de origen no soportado: {type(source)!r}")

    composite_names = COMPOSITE_TYPE_NAMES | (extra_composite_names or frozenset())

    # Heurística para distinguir "esto es una ruta de archivo" de "esto ya
    # es el contenido en memoria": si termina en una extensión reconocida
    # Y no tiene un newline (el contenido JSON/YAML real casi siempre tiene
    # al menos un salto de línea, una ruta nunca lo tiene), se trata como
    # ruta. Bug real corregido acá: antes se usaba solo os.path.isfile(source),
    # que depende pura y exclusivamente del cwd del proceso en ese momento.
    # Si el caller corría el script desde otro directorio (p.ej. `python
    # examples/demo.py` en vez de `cd examples && python demo.py`), el
    # archivo SÍ existía en disco pero no en esa ruta relativa -- isfile()
    # daba False silenciosamente, y el string "example_protocol.yml" se
    # terminaba tratando como si fuera el CONTENIDO yaml/json a parsear
    # (cae a yaml.safe_load, que lo interpreta como un simple string
    # escalar). Eso nunca lanzaba LoaderError: el load "tenía éxito" y
    # devolvía el string tal cual, y el crash real aparecía recién más
    # tarde y en otro lado (core.py: 'str' object has no attribute 'get'),
    # sin ninguna pista de que el problema era una ruta no encontrada.
    looks_like_path = source.lower().endswith((".json", ".yml", ".yaml")) and "\n" not in source

    is_path = os.path.isfile(source)
    if is_path:
        with open(source, "r", encoding="utf-8") as fh:
            text = fh.read()
        detected = fmt or _detect_format(source)
    elif looks_like_path:
        raise LoaderError(
            f"no se encontró el archivo {source!r} (cwd actual: {os.getcwd()!r}). "
            f"Si la ruta es relativa al script y no al directorio desde donde "
            f"se ejecuta, usá una ruta absoluta, p.ej.: "
            f"os.path.join(os.path.dirname(__file__), {os.path.basename(source)!r})"
        )
    else:
        text = source
        detected = fmt or _detect_format(source)

    if detected == "json":
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LoaderError(f"JSON inválido: {exc}") from exc

    if detected == "yaml":
        if yaml is None:
            raise LoaderError(
                "PyYAML no está instalado. Instalalo con: pip install pyyaml"
            )
        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise LoaderError(f"YAML inválido: {exc}") from exc
        if raw is None:
            raise LoaderError("el YAML está vacío")
        return _yaml_to_protodef(raw, composite_names)

    raise LoaderError(f"formato desconocido: {detected!r} (usar 'json' o 'yaml')")


def protocol_dict_to_yaml(protocol_dict: dict) -> str:
    """
    Operación inversa "mejor esfuerzo": toma un dict ya en forma
    ['tipo', opts] (el formato node-protodef original, p.ej. el
    protocol.json de minecraft-data) y lo convierte a la sintaxis YAML
    natural de shorthand para que sea legible/editable a mano.

    Útil para migrar un protocol.json existente a .yml una sola vez:

        import json
        from protolib.loader import protocol_dict_to_yaml
        raw = json.load(open("protocol.json"))
        open("protocol.yml", "w").write(protocol_dict_to_yaml(raw))
    """
    if yaml is None:
        raise LoaderError("PyYAML no está instalado. Instalalo con: pip install pyyaml")

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
