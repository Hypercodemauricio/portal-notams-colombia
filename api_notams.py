"""
API del Portal Aeronautico NOTAMs Colombia.

Cambios clave respecto a la version original:
  * Rutas absolutas: la API ya no depende del directorio de trabajo.
  * Conexiones SQLite en modo solo-lectura: la API no puede corromper la base
    que el extractor esta reemplazando.
  * Errores HTTP reales (404/503) en vez de responder 200 con {"error": ...},
    que hacia imposible detectar fallos desde el frontend o un monitor.
  * Compresion GZip: index.html pesa ~530 KB y se enviaba sin comprimir.
  * Endpoint /health para monitoreo y para saber cuando fue la ultima
    extraccion exitosa.
  * Configuracion por variables de entorno (con los valores actuales como
    respaldo, para no romper el despliegue existente).
"""

import os
import re
import sqlite3
import logging
import warnings
from pathlib import Path
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, TimeoutError as EsperaAgotada

import google.generativeai as genai
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

import cierres
import rac

warnings.filterwarnings("ignore")
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s"
)
log = logging.getLogger("api")

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / os.getenv("NOTAMS_DB", "sistema_notams.db")
INDEX_PATH = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"

# Python carga el modulo una vez y no lo relee. Si se actualizan los archivos
# con el servidor en marcha, la API sigue sirviendo la version anterior: paso
# justamente eso al anadir /api/cierres, y el lanzador no lo detecto porque
# comprobaba una ruta concreta -el video- que ya existia en la version vieja.
# Se guarda la fecha de los archivos tal como estaban al importar; /health
# compara con la del disco y dice si el proceso quedo desfasado. Asi la
# comprobacion no hay que actualizarla cada vez que se anade un endpoint.
_VIGILADOS = ("api_notams.py", "cierres.py", "extractor.py",
              "rac.py", "rac_segmentar.py", "rac_catalogo.py")


def _firma_codigo() -> float:
    ultima = 0.0
    for nombre in _VIGILADOS:
        ruta = BASE_DIR / nombre
        if ruta.exists():
            ultima = max(ultima, ruta.stat().st_mtime)
    return round(ultima, 3)


FIRMA_AL_ARRANCAR = _firma_codigo()

# PENDIENTE DE SEGURIDAD: mover esta clave a un archivo .env antes de publicar
# el repositorio en GitHub. Ya se puede sobrescribir con la variable de entorno
# GEMINI_API_KEY sin tocar el codigo.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "PON_TU_CLAVE_EN_EL_ARCHIVO_ENV")
MODELO_IA = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
# Sin limite de tiempo, una peticion a Gemini que no responde deja la conexion
# del visitante colgada indefinidamente: la rueda gira y nunca pasa nada. Con
# limite, falla rapido y el frontend muestra su mensaje.
IA_TIMEOUT = int(os.getenv("GEMINI_TIMEOUT", "20"))

# Un solo hilo suelto por peticion es suficiente y evita que un pico de
# consultas abra hilos sin control.
_ejecutor_ia = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ia")


def preguntar_a_gemini(prompt: str) -> str:
    """
    Consulta a Gemini con un plazo que se cumple de verdad.

    El parametro request_options={"timeout": N} del SDK no basta: se aplica
    por intento y la libreria reintenta por debajo, asi que con la red
    cortada la llamada seguia colgada pasados dos minutos -medido-. Aqui el
    plazo lo impone el servidor: si no hay respuesta a tiempo, se corta y se
    responde igual.

    El hilo que quedo esperando no se puede matar, pero deja de importar: la
    peticion del visitante ya volvio.
    """
    def _llamar():
        modelo = genai.GenerativeModel(MODELO_IA)
        return modelo.generate_content(
            prompt, request_options={"timeout": IA_TIMEOUT}
        ).text

    futuro = _ejecutor_ia.submit(_llamar)
    try:
        return futuro.result(timeout=IA_TIMEOUT)
    except EsperaAgotada:
        futuro.cancel()
        raise TimeoutError(
            f"Gemini no respondio en {IA_TIMEOUT} s."
        )

# Por defecto se mantiene "*" para no romper clientes existentes. En produccion
# conviene fijar CORS_ORIGINS al dominio real del portal.
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",")]

