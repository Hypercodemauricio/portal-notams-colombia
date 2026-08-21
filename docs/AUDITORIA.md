# Auditoría del servidor original y cambios aplicados

Levantamiento hecho el **16 de agosto de 2026** sobre la instancia EC2
`i-073e69280c43b413d` (`18.188.1.55`, us-east-2), Ubuntu 24.04.4, 19 GB de disco
al 43 %, 126 días de uptime.

## Estado encontrado

| Elemento | Detalle |
|---|---|
| Ruta del proyecto | `/home/ubuntu/proyecto_notams` (4,5 MB) |
| Servicio | systemd `notams.service` → `uvicorn api_notams:app --host 0.0.0.0 --port 8000` |
| Proxy | nginx 1.24.0, `sites-enabled/notams`, puerto 80 → `127.0.0.1:8000` |
| Datos | SQLite `sistema_notams.db` |
| Actualización | cron cada 15 min → `extractor.py` (Selenium + Chrome + pdfplumber) |
| Control de versiones | **ninguno** — sin `.git`, sin `requirements.txt`, sin entorno virtual |

---

## Hallazgos

### 1. La base de datos se borraba antes de saber si la descarga iba a funcionar

**Severidad: crítica.** Era el problema de fondo del sistema.

`extractor.py` empezaba llamando a `resetear_base_datos()`, que hacía
`os.remove(DB_NAME)` y recreaba la tabla vacía. Solo *después* intentaba
descargar el PDF. Si la Aerocivil fallaba —cosa frecuente— la ejecución
terminaba con la base vacía y el portal servía cero NOTAMs hasta que una
ejecución posterior tuviera suerte.

Los números del `cron.log`, sobre **13.081 ejecuciones**:

| Resultado | Veces | % |
|---|---:|---:|
| PDF inválido (respuesta del firewall en vez del PDF) | 2.063 | 15,8 % |
| Error de Selenium (`Message:`, elemento no encontrado) | 444 | 3,4 % |
| Timeout de lectura contra aerocivil.gov.co | 7 | 0,05 % |
| Chrome no arrancó | 2 | 0,02 % |
| **Total de fallos** | **2.516** | **19,2 %** |

Es decir: **aproximadamente 1 de cada 5 ciclos dejaba el portal sin datos**
durante al menos 15 minutos. En el momento exacto de la descarga la base tenía
**0 filas**, precisamente porque el ciclo de las 22:30 había fallado.

**Corregido:** la extracción ahora escribe en `sistema_notams.db.tmp` y solo
reemplaza la base de producción con `os.replace()` —operación atómica— si los
datos pasan las validaciones. Si la descarga falla, la base no se toca.

### 2. Sin reintentos ante un origen inestable

Un solo intento por ejecución contra un portal que falla el 19 % de las veces.

**Corregido:** 3 intentos con espera incremental (10 s, 20 s). Con la tasa de
fallo observada, esto debería reducir los ciclos fallidos de ~19 % a menos
del 1 %.

### 3. Sin validación de resultados

Si la Aerocivil cambiara el formato del PDF, la expresión regular devolvería
pocos NOTAMs o ninguno, y esos datos incompletos se habrían publicado igual.

**Corregido:** se descarta la extracción si devuelve menos de 50 NOTAMs, o si
cae más del 50 % respecto a la anterior. `--force` permite saltarse ambas.

### 4. API expuesta directamente a internet

El servicio arrancaba con `--host 0.0.0.0 --port 8000`, así que la API era
accesible sin pasar por nginx, saltándose el proxy, sus cabeceras y sus logs.

**Corregido en el repositorio:** `deploy/notams.service` escucha en
`127.0.0.1` con `--proxy-headers`.

**Ojo — todavía no está en el servidor.** La unidad de systemd de la instancia
sigue con `--host 0.0.0.0`, comprobado el 18 de agosto. Cambiarla es un paso
aparte del despliegue del portal: son dos cosas distintas y conviene no
tocarlas a la vez.

### 5. Claves API expuestas

| Clave | Ubicación | Situación |
|---|---|---|
| Google Gemini | `api_notams.py`, línea 13 | En el código fuente del servidor |
| AVWX | `index.html` (`AVWX_TOKEN`) | **Ya es pública**: se entrega al navegador de cada visitante |

