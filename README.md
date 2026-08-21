# Portal Aeronáutico NOTAMs Colombia

Portal web y API de consulta de **NOTAMs nacionales**, extraídos automáticamente
del boletín *Charlie1* que publica la Aeronáutica Civil de Colombia. Incluye
consulta de METAR/TAF en vivo y un traductor de reportes aeronáuticos a lenguaje
llano asistido por IA.

---

## Qué hace

| Componente | Archivo | Función |
|---|---|---|
| **API** | `api_notams.py` | Sirve el portal y expone los NOTAMs vía HTTP (FastAPI + Uvicorn) |
| **Extractor** | `extractor.py` | Cada 15 min descarga el PDF Charlie1 de la Aerocivil, lo parsea y actualiza la base |
| **Frontend** | `index.html` | Portal de una sola página: mapa Leaflet, consulta por aeródromo, METAR/TAF y análisis IA |
| **Cierres** | `cierres.py` | Clasifica qué NOTAMs cierran o limitan un aeródromo |
| **Reglamentos** | `rac_indexar.py`, `rac.py` | Descarga los RAC y los deja buscables por palabra clave |
| **Datos** | `sistema_notams.db` | SQLite. Se regenera en cada extracción exitosa (no se versiona) |
| **Reglamentos (datos)** | `sistema_rac.db` | SQLite + FTS5. Se construye a mano con `rac_indexar.py` |

### Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/` | Portal web |
| `GET` | `/health` | Estado del servicio, total de NOTAMs y fecha de la última extracción |
| `GET` | `/api/notams/{icao}` | NOTAMs de un aeródromo (`SKBO`, `SKRG`, …) |
| `GET` | `/api/notams_all` | Todos los NOTAMs vigentes |
| `GET` | `/api/aerodromos` | Aeródromos con NOTAMs y cuántos tiene cada uno |
| `GET` | `/api/traducir?texto=…` | Traduce un METAR/TAF/NOTAM al español con Gemini |
| `GET` | `/api/cierres` | Aeródromos cerrados o limitados, agrupados por tipo |
| `GET` | `/api/rac/buscar?q=…` | Busca la palabra clave en el texto de los RAC |
| `GET` | `/api/rac/consultar?q=…` | Lo mismo, más un resumen de la IA citando el numeral |
| `GET` | `/api/rac/documentos` | Reglamentos indexados y la versión de cada uno |

Documentación interactiva en `/docs` (generada por FastAPI).

---

## Instalación en un servidor nuevo

Probado en Ubuntu 22.04 y 24.04.

```bash
git clone https://github.com/<usuario>/portal-notams-colombia.git
cd portal-notams-colombia
sudo bash deploy/install.sh
```

El script instala dependencias, Google Chrome (que el extractor necesita para
sortear el portal de la Aerocivil), crea el entorno virtual, registra el servicio
systemd, configura nginx y la rotación de logs, programa el cron cada 15 minutos
y lanza una primera extracción.

Después edita `/home/ubuntu/proyecto_notams/.env` y pon tu `GEMINI_API_KEY`.

### Verificación

```bash
systemctl status notams
curl -s localhost/health
journalctl -u notams -f
tail -f /home/ubuntu/proyecto_notams/logs/extractor.log
```

---

## Desarrollo local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # y completa GEMINI_API_KEY

