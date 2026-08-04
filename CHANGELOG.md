# Changelog

## 0.3.4

### Added
- **`anonymousNbt` / `anonOptionalNbt`** (`nbt.py`, registrados como
  primitivos en `primitives.py`): variante de `nbt`/`optionalNbt` SIN el
  prefijo `[u16 nameLen][nameLen bytes]` que sí llevan esos dos -- formato
  `[u8 tagType][payload]` directo. Usado por Minecraft moderno (1.20.2+)
  en chat components, `custom_data` de items, `block_entity_data`, y
  varios otros lugares donde el nombre del tag raíz siempre sería el
  string vacío. Reutiliza `_read_payload`/`_write_payload` de `nbt.py`
  tal cual (mismo código de payload que `read_nbt`/`write_nbt`, solo
  cambia qué se lee/escribe alrededor del tagType). El dict resultante
  no tiene key `"name"` (a diferencia de `read_nbt`), para que sea
  imposible confundir accidentalmente un valor de uno con el del otro.
  `anonOptionalNbt` es al `anonymousNbt` lo que `optionalNbt` ya es a
  `nbt` -- mismo wire format, nombre aparte solo para calzar con
  protocol.json real.
- **`registryEntryHolder`** (composite nuevo en `core.py`): patrón
  `IdOr<T>` de Minecraft moderno -- "id numérico (`idType`, default
  varint) → si es 0, sigue un valor inline del tipo declarado en
  `otherwise.type`; si no, es una referencia a un registro del servidor
  por índice (`raw_id - 1`, para no chocar con el 0 que ya significa
  inline)". Usado para trims, biomas, sonidos, banner patterns, etc.
  donde el dato puede venir precargado por índice de registro (lo
  normal) o mandado completo en el propio paquete (cuando el cliente
  todavía no tiene ese entry registrado). Representación en Python:
  `{"type": "reference", "id": N}` o `{"type": "inline", "value": V}`
  -- explícito y sin ambigüedad, mismo criterio que ya usa la librería
  para NBT/mapper. Lanza `InvalidTypeDefinition` si `value["type"]` no
  es ninguno de los dos al escribir.
- **`registryEntryHolderSet`** (composite nuevo en `core.py`): variante
  de `registryEntryHolder` para CONJUNTOS -- `[varint count]`; si
  `count == 0`, sigue un `cstring` con el nombre de una tag ya conocida
  por ambos lados (típicamente con prefijo `#`, p.ej.
  `"#minecraft:trim_materials"`); si `count > 0`, siguen exactamente
  `count` ids (`idType`, default varint) de una lista explícita --
  acá `count` SÍ es literalmente "cuántos ids hay", sin el +1/-1 de
  `registryEntryHolder` (esa corrección de índice es propia del caso
  "un solo id o inline", no aplica a una lista). Representación en
  Python: `{"type": "tag", "tagName": "..."}` o
  `{"type": "ids", "ids": [...]}`. Nota documentada en los tests: una
  lista `ids` vacía serializa igual que el caso `tag` (`count=0` en
  ambos) -- así es también el protocolo vanilla real, no es una
  limitación de este port.
- Ambos composites nuevos se agregaron como entradas nuevas en
  `_composite_handlers`/`_composite_write_handlers` (dict, en
  `Protocol.__init__`) y como métodos nuevos al final de la clase
  `Protocol`, antes de la sección "API de alto nivel" -- ninguna línea
  existente de `core.py` se tocó para esto.
- Tests: `TestAnonymousNbtRoundtrip` / `TestAnonymousNbtViaPrimitivesRegistry`
  en `test_nbt.py` (roundtrip, caso `None`/`TAG_End`, comparación de
  tamaño byte a byte contra `nbt` con nombre vacío) y
  `TestRegistryEntryHolder` / `TestRegistryEntryHolderSet` en
  `test_core_composites.py` (ambos casos de cada composite, tipo de id
  default vs. custom, uso dentro de un container real, error paths).

## 0.3.3

