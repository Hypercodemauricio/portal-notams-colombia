# Buscador de Reglamentos Aeronáuticos (RAC)

Busca por palabra clave dentro del texto de los RAC vigentes y devuelve los
apartados relacionados, con su numeral, su capítulo y **la fecha de la versión
de la que salieron**.

---

## Cómo está armado

```
rac_catalogo.py    qué reglamentos se indexan, con su idFile y su fecha de versión
rac_indexar.py     descarga los PDF y construye sistema_rac.db      (se corre a mano)
rac_segmentar.py   corta el PDF en apartados                        (la parte delicada)
rac.py             búsqueda FTS5 + armado del contexto para la IA
api_notams.py      /api/rac/documentos, /api/rac/buscar, /api/rac/consultar
index.html         pestaña "Reglamentos"
```

La base `sistema_rac.db` es independiente de `sistema_notams.db`. El extractor
de NOTAMs reemplaza su base cada 15 minutos; el índice de reglamentos no tiene
por qué verse afectado por eso, ni al revés.

---

## Primera carga

En Windows, desde la carpeta `Servidor AWS`:

```powershell
.\06-DESCARGAR-RAC.ps1
```

O directamente:

```bash
python3 rac_indexar.py              # descarga lo que falte e indexa todo
python3 rac_indexar.py --revisar    # solo muestra qué hay y qué falta
python3 rac_indexar.py --rac 91 215 # trabaja solo con esos
python3 rac_indexar.py --solo-indexar  # usa los PDF de rac_pdf/, sin descargar
```

Los PDF quedan en `rac_pdf/` (no se versionan). El índice se escribe primero en
`sistema_rac.db.tmp` y solo al final reemplaza al bueno, con copia en `.prev`:
si el proceso muere a la mitad, el portal sigue respondiendo con el índice
anterior.

Si un reglamento no baja, los demás se indexan igual y al final se dice cuál
quedó fuera.

---

## Qué reglamentos hay

16 en total: los de operación y los de apoyo. La lista completa, con la fecha de
cada versión, está en `rac_catalogo.py`.

| Grupo | RAC |
|---|---|
| Operación | 91, 100, 121, 135, 211, 212 |
| Apoyo | 1, 14, 61, 65, 203, 204, 205, 210, 215, 219 |

No están los de aeronavegabilidad de fabricación (21, 22, 23…) ni los
administrativos (11, 13). Añadir uno es agregar una línea al catálogo y volver
a correr el indexador.

---

## Cuando la Aerocivil publica una enmienda

Los RAC se enmiendan seguido, y el portal muestra la fecha de versión en cada
resultado justamente para que se note cuándo el texto ya no es el vigente.

Para actualizar uno:

1. Busca el reglamento en la página de la Aerocivil y mira el `idFile` del
   enlace de descarga y la fecha nueva.
2. Cambia esos dos valores en `rac_catalogo.py`.
3. `python3 rac_indexar.py --rac 91 --forzar-descarga`

**No hay revisión automática de versiones.** Se decidió así a propósito: el
portal de la Aerocivil cambia de estructura sin avisar —ya nos pasó con el
boletín Charlie1— y un vigilante que falle en silencio es peor que no tenerlo,
porque da la impresión de que todo está al día.

---

## Cómo se corta el PDF

Es lo único que puede fallar de forma silenciosa y confusa: si el corte queda
mal, el buscador cita un numeral y muestra el texto de otro. Las reglas:

1. **El numeral tiene que empezar por el número del reglamento.** En el RAC 91,
   solo `91.xxx` es un numeral. Así, `1.500 m`, `0.5 NM` o una fila de tabla que
   empiece por un decimal no pueden confundirse con un encabezado.
2. **La portada y la tabla de contenido se descartan por región, no por línea.**
   Un renglón del índice tiene el mismo texto que el encabezado real; si
   sobrevive uno, se crea un apartado con el título correcto y el cuerpo vacío,
   que gana en la búsqueda —el título coincide palabra por palabra— y le muestra
   al usuario un numeral sin contenido.
3. **El encabezado y el pie se detectan por frecuencia y por forma.** Por
   frecuencia sale el encabezado, que es idéntico en todas las páginas. El pie
   cambia en cada hoja (`RAC 91 91-2`, `RAC 91 91-3`), así que se compara la
   *forma*: los dígitos sustituidos por `#`.
4. **El título que se va a la línea siguiente se recupera.**
5. **Los apartados sin cuerpo no entran en el índice de búsqueda.** Siguen
   guardados porque dan contexto de ubicación, pero no compiten.

6. **Un encabezado de sección va en mayúscula y es corto.** La línea
   `capítulo E)` —el final de una frase partida— cumplía el patrón y creaba un
   capítulo falso que se llevaba 4.709 caracteres del numeral anterior.