python3 extractor.py      # primera carga de NOTAMs
python3 rac_indexar.py    # descarga los RAC y arma el buscador de reglamentos
python3 api_notams.py     # http://127.0.0.1:8000
```

Pruebas (no requieren red):

```bash
python3 tests/test_basico.py
```

---

## Configuración

Todo se controla por variables de entorno; ver `.env.example`. Las más
relevantes:

| Variable | Por defecto | Para qué sirve |
|---|---|---|
| `GEMINI_API_KEY` | — | Clave de Google AI Studio para el traductor |
| `CORS_ORIGINS` | `*` | Dominios autorizados. En producción, fijar al dominio real |
| `NOTAMS_MINIMO` | `50` | Si una extracción devuelve menos NOTAMs, se descarta |
| `NOTAMS_CAIDA_MAX_PCT` | `50` | Caída máxima tolerada frente a la extracción anterior |
| `NOTAMS_INTENTOS` | `3` | Reintentos de descarga del PDF |

---

## Cómo funciona la actualización de datos

El punto delicado es que el portal de la Aerocivil falla con frecuencia. Para
que eso no deje el portal sin datos, cada extracción sigue este flujo:

```
descargar PDF (hasta 3 intentos)
        │
        ├── falla ──► no se toca nada, la base anterior sigue sirviendo
        │
        └── éxito ──► parsear ──► escribir en sistema_notams.db.tmp
                                          │
                                          ├── ¿menos de 50 NOTAMs?      ─► descartar
                                          ├── ¿caída mayor al 50%?      ─► descartar
                                          └── ok ─► os.replace() atómico ─► publicado
```

La base anterior se conserva en `sistema_notams.db.prev` por si hay que
revertir a mano.

Para forzar la publicación saltándose las validaciones:

```bash
python3 extractor.py --force
```

---

## Estructura

```
.
├── api_notams.py            API FastAPI
├── extractor.py             Extractor del boletín Charlie1
├── cierres.py               Clasificación de cierres y restricciones
├── rac_catalogo.py          Qué RAC se indexan, con su versión
├── rac_indexar.py           Descarga los RAC y construye sistema_rac.db
├── rac_segmentar.py         Corta el PDF de un RAC en apartados
├── rac.py                   Búsqueda FTS5 sobre los reglamentos
├── index.html               Portal web
├── requirements.txt
├── .env.example
├── static/                  Servido en /static, con caché de 30 días
│   ├── hero-nubes.mp4       Video de fondo (H.264)
│   ├── hero-nubes.webm      El mismo, en VP9: 40 % más liviano
│   └── hero-nubes-poster.jpg  Respaldo mientras carga y con reduced-motion
├── deploy/
│   ├── install.sh           Instalación completa en servidor nuevo
│   ├── notams.service       Unidad systemd
│   ├── nginx-notams.conf    Proxy inverso
│   └── logrotate-notams     Rotación de logs
├── docs/
│   ├── AUDITORIA.md         Diagnóstico del servidor original y cambios aplicados
│   └── REGLAMENTOS.md       Cómo funciona el buscador de RAC y cómo actualizarlo
└── tests/
    └── test_basico.py
```

---

## Pendientes conocidos

Ver `docs/AUDITORIA.md` para el detalle. Lo más importante:

- **Claves API en el código.** `GEMINI_API_KEY` sigue con un valor por defecto
  dentro de `api_notams.py`, y el token de AVWX está incrustado en `index.html`,
  donde es visible para cualquier visitante del portal. **Hay que rotarlas y
  moverlas a `.env` antes de publicar este repositorio.**
- Sin HTTPS: el portal responde solo por HTTP.

---

## Buscador de Reglamentos

La pestaña **Reglamentos** busca por palabra clave dentro de 16 RAC (los de
operación y los de apoyo) y devuelve los apartados con su numeral y la fecha de
la versión de la que salieron. La IA resume citando el numeral, pero el texto
literal se muestra siempre debajo.

El índice **no se construye solo**: hay que correr `rac_indexar.py` la primera
vez y cada vez que la Aerocivil publique una enmienda. El detalle, en
[`docs/REGLAMENTOS.md`](docs/REGLAMENTOS.md).

---

## Fuentes de datos

- **NOTAMs:** boletín Charlie1 de la [Aeronáutica Civil de Colombia](https://www.aerocivil.gov.co/publicaciones/3708/listas-de-verificacion-y-listas-de-notam-validos/)
- **METAR/TAF:** [AVWX REST API](https://avwx.rest/)
- **Reglamentos:** RAC publicados por la Aeronáutica Civil de Colombia
- **Traducción:** Google Gemini

> Este portal es una herramienta de consulta y **no sustituye la información
> oficial de la autoridad aeronáutica**. Para operaciones reales, consulte
> siempre las publicaciones AIS oficiales.
