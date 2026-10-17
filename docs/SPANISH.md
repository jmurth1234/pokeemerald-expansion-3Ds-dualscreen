# Pokémon Esmeralda en español

Emerald 3DS funciona con la ROM española limpia de
**Pokémon Esmeralda (España, BPES)**, con sus textos y gráficos. La traducción
no se distribuye: sale de tu propia ROM, en tu ordenador o en tu navegador.

El usuario confirmó el funcionamiento de la última compilación en su 3DS
física el 12 de octubre de 2026, sin detallar resultados por escenario.

## Instalar

La vista experimental de luces suaves usa el mismo código en los dos idiomas:
transiciones de dos horas de juego y cristales cálidos en casas voxel
compatibles, incluidas las de Ciudad Petalia. Se activa con **MEJORAS →
DIA / NOCHE → AUTO** y respeta **VELOC. RELOJ**. No cambia los horarios de
encuentros ni la partida. Los cristales luminosos en el modo
clásico 2D siguen pendientes. El usuario confirmó esta compilación en consola
y autorizó incorporarla a la PR #57. Consulta el
[alcance y procedimiento](DAY_NIGHT.md#experimental-follow-up-softer-transitions-and-warm-windows).

La [ampliación de iluminación urbana](CITY_LIGHTS.md) amplía los
cristales voxel a Centros Pokémon, tiendas, gimnasios, bloques de Ciudad
Férrica y Devon, y enciende las farolas originales de Ciudad Férrica con
pequeños halos cálidos sobre el suelo. Usa el mismo reloj en inglés y español.
El usuario confirmó su funcionamiento en 3DS y autorizó subirla a la PR #57;
no añade farolas a ciudades que no las tienen ni cubre edificios sin modelo.

Igual que la versión inglesa: el builder web y el de Windows aceptan las dos
ROM y eligen solos el idioma. Cada release incluye las dos variantes: el
ejecutable inglés y el español, cada uno con su receta. Para la actualización
rápida (solo el 3DSX), la web comprueba tu `emerald3ds.pak` y te ofrece
`Emerald3DS-es.3dsx`, que se guarda en la SD como `Emerald3DS.3dsx`.

| Idioma | Código | SHA-1 de la ROM limpia |
|---|---|---|
| Español | BPES | `fe1558a3dcb0360ab558969e09b690888b846dd9` |
| Inglés | BPEE | `f3ae088181bf583e55daf962a92bb46f4f1d07b7` |

## Compilar la versión española desde el código

Con los requisitos de la [guía de desarrollo](DEVELOPMENT.md):

```sh
python tools/bootstrap.py --make --spanish-rom "/ruta/Pokemon Esmeralda.gba" -j8
```

El bootstrap aplica los parches, compila las herramientas y los includes
generados, ejecuta `tools/localize_spanish.py` con tu ROM y compila el 3DSX en
`build/upstream/3ds_port/emerald3ds.3dsx`. Cambiar de idioma (volver a
ejecutar sin `--spanish-rom`) reinicia el árbol y borra los objetos compilados
y los recursos localizados, para no reutilizar datos del otro idioma.

El motor comprueba el idioma con el que se compiló: un ejecutable español solo
acepta un paquete de datos generado desde BPES, y uno inglés solo desde BPEE.

## Cómo se obtiene la versión española

`tools/localize_spanish.py` comprueba el SHA-1 de la ROM y las huellas SHA-256
de los archivos fuente antes de escribir en el árbol generado. Los manifiestos
de `tools/locales/` contienen posiciones en el código, desplazamientos y
tamaños en la ROM, nombres de símbolos y direcciones. No contienen textos,
gráficos, sonidos ni secciones de la ROM.

El proceso sustituye los textos y campos del código por los de tu ROM, extrae
104 recursos gráficos y reconstruye las 55 páginas de créditos y los pisos de
la Colina Desafío. Incluye nombres, diálogos, menús, vocabulario, canciones
del bardo, frases de entrenadores, cartas, preguntas y los textos en braille de
las ruinas. La pantalla táctil usa etiquetas españolas y la Pokédex muestra
metros y kilos.

Los parches de idioma (`patches/pokeemerald/0030-…` a `0036-…`, todos bajo
`PORT_BRIDGE`) copian por nombre las tablas de la Colina Desafío y adaptan los
nombres de bayas, las descripciones de los rivales, los títulos de concursos,
la pantalla «FIN», el ancho de una tabla de textos de combate, las unidades
de la Pokédex y las ventanas de la etiqueta de bayas. En la compilación inglesa no cambian nada salvo la copia de la
Colina Desafío, que ya no depende del orden en que el enlazador coloca las
tablas.

Si un cambio del port modifica alguno de los archivos fuente del manifiesto,
la localización se detiene («Source differs from the pinned patched tree»)
hasta que se actualicen sus posiciones y huellas.

Las pestañas de opciones, mejoras y trucos de la pantalla táctil también
están traducidas, incluidos sus valores, acciones y el menú para dar objetos.
Las etiquetas usan abreviaturas y texto sin tildes donde el conversor de
la interfaz solo admite ASCII. Los nombres de objetos proceden de tu ROM.
El ciclo opcional de día/noche y la velocidad del reloj aparecen como
`DIA / NOCHE` y `VELOC. RELOJ`; las opciones binarias muestran `NO` y `SI`.
`POKéMON SALV.` permite elegir `ORIGINAL` o `POR HORARIO`, de forma
independiente del tinte. `VER RELOJ` añade hora, franja y velocidad al pie del
mapa inferior; las franjas se muestran como `MAÑANA` (04:00–09:59),
`DÍA` (10:00–15:59), `TARDE` (16:00–19:59) y `NOCHE` (20:00–03:59).
El indicador admite los glifos Ñ e Í del juego. TARDE es una subdivisión
visual de DÍA; no modifica las tablas de encuentros. La versión experimental
acelera también el crecimiento de las bayas, conservando sus reglas originales.
Los eventos diarios siguen el RTC real. Al volver a 1X se mantiene la hora
acumulada; con la consola suspendida o la aplicación cerrada transcurre tiempo
a 1X. La fase y las fracciones pendientes se guardan junto a la partida en
`emerald3ds.clock` (y, si existe, `emerald3ds.clock.bak`). Copia esos archivos
junto con `emerald3ds.sav` para conservar también el estado del reloj.
El mensaje del reparto de experiencia moderno está traducido y los prefijos
de Pokémon enemigos/salvajes incluyen separación antes del nombre.
Las dos opciones nuevas están desactivadas de forma predeterminada.

## Autoría

Traducción y herramientas de localización de Jesus Oliva
([pull request #6](https://github.com/ZallaxDev/pokeemerald-3Ds-dualscreen/pull/6)).