**No corregido — decisión del usuario, pendiente antes de publicar en GitHub.**
Ambas ya se pueden sobrescribir con variables de entorno sin tocar código, pero
los valores originales siguen presentes como respaldo. El token de AVWX debería
además pasar a un endpoint del backend, para que deje de viajar al cliente.

### 6. Logs sin rotación

`cron.log` iba en **3,4 MB** y crecía sin control; nada lo limpiaba.

**Corregido:** el extractor escribe en `logs/extractor.log` con rotación
(5 archivos × 2 MB), más una configuración de `logrotate` como red de seguridad.

### 7. Rutas relativas dependientes del directorio de trabajo

Tanto la API como el extractor abrían `sistema_notams.db` e `index.html` por
ruta relativa. Funcionaba solo porque el servicio define `WorkingDirectory` y el
cron hacía `cd` antes de ejecutar. Cualquier invocación manual desde otro
directorio creaba una base vacía en el lugar equivocado.

**Corregido:** todas las rutas se resuelven desde la ubicación del archivo.

### 8. Errores devueltos con código HTTP 200

La API respondía `{"error": "..."}` con estado 200. Para el frontend o un
monitor externo, un fallo de base de datos era indistinguible de una consulta
exitosa sin resultados.

**Corregido:** 400 para códigos OACI inválidos, 503 cuando la base no está
disponible, y un endpoint `/health` para monitoreo.

### 9. Sin compresión

`index.html` pesa 533 KB y se enviaba sin comprimir en cada visita.

**Corregido:** GZip en FastAPI y en nginx → **353 KB, un 34 % menos**.

**Ampliado después:** la compresión no rendía más porque el grueso del archivo
eran siete fotos en base64 incrustadas —el pase de fondo—, y el navegador las
volvía a descargar en cada visita: un HTML con datos en vivo no se puede
cachear. Se reemplazaron por un video servido desde `/static`, que sí se cachea
(`max-age` de 30 días, `immutable`) y queda excluido de gzip por venir ya
comprimido.

| | Antes | Ahora |
|---|---:|---:|
| `index.html` | 565 KB | 126 KB |
| Fondo | dentro del HTML | 194 KB WebM / 343 KB MP4 |
| Primera visita | 565 KB | ~320 KB |
| Visitas siguientes | 565 KB | 126 KB |

### 10. Archivos duplicados y residuos

`extractor_backup.py`, `extractor_aerocivil.py` y `extractor_aerocivil_backup.py`
eran **idénticos entre sí** (mismo MD5), más `extractor_backup_20260401.py` y un
`index.html.bak` como cuarta y quinta copia. También quedaban `nohup.out`, un
`charlie1_temporal.pdf` de 39 bytes (un PDF fallido) y una base huérfana
`sistema_notam.db` —singular, sin la `s`— completamente vacía y sin tablas.

**Corregido:** eliminados. Git cumple ahora esa función.

### 11. Servicios innecesarios expuestos

**CUPS** (servidor de impresión) escuchando en el puerto 631 en todas las
interfaces, y Google Chrome instalado como paquete completo. En una instancia
que solo sirve una API, CUPS es superficie de ataque sin contrapartida.

**No corregido** — requiere intervención en el servidor:

```bash
sudo systemctl disable --now cups cups-browsed
sudo snap remove cups
```

Chrome sí es necesario: el extractor lo usa con Selenium.

### 12. Cuatro códigos OACI equivocados en el directorio

**Severidad: alta.** El directorio de aeródromos tenía cuatro códigos con las
letras cambiadas de sitio, y el nombre y la ciudad correctos al lado:

| La tabla decía | Es en realidad | Y ese código es de |
|---|---|---|
| `SKPV` Popayán | `SKPP` | — (SKPV no existe) |
| `SKGI` Arauca | `SKUC` | SKGI es Flandes |
| `SKPI` Ipiales | `SKIP` | SKPI es Pitalito |
| `SKAP` Apartadó | `SKLC` Carepa | SKAP es la base aérea de Apiay |

No era un problema cosmético: **la alerta de cierre se busca por código**.
Popayán estaba cerrado (`D0669/26 AD CLSD`) el 18 de agosto y el directorio no
decía nada, porque buscaba cierres de un código que no existe.

Se detectó cruzando la tabla contra los nombres que la propia Aerocivil escribe
en cada NOTAM (`CIUDAD/NOMBRE (ICAO)`), y cada corrección se verificó además
contra fuentes externas antes de aplicarla.