7. **En un RAC de un solo dígito el segundo nivel se limita a dos cifras.** En
   español el punto también separa los miles: `1.944 Sobre Aviación Civil
   Internacional…` es el año 1944 al final de una frase, y se convertía en un
   apartado que se llevaba 178 definiciones consigo.
8. **Los apartados de más de 6.000 caracteres se trocean** por renglón, sin
   cortar frases, y cada trozo dice "(parte n de m)". El APÉNDICE 7 del RAC 215
   —el formato del plan de vuelo— son 33.000 caracteres de una pieza: sin
   trocear, la búsqueda devolvía un bloque ilegible con un extracto suelto.
9. **Un glosario se corta por término, no por numeral** (ver abajo).

`tests/fixture_rac.py` genera un PDF con esas trampas metidas a propósito, y
`tests/test_basico.py` comprueba que ninguna se cuela. Se puede correr sin red
y sin tener los PDF oficiales.

### Glosarios

El RAC 1 no es un articulado: son 130 páginas de "Término: definición". Va
marcado con `glosario=True` en el catálogo, y dentro de sus secciones de
definiciones y abreviaturas se ignora la numeración y se corta por término. Así
la búsqueda devuelve la definición exacta —"Aeródromo de alternativa"— citada
como RAC 1, 1.2.1, en vez de un único bloque con el diccionario entero.

Si el reconocimiento de términos encuentra menos de 50, no parte nada y deja el
apartado como estaba: más vale un bloque grande que un diccionario partido por
donde no era.

---

## La búsqueda

- **FTS5 con `remove_diacritics 2`**: "aerodromo" encuentra "aeródromo".
- **Sinónimos aeronáuticos** (`rac.py`): quien escribe `FPL` no encontraría nada,
  porque el reglamento dice "plan de vuelo"; quien escribe "gasolina" tampoco,
  porque dice "combustible". El término original **siempre se conserva** y los
  sinónimos se le suman dentro de un grupo `O`: `(ifr OR instrumentos*)`. La
  primera versión sustituía, y entonces buscar "AIS" ya no encontraba la
  entrada "AIS" del glosario, que era justo lo que se buscaba.
- **Muletillas fuera**: en "qué significa AIS", la palabra "significa" iba en Y
  con el término y devolvía "cambios significativos" en vez de la definición.
- **El glosario no tapa al articulado**: las definiciones son cortas y llevan el
  término en el título, así que BM25 las ponía primero aunque la pregunta fuera
  operativa. Se les aplica un castigo de orden, salvo cuando la pregunta sí es
  de definición ("qué es", "qué significa"). Y ningún numeral puede ocupar más
  de dos puestos, para que una sección no llene la pantalla.
- **Sin coincidencias exactas**, se reintenta con cualquiera de los términos y
  se avisa en pantalla, para que no parezca que esos resultados contienen todo
  lo que se escribió.
- **Un numeral suelto** (`91.310`) va directo a su apartado.
- **Entre comillas** se busca la frase literal.
- La consulta del usuario se traduce a sintaxis FTS5 **escapando todo**. Sin
  eso, escribir `combustible AND "` no da cero resultados: da un error 500.

---

## La IA, y por qué va después

`/api/rac/consultar` **primero busca el texto y después** se lo pasa al modelo,
con la orden de citar el numeral en cada afirmación y de decir "los apartados
encontrados no responden esta pregunta" cuando no estén. El portal muestra el
resumen arriba, pero **el texto literal siempre debajo**, con su numeral y su
fecha.

Preguntarle a un modelo "¿qué dice el RAC sobre X?" sin darle el texto es
justamente la pregunta que se responde con un requisito verosímil que no
existe. En materia normativa eso no es un error cosmético.

Si Gemini no responde, el endpoint devuelve igual los apartados literales.
Está probado: con Gemini inalcanzable, la búsqueda sigue funcionando.

---

## Limitaciones conocidas

- **PDF escaneados o con otra numeración**: si un RAC estuviera escaneado como
  imagen, o no numerara sus apartados como `<n>.<nnn>` —el RAC 1, que es un
  glosario, es el candidato— el indexador lo **rechaza en vez de guardar
  basura**, y avisa: "solo se reconocieron N apartados". Falla ruidosamente a
  propósito. Si el reglamento es legítimamente corto, se puede bajar el umbral
  con `RAC_APARTADOS_MINIMOS=3`; si está escaneado, hace falta OCR y por ahora
  lo mejor es sacarlo del catálogo.
- **Tablas**: el texto de las tablas se indexa como texto corrido. Se encuentra,
  pero se lee peor que en el PDF.
- **Sin control automático de vigencia** (ver arriba).
- El buscador **no sustituye la publicación oficial**. Cada respuesta lo dice.
