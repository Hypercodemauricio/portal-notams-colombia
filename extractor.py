"""
Extractor de NOTAMs nacionales desde el portal de la Aerocivil (Colombia).

Cambios clave respecto a la version original:
  * Actualizacion ATOMICA: se construye una base temporal y solo se reemplaza
    la base en produccion si la extraccion produjo resultados. Si el portal
    de la Aerocivil falla, la base anterior queda intacta y la API sigue
    respondiendo con los ultimos NOTAMs buenos.
  * Reintentos con espera incremental en la descarga del PDF.
  * Validacion de resultados antes de publicar (minimo de NOTAMs esperado).
  * Logging rotativo en vez de print + cron.log creciendo sin limite.
  * Rutas absolutas: ya no depende del directorio de trabajo del cron.
  * El navegador se cierra siempre, incluso ante excepcion.
"""

import os
import re
import sys
import time
import shutil
import sqlite3
import logging
import argparse
from pathlib import Path
from logging.handlers import RotatingFileHandler

import requests
import pdfplumber
import urllib3
# Selenium y webdriver_manager se importan DENTRO de obtener_url_pdf(), no
# aqui. Solo hacen falta para abrir el portal de la Aerocivil, pero al
# importarlos arriba cualquier uso del extractor los exigia: en un equipo sin
# Chrome, "extractor.py --reparsear" -que no toca la red- moria con
# ModuleNotFoundError antes de ejecutar una sola linea util.

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --------------------------------------------------------------------------
# Configuracion
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"

DB_PATH = BASE_DIR / os.getenv("NOTAMS_DB", "sistema_notams.db")
DB_TMP_PATH = DB_PATH.with_suffix(".db.tmp")
DB_PREV_PATH = DB_PATH.with_suffix(".db.prev")
PDF_PATH = BASE_DIR / "charlie1_temporal.pdf"

URL_AEROCIVIL = os.getenv(
    "NOTAMS_URL",
    "https://www.aerocivil.gov.co/publicaciones/3708/"
    "listas-de-verificacion-y-listas-de-notam-validos/",
)

# Si la extraccion devuelve menos NOTAMs que esto, se considera sospechosa y
# NO se publica: es mas seguro servir datos de hace 15 minutos que una lista
# recortada por un cambio de formato en el PDF.
MINIMO_NOTAMS = int(os.getenv("NOTAMS_MINIMO", "50"))

# Si el resultado cae mas de este porcentaje respecto a la ultima corrida
# valida, tampoco se publica sin --force.
CAIDA_MAXIMA_PCT = float(os.getenv("NOTAMS_CAIDA_MAX_PCT", "50"))

