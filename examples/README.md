# Ejemplo de protocolo — 4 patrones que rompieron el bot

Archivos:
- `example_protocol.yml` — el protocolo fuente, comentado
- `example_protocol.json` — la misma definición ya traducida a la forma
  nativa `["tipo", opts]` que entiende node-protodef (generada con tu
  propio loader, para que veas exactamente cómo queda "por dentro")
- `demo.py` — corre ambos paquetes de ejemplo y valida el round-trip

## Patrón 1 — tipo nombrado parametrizable (`$arg`)

```yaml
itemPorTipo:
  switch:
    compareTo: $compareTo   # placeholder
    fields: {...}
```

Se usa pasándole el valor real en el punto de uso:

```yaml
type:
- itemPorTipo
- compareTo: tipo
```

Así es literalmente `entityMetadataItem` en minecraft-data. Sirve para
definir un tipo "genérico" una sola vez y reusarlo con distinto campo de
comparación según el contexto.

## Patrón 2 — `compareTo: ../campo`

Sube un nivel para comparar contra un campo del container padre en vez del
container actual.

**Trampa importante:** `../` solo salta el nivel de un container **inline**
(literal, escrito directo dentro de `array.type` o de otro container). Si en
cambio referenciás un tipo por nombre (`type: miTipoNombrado`), ese tipo
nombrado SIEMPRE empuja su propio nivel de `..`, así que un `../campo`
adentro suyo no llega al padre real — llega, como mucho, al propio
container nombrado. Si necesitás que el switch vea un campo de más arriba,
declará el container inline en el punto de uso (como hace
`packet_ejemplo_jugadores` acá) en vez de moverlo a un tipo con nombre aparte.

## Patrón 3 — array de containers con switch adentro

Cada item del array es un container con un campo `anon: true` que es un
switch. El switch puede mirar campos del mismo item (`uuid` leído antes) o,
combinado con el patrón 2, campos del container que contiene el array.

## Patrón 4 — bitfield empaquetado + switch en el mismo container

Un solo byte trae dos campos (`tipo` + `indice`) empaquetados con
`bitfield`. Se declaran como campo `anon: true` para que se mezclen en el
container padre, y el campo siguiente (`valor`) usa un switch/tipo
parametrizado que lee `tipo` para saber qué leer.

Este es el patrón real de `entity_metadata` en Minecraft 1.8.9-1.16: cada
entrada es 1 byte `(tipo<<5 | indice)` + el valor según `tipo`, terminado
por un byte `0x7F`.

## Patrón 5 — catálogo de todos los natives

`packet_ejemplo_natives` no es un paquete de ningún protocolo real: es un
container con un campo por cada uno de los 61 natives registrados en
`protolib/primitives.py` (familia `u8`..`i64` completa BE/LE, `f16`/`f32`/
`f64`, varints/zigzag/varint128, `bool`/`void`, `cstring`/`string64`/
`utf16be64`, `UUID`, las 4 variantes de NBT, los buffers/fixed-point de
ClassiCube, y `restBuffer`). Sirve como referencia rápida del nombre
exacto que cada native espera en el `.yml`/`.json` y de la forma que
espera su valor en Python (ver `demo.py`, diccionario `params` de la
sección "Patrón 5").

**Único cuidado real:** `restBuffer` consume todo lo que quede en el
buffer, así que va **al final** del container -- cualquier campo declarado
después de él nunca se llegaría a leer. El resto de los campos no tiene
orden obligatorio entre sí.

## Correr el demo

```bash
cd examples
python demo.py
```

Debería imprimir ambos paquetes serializados/parseados y confirmar que
`bytes_read == len(data)` (nada quedó sin leer, nada se leyó de más).

(También funciona corriéndolo desde afuera, `python examples/demo.py`
desde la raíz del repo — el script resuelve la ruta del `.yml` relativa
a sí mismo, no al directorio desde donde lo ejecutes.)