app = FastAPI(
    title="API NOTAMs Colombia",
    description="NOTAMs nacionales extraidos del Charlie1 de la Aerocivil.",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Archivos estaticos: el video de fondo y su poster. Antes el fondo eran siete
# fotos en base64 incrustadas en index.html (442 KB que se redescargaban en
# cada visita porque el HTML no se puede cachear). Servidos aqui, nginx y el
# navegador si los pueden cachear.
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
else:
    log.warning("No existe %s: el portal usara el fondo solido.", STATIC_DIR)


@app.middleware("http")
async def politica_de_estaticos(request: Request, call_next):
    """
    Dos ajustes solo para /static:

    1. Sin gzip. El MP4, el WebM y el JPG ya vienen comprimidos; volver a
       pasarlos por gzip gasta CPU en cada peticion para no ganar nada.
       GZipMiddleware decide unicamente por la cabecera Accept-Encoding, asi
       que la forma limpia de excluirlos es quitarla antes de que la vea.
       Este middleware se registra el ultimo, por lo que envuelve al de gzip.

    2. Cache larga. Son archivos que no cambian; sin esto el navegador
       revalida el video en cada carga de la pagina.
    """
    if request.url.path.startswith("/static/"):
        request.scope["headers"] = [
            (k, v) for (k, v) in request.scope["headers"] if k != b"accept-encoding"
        ]
        respuesta = await call_next(request)
        respuesta.headers["Cache-Control"] = "public, max-age=2592000, immutable"
        return respuesta
    return await call_next(request)

genai.configure(api_key=GEMINI_API_KEY)


@contextmanager
def abrir_db():
    """
    Conexion en modo solo-lectura. Si el extractor esta reemplazando la base
    justo en este instante, fallamos con 503 en vez de devolver datos rotos.
    """
    if not DB_PATH.exists():
        raise HTTPException(
            status_code=503,
            detail="La base de datos aun no ha sido generada por el extractor.",
        )
    conexion = None
    try:
        conexion = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
        conexion.row_factory = sqlite3.Row
        yield conexion
    except sqlite3.Error as e:
        log.error("Error de base de datos: %s", e)
        raise HTTPException(status_code=503, detail="Base de datos no disponible.")
    finally:
        if conexion is not None:
            conexion.close()


@app.get("/", include_in_schema=False)
async def servidor_web():
    if not INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="index.html no encontrado")
    return FileResponse(INDEX_PATH)


@app.get("/health")
def health():
    """Estado del servicio. Util para monitoreo y para el balanceador."""
    estado = {"estado": "ok", "base_de_datos": str(DB_PATH.name)}

    # Si los archivos del disco son mas nuevos que los que este proceso
    # cargo, hay que reiniciar: lo que se sirve no es lo que hay escrito.
    estado["codigo_actualizado"] = _firma_codigo() <= FIRMA_AL_ARRANCAR
    if not estado["codigo_actualizado"]:
        estado["estado"] = "degradado"
        estado["detalle"] = ("El codigo en disco es mas nuevo que el que esta "
                             "corriendo. Reinicia el servicio.")
        return estado
    try:
        with abrir_db() as conexion:
            estado["total_notams"] = conexion.execute(
                "SELECT COUNT(*) FROM notams"
            ).fetchone()[0]
            fila = conexion.execute(
                "SELECT valor FROM metadatos WHERE clave = 'ultima_extraccion'"
            ).fetchone()
            estado["ultima_extraccion"] = fila[0] if fila else None

            # Cuantos registros traen mas de un NOTAM dentro. Deberia ser 0.
            # Si un cambio del boletin vuelve a pegarlos, se ve aqui sin tener
            # que leer el log ni notarlo de casualidad en la pantalla.
            fila = conexion.execute(
                "SELECT valor FROM metadatos WHERE clave = 'notams_pegados'"
            ).fetchone()
            if fila is not None:
                estado["notams_pegados"] = int(fila[0])
                if estado["notams_pegados"]:
                    estado["estado"] = "degradado"
                    estado["detalle"] = (
                        f"{fila[0]} registro(s) contienen mas de un NOTAM. "
                        f"Corre: python3 extractor.py --reparsear")
    except HTTPException as e:
        estado["estado"] = "degradado"
        estado["detalle"] = e.detail
        return estado
    except sqlite3.Error:
        estado["total_notams"] = None

    # El buscador de reglamentos usa su propia base y se construye aparte.
    # Que falte no deja el portal fuera de servicio: se informa y ya.
    estado["rac_indexado"] = rac.hay_base()
    if estado["rac_indexado"]:
        try:
            estado["rac_documentos"] = len(rac.documentos())
        except Exception:  # noqa: BLE001
            estado["rac_documentos"] = None

    if not estado.get("total_notams"):
        estado["estado"] = "degradado"
        estado["detalle"] = "La base no contiene NOTAMs."
    return estado


