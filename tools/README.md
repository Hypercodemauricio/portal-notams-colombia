# Herramientas de verificación

## Auditoría de contraste

Mide el contraste real de cada trozo de texto de la página contra lo que hay
detrás. Con un fondo en video eso cambia fotograma a fotograma, así que no
sirve comparar el color declarado contra `--bg`.

```bash
# con el portal corriendo en :8000
node tools/auditar.mjs http://127.0.0.1:8000/ /tmp/audit-met
python3 tools/auditar.py /tmp/audit-met

# una pestaña o el tema claro
node tools/auditar.mjs http://127.0.0.1:8000/ /tmp/audit-geo "window.switchTab('geo')"
node tools/auditar.mjs http://127.0.0.1:8000/ /tmp/audit-claro "window.toggleTheme()"
```

Sale con código 1 si algo queda por debajo de WCAG AA, así que sirve tal cual
en un gancho de pre-commit o en CI.

### Cómo mide

Captura cada vista **dos veces sobre el mismo fotograma del video**: una
normal y otra con todo el texto en transparente. Los píxeles que cambian
entre ambas son, por definición, los del texto; el contraste se calcula solo
ahí, contra lo que la segunda captura muestra debajo.

Los tres métodos que se descartaron antes de llegar a este, por si alguien
tiene la tentación de simplificarlo:

| Método | Por qué falla |
|---|---|
| Color declarado contra `--bg` | Ignora el video, los desenfoques y las superficies translúcidas |
| Ocultar todo el contenedor y fotografiar el fondo | Oculta también las barras translúcidas sobre las que va el texto → falsos negativos |
| Volver transparente solo el elemento medido | Los hijos con color propio siguen pintados, y las cajas de línea se solapan → más falsos negativos |
| Medir la caja entera del elemento | Dentro caen cosas que no son fondo de texto (un icono de acento, un punto de color, un borde punteado) y el peor píxel acaba siendo uno de esos → 1.03:1 donde el texto se lee perfecto |

Se toma el percentil 5 y no el mínimo absoluto: los píxeles del borde del
glifo son una mezcla del texto y el fondo, y no son los que definen si algo
se lee.