### Added
- **`tests/`**: suite de +140 tests (`unittest`, corre sin dependencias
  extra y también es compatible con `pytest`) cubriendo `io`, todos los
  primitivos, `conditions`, cada composite handler de `core.py`, `loader`,
  `framer`, `nbt`, y un `test_examples_roundtrip.py` que ejercita
  `example_protocol.yml`/`.json` y `classicube_protocol.yml`/`.json`
  reales de punta a punta (antes de esto, "tested against two real
  protocols" no vivía en ningún lado como código ejecutable).
- **`LICENSE`**: `pyproject.toml` declaraba MIT pero el archivo no
  estaba en el repo.
- **`protolib/py.typed`** (PEP 561): sin esto, mypy/pyright de quien
  instale el paquete no reconocían los type hints como propios.
- `pyproject.toml`: extra `dev` (`pytest`, `pyyaml`) y
  `[tool.pytest.ini_options]`.

### Fixes
- **`conditions.py`**: `eval_condition` con una expresión mal formada
  dejaba escapar un `ValueError` crudo del tokenizer/parser en vez del
  `ConditionError` que `errors.py` define y el README documenta (sección
  12) -- nadie que hiciera `except ConditionError` lo estaba atrapando
  nunca. Encontrado escribiendo los tests de `conditions.py`.
- **`core.py` (`mapper`)**: una key de `mappings` sin comillas en YAML
  (`0x00: nombre` en vez de `'0x00': nombre`) carga como `int` nativo,
  no como string -- PyYAML resuelve hex/numéricos implícitos. `_read_mapper`/
  `_write_mapper` llamaban `key.lower()` sin chequear el tipo primero y
  reventaban con `AttributeError` en cuanto ese mapper se usara para
  leer/escribir un paquete real (no al cargar el protocolo). Los 5
  protocolos de `examples/` nunca lo mostraron porque ahí SIEMPRE se citan
  las keys a mano, una convención no documentada en ningún lado. `switch`
  ya era inmune a esto por su propio diseño; `mapper` ahora sigue el mismo
  criterio (`Protocol._normalize_mapper_key`).
- **`loader.py`**: el shorthand YAML de una sola clave (`buffer:\n  count:
  16`) se corrompía a `["buffer", ["count", 16]]` en vez de `["buffer",
  {"count": 16}]`, porque `count` es TAMBIÉN un nombre de tipo compuesto
  válido y el dict de opciones volvía a pasar por el chequeo completo de
  shorthand. La forma explícita `["buffer", {"count": 16}]` ya tenía esta
  protección (ver comentario original en `_yaml_to_protodef`); ahora la
  rama de shorthand-de-diccionario usa el mismo criterio.

## 0.3.2

### Added
- **`varint128` / `uvarint128`**: LEB128 extendido a 128 bits, para IDs
  grandes que no entran en 64 bits (Snowflake IDs, hashes truncados,
  UUIDs codificados como entero variable en vez de los 16 bytes fijos
  de `UUID`). Reutilizan el mismo helper genérico de `varint`/`varlong`
  (`max_bits=128`); ocupan hasta 19 bytes en el peor caso. Probados en
  0, valores negativos extremos, y valores por encima de 2⁶⁴.

### Fixes
- `pyproject.toml` decía `version = "0.3.1"` pero `protolib/__init__.py`
  seguía en `__version__ = "0.3.0"` (se traspapeló en la subida
  anterior). Ambos ahora están sincronizados en cada release.

## 0.3.0

### Fixes
- **`loader.py`**: cargar un `Protocol("archivo.yml")` fallaba de forma
  silenciosa y confusa si el proceso no estaba parado exactamente en el
  directorio del archivo (dependía de `os.path.isfile()` sobre el cwd).
  El string terminaba tratándose como contenido YAML/JSON crudo en vez
  de ruta, y el crash real aparecía después, en otro módulo, con un
  mensaje que no decía nada del problema real
  (`'str' object has no attribute 'get'`). Ahora se detecta el caso y
  se lanza un `LoaderError` claro, con la ruta buscada y el cwd usado.
- `examples/demo.py`, `examples/demo_classicube.py`, `examples/servidor.py`:
  resuelven la ruta de su `.yml` relativa al propio script (no al cwd),
  así corren igual sin importar desde qué directorio se invoquen.

### Added
- Primitivos de ancho fijo que faltaban para completar la familia
  8/16/24/32/40/48/56/64 bits: `u40`, `i40`, `u56`, `i56` y sus
  variantes little-endian `lu40`, `li40`, `lu56`, `li56`.
  Con esto quedan las 32 combinaciones completas:
  `{u,i} × {8,16,24,32,40,48,56,64}` y sus versiones `l{u,i}`.

### Docs
- `README.md`: aclaración explícita sobre cómo se resuelve una ruta
  pasada a `Protocol(...)` (relativa al cwd del proceso, no al script),
  y patrón recomendado (`os.path.dirname(os.path.abspath(__file__))`)
  para evitarlo.
- `examples/README.md`: nota de que los demos ahora corren igual desde
  cualquier directorio.

## 0.2.3 (previo, sin changelog registrado)
Estado inicial recibido: engine central (`core.py`), primitivos base
hasta `u24`/`u48`, NBT, framer, loader JSON/YAML, condiciones sin
`eval()`. Incluía ya varios bugs corregidos y documentados en el propio
código (ver comentarios "Bug real corregido acá" en `core.py`).