INTENTOS = int(os.getenv("NOTAMS_INTENTOS", "3"))
ESPERA_BASE = int(os.getenv("NOTAMS_ESPERA", "10"))  # segundos
TIMEOUT_HTTP = int(os.getenv("NOTAMS_TIMEOUT", "30"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# Sin \b, "NR" coincidia dentro de "ENR 6.1" y "NR" a secas tambien pillaba
# "ACFT NR 19" o "COMANDO AEREO DE COMBATE NR 1", que no son referencias a
# ningun NOTAM. Cada falso positivo aqui hace que un NOTAM real se pegue al
# anterior en vez de quedar como registro propio.
PALABRAS_REFERENCIA = re.compile(
    r"\b(NOTAM|RPLC|REPLACES|CANX|CANCELS|REF|C/W|COMPLY\s+WITH|SBJT|SEE\s+ALSO|"
    r"PREVIOUS|SUPERSEDES|AMENDED\s+BY|AMDT|WI\s+NOTAM)\s*[A-Z]?\s*\d",
    re.IGNORECASE,
)

# Un encabezado de NOTAM va seguido del aerodromo y su vigencia:
#     C 2816 / 26 RIONEGRO/... (SKRG) 2607160600 / 2610052359
#     C 2248 / 26 FIR/UIR BOGOTA     2606041148 / 2609012359
# El patron anterior solo pedia "algo con diez digitos en los proximos 150
# caracteres", que se cumple casi siempre y no discriminaba nada.
# El nombre puede llevar comas: "BOGOTA, D.C./BOGOTA - EL DORADO".
SEGUIDOR_ENCABEZADO = re.compile(
    r"^.{0,95}?(?:\(SK[A-Z]{2}\)|FIR/UIR\s+[A-ZÁÉÍÓÚÑ]+)\s*(?:\d{10}|PERM)",
    re.DOTALL,
)

# Cabecera completa de un NOTAM: el sujeto (aerodromo entre parentesis o FIR)
# seguido del par de vigencias. Un NOTAM bien cortado tiene exactamente UNA.
# Esta comprobacion no depende de las reglas de corte de arriba, y por eso
# sirve para vigilarlas: si un registro termina con dos cabeceras, dentro hay
# dos NOTAMs, sin importar por que regla se escaparon.
CABECERA_NOTAM = re.compile(
    r"(?:\(SK[A-Z]{2}\)|FIR/UIR\s+[A-ZÁÉÍÓÚÑ]+)\s*(?:\d{10}|PERM)\s*/\s*"
    r"(?:\d{10}|PERM)")

# El boletin trae dos numeraciones seguidas del mismo NOTAM, a veces con un
# sello de fecha en medio: "C3308/24 260818 1837 3 C 1297 / 25 ARMENIA...".
# Si entre dos candidatos solo hay digitos, espacios y barras, son el mismo.
RELLENO_ENTRE_NUMERACIONES = re.compile(r"^[\d\s/]*$")

log = logging.getLogger("extractor")


def configurar_logging(verboso: bool = False) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    log.setLevel(logging.DEBUG if verboso else logging.INFO)
    formato = logging.Formatter(
        "%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
    # 5 archivos de 2 MB: el log deja de crecer sin control.
    archivo = RotatingFileHandler(
        LOG_DIR / "extractor.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    archivo.setFormatter(formato)
    log.addHandler(archivo)

    consola = logging.StreamHandler(sys.stdout)
    consola.setFormatter(formato)
    log.addHandler(consola)


# --------------------------------------------------------------------------
# Base de datos
# --------------------------------------------------------------------------
ESQUEMA = """
CREATE TABLE IF NOT EXISTS notams (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    icao_code    TEXT NOT NULL,
    notam_id     TEXT UNIQUE NOT NULL,
    content      TEXT,
    last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notams_icao ON notams(icao_code);

CREATE TABLE IF NOT EXISTS metadatos (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
"""


def crear_base_temporal() -> sqlite3.Connection:
    """Crea la base temporal desde cero. Nunca toca la base en produccion."""
    if DB_TMP_PATH.exists():
        DB_TMP_PATH.unlink()
    conexion = sqlite3.connect(DB_TMP_PATH)
    conexion.executescript(ESQUEMA)
    conexion.commit()
    log.info("Base temporal creada en %s", DB_TMP_PATH.name)
    return conexion


def contar_notams(ruta: Path) -> int:
    if not ruta.exists():
        return 0
    try:
        with sqlite3.connect(f"file:{ruta}?mode=ro", uri=True) as c:
            return c.execute("SELECT COUNT(*) FROM notams").fetchone()[0]
    except sqlite3.Error:
        return 0


def publicar(total: int, forzar: bool = False) -> bool:
    """
    Reemplaza la base en produccion por la temporal, pero solo si los datos
    pasan las validaciones. Devuelve True si se publico.
    """
    anterior = contar_notams(DB_PATH)

    if total < MINIMO_NOTAMS and not forzar:
        log.error(
            "NO se publica: solo %d NOTAMs extraidos (minimo %d). "
            "La base actual con %d NOTAMs se conserva.",
            total, MINIMO_NOTAMS, anterior,
        )
        return False

    if anterior > 0 and not forzar:
        caida = (anterior - total) / anterior * 100
        if caida > CAIDA_MAXIMA_PCT:
            log.error(
                "NO se publica: caida del %.1f%% (%d -> %d), supera el maximo "
                "permitido de %.0f%%. Revisa si cambio el formato del PDF. "
                "Usa --force para publicar de todas formas.",
                caida, anterior, total, CAIDA_MAXIMA_PCT,
            )
            return False

    # Guardamos la base anterior por si hay que revertir a mano.
    if DB_PATH.exists():
        shutil.copy2(DB_PATH, DB_PREV_PATH)

    # os.replace es atomico dentro del mismo sistema de archivos: la API nunca
    # ve un archivo a medio escribir.
    #
    # En Windows, ademas, no se puede reemplazar un archivo que otro proceso
    # tiene abierto: si el portal esta sirviendo una consulta en ese instante,
    # os.replace falla con "Acceso denegado" y el trabajo hecho se pierde,
    # aunque el reparseo haya salido bien. En Linux esto no ocurre. Se
    # reintenta unas cuantas veces -la ventana en la que la API tiene la base
    # abierta es de milisegundos- y, si aun asi no se puede, se dice con
    # claridad que hay que cerrar el portal, en vez de soltar una traza.
    for intento in range(1, 7):
        try:
            os.replace(DB_TMP_PATH, DB_PATH)
            break
        except PermissionError:
            if intento == 6:
                log.error(
                    "NO se pudo publicar: el archivo %s esta abierto por otro "
                    "programa (normalmente el portal). Cierra la ventana de "
                    "PORTAL.bat y vuelve a ejecutarlo. Los datos nuevos quedan "
                    "en %s y no se pierde nada.",
                    DB_PATH.name, DB_TMP_PATH.name)
                return False
            log.warning("El archivo esta ocupado; reintento %d de 5...", intento)
            time.sleep(1.5 * intento)

    log.info("Publicado: %d NOTAMs (antes habia %d).", total, anterior)
    return True


# --------------------------------------------------------------------------
# Descarga
# --------------------------------------------------------------------------
def obtener_url_pdf() -> tuple[str, list]:
    """Abre el portal con Selenium y devuelve la URL del PDF Charlie1."""
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.service import Service
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from webdriver_manager.chrome import ChromeDriverManager
    except ImportError as e:
        raise RuntimeError(
            f"Falta una dependencia para descargar el boletin ({e}). "
            f"Instala: pip install selenium webdriver-manager. "
            f"Los modos --reparsear y --revisar no la necesitan.") from e

    opciones = Options()
    opciones.add_argument("--headless=new")
    opciones.add_argument("--no-sandbox")
    opciones.add_argument("--disable-dev-shm-usage")
    opciones.add_argument("--disable-gpu")
    opciones.add_argument(f"user-agent={USER_AGENT}")

    driver = None
    try:
        servicio = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=servicio, options=opciones)
        driver.set_page_load_timeout(60)
        driver.get(URL_AEROCIVIL)

        enlace = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(., 'Charlie1')]"))
        )
        url = enlace.get_attribute("href")
        cookies = driver.get_cookies()
        log.info("URL del PDF localizada: %s", url)
        return url, cookies
    finally:
        # El original dejaba procesos de Chrome huerfanos cuando algo fallaba
        # entre driver.get() y driver.quit().
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                log.warning("No se pudo cerrar el navegador limpiamente.")


def descargar_pdf(url: str, cookies: list) -> bool:
    cabeceras = {
        "User-Agent": USER_AGENT,
        "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    sesion = requests.Session()
    for cookie in cookies:
        sesion.cookies.set(cookie["name"], cookie["value"])

    respuesta = sesion.get(url, verify=False, headers=cabeceras, timeout=TIMEOUT_HTTP)
    respuesta.raise_for_status()

    if not respuesta.content.startswith(b"%PDF-"):
        inicio = respuesta.content[:120].decode("utf-8", "replace")
        log.warning(
            "La respuesta no es un PDF (%d bytes). Inicio del contenido: %r",
            len(respuesta.content), inicio,
        )
        return False

    PDF_PATH.write_bytes(respuesta.content)
    log.info("PDF descargado: %.1f KB", len(respuesta.content) / 1024)
    return True


def descargar_con_reintentos() -> bool:
    """El portal de la Aerocivil falla de forma intermitente; reintentamos."""
    for intento in range(1, INTENTOS + 1):
        try:
            log.info("Intento %d de %d", intento, INTENTOS)
            url, cookies = obtener_url_pdf()
            if descargar_pdf(url, cookies):
                return True
        except Exception as e:
            log.warning("Intento %d fallido: %s: %s", intento, type(e).__name__, e)

        if intento < INTENTOS:
            espera = ESPERA_BASE * intento
            log.info("Esperando %d s antes de reintentar...", espera)
            time.sleep(espera)

    log.error("Agotados los %d intentos de descarga.", INTENTOS)
    return False


# --------------------------------------------------------------------------
# Parseo del PDF
# --------------------------------------------------------------------------
def extraer_notams(ruta_pdf: Path) -> list[tuple[str, str, str]]:
    texto_completo = []
    with pdfplumber.open(ruta_pdf) as pdf:
        log.info("PDF con %d paginas", len(pdf.pages))
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                texto_completo.append(texto)

    texto_limpio = " ".join(texto_completo).replace("(cid:13)", " ")
    texto_limpio = re.sub(r"\s+", " ", texto_limpio).strip()

    notams = dividir_en_notams(texto_limpio)
    log.info("NOTAMs identificados: %d", len(notams))
    avisar_de_pegados(notams)
    return notams


def dividir_en_notams(texto_limpio: str) -> list:
    """
    Corta un texto corrido en NOTAMs. Se usa con el boletin completo y tambien
    al reparsear la base ya guardada, para que las dos rutas apliquen
    exactamente las mismas reglas.
    """
    patron_id = re.compile(r"([A-Z]\s*\d{4}\s*/\s*\d{2})")
    candidatos = []
    for match in patron_id.finditer(texto_limpio):
        # Solo los 15 caracteres justo antes: buscar en 80 hacia atras hacia
        # que "... RPLC NOTAM C1208/26 C 1919 / 26 ARMENIA (SKAR) ..."
        # descartara C1919/26, que es un NOTAM nuevo y no la referencia.
        texto_antes = texto_limpio[max(0, match.start() - 15):match.start()].upper()
        if PALABRAS_REFERENCIA.search(texto_antes):
            continue
        if not SEGUIDOR_ENCABEZADO.search(texto_limpio[match.end():match.end() + 180]):
            continue
        if candidatos and RELLENO_ENTRE_NUMERACIONES.match(
                texto_limpio[candidatos[-1].end():match.start()]):
            continue        # misma numeracion repetida, no un NOTAM nuevo
        candidatos.append(match)

    notams = []
    for i, actual in enumerate(candidatos):
        fin = candidatos[i + 1].start() if i + 1 < len(candidatos) else len(texto_limpio)
        contenido = texto_limpio[actual.start():fin].strip()
        notam_id = actual.group(1).replace(" ", "")

        # El orden importa. Buscar "SK[A-Z]{2}" suelto en cualquier parte del
        # texto asignaba a un aerodromo NOTAMs que eran del FIR, solo porque
        # el cuerpo mencionaba un aerodromo: 11 casos medidos sobre 453.
        # Manda el codigo entre parentesis, que es el sujeto del NOTAM.
        entre_parentesis = re.search(r"\((SK[A-Z]{2})\)", contenido)
        arriba = contenido[:120].upper()
        if "FIR/UIR BOGOTA" in arriba:
            icao = "SKED"
        elif "FIR/UIR BARRANQUILLA" in arriba:
            icao = "SKEC"
        elif entre_parentesis:
            icao = entre_parentesis.group(1).upper()
        elif "FIR/UIR BOGOTA" in contenido:
            icao = "SKED"
        elif "FIR/UIR BARRANQUILLA" in contenido:
            icao = "SKEC"
        else:
            suelto = re.search(r"SK[A-Z]{2}", contenido)
            icao = suelto.group(0).upper() if suelto else "OTROS"

        notams.append((icao, notam_id, contenido))

    return notams


def contar_pegados(notams: list) -> list:
    """[(id, cuantas cabeceras)] de los registros que traen mas de un NOTAM."""
    pegados = []
    for _, notam_id, contenido in notams:
        cabeceras = len(CABECERA_NOTAM.findall(contenido))
        if cabeceras > 1:
            pegados.append((notam_id, cabeceras))
    return pegados


def avisar_de_pegados(notams: list) -> list:
    """
    Vigilancia de las reglas de corte.

    Un NOTAM enterrado dentro de otro no rompe nada visible: el portal muestra
    453 registros y parece correcto, pero uno de ellos esconde el aviso de otro
    aerodromo, que asi no aparece en ninguna busqueda. Por eso se comprueba en
    cada extraccion y queda en el log y en /health, en vez de esperar a que
    alguien lo note leyendo.
    """
    pegados = contar_pegados(notams)
    if pegados:
        enterrados = sum(n - 1 for _, n in pegados)
        log.warning(
            "%d registro(s) contienen %d NOTAM(s) pegados: %s",
            len(pegados), enterrados,
            ", ".join(f"{i} ({n} cabeceras)" for i, n in pegados[:8]))
    else:
        log.info("Ningun registro con NOTAMs pegados.")
    return pegados


# --------------------------------------------------------------------------
# Flujo principal
# --------------------------------------------------------------------------
def reparsear_base() -> list:
    """
    Vuelve a cortar los NOTAMs ya guardados, sin descargar nada.

    Sirve cuando las reglas de corte mejoran: la base existente se hizo con
    las reglas viejas y, hasta la siguiente descarga, sigue mostrando los
    NOTAMs pegados. Como el texto pegado esta entero dentro del registro, se
    puede volver a cortar sin tocar la Aerocivil.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(f"No existe {DB_PATH}")

    conexion = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        filas = conexion.execute(
            "SELECT icao_code, notam_id, content FROM notams").fetchall()
    finally:
        conexion.close()

    salida, sin_cortar = [], 0
    for icao, notam_id, contenido in filas:
        trozos = dividir_en_notams(contenido)
        if trozos:
            salida.extend(trozos)
        else:
            # Si las reglas no reconocen ni el encabezado del propio registro,
            # se conserva tal cual: perder un NOTAM es peor que dejarlo pegado.
            salida.append((icao, notam_id, contenido))
            sin_cortar += 1

    log.info("Reparseo: %d registros -> %d NOTAMs (%d sin reconocer)",
             len(filas), len(salida), sin_cortar)
    avisar_de_pegados(salida)
    return salida


def main() -> int:
    parser = argparse.ArgumentParser(description="Extractor de NOTAMs de la Aerocivil")
    parser.add_argument("--force", action="store_true",
                        help="publica aunque no pase las validaciones de volumen")
    parser.add_argument("--reparsear", action="store_true",
                        help="no descarga: vuelve a cortar la base existente "
                             "con las reglas actuales")
    parser.add_argument("--revisar", action="store_true",
                        help="solo informa si la base tiene NOTAMs pegados")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configurar_logging(args.verbose)
    log.info("=" * 60)

    if args.revisar:
        conexion = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            filas = conexion.execute(
                "SELECT icao_code, notam_id, content FROM notams").fetchall()
        finally:
            conexion.close()
        pegados = avisar_de_pegados([tuple(f) for f in filas])
        print(f"{len(filas)} registros, {len(pegados)} con NOTAMs pegados "
              f"({sum(n - 1 for _, n in pegados)} enterrados)")
        return 1 if pegados else 0

    if args.reparsear:
        notams = reparsear_base()
        return publicar_notams(notams, forzar=True)

    log.info("Inicio de extraccion")

    if not descargar_con_reintentos():
        log.error(
            "Extraccion abortada. La base de datos NO se modifico "
            "(%d NOTAMs siguen disponibles).", contar_notams(DB_PATH),
        )
        return 1

    try:
        notams = extraer_notams(PDF_PATH)
    except Exception as e:
        log.exception("Fallo el parseo del PDF: %s", e)
        return 1
    finally:
        PDF_PATH.unlink(missing_ok=True)

    return publicar_notams(notams, forzar=args.force)


def publicar_notams(notams: list, forzar: bool = False) -> int:
    conexion = crear_base_temporal()
    try:
        conexion.executemany(
            "INSERT OR REPLACE INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
            notams,
        )
        conexion.execute(
            "INSERT OR REPLACE INTO metadatos (clave, valor) "
            "VALUES ('ultima_extraccion', datetime('now'))"
        )
        conexion.execute(
            "INSERT OR REPLACE INTO metadatos (clave, valor) VALUES ('total', ?)",
            (str(len(notams)),),
        )
        # Queda en la base para que /health lo publique: si un dia una variante
        # nueva del boletin vuelve a pegar dos NOTAMs, se ve sin abrir el log.
        conexion.execute(
            "INSERT OR REPLACE INTO metadatos (clave, valor) "
            "VALUES ('notams_pegados', ?)",
            (str(len(contar_pegados(notams))),),
        )
        conexion.commit()
        total = conexion.execute("SELECT COUNT(*) FROM notams").fetchone()[0]
    finally:
        conexion.close()

    if not publicar(total, forzar=forzar):
        DB_TMP_PATH.unlink(missing_ok=True)
        return 2

    log.info("Base publicada con %d NOTAMs.", total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
