# Azuliza / Dewford: cobertura exterior 3D

## Diagnóstico

La cobertura anterior no incluía las casas de tejado azul, el Hall ni el
gimnasio exterior. Sus metatiles pertenecen a `gTileset_Dewford`, mientras
que las referencias existentes de casas y gimnasio usan otros dibujos.
El matcher no debe reutilizarlos simplemente por tener una silueta parecida:
eso sustituiría los colores y detalles originales de Azuliza.

El Centro Pokémon **ya estaba modelado**: `pokemon_center` encuentra
`LAYOUT_DEWFORD_TOWN`, layout 12, en `(1, 7)`, con suelo `0x124` y seis
celdas distintas de la referencia; cuatro reciben patches de suelo.
La lectura de los assets VXB8 staged EN/ES anteriores a regenerar confirma
ese placement, además de tres rocas, pero ninguna casa azul/Hall/gimnasio.
Si el Centro sigue plano con assets regenerados, se debe investigar
carga/selección de assets, páginas y runtime: el test de geometría no
prueba el renderer, TEV o shaders.

## Modelos y placements

Coordenadas en celdas del mapa, sin el borde adicional del motor:

| Modelo | Rectángulo de referencia `(x,y,w,h)` | Placements en Azuliza |
| --- | --- | --- |
| `dewford_house` | `(16,11,4,4)` | `(16,11)` y `(7,5)` |
| `dewford_hall` | `(1,0,5,4)` | `(1,0)` |
| `dewford_gym_exterior` | `(5,13,6,5)` | `(5,13)` |
| `pokemon_center`, existente | Petalburg `(19,13,4,4)` | `(1,7)` |

Las cinco entradas del mapa quedan dentro de placements distintos, sin
solapamiento. Las casas comparten geometría de una planta, paredes laterales
y cubierta a cuatro aguas, pero extraen su propio arte azul. El Hall utiliza
el ancho de cinco celdas y su fachada original. El gimnasio utiliza la
geometría existente con su textura amarilla local, no la de Petalburg.

No se pinta arte nuevo ni se estiran texels: fachadas y cursos visibles
pasan la prueba de proyección GBA píxel por píxel y densidad 1:1.
La profundidad real se completa repitiendo cursos del propio dibujo.
Las columnas limpias del caballete evitan copiar hojas sobre el tejado.
La casa norte admite una primera fila distinta con árboles; se comparan
las filas 1..3, conservando la restricción de tileset secundario.
Suelo local: arena `0x124`. No se cambia el matcher ni el formato VXB8.

## Ventanas authored

Rectángulos en píxeles de la textura del modelo; extremos derecho/inferior
exclusivos. Se utilizan las máscaras existentes, sin alterar su predicado:

| Modelo | Rectángulos de cristal | Píxeles activos |
| --- | --- | ---: |
| `dewford_house` | `(34,46,39,52)`, `(41,46,46,52)` | 60 |
| `dewford_hall` | `(50,46,55,52)`, `(57,46,62,52)` | 60 |
| `dewford_gym_exterior` | `(11,50,21,53)`, `(27,50,37,53)`, `(75,50,85,53)`, `(49,62,55,77)`, `(57,62,63,77)` | 270 |

Los últimos dos rectángulos del gimnasio son los cristales de las puertas,
no sus marcos ni partes sólidas. Las puertas amarillas de casas/Hall no
emiten luz. No se incluyen tejados, postes ni paneles claros de fachada.
El test exige igualdad exacta del conjunto de píxeles activos y comprueba
cada texel L8 exportado mediante el offset/crop y swizzle reales de VXB8.
La máscara no recolorea la textura original.

## Pruebas reproducibles

Desde la raíz, con Pillow y un árbol bootstrapped **solo de lectura**:

```sh
/tmp/emerald-cozy-python/bin/python 3ds_port/tests/voxel_dewford_test.py
/tmp/emerald-cozy-python/bin/python 3ds_port/tests/voxel_dewford_test.py --tree build/upstream-es
docker run --rm -v "$PWD:/repo:ro" -w /repo emerald3ds-build:local \
  python3 3ds_port/tests/voxel_dewford_test.py
```

Las tres variantes pasan. El test ejecuta las ramas reales de generación
exterior y exportación, aislando la expansión/reutilización de interiores.
Comprueba geometría con altura y profundidad, arte, densidad, placements,
suelo, ausencia de solapamiento, las cinco entradas y tablas/páginas VXB8.
Incluye el Centro existente y las tres máscaras nuevas. El binario temporal
se elimina; nunca se escribe en los árboles staged ni sus assets.