@app.get("/api/notams/{icao}")
def obtener_notams(icao: str):
    """NOTAMs vigentes para un aerodromo (codigo OACI, p.ej. SKBO)."""
    codigo = icao.strip().upper()
    if not codigo.isalnum() or not 2 <= len(codigo) <= 5:
        raise HTTPException(status_code=400, detail="Codigo OACI invalido.")

    with abrir_db() as conexion:
        filas = conexion.execute(
            "SELECT notam_id, icao_code, content, last_updated "
            "FROM notams WHERE icao_code = ? ORDER BY notam_id",
            (codigo,),
        ).fetchall()

    return {
        "total": len(filas),
        "datos": [
            {
                "id_notam": f["notam_id"],
                "aerodromo": f["icao_code"],
                "texto": f["content"],
                "ultima_actualizacion": f["last_updated"],
            }
            for f in filas
        ],
    }


@app.get("/api/notams_all")
def get_all_notams():
    """Todos los NOTAMs cargados. Lo consume el mapa del portal."""
    with abrir_db() as conexion:
        filas = conexion.execute(
            "SELECT icao_code, notam_id, content FROM notams ORDER BY icao_code, notam_id"
        ).fetchall()

    return {
        "total": len(filas),
        "datos": [
            {"aerodromo": f["icao_code"], "id_notam": f["notam_id"], "texto": f["content"]}
            for f in filas
        ],
    }


@app.get("/api/aerodromos")
def listar_aerodromos():
    """Aerodromos con NOTAMs vigentes y cuantos tiene cada uno."""
    with abrir_db() as conexion:
        filas = conexion.execute(
            "SELECT icao_code, COUNT(*) AS total FROM notams "
            "GROUP BY icao_code ORDER BY total DESC"
        ).fetchall()
    return {"datos": [{"aerodromo": f["icao_code"], "total": f["total"]} for f in filas]}


@app.get("/api/traducir")
def analizar_con_ia(texto: str = Query(..., min_length=3, max_length=8000)):
    """Traduce/resume un METAR, TAF o NOTAM al espanol usando Gemini."""
    contexto = f"""Actúa como experto AIS. Traduce este reporte METAR, TAF o NOTAM al español de forma extremadamente breve, directa y al grano, como para una lectura rápida.
    - Usa máximo uno o dos párrafos cortos.
    - NO incluyas tablas, ni listas, ni desgloses de abreviaturas.
    - Si es un NOTAM de peligro, resáltalo con 🚨.
    Reporte original: {texto}"""
    try:
        return {"explicacion": preguntar_a_gemini(contexto)}
    except Exception as e:
        # Se mantiene 200 con mensaje amable: el frontend ya espera este formato
        # y una traduccion fallida no debe romper la vista del NOTAM.
        log.warning("Fallo el analista IA: %s", e)
        return {"explicacion": f"El Analista IA no esta disponible: {e}"}


@app.get("/api/analizar_zona")
def analizar_zona(
    coords: str = Query(..., min_length=3, max_length=4000),
    tipo: str = Query("punto", max_length=16),
    radio_nm: float = Query(0, ge=0, le=1000),
):
    """
    Describe en palabras la zona que cubren unas coordenadas.

    Antes de esto, el panel de Coordenadas llamaba a api.anthropic.com
    directamente desde el navegador del visitante y sin credencial. No
    fallaba a veces: no podia funcionar nunca, ni por autenticacion ni por
    CORS. Una clave de API no puede vivir en el navegador, asi que la
    consulta tiene que salir del servidor.

    Las coordenadas se parsean y se validan aqui en vez de reenviar el
    texto crudo: asi el modelo recibe numeros y no lo que el usuario haya
    pegado en el area de texto.
    """
    puntos = []
    for linea in coords.replace(";", "\n").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        partes = linea.replace(",", " ").split()
        if len(partes) < 2:
            continue
        try:
            lat, lng = float(partes[0]), float(partes[1])
        except ValueError:
            continue
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            puntos.append((lat, lng))
        if len(puntos) >= 60:
            break

    if not puntos:
        raise HTTPException(
            status_code=400,
            detail="No se reconocio ninguna coordenada valida.",
        )

    lat_c = sum(p[0] for p in puntos) / len(puntos)
    lng_c = sum(p[1] for p in puntos) / len(puntos)
    listado = "\n".join(f"{la:.6f}, {ln:.6f}" for la, ln in puntos)

    detalle_radio = ""
    if radio_nm > 0:
        detalle_radio = f"\nRadio declarado: {radio_nm:g} NM alrededor del centro."

    contexto = f"""Eres experto en geografia de Colombia y Latinoamerica, con criterio aeronautico.
Analiza la zona que cubren estas coordenadas.

Coordenadas ({len(puntos)} punto(s), tipo {tipo}):
{listado}
Centro aproximado: {lat_c:.4f}, {lng_c:.4f}{detalle_radio}

Responde exactamente con estas seis lineas, una por apartado, sin encabezados ni listas:
UBICACION: pais, departamento y municipio mas cercano.
ZONA: que tipo de area es y sus caracteristicas.
REFERENCIAS: aerodromos, poblaciones o accidentes geograficos cercanos.
ENTORNO: relieve, hidrografia y clima dominante.
RELEVANCIA: por que puede importarle a un piloto o a un despachador.
FIABILIDAD: di claramente si alguna de las afirmaciones anteriores es aproximada.

Se concreto y breve. Si no estas seguro de un dato, dilo en vez de inventarlo."""

    try:
        return {"analisis": preguntar_a_gemini(contexto), "puntos": len(puntos),
                "centro": {"lat": round(lat_c, 6), "lng": round(lng_c, 6)}}
    except Exception as e:
        # Mismo criterio que /api/traducir: 200 con un mensaje legible. El
        # panel muestra las mediciones aunque el texto falle, y esas se
        # calculan en el navegador sin depender de nadie.
        log.warning("Fallo el analisis de zona: %s", e)
        return {"analisis": f"El analisis de zona no esta disponible: {e}",
                "puntos": len(puntos),
                "centro": {"lat": round(lat_c, 6), "lng": round(lng_c, 6)}}


