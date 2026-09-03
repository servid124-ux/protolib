# Changelog

## 0.4.3

### Nuevos tipos RakNet (compatibilidad más allá de Minecraft)
`node-raknet` (el paquete oficial usado por Minecraft Bedrock por
debajo, y también documentado como protocolo independiente en
https://github.com/vp817/RakNetProtocolDoc) usa ProtoDef igual que
Java/Bedrock, pero con datatypes propios que protolib no cubría
todavía. Revisados los 3 candidatos reales del spec de RakNet:

- **`raknetMagic`** (nueva primitiva): la secuencia fija de 16 bytes
  (`00 FF FF 00 FE FE FE FE FD FD FD FD 12 34 56 78`) que marca los
  paquetes OFFLINE de RakNet (`UnconnectedPing`,
  `OpenConnectionRequest1/2`, etc.). A diferencia de un buffer fijo
  común, **valida al leer** -- un mismatch dispara la nueva excepción
  `MagicMismatchError`, que es exactamente el propósito de este campo
  en el protocolo real (detectar que algo no es un paquete RakNet
  válido). `write()` ignora el valor pasado y siempre emite la
  constante.
- **`raknetAddress`** (nuevo composite): `SystemAddress` de RakNet,
  IPv4 (7 bytes: `u8 version` + `u32 BE` de dirección **con todos los
  bits invertidos** + `u16 BE` puerto) o IPv6 (29 bytes: `u8 version` +
  `u16 LE` address_family + `u16 BE` puerto + `u32 BE` flow_info + 16
  bytes crudos de dirección + `u32 BE` scope_id). La inversión de bits
  del IPv4 es una rareza real de RakNet (no un error de transcripción)
  -- verificada a mano contra bytes conocidos, no solo por roundtrip
  interno (un bug simétrico en la inversión pasaría desapercibido en
  un roundtrip que solo se compara consigo mismo).
- **`bit`** de RakNet (1 bit, MSb-first, padded a byte completo) --
  **no se agregó como tipo nuevo**: ya está cubierto exactamente por
  el `bitfield` existente con un solo campo de `size: 1` (mismo
  resultado byte a byte, confirmado con test dedicado). Agregar un
  tipo redundante hubiera sido peor que no agregar nada.

Validado con: fuzzing de 2000 direcciones IPv4 aleatorias (0 fallos),
verificación manual byte a byte de la inversión de bits (no solo
autoconsistencia), y un packet `UnconnectedPing` real de RakNet
construido y parseado de punta a punta (33 bytes, coincide con el
tamaño esperado por la spec). 20 tests nuevos en
`tests/test_raknet_types.py`.

## 0.4.2

### Soporte de protocolos "stateless" (Minecraft Bedrock-style)
Validado contra `minecraft-data` real (52/52 versiones de
`data/bedrock/*/protocol.json`, desde `0.14` hasta `1.26.40`), además
de las 59 versiones de `data/pc/*/protocol.json` (Java) ya validadas
en 0.4.1 -- ningún tipo faltante en ninguna de las 111 versiones
combinadas.

A diferencia de Java (`handshaking`/`status`/`login`/`configuration`/
`play`, cada uno con `toClient`/`toServer`), el `protocol.json` de
Bedrock **no declara ningún estado**: todo, incluido el tipo
dispatcher de paquetes, vive directo bajo el bloque `types:` de nivel
superior (y ese dispatcher se llama `mcpe_packet`, no `packet`). Antes
de este cambio, `parse_packet`/`serialize_packet` exigían siempre un
`state`/`direction` real (`get_scope` fallaba con `KeyError` para
cualquier protocolo así), obligando a usar `read_type`/`write_type` de
bajo nivel a mano con `scope=None` como único camino para protocolos
sin estados.

- **`Protocol(...)` acepta `packet_type_name`** (nuevo kwarg,
  default `"packet"` -- no rompe nada existente): nombre del tipo
  dispatcher que `parse_packet`/`serialize_packet` leen/escriben.
  Bedrock: `Protocol(bedrock_json, packet_type_name="mcpe_packet")`.
- **`parse_packet`/`serialize_packet`/`read_named`/`write_named`
  aceptan `state=None, direction=None`** cuando el protocolo cargado
  no declara estados (detectado automáticamente en `__init__`,
  `self._stateless`). Con eso, Bedrock funciona con la misma API de
  alto nivel que Java, sin bajar a `read_type`/`write_type`:
  ```python
  proto = Protocol(bedrock_json, packet_type_name="mcpe_packet")
  data = proto.serialize_packet(None, None, "disconnect", {...})
  pkt = proto.parse_packet(None, None, data)
  ```
- Nueva excepción **`StatelessProtocolError`**: si se llama un
  protocolo CON estados pasando `None`, o un protocolo SIN estados
  pasando un `state`/`direction` real, el error explica cuál es el
  caso y qué se esperaba -- en vez de un `UnknownTypeError` genérico
  de más abajo en la resolución de scope.
- `get_scope(state, direction)` sigue exactamente igual que antes
  (sigue exigiendo nombres reales) -- el cambio es aditivo, vive en un
  nuevo helper interno `_resolve_scope` usado solo por los 4 métodos
  de alto nivel.
- 10 tests nuevos en `tests/test_stateless_protocol.py` (roundtrip
  stateless completo, roundtrip stateful sin cambios de
  comportamiento, y los 2 casos de mismatch state/protocolo).

### Plantillas de referencia actualizadas
`examples/full_reference_template.json` y `.yml` (el catálogo
exhaustivo de todos los natives soportados) no incluían todavía
`compressedNbt` ni `lpVec3`, agregados en 0.4.1 -- quedaban
documentados en el `README.md` pero ausentes en las plantillas que
sirven de referencia ejecutable. Agregados en ambos archivos, junto
al resto de los tipos NBT (justo antes de `campo_buffer64`), con
comentario explicativo en el `.yml`. Verificado con roundtrip real de
los 63 campos del container `packet_ejemplo_natives` (ambos formatos,
JSON y YAML, producen el mismo resultado).

## 0.4.1

### Nuevas primitivas (compatibilidad con node-minecraft-protocol)
Comparando el engine contra las custom datatypes de
`node-minecraft-protocol` (`src/datatypes/minecraft.js` y
`compiler-minecraft.js`) para que un `protocol.json` real de Minecraft
cargue en protolib sin necesitar parches, aparecieron dos primitivas
que el motor todavía no tenía (`varlong`, `restBuffer`, `UUID`,
`entityMetadataLoop`, `topBitSetTerminatedArray`,
`registryEntryHolder(Set)` ya existían desde 0.2.0/0.3.4):

- **`compressedNbt`**: NBT gzip-comprimido con longitud como prefijo
  (`i16`), usado por el campo de Slot Data en versiones pre-1.20.2
  (`[i16 length][length bytes de gzip]`, `length == -1` significa
  ausente). Portado 1:1 desde el lector/escritor de
  `node-minecraft-protocol`, reutilizando el `read_nbt`/`write_nbt` ya
  existente para el contenido descomprimido. Nueva excepción
  `NBTDecompressionError` para datos gzip corruptos/truncados (antes
  hubiese sido un `OSError`/`zlib.error` crudo sin contexto).
- **`lpVec3`** ("length-prefixed vec3"): encoding cuantizado y de
  tamaño variable para un triple `{x, y, z}` de floats, usado en
  ciertos campos de posición relativa/delta de Minecraft moderno.
  Portado campo-por-campo desde `src/datatypes/lpVec3.js` (mismas
  constantes `MAX_QUANTIZED_VALUE`/`ABS_MIN_VALUE`/`ABS_MAX_VALUE` y el
  mismo empaquetado de bits) para que el resultado sea el mismo
  vector-tras-cuantización que produciría el original en JS: vector
  cero → 1 byte; vector normal → 6 bytes (1 byte de marcador/escala + 1
  byte + `u32BE` empaquetados como entero de 48 bits con 3 componentes
  cuantizados de 15 bits); si la escala no entra en 2 bits, un varint
  adicional de continuación.

Ambas quedan registradas en `PRIMITIVES` (`"compressedNbt"`,
`"lpVec3"`) y exportadas desde `protolib/__init__.py`
(`read_compressed_nbt`, `write_compressed_nbt`, `NBTDecompressionError`),
con 8 tests nuevos en `tests/test_primitives.py` (roundtrip, tamaño
cero, vector grande con continuación, `size_of` consistente con lo
realmente escrito, NaN saneado a 0, gzip corrupto lanzando la
excepción correcta).

## 0.4.0

### Fixed (encontrados por stress-testing adversarial de protobuf, después del primer release de esta feature)
- **Un miembro de `oneof` puesto explícitamente en su propio valor
  default se perdía indistinguible de "ningún miembro activo"**
  (`proto_codec.py`, `_write_singular_field`): un `oneof` existe
  específicamente para responder "¿cuál miembro está activo?", una
  pregunta distinta de "¿el valor es distinto de su default?". Antes
  de este fix, `{"a": 0}` (miembro `a` explícitamente en 0) y `{}`
  (ningún miembro presente) codificaban ambos a `b""` y decodificaban
  ambos de vuelta a `{}`, perdiendo en silencio cuál miembro (si
  alguno) había sido realmente elegido -- una violación real de la
  semántica de `oneof` de la spec. Encontrado probando precisamente ese
  caso límite tras el primer release de `protolib.protobuf`. Fix: un
  campo miembro de un `oneof` ya NO se omite del wire por estar en su
  valor default; solo se omite si la clave está completamente ausente
  del dict de entrada (igual que antes).
- **Basura dentro del boundary LEN de un submensaje producía un
  `BufferUnderrun` crudo sin contexto en vez de un error claro**
  (`proto_codec.py`, `_read_message_fields`): si los bytes restantes
  dentro del límite declarado de un mensaje no llegaban a formar un tag
  completo (por ejemplo, un LEN prefix que no coincide realmente con lo
  codificado adentro), la excepción de bajo nivel se propagaba desde
  varios frames de profundidad sin decir qué mensaje ni en qué offset
  falló. Encontrado probando bytes corruptos/maliciosos a propósito.
  Fix: el error ahora se re-lanza como `ProtobufDecodeError` con el
  nombre del mensaje y el offset exacto; además se agregó una
  verificación explícita de que ningún campo pueda leer bytes más allá
  del límite de SU PROPIO mensaje (detectado inmediatamente al ocurrir,
  no solo al final del bucle de lectura).

### Verificado exhaustivamente (sin encontrar más problemas)
Tras los dos fixes de arriba, se sometió el codec a pruebas
adversariales adicionales sin encontrar más divergencias de la spec:
valores en los límites exactos de rango de cada tipo escalar (INT32_MIN/
MAX, UINT64_MAX, etc.), overflow explícito rechazado en `uint32`/
`uint64`, UTF-8 real (acentos, emojis, texto mixto), los 256 valores de
byte posibles en un campo `bytes`, field number en el límite exacto de
la spec (536,870,911), mensajes vacíos, buffers truncados a mitad de
varint, un LEN que reclama más bytes de los disponibles, `map` con
clave `int32`/`bool` (no solo `string`) y con valor mensaje (no solo
escalar), claves de `map` duplicadas en el wire (gana la última, como
exige la spec), recursión real de 100 niveles de profundidad y árboles
con 1000 hijos directos.

### Added
- **Soporte real de Google Protocol Buffers (`.proto`)** -- nuevo
  módulo independiente `protolib.protobuf`, separado del motor
  YAML/JSON estilo node-protodef existente (`protolib.core.Protocol`),
  que sigue exactamente igual y no se ve afectado por esto en absoluto.
  Las dos formas de describir un protocolo binario conviven: YAML/JSON
  para el motor genérico de siempre, `.proto` para hablar el formato de
  Google directamente.
  - `wire.py`: el wire format real de protobuf -- varint base-128,
    tags (`field_number << 3 | wire_type`), zigzag para
    `sint32`/`sint64`, `fixed32`/`fixed64`/`float`/`double`
    little-endian, valores LEN-delimited (`string`/`bytes`/mensajes
    embebidos). Verificado byte a byte contra los vectores oficiales de
    la documentación de Google
    (https://protobuf.dev/programming-guides/encoding/): `int32 a=150`
    codifica exactamente a `08 96 01`, `string b="testing"` coincide
    exacto, la tabla de zigzag de 32 bits coincide completa.
  - `proto_lexer.py` / `proto_parser.py`: parser recursivo-descendente
    de archivos `.proto` reales (sintaxis proto3) -- `message`, `enum`,
    anidamiento arbitrario, `repeated`, `map<K, V>`, `oneof`,
    `reserved` (rangos y nombres), opciones de campo/archivo/mensaje
    (`[deprecated = true]`, `option java_package = ...`) parseadas y
    descartadas correctamente (no afectan la forma del wire, que es lo
    único que le importa a este puerto), y bloques `service` (gRPC)
    saltados correctamente sin intentar traducirlos. Sintaxis proto2,
    campos `required`, y `extend` se **rechazan explícitamente** con
    `UnsupportedProtoFeatureError` en vez de parsearse de forma
    incorrecta o silenciosa.
  - `proto_schema.py`: resuelve cada referencia de tipo (`type_name`
    como string en el AST) a su `ProtoMessage`/`ProtoEnum` real,
    siguiendo las reglas propias de resolución de nombres de protobuf:
    lookup por scope anidado (de adentro hacia afuera), nombres
    completamente calificados con `.` inicial, y tipos **recursivos**
    (un mensaje que se referencia a sí mismo, directa o
    indirectamente -- ej. un árbol -- es válido y se resuelve
    correctamente, no se trata como error).
  - `proto_codec.py`: codifica/decodifica dicts de Python contra bytes
    reales de wire format, incluyendo:
    - **Packed encoding** (el default de proto3 para `repeated` de
      tipos numéricos/bool/enum: todos los valores concatenados en un
      solo bloque LEN en vez de un tag por elemento) al escribir, y
      aceptación de la forma **no empacada** al leer (exigido por la
      spec para interoperar con encoders que no empaquen).
    - Omisión correcta de valores en su default (`0`, `""`, `false`,
      valor de enum `0`) al escribir un campo singular -- comportamiento
      real de proto3, no pérdida de datos (el default vuelve solo al
      leer un campo ausente).
    - `map<K, V>` desazucarado a su forma real de mensaje sintético
      (`key = 1; value = 2;`), tal como protobuf lo define internamente.
    - **Exclusividad de `oneof`** en ambas direcciones: escribir un
      dict con más de un miembro del mismo `oneof` presente lanza
      `ProtoOneofViolationError` en vez de escribir ambos al wire
      (encontrado y arreglado durante el desarrollo de esta misma
      feature); leer bytes donde el wire trae más de un miembro del
      mismo `oneof` (un encoder no conforme, datos corruptos) conserva
      solo el **último**, limpiando cualquier miembro anterior del
      mismo grupo del resultado, tal como exige la spec.
    - Forward-compatibility real: un field number desconocido en el
      wire se salta (no es un error), y un valor de enum no reconocido
      hace roundtrip como el entero crudo en vez de fallar -- ambos
      comportamientos exigidos por la spec de protobuf para que un
      binario viejo pueda leer datos de un `.proto` más nuevo sin
      romperse.
  - API pública de alto nivel, a la par de `Protocol.read_named`/
    `write_named` del motor YAML/JSON: `ProtoFileSchema.from_file(path)`
    / `.from_source(texto)`, luego `.encode(nombre_mensaje, dict)` /
    `.decode(nombre_mensaje, bytes)`.
  - Nuevo ejemplo: `examples/addressbook.proto` (el clásico de la
    documentación de Google), usado también como fixture de test.
  - **`examples/full_reference_template.proto`**: catálogo exhaustivo
    de `protolib.protobuf`, contraparte en lenguaje `.proto` de
    `full_reference_template.yml`/`.json` (el catálogo del motor
    YAML/JSON) -- NO es una traducción línea por línea de esos dos
    (protobuf y el motor YAML/JSON son lenguajes de esquema
    independientes con wire formats distintos), sino el catálogo
    equivalente para el motor de protobuf: los 15 tipos escalares,
    `repeated` empacado y no-empacado, mensajes/enums anidados,
    referencias cruzadas entre mensajes, `map<K, V>`, `oneof` (incluido
    el caso de rechazo por violar exclusividad), un tipo recursivo
    (`TreeNode`), y `reserved`. Ejercitado de punta a punta en
    `examples/demo_protobuf.py` y cubierto por 9 tests nuevos
    (`TestFullReferenceTemplate` en `tests/test_protobuf_codec.py`) que
    cargan el archivo real y confirman roundtrip exacto en cada
    mensaje, para que el catálogo no pueda quedar desincronizado en
    silencio de lo que el codec realmente soporta.
  - 72 tests nuevos en total (`tests/test_protobuf_wire.py`,
    `tests/test_protobuf_parser.py`, `tests/test_protobuf_codec.py`,
    esta última incluyendo `TestFullReferenceTemplate`), incluyendo los
    vectores oficiales de la spec, sin tocar ni un solo test de la
    suite existente del motor YAML/JSON.

### Fixed
- **`bitfield` truncaba overflow en silencio al escribir** (`core.py`,
  `_write_bitfield`): un valor que no cabía en los `size` bits
  declarados de un sub-campo se enmascaraba con `& ((1 << size) - 1)`
  sin avisar, corrompiendo el paquete de forma silenciosa en el punto
  equivocado (el bug real queda escondido en quien llamó con un valor
  fuera de rango, no en el bitfield). Inconsistente con el resto de la
  librería: un entero de ancho fijo (`primitives.py`, vía
  `struct.pack`) ya fallaba con `struct.error` en overflow, y un
  `buffer` de tamaño fijo ya fallaba si el tamaño no cuadraba
  (`_write_buffer`). Fix: `_write_bitfield` ahora valida cada sub-campo
  contra el rango que le corresponde según `size`/`signed` (mismo rango
  que tendría un entero de ese ancho) y levanta `BitfieldOverflowError`
  con el nombre del campo, el valor recibido y el rango válido, en vez
  de truncar.
- **`topBitSetTerminatedArray` podía lanzar `IndexError` crudo**
  (`core.py`, `_read_top_bit_set_terminated_array`): el primer byte de
  cada entrada se releía indexando `r.buffer[start_offset]`
  directamente, sin pasar por ninguna verificación de límites. Si
  `item_type` llegaba a consumir 0 bytes justo al final del buffer
  (p. ej. un container inline vacío), esto explotaba con un
  `IndexError` sin ningún contexto en vez de un error de librería
  legible. Fix: nuevo método `Reader.ensure_at(offset, n)` (variante de
  `ensure()` que verifica disponibilidad desde un offset arbitrario, no
  solo desde la posición actual del reader) usado antes de ese
  indexado, para que el fallo -- si ocurre -- sea un `BufferUnderrun`
  normal y legible.
- **Clave de `mapper.mappings` inválida fallaba con `ValueError`/
  `AttributeError` genérico y sin contexto** (`core.py`,
  `_normalize_mapper_key`): un typo en una clave (p. ej. `"0xZZ"` en
  vez de `"0x00"`) hacía que `int(key, 16)` reventara con un error
  crudo de Python, sin decir qué mapper ni qué clave lo causó -- y
  recién la primera vez que ese mapper se usaba para leer/escribir un
  paquete real, lejos de la línea del `protocol.yml` donde estaba el
  typo. Fix: `InvalidMapperKeyError` nueva, con la clave exacta, el
  diccionario `mappings` completo de donde vino, y la razón subyacente.

### Investigado, no arreglado (limitación documentada)
- **`"../../campo"` (dos o más niveles) sigue sin subir la cadena real
  de padres** (`core.py`, `resolve_field_path`): se evaluaron tres
  diseños distintos para que `parent` recuerde su propio ancestro
  (permitiendo subir N niveles reales en vez de caer a `root` después
  del primer salto). Los tres approaches arreglaban el caso de N
  niveles pero introducían regresiones reales en el caso de 1 nivel ya
  soportado (`../campo` dentro de un item de `array`/case de `switch`),
  porque el significado de `parent` cambia según si el nivel actual usó
  `push_level=True` o `push_level=False` -- una ambigüedad de diseño
  más profunda que un fix acotado. Se revirtió cada intento tras
  confirmar la regresión con la suite completa; el comportamiento y la
  limitación documentada quedan exactamente como en 0.3.9.
- **Un tipo NOMBRADO usado directamente como item de `array` (o case de
  `switch`) siempre empuja su propio nivel `parent`, incluso cuando
  "debería" ser transparente a `../`** (`core.py`,
  `_read_array_item`/`_write_array_item`, `_read_switch`/
  `_write_switch`): a diferencia de un container INLINE en esa misma
  posición (que sí es transparente, arreglado en 0.3.7), un tipo
  nombrado que resuelve a un container no lo es -- un `"../x"` dentro
  de sus propios campos de primer nivel no llega al padre real del
  array/switch. Encontrado y reproducido durante el trabajo en el punto
  anterior. Se intentó generalizar la transparencia a cualquier tipo
  nombrado container-shaped; esto arregla ese caso pero rompe el mucho
  más común de un item nombrado con sus PROPIOS sub-containers
  anidados, que legítimamente necesitan que el item sea su propio nivel
  (un `"../"` dentro de un sub-container del item debe resolver contra
  el item, no saltárselo). Los dos casos son indistinguibles solo a
  partir de `item_type`; revertido. Workaround disponible: declarar la
  forma del item inline (`type: {container: [...]}`) en vez de por
  nombre, si se necesita esa transparencia -- esa forma sí funciona hoy.

## 0.3.8

Auditoría directa contra el código fuente real de `node-protodef`
1.19.0 (no contra su documentación) para encontrar divergencias de
comportamiento en los tipos compuestos que ya teníamos portados
(`mapper`, `bitflags`). `container`/`array`/`switch`/`varint`/`pstring`/
`buffer`/`bitfield`/`option`/`count` se auditaron también y ya estaban
a la par o mejor que el original (contexto `push_level` para `../` en
containers inline, `max_bits` parametrizado en varint en vez de
32/64/128 hardcodeados, `count` sin default silencioso a `varint`
donde node-protodef deja un `// TODO: debería lanzar error`) -- no se
tocó nada ahí.

### Fixed
- **`mapper` sin match pasaba el valor crudo en silencio en vez de
  fallar** (`core.py`, `_read_mapper`/`_write_mapper`): al leer, si el
  valor crudo no tenía entrada en `mappings`, se devolvía tal cual
  (`return raw`) en lugar de levantar error. Al escribir, si el nombre

  simbólico no tenía mapeo inverso, se levantaba `InvalidTypeDefinition`
  -- útil pero no distinguible de un error real de definición de
  protocolo. `node-protodef` (`utils.js: readMapper`/`writeMapper`)
  lanza en ambos casos (`throw new Error(value + ' is not in the
  mappings value')`), porque un `mapper` modela un conjunto cerrado
  (estado del paquete, tipo de entidad, cara de bloque...) y un valor
  sin mapear casi siempre es un desync real del stream o una tabla de
  mappings desactualizada, no algo seguro de ignorar. Nueva excepción
  dedicada `MapperValueNotFoundError` (antes de esto sólo existía
  `SwitchCaseNotFound` para el caso análogo de `switch`) usada en
  ambas direcciones. **Breaking change** si algún protocolo dependía
  del passthrough silencioso al leer.
- **`bitflags` con `flags` como lista invertía el orden si `big:
  true`** (`core.py`, `_read_bitflags`/`_write_bitflags`): la forma
  posicional (`flags: ["air", "water", "lava"]`) usaba
  `reversed(flag_names)` cuando `big=True`, asignando el bit `N-1-i`
  al flag `i` en vez de `i`. En `node-protodef`
  (`utils.js: readBitflags`/`writeBitflags`) real, `big` sólo decide
  si la máscara se calcula con `BigInt` (`1n << BigInt(k)`) o `Number`
  (`1 << k`) -- nunca cambia qué bit le corresponde a qué nombre; el
  orden siempre es el índice tal cual en la lista. Como Python no
  necesita esa distinción (los `int` ya son de precisión arbitraria),
  `big` ahora es un no-op para la forma en lista: se sigue aceptando
  en el schema (para no romper protocolos que ya lo declaraban) pero
  ya no altera el resultado. **Breaking change** si algún protocolo
  con `"big": true` sobre `flags` en forma de lista dependía del orden
  invertido (el caso con `flags` como dict + `shift` no se ve
  afectado, nunca tuvo este bug).

### Tests
- `test_core_composites.py::TestMapper`: los dos tests que fijaban el
  comportamiento viejo (`test_unknown_raw_value_passes_through_unmapped`,
  `test_unknown_symbolic_name_raises_on_write` esperando
  `InvalidTypeDefinition`) se reescribieron para esperar
  `MapperValueNotFoundError` en ambas direcciones.
- `test_core_composites.py::TestBitflags::test_list_form_big_does_not_reverse_order`
  (nuevo): fija que `"air"` siempre es el bit 0 y `"lava"` el bit 2,
  con y sin `"big": True`, tanto en lectura como en escritura --
  regresión directa del bug de arriba, que no tenía ningún test
  cubriéndolo (por eso pasó desapercibido).
- 153/157 tests pasan; los 4 restantes (`test_examples_roundtrip.py`)
  fallan por `examples/example_protocol.json` y
  `examples/classicube_protocol.yml` ausentes del entorno de
  desarrollo usado para este audit, no por código -- no relacionado
  con este release.

## 0.3.7

### Fixed
- **Un `container` inline resuelto por un `switch` "roba" el nivel
  `parent`** (`core.py`, `_read_switch`/`_write_switch`): cuando
  `switch.fields` resuelve a un `container` inline (`["container",
  [...]]`, literal ahí mismo, no un tipo nombrado), se despachaba con
  `read_type`/`write_type` normal -> `push_level=True` por default,
  igual que cualquier container común. Eso convertía ese container en
  su propio `child_parent`, así que un `../algo` dentro de él subía
  solo hasta el switch mismo en vez de hasta el container que
  contiene al campo del switch -- el mismo problema que ya estaba
  identificado y resuelto para un container usado como item inline de
  un `array` (ver `_read_array_item`/`_write_array_item` y el
  comentario en `_read_container` sobre por qué ahí se usa
  `push_level=False`), pero nunca se había aplicado al caso análogo
  de `switch`. Encontrado diseccionando a mano un `declare_commands`
  real de un server 1.16.5 (Aternos): el campo `suggestionType` de un
  `command_node` con `has_custom_suggestions=1` depende de
  `../flags/has_custom_suggestions`, y ese `../` resolvía contra el
  container `extraNodeData` (salido del switch) en lugar de contra el
  `command_node` real -- `compare_val` salía `None`, caía al
  `default: "void"`, y el parseo se desalineaba 21 bytes (el tamaño
  de la string `suggestionType` no leída), reventando 3 nodos
  después con `buffer exhausted`. Fix: mismo patrón que
  `_read_array_item` -- si `case_type` es un container inline, se
  despacha con `push_level=False`, transparente a `../`. Validado con
  el paquete real de 328 bytes (20 nodos): parsea completo y
  re-serializa byte-a-byte idéntico al original capturado del server.

## 0.3.6

### Fixed
- **`switch` sobre un campo `bool` nunca matchea** (`core.py`,
  `_resolve_switch_case`): `str(True)` da `"True"` en Python, pero
  minecraft-data (formato node-protodef/JSON) siempre usa
  `"true"`/`"false"` en minúscula como keys del switch. Nunca
  matcheaba para ningún protocolo real con switch sobre bool.
  Afectaba `slot`/`present` (item slots 1.13+), `packet_map_chunk`/
  `groundUp` (chunk data 1.15+), `packet_face_player`/`isEntity`
  (1.13+) -- 16 de 29 `protocol.json` en el rango 1.7-1.16.5. Fix:
  chequear `isinstance(compare_val, bool)` ANTES que cualquier otro
  caso (en Python `bool` es subclase de `int`, así que el orden
  importa) y mapear a `"true"`/`"false"` literal.
- **`compareTo` con `/` en medio del path nunca se resuelve**
  (`core.py`, `_resolve_compare_value`): un `compareTo` relativo con
  `/` en medio (ej. `"flags/kind"`, sin `/` ni `../` al inicio --
  típico para indexar un sub-campo de un `bitfield` hermano) nunca
  llegaba a `resolve_field_path` (que sí sabe recorrer paths con `/`).
  Caía al `eval_condition(...)` dentro de un `try/except` que se
  tragaba el error en silencio y devolvía `None`. Rompía cualquier
  protocolo con un switch dependiente de un sub-campo de bitfield
  hermano -- ejemplo real: `declared_command_node` en el protocolo de
  comandos (`/help`, tab-complete) de 1.13+. Fix: cualquier
  `compareTo` que contenga `/` (no solo al inicio) ahora pasa por
  `resolve_field_path`. El check `compare_to in fields` se mantiene
  antes, por si algún protocolo tuviera un nombre de campo literal
  con `/` en vez de usarlo como separador de path.

### Added
- **`utf16be64`** (`primitives.py`): instancia lista para usar por nombre
  desde `protocol.yml`/`.json` (`type: utf16be64`) de la fábrica
  `make_fixed_utf16be_string(64)` que ya existía hacía rato -- tenía el
  mismo hueco que `string64` cerró para `make_fixed_cp437_string`, pero
  a nadie se le había ocurrido cerrarlo también acá hasta ahora. 64
  CARACTERES (no bytes) UTF-16BE, space-padded -- 128 bytes en el wire
  (`length * 2`). A diferencia de `string64` (CP437, reemplaza con "?"
  cualquier carácter que la codepage no represente), UTF-16BE sí
  codifica sin pérdida acentos/diacríticos y la mayoría de otros
  alfabetos -- por eso conviene tenerlo aparte y no como reemplazo de
  `string64`, cada uno calza con el protocolo real que lo pide (ambos
  aparecen en variantes del campo username/MOTD de Minecraft Classic
  según el server software). Agregada a `PRIMITIVES` con el mismo
  criterio que `string64`: instancia ya armada, longitud fija en 64; si
  se necesita otra longitud, generarla con `make_fixed_utf16be_string(N)`
  y registrarla aparte bajo su propio nombre (mismo comentario que ya
  documentaba esto para `string64`).
- Tests: `test_utf16be64_pads_and_strips_spaces`,
  `test_utf16be64_truncates_overlong_input` y
  `test_utf16be64_handles_non_latin_chars` en `test_primitives.py` --
  mismo patrón que los tests ya existentes de `string64`, más un caso
  específico (`"héllo wörld"`) que documenta justo la diferencia de
  fondo con `string64`: acá el roundtrip es exacto, sin `"?"` de por
  medio.
- README (sección 5, tabla de `native`): fila nueva para `utf16be64`.
- `examples/example_protocol.yml` / `.json`: `utf16be64` sumado al
  catálogo de natives (`packet_ejemplo_natives`, ver entrada de abajo)
  y a `demo.py`.

### Added
- `examples/example_protocol.yml` / `.json`: **patrón 5**, nuevo --
  `packet_ejemplo_natives`, un container con un campo por cada uno de los
  61 natives registrados en `primitives.py` (antes el ejemplo solo
  declaraba 14 en `types:`, los mínimos que usaban los patrones 1-4). El
  `.json` se regeneró desde el `.yml` con el propio `load_protocol_dict`
  de la librería (mismo criterio que ya se documenta arriba para este
  archivo), y se confirmó equivalencia byte a byte entre ambos
  serializando el packet con los dos y comparando el resultado. Objetivo:
  que alguien pueda abrir un solo archivo y ver, para cada native, el
  nombre exacto que se referencia en `.yml`/`.json` y la forma que su
  valor toma en Python (ver `demo.py`), sin tener que ir a buscarlo en
  `primitives.py` uno por uno. Único orden obligatorio dentro del
  container nuevo: `restBuffer` va al final (consume todo lo que quede
  en el buffer), el resto de los 60 campos no tiene restricción de orden
  entre sí.
- `examples/demo.py`: tercer bloque (\"Patrón 5\") que arma un `params`
  con valor válido para los 61 campos, serializa/parsea
  `packet_ejemplo_natives` y valida `bytes_read == len(data)` igual que
  los otros dos bloques del demo.
- `examples/README.md`: sección \"Patrón 5\" documentando el catálogo y
  la única regla de orden (`restBuffer` al final).

## 0.3.5

### Added
- **`f16` / `lf16`** (`primitives.py`): half-float, IEEE 754 binary16
  (2 bytes), big-endian y little-endian. Completa el hueco que quedaba
  en la familia de floats (antes solo `f32`/`f64` + sus `l*`). No
  requirió código de bajo nivel nuevo: `struct` soporta el formato
  `'e'` de forma nativa desde Python 3.6, así que se registra con el
  mismo helper genérico `_fixed_size_primitive` que ya usan `f32`/`f64`
  (`_fixed_size_primitive("f16", "e")` / con `little_endian=True` para
  `lf16`) -- ninguna línea de `_fixed_size_primitive` ni de ningún otro
  primitivo existente se tocó para esto. Útil para protocolos modernos
  que codifican posición/rotación compacta (p.ej. deltas de rotación)
  donde `f32` desperdicia precisión que nunca se usa; rango efectivo
  ~6.1e-5 a 65504, ~3 dígitos decimales de precisión -- valores que no
  entran o pierden demasiada precisión se redondean/saturan de forma
  silenciosa al empaquetar (comportamiento nativo de `struct`, sin
  chequeo extra agregado).
- Ambos agregados a `PRIMITIVES` (`primitives.py`) para poder
  referenciarse por nombre (`f16`/`lf16`) directo desde `protocol.yml`/
  `.json`, igual que cualquier otro fixed-width.
- README (sección 5, tabla de `native`): fila nueva para `f16` y
  mención de `lf16` en la fila de la familia little-endian.
- Tests: `test_f16_roundtrip_and_size`, ­
  `test_f16_big_vs_little_endian_byte_order_differs` y
  `test_f16_precision_loss_vs_f32` en `test_primitives.py` -- roundtrip
  y tamaño (2 bytes) para ambas variantes, valor de referencia BE vs.
  LE byte a byte (`1.5` → `3e 00` / `00 3e`, mismo criterio que el test
  ya existente para `u16`/`lu16`), y un caso explícito documentando que
  `f16` SÍ pierde precisión frente a `f32` para un valor no-exacto en
  binario (`1/3`) -- para que quede claro que no es un reemplazo
  drop-in de `f32`, es una elección consciente de tamaño vs. precisión.

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