**Corregido**, y el directorio pasó de 31 a 63 aeródromos, tomando nombres y
ciudades de los NOTAM oficiales. El horario no viaja en el NOTAM: los 32
añadidos quedan con una raya en vez de un dato inventado.

### 13. El "Análisis de zona" no podía funcionar

El panel de Coordenadas llamaba a `api.anthropic.com` **desde el navegador del
visitante**, sin ninguna credencial en las cabeceras. No fallaba a ratos: era
imposible que funcionara. Faltaba la clave, y aunque hubiera estado ahí el
navegador habría bloqueado la petición por CORS —y una clave dentro del HTML es
pública por definición, se la lleva cualquiera que abra el portal—.

**Corregido:** endpoint `/api/analizar_zona` en el backend, que es donde sí
puede vivir una clave. El navegador llama al propio portal. Las coordenadas se
parsean y validan en el servidor (rango real de latitud y longitud, máximo 60
puntos) en vez de reenviar al modelo lo que el usuario haya pegado.

### 14. Las llamadas a Gemini no tenían plazo

Al probar el endpoint nuevo con la red hacia Google cortada, la petición se
quedó colgada **más de dos minutos** y solo terminó porque el cliente se
rindió. `request_options={"timeout": N}` del SDK no basta: se aplica por
intento y la librería reintenta por debajo. `/api/traducir` arrastraba el mismo
fallo desde el principio.

**Corregido:** el plazo lo impone el servidor, ejecutando la llamada en un hilo
con fecha límite. Medido después del arreglo, ambos endpoints responden a los
6 s exactos con un mensaje legible en vez de dejar la rueda girando.

### 15. Contraste de texto por debajo de WCAG AA

Al pasar el fondo a video se midió el contraste real de cada trozo de texto
de la pagina —no el color declarado contra `--bg`, sino los píxeles que ocupa
el texto contra lo que efectivamente hay detrás en cinco instantes distintos
del clip.

Resultado: **17 trozos por debajo del mínimo AA**, repartidos entre los dos
temas. La causa era la misma en casi todos: los dos grises más apagados de la
escala se eligieron contra un fondo sólido y se usan en texto de 9 a 12 px,
justo el tamaño que exige 4.5:1. El tema claro nunca se había revisado y
arrastraba además un ámbar y un naranja demasiado claros.

**Corregido:** se subieron `--text-3` y `--text-4` en ambos temas, se añadió
`--text-nav` (el color de las pestanas estaba escrito a pelo y el tema claro
no podía cambiarlo), se oscureció el acento y el ámbar del tema claro, y se
cerraron las tres superficies translucidas donde el video se colaba detrás
del texto: pie de página, franja de telemetría y estado vacío.

Las ocho combinaciones de pestaña y tema pasan AA. La comprobación es
reproducible: `auditar.mjs` captura cada vista dos veces —con el texto y con
el texto en transparente— y `auditar.py` mide solo los píxeles que cambian
entre ambas, que son por definicion los del texto.

### 16. El separador de NOTAMs fusionaba registros

**Severidad: alta.** El extractor corta el boletín donde encuentra un
encabezado (`C 2816 / 26`), pero descartaba el candidato si en los **80
caracteres anteriores** aparecía una palabra de referencia. En un texto como

```
... REF AIP ENR 3.1 RPLC NOTAM C1208/26 C 1919 / 26 ARMENIA/EL EDEN (SKAR) ...
                    └── referencia ──┘ └── NOTAM nuevo, descartado ──┘
```

el segundo NOTAM se quedaba pegado al primero. Sobre los 453 registros del
servidor: **13 contenían más de un NOTAM, sepultando 26**. Uno llegaba a
tener doce.

Tres causas, las tres medidas:

1. La ventana de 80 caracteres. Basta mirar los 15 justo antes.
2. `NR` en la lista de palabras de referencia, **sin `\b`**: coincidía dentro
   de `E**NR** 6.1` y con `ACFT NR 19`, que no referencian nada.
3. El seguidor pedía «algo con diez dígitos en los próximos 150 caracteres»,
   que se cumple casi siempre. Ahora exige `(SKXX)` o `FIR/UIR` seguido de la
   vigencia, que es el formato real. El nombre puede llevar coma
   (`BOGOTÁ, D.C./BOGOTA - EL DORADO`), detalle que rompió el primer intento.

**Corregido.** Sobre los mismos datos: 479 NOTAMs en vez de 453. El único que
sigue sin separarse (`C 3129 / 26 04/26) RPLC NOTAM`) viene truncado de origen.