@app.get("/api/cierres")
def listar_cierres(
    ambito: str = Query("todos", pattern="^(todos|aerodromos|fir)$"),
    severidad: str = Query("todos", pattern="^(todos|cierre)$"),
):
    """
    NOTAMs que cierran o limitan algo, agrupados por lo que afectan.

    Nace de ver al operador buscando "CLSD" y luego "LTD" a mano en la
    pestana de NOTAMs, una palabra por vez. Aqui se aplica el mismo criterio
    de golpe y ademas se separa lo grave de lo accesorio.

    Los NOTAM de los dos FIR colombianos -SKED Bogota y SKEC Barranquilla-
    entran igual que los de aerodromo: afectan al espacio aereo por el que
    se vuela, no a una pista concreta.

    Las reglas viven en cierres.py y se escribieron mirando los 453 NOTAMs
    reales del servidor, no de memoria.
    """
    with abrir_db() as conexion:
        filas = conexion.execute(
            "SELECT icao_code, notam_id, content, last_updated FROM notams"
        ).fetchall()

    grupos = {}
    afectados, n_cierres, n_lim, n_fir = set(), 0, 0, 0

    for f in filas:
        icao, texto = f["icao_code"], f["content"]
        r = cierres.clasificar(icao, texto)
        if not r:
            continue
        cat, etiqueta, sev, prio, amb = r
        es_fir = icao in cierres.FIRS

        if ambito == "aerodromos" and es_fir:
            continue
        if ambito == "fir" and not es_fir:
            continue
        if severidad == "cierre" and sev != "cierre":
            continue

        ini, fin = cierres.vigencia(texto)
        entrada = {
            "id_notam": f["notam_id"],
            "aerodromo": icao,
            "nombre": cierres.nombre_aerodromo(texto),
            "ambito": amb,
            "es_fir": es_fir,
            "resumen": cierres.resumen_operativo(texto),
            "texto": texto,
            "vigencia_inicio": ini,
            "vigencia_fin": fin,
        }
        # La clave lleva la severidad: "Pista cerrada" y "Pista limitada"
        # son dos bloques distintos. Juntarlos daba un grupo rotulado
        # "Pista limitada" pero marcado como cierre, que es justo lo que un
        # operador no debe tener que descifrar.
        g = grupos.setdefault(
            (cat, sev),
            {"clave": f"{cat}_{sev}", "etiqueta": etiqueta, "severidad": sev,
             "prioridad": prio, "notams": []},
        )
        g["notams"].append(entrada)

        afectados.add(icao)
        if es_fir:
            n_fir += 1
        if sev == "cierre":
            n_cierres += 1
        else:
            n_lim += 1

    for g in grupos.values():
        g["total"] = len(g["notams"])
        # Dentro del grupo, primero lo que cierra y luego por aerodromo.
        g["notams"].sort(key=lambda n: (not n["resumen"].upper().count("CLSD"),
                                        n["aerodromo"], n["id_notam"]))

    ordenados = sorted(grupos.values(), key=lambda g: (g["prioridad"], -g["total"]))

    return {
        "total": n_cierres + n_lim,
        "resumen": {
            "cierres": n_cierres,
            "limitaciones": n_lim,
            "aerodromos_afectados": len([a for a in afectados if a not in cierres.FIRS]),
            "notams_fir": n_fir,
        },
        "grupos": ordenados,
    }