Previews opcionales, también fuera del repositorio:

```sh
/tmp/emerald-cozy-python/bin/python 3ds_port/tests/voxel_dewford_test.py \
  --preview /tmp/dewford-3d-proof
```

Una ejecución amplia del CLI con `--only dewford_ --verify` verifica los
exteriores con cero errores, pero también selecciona interiores por prefijo:
la composición del Hall interior reporta 1.634 píxeles distintos en esa
selección parcial. No equivale a la validación de una generación completa
con todo el mobiliario; no se modifica ese código fuera de scope.

El CLI de producción con `--only dewford_house --verify --output` también
pasa dentro de `emerald3ds-build:local`, montando los scripts actuales sobre
el árbol de entrada solo en el contenedor, ambos de lectura. Exporta un
modelo de 268 triángulos, dos placements, 804 vértices y una página 64x64
con máscara, a `/tmp/dewford-buildings.bin` dentro del contenedor.
`py_compile` y `git diff --check` de los cambios también pasan.

## Cartel a la derecha del gimnasio

El evento `DewfordTown_EventScript_GymSign` está en `(11,16)`, fuera del
footprint aceptado del gimnasio `(5,13,6,5)`: este termina en la columna 10.
El cartel ocupa `0x210` en `(11,16)` y su parte superior ocupa `0x208` en
`(11,15)`; ambos se dibujan sobre arena `0x124`.

La detección anterior exigía tres lados libres o un metatile aprendido de
otro cartel aislado. La pared al oeste impedía ambas vías para este dibujo
local, clasificado como `wall`; el generador no exportaba su registro y el
runtime también exigía el rol `signpost`. Además, el límite de 14 píxeles
para cabezas de farolas rechaza este panel de 15 píxeles. La arena tiene
arte también en la capa superior, de modo que no sirve asumir que toda
esa capa pertenece al cartel ni buscar solo vecinos sin capa superior.

El generador de carteles recupera paneles de dos celdas con evento explícito,
norte/sur transitables y al menos un lateral libre. Exige que las capas
inferiores del panel y de su cabeza coincidan exactamente con un donante
sur completo y limpio, y resta su fondo antes de conectar la cabeza al
panel. Los límites conservadores de farolas siguen intactos. El runtime
utiliza el registro VXS2 como autoridad para geometría, suelo de la cabeza
y sombras, aunque el clasificador de regiones conserve `wall`.

La fixture real EN/ES exige las filas exactas: 212 píxeles del cuerpo y 99
de la cabeza, sin arena; altura de 23/16 celdas y profundidad de 2/16.
Compila únicamente el test host de carteles y consume el VXS2 generado en
un directorio temporal. Comprueba origen de mapa desplazado, UV de ambas
celdas, contacto con el suelo, sombras y ausencia de emisión nocturna de
farola. La prueba exterior completa exige que ninguna de las dos celdas
se solape con un modelo aceptado. Ejecución aislada:

```sh
/tmp/emerald-cozy-python/bin/python -B 3ds_port/tests/voxel_dewford_test.py --sign-only
/tmp/emerald-cozy-python/bin/python -B 3ds_port/tests/voxel_dewford_test.py --sign-only --tree build/upstream-es
```

La comparación temporal de todos los registros anteriores conserva los
360 registros byte por byte, incluidas las farolas: solo añade `(12,11,16)`.
La corrección del cartel no modifica specs de edificios, luces ni mapas.
Para probar en consola se debe regenerar `voxel/signposts.bin` y usar el
runtime actualizado. La build final EN/ES incorpora ambos cambios.

## Integración y límites

Regenerar los assets de la build destinada a consola y sustituir juntos
ejecutable, SMDH y pack: actualizar solo el ejecutable no incorpora estos
modelos. Las pruebas de luces comprueban ahora 22 modelos exteriores.
`make verify` incluye esta regresión y la de Pokédex de captura. La aceptación
visual de modelos y luces de Azuliza está confirmada por el usuario en
hardware; el usuario también confirma el cartel en la versión final y
autoriza publicar los cambios el 12 de octubre de 2026.

Como observación adicional, previews de Férrica con `building_mask` marcado
en magenta sobre el arte y proyectado por los UV existentes no muestran
desalineación en `rustboro_stone_31_15`, `rustboro_olive_9_25` o
`devon_corporation`: 96, 276 y 780 píxeles de máscara, respectivamente.
Los tres pasan proyección exacta y densidad sin errores. Son pruebas del
rasterizador software, no del estado TEV ni de la emisión en consola.
