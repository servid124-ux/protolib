"""
protolib/core.py

Motor central de la librería. La clase Protocol carga un protocol.json
(o un dict de tipos equivalente) y sabe:

    protocol.read_type(type_def, reader, scope, fields)   -> valor python
    protocol.write_type(type_def, value, writer, scope, fields)

    protocol.parse_packet(state, direction, data: bytes) -> ParsedPacket
    protocol.serialize_packet(state, direction, name, params: dict) -> bytes

Tipos compuestos soportados (definidos como ["tipoBase", opciones]):
    container       - lista ordenada de campos con nombre
    array           - lista homogénea, con count fijo, countType, o
                       count-referenciando-otro-campo
    switch          - elige el tipo según el valor de otro campo (compareTo)
    mapper          - traduce un entero crudo a un nombre simbólico (y viceversa)
    option          - valor opcional precedido por un bool ("presente?")
    bitfield        - empaqueta/desempaqueta sub-campos de N bits cada uno
    bitflags        - entero interpretado como conjunto de flags con nombre
    buffer          - bytes crudos, con longitud fija o por countType/count
    pstring         - string con longitud prefijada por countType (varint, u16, etc)
    entityMetadataLoop - lista de entradas hasta encontrar un terminador
    topBitSetTerminatedArray - lista que termina cuando el bit más alto del
                       primer byte leído en una entrada NO está seteado
                       (patrón de listas LEB128-like)

Los tipos primitivos (varint, i32, bool, cstring, etc.) viven en
primitives.py y se resuelven por nombre.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from .io import Reader, Writer
from .primitives import PRIMITIVES
from .conditions import eval_condition
from .errors import (
    UnknownTypeError,
    InvalidTypeDefinition,
    SwitchCaseNotFound,
)
from .loader import load_protocol_dict

TypeDef = Any  # str | [str, dict] -- no tipamos más estricto por simplicidad


def resolve_field_path(path: Any, fields: dict, root: dict | None, parent: dict | None) -> Any:
    """
    Resuelve un path de campo estilo node-protodef (utils.js:getField).

    Soporta:
      "nombre"        -> fields["nombre"]                 (campo del container actual)
      "../nombre"     -> parent["nombre"]                 (un nivel arriba)
      "/nombre"       -> root["nombre"]                   (desde la raíz absoluta)

    Además, paridad con node-protodef: `count`/`countFor` puede ser
    directamente un entero literal (tamaño fijo), no solo un path de campo
    -- p.ej. ["array", {"count": 4, "type": "i32"}]. En ese caso se
    devuelve tal cual, sin tratarlo como string.

    Nota: node-protodef sube niveles siguiendo un puntero '..' que cada
    container encadena a su padre real, así que "../../x" puede subir N
    niveles. Este port no mantiene esa cadena completa (solo fields/root/
    parent explícitos), así que "../../x" y superior caen a "root" -- cubre
    el caso real usado en protocolos existentes (como mucho un "../").
    """
    if isinstance(path, int):
        return path

    if path.startswith("/"):
        context = root if root is not None else fields
        segments = [s for s in path.split("/") if s != ""]
    elif path.startswith("../"):
        up_count = 0
        rest = path
        while rest.startswith("../"):
            up_count += 1
            rest = rest[3:]
        context = parent if up_count == 1 else (root if root is not None else parent)
        segments = [s for s in rest.split("/") if s != ""]
    else:
        context = fields
        segments = [s for s in path.split("/") if s != ""]

    current = context
    for seg in segments:
        if current is None:
            return None
        current = current.get(seg) if isinstance(current, dict) else None
    return current


def substitute_type_args(type_def: Any, type_args: dict) -> Any:
    """
    Sustituye placeholders "$nombre" dentro de una definición de tipo por el
    valor correspondiente en `type_args`, recorriendo recursivamente dicts y
    listas. Paridad con node-protodef (protodef.js: findArgs/setField/
    produceArgs, usado por extendType).

    Esto es lo que permite tipos nombrados "parametrizables" como:

        entityMetadataItem:
          switch:
            compareTo: $compareTo
            fields: {...}

    invocados en el protocolo como:

        type:
          - entityMetadataItem
          - compareTo: type

    Al invocarse así, la definición default de entityMetadataItem (que tiene
    el placeholder "$compareTo") se clona y el placeholder se reemplaza por
    el valor real pasado en el punto de uso ("type" en este ejemplo) --
    resultando en un switch efectivo con compareTo="type".

    Un placeholder sin valor provisto en `type_args` (o `type_args=None`)
    se deja tal cual el string "$nombre" -- igual que node-protodef, que
    solo sustituye los args que sí vinieron en typeArgs.
    """
    if isinstance(type_def, str):
        if type_def.startswith("$") and isinstance(type_args, dict):
            key = type_def[1:]
            if key in type_args:
                return type_args[key]
        return type_def
    if isinstance(type_def, list):
        return [substitute_type_args(item, type_args) for item in type_def]
    if isinstance(type_def, dict):
        return {k: substitute_type_args(v, type_args) for k, v in type_def.items()}
    return type_def


@dataclass
class ParsedPacket:
    name: str
    params: dict[str, Any]
    bytes_read: int


@dataclass
class Scope:
    """Representa un 'state.direction' (p.ej. play.toClient) con sus tipos propios,
    que tienen prioridad sobre los tipos globales del protocolo."""

    types: dict[str, TypeDef] = field(default_factory=dict)


class Protocol:
    """
    Carga y representa un protocol.json completo.

    Estructura esperada del dict de entrada (mismo formato que
    node-minecraft-protocol / node-protodef):

        {
          "types": { "<nombre>": <typeDef>, ... },
          "<state>": {
             "toClient": { "types": { ... } },
             "toServer": { "types": { ... } }
          },
          ...
        }

    Los nombres de tipo se resuelven primero contra el scope local
    (state.direction.types) y si no aparecen ahí, contra los tipos globales.

    `protocol_source` acepta:
      - un dict ya parseado (formato node-protodef clásico)
      - una ruta a archivo .json, .yml o .yaml
      - un string con contenido JSON o YAML en memoria

    Los archivos .yml/.yaml admiten una sintaxis "shorthand" más legible
    (ver protodef/loader.py), que se traduce automáticamente al formato
    interno ["tipo", opciones]. El JSON de minecraft-data funciona sin
    cambios.
    """

    def __init__(self, protocol_source: dict[str, Any] | str, *, fmt: str | None = None):
        protocol_json = load_protocol_dict(protocol_source, fmt=fmt)
        self.raw = protocol_json
        self.global_types: dict[str, TypeDef] = protocol_json.get("types", {})
        self._scopes: dict[tuple[str, str], Scope] = {}

        for state_name, state_val in protocol_json.items():
            if state_name == "types" or not isinstance(state_val, dict):
                continue
            for direction in ("toClient", "toServer"):
                if direction in state_val:
                    self._scopes[(state_name, direction)] = Scope(
                        types=state_val[direction].get("types", {})
                    )

        self._composite_handlers: dict[str, Callable] = {
            "container": self._read_container,
            "array": self._read_array,
            "count": self._read_count,
            "switch": self._read_switch,
            "mapper": self._read_mapper,
            "option": self._read_option,
            "bitfield": self._read_bitfield,
            "bitflags": self._read_bitflags,
            "buffer": self._read_buffer,
            "pstring": self._read_pstring,
            "entityMetadataLoop": self._read_entity_metadata_loop,
            "topBitSetTerminatedArray": self._read_top_bit_set_terminated_array,
            "cstring": self._read_cstring_encoded,
        }
        self._composite_write_handlers: dict[str, Callable] = {
            "container": self._write_container,
            "array": self._write_array,
            "count": self._write_count,
            "switch": self._write_switch,
            "mapper": self._write_mapper,
            "option": self._write_option,
            "bitfield": self._write_bitfield,
            "bitflags": self._write_bitflags,
            "buffer": self._write_buffer,
            "pstring": self._write_pstring,
            "entityMetadataLoop": self._write_entity_metadata_loop,
            "topBitSetTerminatedArray": self._write_top_bit_set_terminated_array,
            "cstring": self._write_cstring_encoded,
        }

    # -------------------------------------------------------------------
    # Resolución de nombres de tipo
    # -------------------------------------------------------------------

    def get_scope(self, state: str, direction: str) -> Scope:
        try:
            return self._scopes[(state, direction)]
        except KeyError:
            raise UnknownTypeError(f"{state}.{direction} (state/direction no definido)")

    def _resolve_named_type(self, name: str, scope: Scope | None) -> TypeDef | None:
        if scope is not None and name in scope.types:
            return scope.types[name]
        if name in self.global_types:
            return self.global_types[name]
        return None

    # -------------------------------------------------------------------
    # Lectura
    # -------------------------------------------------------------------

    def read_type(self, type_def: TypeDef, r: Reader, scope: Scope | None,
                   fields: dict[str, Any], root: dict | None = None,
                   parent: dict | None = None) -> Any:
        if isinstance(type_def, str):
            if type_def in PRIMITIVES:
                return PRIMITIVES[type_def].read(r)
            resolved = self._resolve_named_type(type_def, scope)
            if resolved is None:
                raise UnknownTypeError(type_def)
            return self.read_type(resolved, r, scope, fields, root, parent)

        if isinstance(type_def, list) and len(type_def) == 2:
            base, opts = type_def
            handler = self._composite_handlers.get(base)
            if handler is not None:
                return handler(opts, r, scope, fields, root, parent)
            # `base` no es un tipo base compuesto (container/switch/array/...)
            # -- puede ser un tipo NOMBRADO parametrizable, invocado como
            # [nombre, typeArgs], p.ej. [entityMetadataItem, {compareTo: type}].
            # Paridad con node-protodef (protodef.js: extendType/produceArgs):
            # se resuelve la definición default del tipo nombrado y se
            # sustituyen los placeholders "$arg" por los typeArgs dados acá.
            named = self._resolve_named_type(base, scope)
            if named is None:
                raise UnknownTypeError(f"(tipo base compuesto) {base}")
            substituted = substitute_type_args(named, opts if isinstance(opts, dict) else None)
            return self.read_type(substituted, r, scope, fields, root, parent)

        raise InvalidTypeDefinition(type_def)

    # ---- container ------------------------------------------------------

    def _read_container(self, opts: list[dict], r: Reader, scope: Scope,
                          fields: dict, root: dict | None, parent: dict | None,
                          *, push_level: bool = True) -> dict:
        result: dict[str, Any] = {}
        effective_root = root if root is not None else result
        # `push_level` decide si ESTE container pasa a ser el `parent`
        # ("..") que ven sus propios sub-campos. Un container normal
        # (resuelto por nombre, campo con nombre, etc.) sí empuja nivel --
        # así funciona node-protodef. La excepción es un container usado
        # directamente como item_type inline de un array: ahí node-protodef
        # NO empuja contexto (el array es transparente para ".."), así que
        # "../campo" dentro de un item de array debe saltar ese item y
        # resolver contra el parent real que el array mismo tenía (p.ej.
        # el container que contiene el array, no el item con `uuid`).
        child_parent = result if push_level else parent
        for f in opts:
            if "condition" in f:
                if not eval_condition(f["condition"], result, effective_root, fields):
                    continue
            value = self.read_type(f["type"], r, scope, result, effective_root, child_parent)
            if f.get("anon"):
                if isinstance(value, dict):
                    result.update(value)
            else:
                result[f["name"]] = value
        return result

    def _write_container(self, opts: list[dict], value: dict, w: Writer, scope: Scope,
                           fields: dict, root: dict | None, parent: dict | None,
                           *, push_level: bool = True) -> None:
        data = value or {}
        effective_root = root if root is not None else data
        child_parent = data if push_level else parent
        for f in opts:
            if "condition" in f:
                if not eval_condition(f["condition"], data, effective_root, fields):
                    continue
            if f.get("anon"):
                self.write_type(f["type"], data, w, scope, data, effective_root, child_parent)
            else:
                self.write_type(f["type"], data.get(f["name"]), w, scope, data, effective_root, child_parent)

    # ---- array ------------------------------------------------------------

    def _read_array(self, opts: dict, r: Reader, scope: Scope,
                      fields: dict, root, parent) -> list:
        if "count" in opts:
            count = resolve_field_path(opts["count"], fields, root, parent)
            if not isinstance(count, int) or count < 0:
                raise InvalidTypeDefinition(
                    f"array: 'count' ({opts['count']!r}) resolvió a {count!r}, "
                    f"se esperaba un entero >= 0 -- revisá que el campo exista "
                    f"y que el path (../, /) apunte al nivel correcto"
                )
        elif "countType" in opts:
            count = self.read_type(opts["countType"], r, scope, fields, root, parent)
        else:
            raise InvalidTypeDefinition("array requiere 'count' o 'countType'")

        item_type = opts["type"]
        return [self._read_array_item(item_type, r, scope, fields, root, parent) for _ in range(count)]

    def _read_array_item(self, item_type, r: Reader, scope: Scope,
                           fields: dict, root, parent) -> Any:
        # Un container inline (["container", opts], literal dentro del
        # array.type -- no un tipo nombrado como "packet_player_info") no
        # debe empujar su propio nivel de parent: ver comentario en
        # _read_container. Cualquier otro item_type (tipo nombrado, switch,
        # primitivo) se comporta exactamente igual que antes.
        if isinstance(item_type, list) and len(item_type) == 2 and item_type[0] == "container":
            return self._read_container(item_type[1], r, scope, fields, root, parent, push_level=False)
        return self.read_type(item_type, r, scope, fields, root, parent)

    def _write_array(self, opts: dict, value: list, w: Writer, scope: Scope,
                       fields: dict, root, parent) -> None:
        items = value or []
        if "countType" in opts:
            self.write_type(opts["countType"], len(items), w, scope, fields, root, parent)
        # si usa "count" (referencia a otro campo), el caller debe haber
        # escrito ese campo antes (responsabilidad del container que arma 'fields')
        item_type = opts["type"]
        for item in items:
            self._write_array_item(item_type, item, w, scope, fields, root, parent)

    def _write_array_item(self, item_type, item, w: Writer, scope: Scope,
                            fields: dict, root, parent) -> None:
        if isinstance(item_type, list) and len(item_type) == 2 and item_type[0] == "container":
            self._write_container(item_type[1], item, w, scope, fields, root, parent, push_level=False)
            return
        self.write_type(item_type, item, w, scope, fields, root, parent)

    # ---- count (length-prefix "separado", declarado como campo hermano) -------
    # Uso típico: un container donde el prefijo de longitud de un array/buffer
    # NO está pegado a ese array (caso normal de countType), sino que es un
    # campo propio en otra posición del container, y el array referencia ese
    # campo por nombre via su propio "count". El tipo `count` en sí, al leer,
    # simplemente lee un entero (typeArgs.type); al escribir, IGNORA el valor
    # que se le pase y escribe len(getField(countFor)) -- igual que
    # node-protodef structures.js: readCount/writeCount.
    #
    # opts: {"type": "u8"|"varint"|..., "countFor": "<path al campo, admite ../ y />"}

    def _read_count(self, opts: dict, r: Reader, scope: Scope,
                      fields: dict, root, parent) -> Any:
        return self.read_type(opts["type"], r, scope, fields, root, parent)

    def _write_count(self, opts: dict, value: Any, w: Writer, scope: Scope,
                       fields: dict, root, parent) -> None:
        target = resolve_field_path(opts["countFor"], fields, root, parent)
        length = len(target) if target is not None else 0
        self.write_type(opts["type"], length, w, scope, fields, root, parent)

    # ---- switch -------------------------------------------------------------

    def _resolve_compare_value(self, opts: dict, fields: dict, root, parent) -> Any:
        # node-protodef (compiler-conditional.js): el switch compara contra
        # `compareTo` (path a un campo ya leído/escrito) O contra
        # `compareToValue` (un literal fijo, sin indirección -- p.ej. útil
        # para elegir tipo según el nombre del propio packet, que ya viene
        # calculado desde afuera). Solo uno de los dos debería estar presente.
        if "compareToValue" in opts:
            return opts["compareToValue"]
        compare_to = opts["compareTo"]
        if compare_to.startswith("fields."):
            return eval_condition(compare_to, fields, root, parent)
        if compare_to in fields:
            return fields[compare_to]
        # Paths estilo node-protodef (utils.js:getField): "../campo" sube al
        # container padre, "/campo" es absoluto desde la raíz. Antes esto
        # caía a eval_condition, que solo entiende sintaxis "$parent.campo"
        # -- nunca "../campo" -- así que un compareTo con ".." siempre
        # resolvía a None (bug real detrás de SwitchCaseNotFound con
        # compareTo="../algo"). resolve_field_path es quien sabe navegar
        # "../" y "/", así que lo usamos primero para esos casos.
        if compare_to.startswith("../") or compare_to.startswith("/"):
            return resolve_field_path(compare_to, fields, root, parent)
        try:
            return eval_condition(compare_to, fields, root, parent)
        except Exception:
            return None

    def _resolve_switch_case(self, opts: dict, compare_val: Any, root) -> TypeDef | None:
        case_key = str(compare_val) if not isinstance(compare_val, str) else compare_val
        case_type = opts["fields"].get(case_key, opts["fields"].get(compare_val))
        if case_type is not None:
            return case_type
        # Keys que empiezan con "/" se resuelven contra el root context, no
        # como comparación de string -- paridad con compiler-conditional.js:
        # `if (val.startsWith('/')) val = 'ctx.' + val.slice(1)`. Recorremos
        # las keys del switch buscando una cuyo valor-resuelto-en-root matchee.
        for key, type_for_key in opts["fields"].items():
            if key.startswith("/"):
                root_val = resolve_field_path(key, {}, root, None)
                if root_val == compare_val:
                    return type_for_key
        return None

    def _identify_compare_ref(self, opts: dict) -> str:
        return opts.get("compareToValue", opts.get("compareTo"))

    def _read_switch(self, opts: dict, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> Any:
        compare_val = self._resolve_compare_value(opts, fields, root, parent)
        case_type = self._resolve_switch_case(opts, compare_val, root)
        if case_type is None:
            if "default" in opts:
                return self.read_type(opts["default"], r, scope, fields, root, parent)
            raise SwitchCaseNotFound(self._identify_compare_ref(opts), compare_val)
        return self.read_type(case_type, r, scope, fields, root, parent)

    def _write_switch(self, opts: dict, value: Any, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        compare_val = self._resolve_compare_value(opts, fields, root, parent)
        case_type = self._resolve_switch_case(opts, compare_val, root)
        if case_type is None:
            if "default" in opts:
                return self.write_type(opts["default"], value, w, scope, fields, root, parent)
            raise SwitchCaseNotFound(self._identify_compare_ref(opts), compare_val)
        return self.write_type(case_type, value, w, scope, fields, root, parent)

    # ---- mapper (entero <-> nombre simbólico) --------------------------------

    @staticmethod
    def _normalize_mapper_key(key: Any) -> int:
        """
        Bug real: las mapping keys 'pueden venir como \"0x00\", \"0x1f\",
        \"31\", etc.' según el comentario original -- pero eso asume que
        SIEMPRE llegan como string. Un mapper.mappings escrito en YAML con
        una key numérica/hex SIN comillas (0x00: identification, en vez de
        '0x00': identification) es indistinguible visualmente de la forma
        con comillas, pero PyYAML resuelve esa key como int nativo, no
        como string -- confirmado empíricamente (yaml.safe_load({0x00: x})
        da {0: 'x'}, con key int). El código anterior llamaba key.lower()
        incondicionalmente y reventaba con AttributeError: 'int' object
        has no attribute 'lower' en cuanto ese mapper se usara para
        leer/escribir un paquete real -- no al cargar el protocolo, así
        que el error aparecía lejos de la causa real y sin ninguna pista.
        Los protocolos de examples/ nunca lo mostraron porque ahí SIEMPRE
        se citan las keys a mano ('0x00': ...) -- una convención que no
        está documentada en ningún lado, así que cualquiera que escriba
        un protocol.yml nuevo sin saberlo pisa este bug. `switch` ya era
        inmune a esto (_resolve_switch_case prueba tanto la key-string
        como el valor crudo), así que `mapper` ahora sigue el mismo criterio.
        """
        if isinstance(key, int):
            return key
        return int(key, 16) if key.lower().startswith("0x") else int(key)

    def _read_mapper(self, opts: dict, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> Any:
        raw = self.read_type(opts["type"], r, scope, fields, root, parent)
        mappings = opts["mappings"]
        # normalizamos las keys del mapping (que pueden venir como "0x00",
        # "0x1f", "31", 0x1f sin comillas, etc.) a entero, para no depender
        # del formato exacto con el que esté escrito el protocol.json/yml
        for key, mapped_name in mappings.items():
            if self._normalize_mapper_key(key) == raw:
                return mapped_name
        return raw  # sin mapping conocido: se devuelve el valor crudo

    def _write_mapper(self, opts: dict, value: Any, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        mappings = opts["mappings"]
        if isinstance(value, str):
            numeric = None
            for k, v in mappings.items():
                if v == value:
                    numeric = self._normalize_mapper_key(k)
                    break
            if numeric is None:
                raise InvalidTypeDefinition(
                    f"mapper: no se encontró mapping inverso para {value!r}"
                )
        else:
            numeric = value
        self.write_type(opts["type"], numeric, w, scope, fields, root, parent)


    # ---- option (presente si un bool previo es true) -------------------------

    def _read_option(self, opts: TypeDef, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> Any:
        present = PRIMITIVES["bool"].read(r)
        if not present:
            return None
        return self.read_type(opts, r, scope, fields, root, parent)

    def _write_option(self, opts: TypeDef, value: Any, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        present = value is not None
        PRIMITIVES["bool"].write(present, w)
        if present:
            self.write_type(opts, value, w, scope, fields, root, parent)

    # ---- bitfield (sub-campos empaquetados en N bits) -------------------------

    def _read_bitfield(self, opts: list[dict], r: Reader, scope: Scope,
                         fields: dict, root, parent) -> dict:
        # node-protodef (structures.js:readBitField) NO exige que la suma de
        # bits sea múltiplo de 8: si sobran bits al final, se rellenan con
        # padding hasta completar el último byte. Ya no lanzamos error acá;
        # simplemente redondeamos hacia arriba al byte siguiente.
        total_bits = sum(f["size"] for f in opts)
        num_bytes = (total_bits + 7) // 8
        pad_bits = num_bytes * 8 - total_bits
        raw_bytes = r.read_bytes(num_bytes)
        big = int.from_bytes(raw_bytes, "big")

        result: dict[str, Any] = {}
        bits_left = total_bits + pad_bits
        for f in opts:
            bits_left -= f["size"]
            mask = (1 << f["size"]) - 1
            val = (big >> bits_left) & mask
            if f.get("signed") and val >= (1 << (f["size"] - 1)):
                val -= 1 << f["size"]
            result[f["name"]] = val
        return result

    def _write_bitfield(self, opts: list[dict], value: dict, w: Writer, scope: Scope,
                          fields: dict, root, parent) -> None:
        # Mismo criterio que _read_bitfield: si el total de bits no completa
        # un byte, se rellena (padding) desplazando a la izquierda el sobrante
        # -- paridad con node-protodef: `buffer[offset++] = toWrite << (8 - bits)`
        # en el último byte cuando quedan bits < 8 sin usar.
        total_bits = sum(f["size"] for f in opts)
        num_bytes = (total_bits + 7) // 8
        pad_bits = num_bytes * 8 - total_bits
        big = 0
        for f in opts:
            v = value.get(f["name"], 0) & ((1 << f["size"]) - 1)
            big = (big << f["size"]) | v
        big <<= pad_bits
        w.write_bytes(big.to_bytes(num_bytes, "big"))

    # ---- bitflags (entero como set de flags con nombre) ------------------------

    def _read_bitflags(self, opts: dict, r: Reader, scope: Scope,
                         fields: dict, root, parent) -> dict[str, Any]:
        raw = self.read_type(opts["type"], r, scope, fields, root, parent)
        flags_def = opts["flags"]
        shift = opts.get("shift", False)

        # flags como dict: {"nombre": bitmask, ...} o, si shift=True,
        # {"nombre": posiciónDeBit, ...} -- paridad con spec oficial
        # (utils.md: "shift: Specify if flags is an object and holds bit
        # positions as values opposed to a bitmask").
        if isinstance(flags_def, dict):
            result: dict[str, Any] = {}
            for flag_name, raw_mask in flags_def.items():
                mask = (1 << raw_mask) if shift else raw_mask
                result[flag_name] = bool(raw & mask)
            result["_value"] = raw
            return result

        # flags como lista posicional (cada uno ocupa 1 bit, LSB primero,
        # o MSB primero si big=True) -- paridad con spec oficial.
        flag_names: list[str] = flags_def
        big_endian = opts.get("big", False)
        result: dict[str, Any] = {}
        names = list(reversed(flag_names)) if big_endian else flag_names
        for i, flag_name in enumerate(names):
            if flag_name is None:
                continue
            result[flag_name] = bool((raw >> i) & 1)
        # Paridad con node-protodef: el valor crudo siempre viaja en '_value',
        # además de cada flag individual con su propio nombre.
        result["_value"] = raw
        return result

    def _write_bitflags(self, opts: dict, value: dict, w: Writer, scope: Scope,
                          fields: dict, root, parent) -> None:
        flags_def = opts["flags"]
        shift = opts.get("shift", False)

        # Spec oficial: al escribir, el valor esperado viene envuelto como
        # {"flags": {...}}. Por retrocompatibilidad con protocolos que ya
        # dependían de este port pasando el dict de flags "pelado" (sin
        # envolver), aceptamos ambas formas.
        flag_values = value.get("flags", value) if isinstance(value, dict) else {}

        if isinstance(flags_def, dict):
            raw = 0
            for flag_name, raw_mask in flags_def.items():
                mask = (1 << raw_mask) if shift else raw_mask
                if flag_values.get(flag_name):
                    raw |= mask
            self.write_type(opts["type"], raw, w, scope, fields, root, parent)
            return

        flag_names: list[str] = flags_def
        big_endian = opts.get("big", False)
        names = list(reversed(flag_names)) if big_endian else flag_names
        raw = 0
        for i, flag_name in enumerate(names):
            if flag_name is None:
                continue
            if flag_values.get(flag_name):
                raw |= (1 << i)
        self.write_type(opts["type"], raw, w, scope, fields, root, parent)

    # ---- buffer (bytes crudos) ------------------------------------------------

    def _read_buffer(self, opts: dict, r: Reader, scope: Scope,
                       fields: dict, root, parent) -> bytes:
        if "count" in opts:
            count = resolve_field_path(opts["count"], fields, root, parent)
            if not isinstance(count, int) or count < 0:
                raise InvalidTypeDefinition(
                    f"buffer: 'count' ({opts['count']!r}) resolvió a {count!r}, "
                    f"se esperaba un entero >= 0 -- revisá que el campo exista "
                    f"y que el path (../, /) apunte al nivel correcto"
                )
        elif "countType" in opts:
            count = self.read_type(opts["countType"], r, scope, fields, root, parent)
        elif opts.get("rest"):
            count = r.remaining
        else:
            raise InvalidTypeDefinition("buffer requiere 'count', 'countType' o 'rest'")
        return r.read_bytes(count)

    def _write_buffer(self, opts: dict, value: bytes, w: Writer, scope: Scope,
                        fields: dict, root, parent) -> None:
        data = value or b""
        if "countType" in opts:
            self.write_type(opts["countType"], len(data), w, scope, fields, root, parent)
        elif "count" in opts and isinstance(opts["count"], int):
            # count fijo (entero literal, no un path a otro campo): a
            # diferencia de countType, acá NADIE escribe un largo antes
            # -- el framing del protocolo asume que este campo ocupa
            # EXACTAMENTE ese tanto de bytes. Si `data` viene más corto
            # o más largo, escribir tal cual dejaría el siguiente campo
            # desalineado en quien reciba el paquete, sin ningún error
            # visible acá. Mejor fallar ruidoso que corromper el framing.
            if len(data) != opts["count"]:
                raise InvalidTypeDefinition(
                    f"buffer: se esperaban exactamente {opts['count']} bytes "
                    f"(count fijo), pero el valor tiene {len(data)} -- si el "
                    f"tamaño puede variar, generá un primitivo dedicado con "
                    f"padding explícito (ver make_fixed_buffer en "
                    f"primitives.py) en vez de 'buffer' genérico"
                )
        w.write_bytes(data)

    # ---- pstring (string con longitud prefijada configurable) -----------------

    def _read_pstring(self, opts: dict, r: Reader, scope: Scope,
                        fields: dict, root, parent) -> str:
        count_type = opts.get("countType", "varint")
        encoding = opts.get("encoding", "utf-8")
        if "count" in opts:
            length = resolve_field_path(opts["count"], fields, root, parent)
            if not isinstance(length, int) or length < 0:
                raise InvalidTypeDefinition(
                    f"pstring: 'count' ({opts['count']!r}) resolvió a {length!r}, "
                    f"se esperaba un entero >= 0 -- revisá que el campo exista "
                    f"y que el path (../, /) apunte al nivel correcto"
                )
        else:
            length = self.read_type(count_type, r, scope, fields, root, parent)
        data = r.read_bytes(length)
        return data.decode(encoding)

    def _write_pstring(self, opts: dict, value: str, w: Writer, scope: Scope,
                         fields: dict, root, parent) -> None:
        count_type = opts.get("countType", "varint")
        encoding = opts.get("encoding", "utf-8")
        data = value.encode(encoding)
        if "count" not in opts:
            self.write_type(count_type, len(data), w, scope, fields, root, parent)
        w.write_bytes(data)

    # ---- entityMetadataLoop (lista hasta encontrar terminador) -----------------

    def _read_entity_metadata_loop(self, opts: dict, r: Reader, scope: Scope,
                                      fields: dict, root, parent) -> list:
        """
        opts:
          endVal: valor crudo (byte) que indica fin de la lista.
          endType: tipo primitivo de 1 byte usado solo para el chequeo de
                   terminador (default 'u8'); NO se usa para decodificar
                   cada entrada real.
          type: tipo completo de cada entrada (típicamente un container
                que ya incluye, como campo anon, el bitfield real
                type/key de Minecraft -- ver entityMetadata en el yml).

        Bug real corregido acá: la versión anterior leía el primer byte de
        cada entrada con `end_type` y lo usaba directo como "index" para
        decidir el caso del switch -- pero ese byte es en realidad un
        bitfield empaquetado `(type<<5)|key`, ya declarado como tal dentro
        de `item_type` (el container en el yml). Al leerlo dos veces con
        semánticas distintas (una vez acá como entero plano, descartado, y
        de nuevo dentro del container como bitfield) el offset del reader
        quedaba desalineado con el resto del paquete, produciendo
        BufferUnderrun más adelante en la misma lectura.

        La forma correcta (y genérica, sin hardcodear bits acá) es:
        espiar el byte con peek_byte() SIN consumirlo para chequear si es
        el terminador; si no lo es, dejar que `item_type` (el container
        real, definido en el yml) lea la entrada completa -- incluyendo
        ese mismo byte como parte de su propio bitfield.
        """
        end_val = opts.get("endVal", 0xFF)
        item_type = opts["type"]

        result = []
        while True:
            if r.peek_byte() == end_val:
                r.read_bytes(1)  # consumir el terminador
                break
            entry = self.read_type(item_type, r, scope, fields, root, parent)
            result.append(entry)
        return result

    def _is_container_type(self, type_def: TypeDef, scope: Scope) -> bool:
        """Resuelve (sin leer/escribir bytes) si un tipo es, en el fondo, un
        container -- para saber si una entrada de entityMetadataLoop debe
        pasarse como dict completo o desenvuelta en 'value'."""
        seen: set[str] = set()
        current = type_def
        while isinstance(current, str):
            if current in seen:
                return False  # ciclo raro, no asumimos container
            seen.add(current)
            if current in PRIMITIVES:
                return False
            resolved = self._resolve_named_type(current, scope)
            if resolved is None:
                return False
            current = resolved
        if isinstance(current, list) and len(current) == 2:
            return current[0] == "container"
        return False

    def _write_entity_metadata_loop(self, opts: dict, value: list, w: Writer, scope: Scope,
                                       fields: dict, root, parent) -> None:
        """
        Simétrico al fix de _read_entity_metadata_loop: cada `entry` en
        `value` es el dict completo tal como lo devuelve la lectura
        (típicamente {'type':.., 'key':.., 'value':..}, producido por el
        container real declarado en `item_type` -- bitfield anon type/key
        + campo nombrado value/switch). Se escribe con item_type
        directamente, dejando que el container/bitfield/switch internos
        empaqueten el byte type/key y el payload como corresponda. Al
        final se escribe el byte terminador crudo (end_val) con end_type.
        """
        end_val = opts.get("endVal", 0xFF)
        end_type = opts.get("endType", "u8")
        item_type = opts["type"]

        for entry in (value or []):
            self.write_type(item_type, entry, w, scope, fields, root, parent)
        self.write_type(end_type, end_val, w, scope, fields, root, parent)

    # ---- topBitSetTerminatedArray ------------------------------------------------

    def _read_top_bit_set_terminated_array(self, opts: dict, r: Reader, scope: Scope,
                                              fields: dict, root, parent) -> list:
        """
        Lee entradas de `type` mientras el bit más alto (0x80) del PRIMER byte
        de cada entrada esté seteado. Patrón típico de listas LEB128-like
        en RakNet / formatos custom (cada entrada "anuncia" si hay otra después).
        """
        item_type = opts["type"]
        result = []
        while True:
            start_offset = r.offset
            item = self.read_type(item_type, r, scope, fields, root, parent)
            result.append(item)
            first_byte = r.buffer[start_offset]
            if not (first_byte & 0x80):
                break
        return result

    def _write_top_bit_set_terminated_array(self, opts: dict, value: list, w: Writer, scope: Scope,
                                               fields: dict, root, parent) -> None:
        """
        Al escribir, el llamador es responsable de que cada item ya traiga el
        bit alto seteado salvo el último (esto refleja el protocolo real: el
        marcador de continuación suele ser parte de los datos del propio item,
        no algo que este wrapper pueda inventar).
        """
        item_type = opts["type"]
        items = value or []
        for item in items:
            self.write_type(item_type, item, w, scope, fields, root, parent)

    # -------------------------------------------------------------------
    # Escritura
    # -------------------------------------------------------------------

    def write_type(self, type_def: TypeDef, value: Any, w: Writer, scope: Scope | None,
                    fields: dict[str, Any], root: dict | None = None,
                    parent: dict | None = None) -> None:
        if isinstance(type_def, str):
            if type_def in PRIMITIVES:
                PRIMITIVES[type_def].write(value, w)
                return
            resolved = self._resolve_named_type(type_def, scope)
            if resolved is None:
                raise UnknownTypeError(type_def)
            return self.write_type(resolved, value, w, scope, fields, root, parent)

        if isinstance(type_def, list) and len(type_def) == 2:
            base, opts = type_def
            handler = self._composite_write_handlers.get(base)
            if handler is not None:
                return handler(opts, value, w, scope, fields, root, parent)
            named = self._resolve_named_type(base, scope)
            if named is None:
                raise UnknownTypeError(f"(tipo base compuesto) {base}")
            substituted = substitute_type_args(named, opts if isinstance(opts, dict) else None)
            return self.write_type(substituted, value, w, scope, fields, root, parent)

        raise InvalidTypeDefinition(type_def)

    # ---- cstring compuesto (["cstring", {"encoding": "..."}]) -----------------
    # El primitivo "cstring" (primitives.py) sigue existiendo tal cual, fuerza
    # utf-8 y no se toca. Esto es un tipo COMPUESTO adicional, solo se activa
    # si el protocol.json usa la forma ["cstring", {opts}] en vez del string
    # "cstring" a secas -- así que es 100% aditivo, no cambia comportamiento
    # existente. Paridad con node-protodef (src/datatypes/utils.js: readCString/
    # writeCString aceptan typeArgs.encoding, default 'utf8').

    def _read_cstring_encoded(self, opts: dict, r: Reader, scope: Scope,
                                fields: dict, root, parent) -> str:
        encoding = (opts or {}).get("encoding", "utf-8")
        out = bytearray()
        while True:
            b = r.read_bytes(1)
            if b == b"\x00":
                break
            out += b
        return out.decode(encoding)

    def _write_cstring_encoded(self, opts: dict, value: str, w: Writer, scope: Scope,
                                 fields: dict, root, parent) -> None:
        encoding = (opts or {}).get("encoding", "utf-8")
        w.write_bytes(value.encode(encoding) + b"\x00")

    # -------------------------------------------------------------------
    # API de alto nivel: paquetes completos
    # -------------------------------------------------------------------

    def parse_packet(self, state: str, direction: str, data: bytes) -> ParsedPacket:
        scope = self.get_scope(state, direction)
        r = Reader(data)
        packet = self.read_type("packet", r, scope, {})
        return ParsedPacket(name=packet["name"], params=packet["params"], bytes_read=r.offset)

    def serialize_packet(self, state: str, direction: str, name: str, params: dict) -> bytes:
        scope = self.get_scope(state, direction)
        w = Writer()
        self.write_type("packet", {"name": name, "params": params}, w, scope, {})
        return w.result()

    # acceso directo a un tipo nombrado (sin pasar por "packet"), útil para tests
    # y para parsear/serializar sub-estructuras sueltas (p.ej. un slot, un NBT)
    def read_named(self, state: str, direction: str, type_name: str, data: bytes) -> Any:
        scope = self.get_scope(state, direction)
        r = Reader(data)
        return self.read_type(type_name, r, scope, {})

    def write_named(self, state: str, direction: str, type_name: str, value: Any) -> bytes:
        scope = self.get_scope(state, direction)
        w = Writer()
        self.write_type(type_name, value, w, scope, {})
        return w.result()