### 17. El código OACI se tomaba del primer `SK??` del texto

`re.search(r"SK[A-Z]{2}", contenido)` cogía cualquier aparición, aunque fuera
en el cuerpo. **11 NOTAMs de FIR quedaban archivados bajo un aeródromo** solo
porque el texto mencionaba uno, y por tanto no salían al filtrar por FIR.

**Corregido:** manda la cabecera (`FIR/UIR` en los primeros 120 caracteres),
luego el código entre paréntesis —que es el sujeto del NOTAM— y solo al final
la búsqueda suelta. Sobre los datos reales: 0 mal asignados.

### 18. El texto del NOTAM viajaba dentro de un atributo HTML

**Severidad: alta, y ya estaba pasando.** El botón de análisis se construía así:

```js
onclick="consultarIA('${n.texto.replace(/'/g, "\\'")}')"
```

Solo se escapaban las comillas simples. **Seis NOTAMs reales llevan comillas
dobles** —`CENTRO ADM. "MARANDUA"`— y ahí el atributo se cierra antes de tiempo.
Comprobado en el navegador: el `onclick` quedaba en

```
consultarIA('C 0504 / 26 SANTA RITA - VICHADA/CENTRO ADM.
```

o sea que el botón mandaba **48 de 321 caracteres** a la IA, y el resto del
texto se derramaba en el DOM.

**Corregido** por diseño y no por escapar mejor: los datos ya no entran en
atributos. Se guardan en un registro y el botón pasa una clave. Además se
escapan las ocho interpolaciones de datos externos que iban directas a
`innerHTML`, y el indicador OACI se sanea en el origen —terminaba sin
comprobar en `innerHTML`, en el `id` de un elemento y en la URL de AVWX—.

### 19. El servidor podía servir código viejo sin avisar

Python carga cada módulo una vez. Al desplegar con el servidor en marcha, la
API seguía sirviendo la versión anterior: pasó al añadir `/api/cierres`, que
devolvía 404. El lanzador no lo detectaba porque comprobaba **una ruta
concreta** que ya existía en la versión vieja.

**Corregido:** `/health` guarda la fecha de los archivos de código tal como
estaban al importar y la compara con la del disco, devolviendo
`codigo_actualizado`. `PORTAL.bat` mira esa marca, así que no hay que
actualizar la comprobación cada vez que se añade un endpoint.

---

## Cambios que NO se hicieron

- **Rotación de las claves API** — decisión del usuario. Bloqueante antes de
  publicar el repositorio.
- **HTTPS.** El portal responde solo por HTTP. Con un dominio apuntando al
  servidor, `sudo certbot --nginx -d dominio.com` lo resuelve.
- **Extraer las imágenes base64 de `index.html`.**
- **Reestructurar en módulos.** Se mantuvo la disposición plana para que el
  despliegue existente siga funcionando sin cambios.

---

## Resumen

| # | Hallazgo | Severidad | Estado |
|---|---|---|---|
| 1 | Borrado destructivo de la base | Crítica | Corregido |
| 2 | Sin reintentos | Alta | Corregido |
| 3 | Sin validación de resultados | Alta | Corregido |
| 4 | API expuesta en `0.0.0.0` | Alta | Corregido en el repo, **sin desplegar** |
| 5 | Claves API expuestas | Alta | **Pendiente** |
| 6 | Logs sin rotación | Media | Corregido |
| 7 | Rutas relativas | Media | Corregido |
| 8 | Errores con HTTP 200 | Media | Corregido |
| 9 | Sin compresión | Media | Corregido |
| 10 | Archivos duplicados | Baja | Corregido |
| 11 | CUPS expuesto | Media | **Pendiente** (en el servidor) |
| 12 | Cuatro códigos OACI equivocados (ocultaban cierres) | Alta | Corregido |
| 13 | Análisis de zona imposible (llamada sin credencial desde el navegador) | Alta | Corregido |
| 14 | Llamadas a Gemini sin plazo | Media | Corregido |
| 15 | Contraste bajo WCAG AA | Media | Corregido |
| 16 | Separador de NOTAMs fusionaba registros (26 sepultados) | Alta | Corregido |
| 17 | Código OACI tomado del primer `SK??` del texto | Alta | Corregido |
| 18 | Texto de NOTAM dentro de un atributo HTML | Alta | Corregido |
| 19 | El servidor podía servir código viejo sin avisar | Media | Corregido |