# ---------------------------------------------------------------------------
# Reglamentos Aeronauticos de Colombia (RAC)
# ---------------------------------------------------------------------------
# El orden importa y es deliberado: primero se busca el texto literal en el
# indice, y solo despues se le pide a la IA que lo resuma citando numerales.
# Nunca se le pregunta a la IA "que dice el RAC sobre X" sin darle el texto:
# es justo la pregunta que un modelo responde con un requisito verosimil que
# no existe, y en materia normativa eso no es un error cosmetico.


def _sin_indice():
    raise HTTPException(
        status_code=503,
        detail="El indice de reglamentos no esta construido. "
               "Corre: python3 rac_indexar.py")


@app.get("/api/rac/documentos")
def rac_documentos():
    """Reglamentos indexados, con la fecha de version de cada uno."""
    if not rac.hay_base():
        _sin_indice()
    return {"documentos": rac.documentos()}


@app.get("/api/rac/buscar")
def rac_buscar(
    q: str = Query(..., min_length=2, max_length=200),
    rac_num: str = Query(None, alias="rac", max_length=5),
    limite: int = Query(20, ge=1, le=50),
):
    """Busqueda literal por palabra clave sobre el texto de los RAC."""
    if not rac.hay_base():
        _sin_indice()
    if rac_num and not re.fullmatch(r"[0-9]{1,3}", rac_num):
        raise HTTPException(status_code=400, detail="Numero de RAC no valido")
    return rac.buscar(q, rac=rac_num, limite=limite)


@app.get("/api/rac/apartado/{numero}/{numeral}")
def rac_apartado(numero: str, numeral: str):
    """Texto completo de un numeral concreto, p.ej. /api/rac/apartado/91/91.310"""
    if not rac.hay_base():
        _sin_indice()
    if not re.fullmatch(r"[0-9]{1,3}", numero):
        raise HTTPException(status_code=400, detail="Numero de RAC no valido")
    if not re.fullmatch(r"[0-9A-Za-z. ]{1,30}", numeral):
        raise HTTPException(status_code=400, detail="Numeral no valido")
    resultado = rac.apartado(numero, numeral.strip())
    if not resultado:
        raise HTTPException(status_code=404,
                            detail=f"No existe el numeral {numeral} en el RAC {numero}")
    return resultado


@app.get("/api/rac/consultar")
def rac_consultar(
    q: str = Query(..., min_length=3, max_length=300),
    rac_num: str = Query(None, alias="rac", max_length=5),
):
    """
    Busca los apartados pertinentes y pide a la IA un resumen CON CITAS.

    Devuelve siempre los apartados usados, aunque la IA falle: si Gemini no
    responde, el usuario todavia tiene el texto literal, que es lo que
    realmente importa.
    """
    if not rac.hay_base():
        _sin_indice()

    hallazgos = rac.buscar(q, rac=rac_num, limite=8)
    if not hallazgos["resultados"]:
        return {"respuesta": None, "sin_resultados": True, **hallazgos}

    contexto = rac.contexto_para_ia(hallazgos["resultados"])
    try:
        respuesta = preguntar_a_gemini(rac.PROMPT.format(pregunta=q, contexto=contexto))
        error = None
    except Exception as e:  # noqa: BLE001
        # Se atrapa cualquier excepcion, no solo HTTPException: al agotarse el
        # plazo, preguntar_a_gemini lanza TimeoutError, que no es HTTPException.
        # Con el except estrecho este endpoint devolvia 500 y el usuario perdia
        # TAMBIEN los apartados literales, que son lo unico que de verdad
        # importa. Comprobado con Gemini inalcanzable: 500 y pantalla de error
        # en vez del texto del reglamento.
        log.warning("Resumen IA no disponible en una consulta RAC: %s", e)
        respuesta, error = None, str(e)

    return {"respuesta": respuesta, "error_ia": error,
            "apartados_consultados": len(hallazgos["resultados"]), **hallazgos}


if __name__ == "__main__":
    import uvicorn

    # Antes: host="0.0.0.0", port=80 -- inconsistente con el servicio systemd
    # (que usa el 8000) y requiere root. Ahora coincide con el despliegue real.
    uvicorn.run(
        "api_notams:app",
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
