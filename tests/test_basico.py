"""
Pruebas basicas del portal. No requieren red ni el servidor de la Aerocivil.

    python3 tests/test_basico.py
"""

import os
import re
import sys
import shutil
import sqlite3
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

FALLOS = []


def check(nombre, condicion, extra=""):
    marca = "OK  " if condicion else "FALLA"
    print(f"  [{marca}] {nombre}" + (f"  -> {extra}" if extra else ""))
    if not condicion:
        FALLOS.append(nombre)


# ---------------------------------------------------------------------------
# Extractor: la publicacion atomica nunca debe dejar la base vacia
# ---------------------------------------------------------------------------
def probar_extractor():
    print("\n== Extractor: publicacion atomica ==")
    sandbox = Path(tempfile.mkdtemp())
    shutil.copy(RAIZ / "extractor.py", sandbox / "extractor.py")
    cwd = os.getcwd()
    os.chdir(sandbox)
    sys.path.insert(0, str(sandbox))

    import extractor as ex
    ex.configurar_logging()

    def poblar(ruta, n, prefijo="A"):
        ruta = Path(ruta)
        if ruta.exists():
            ruta.unlink()
        c = sqlite3.connect(ruta)
        c.executescript(ex.ESQUEMA)
        c.executemany(
            "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
            [("SKBO", f"{prefijo}{i:04d}/26", "texto") for i in range(n)],
        )
        c.commit()
        c.close()

    poblar(ex.DB_PATH, 463, "A")
    poblar(ex.DB_TMP_PATH, 460, "B")
    check("extraccion valida se publica", ex.publicar(460))
    check("la base queda con 460", ex.contar_notams(ex.DB_PATH) == 460)

    poblar(ex.DB_TMP_PATH, 3, "C")
    check("extraccion minuscula se rechaza", not ex.publicar(3))
    check("la base anterior sobrevive", ex.contar_notams(ex.DB_PATH) == 460)

    poblar(ex.DB_TMP_PATH, 140, "D")
    check("caida del 70% se rechaza", not ex.publicar(140))
    check("la base sigue intacta", ex.contar_notams(ex.DB_PATH) == 460)

    poblar(ex.DB_TMP_PATH, 140, "D")
    check("--force omite las validaciones", ex.publicar(140, forzar=True))
    check("copia previa disponible para revertir",
          ex.contar_notams(ex.DB_PREV_PATH) == 460)

    os.chdir(cwd)
    sys.path.remove(str(sandbox))
    del sys.modules["extractor"]
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
def probar_api():
    print("\n== API ==")
    try:
        import google.generativeai  # noqa: F401
    except ImportError:
        stubs = Path(tempfile.mkdtemp())
        (stubs / "google" / "generativeai").mkdir(parents=True)
        (stubs / "google" / "__init__.py").write_text("")
        (stubs / "google" / "generativeai" / "__init__.py").write_text(
            "def configure(**k): pass\n"
            "class GenerativeModel:\n"
            "    def __init__(self,*a,**k): pass\n"
            "    def generate_content(self,*a,**k):\n"
            "        class R: text='simulado'\n"
            "        return R()\n"
        )
        sys.path.insert(0, str(stubs))
        # 'google' puede estar cacheado como paquete de espacio de nombres
        # apuntando a otra ruta; hay que soltarlo para que tome el stub.
        for modulo in [m for m in sys.modules if m == "google" or m.startswith("google.")]:
            del sys.modules[modulo]

    sandbox = Path(tempfile.mkdtemp())
    for f in ["api_notams.py", "index.html"]:
        shutil.copy(RAIZ / f, sandbox / f)
    cwd = os.getcwd()
    os.chdir(sandbox)
    sys.path.insert(0, str(sandbox))

    c = sqlite3.connect(sandbox / "sistema_notams.db")
    c.executescript(
        "CREATE TABLE notams (id INTEGER PRIMARY KEY AUTOINCREMENT, icao_code TEXT NOT NULL,"
        " notam_id TEXT UNIQUE NOT NULL, content TEXT,"
        " last_updated DATETIME DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE metadatos (clave TEXT PRIMARY KEY, valor TEXT);"
    )
    c.executemany(
        "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
        [("SKBO", "A0001/26", "RWY 13L/31R CLSD"),
         ("SKBO", "A0002/26", "TWY B CLSD"),
         ("SKRG", "A0003/26", "ILS RWY 01 U/S"),
         ("FIR", "A0004/26", "AREA PELIGROSA ACTIVA")],
    )
    c.execute("INSERT INTO metadatos VALUES ('ultima_extraccion', datetime('now'))")
    c.commit()
    c.close()

    import api_notams
    from fastapi.testclient import TestClient

    cli = TestClient(api_notams.app)

    j = cli.get("/health").json()
    check("/health reporta ok", j["estado"] == "ok", str(j))
    check("/health cuenta los NOTAMs", j["total_notams"] == 4)

    check("busqueda insensible a mayusculas",
          cli.get("/api/notams/skbo").json()["total"] == 2)
    check("aerodromo sin NOTAMs devuelve vacio",
          cli.get("/api/notams/SKXX").json()["total"] == 0)
    check("rechaza codigo OACI invalido",
          cli.get("/api/notams/A").status_code == 400)
    check("/api/notams_all conserva el formato original",
          set(cli.get("/api/notams_all").json()["datos"][0])
          == {"aerodromo", "id_notam", "texto"})
    check("/api/aerodromos agrupa por codigo",
          cli.get("/api/aerodromos").json()["datos"][0]
          == {"aerodromo": "SKBO", "total": 2})
    check("sirve index.html",
          cli.get("/").content[:15].startswith(b"<!DOCTYPE html>"))
    check("gzip activo",
          cli.get("/", headers={"Accept-Encoding": "gzip"})
          .headers.get("content-encoding") == "gzip")

    os.rename(sandbox / "sistema_notams.db", sandbox / "guardada.db")
    check("sin base de datos responde 503",
          cli.get("/api/notams_all").status_code == 503)
    check("/health reporta degradado sin base",
          cli.get("/health").json()["estado"] == "degradado")
    os.rename(sandbox / "guardada.db", sandbox / "sistema_notams.db")

    os.chdir(cwd)
    shutil.rmtree(sandbox, ignore_errors=True)


# ---------------------------------------------------------------------------
# Fondo en video: reemplazo del pase de siete fotos en base64
# ---------------------------------------------------------------------------
def probar_fondo_video():
    print("\n== Fondo en video ==")
    html = (RAIZ / "index.html").read_text(encoding="utf-8")

    check("index.html ya no lleva imagenes en base64",
          "data:image/jpeg;base64" not in html and "data:image/png;base64" not in html)
    check("index.html bajo de 200 KB",
          len(html.encode()) < 200_000, f"{len(html.encode())/1024:.0f} KB")

    for resto in ("bg-slide", "slide-dot", "slide-indicators", "goSlide", "--img1"):
        check(f"sin restos de '{resto}'", resto not in html)

    check("el video esta en el marcado", 'id="bgVideo"' in html)
    check("va silenciado, en bucle y sin pantalla completa en iOS",
          all(a in html for a in ("muted", "loop", "playsinline")))
    check("ofrece WebM antes que MP4",
          html.index("hero-nubes.webm") < html.index("hero-nubes.mp4"))
    check("respeta prefers-reduced-motion",
          "prefers-reduced-motion" in html and "bg-poster" in html)

    est = RAIZ / "static"
    for archivo, tope in (("hero-nubes.mp4", 900_000),
                          ("hero-nubes.webm", 900_000),
                          ("hero-nubes-poster.jpg", 200_000)):
        ruta = est / archivo
        check(f"existe static/{archivo}", ruta.is_file())
        if ruta.is_file():
            check(f"static/{archivo} pesa poco",
                  ruta.stat().st_size < tope, f"{ruta.stat().st_size/1024:.0f} KB")

    # Si la API no monta /static, el fondo se queda en negro.
    # Se carga el modulo REAL del repositorio y no el que quedo en sys.modules:
    # probar_api() importa una copia en un directorio temporal que no tiene
    # carpeta static, asi que reutilizarlo daria un 404 enganoso.
    import importlib.util
    from fastapi.testclient import TestClient

    spec = importlib.util.spec_from_file_location("api_real", RAIZ / "api_notams.py")
    api_real = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api_real)
    check("el modulo real apunta al repositorio", api_real.BASE_DIR == RAIZ,
          str(api_real.BASE_DIR))
    cli = TestClient(api_real.app)
    r = cli.get("/static/hero-nubes.mp4")
    check("la API sirve /static", r.status_code == 200, f"HTTP {r.status_code}")
    check("el MP4 se sirve con su tipo",
          r.headers.get("content-type", "").startswith("video/"),
          r.headers.get("content-type", ""))

    rz = cli.get("/static/hero-nubes.mp4", headers={"Accept-Encoding": "gzip"})
    check("el video NO se recomprime con gzip",
          rz.headers.get("content-encoding") is None,
          rz.headers.get("content-encoding", "sin comprimir"))
    check("el video se sirve con cache larga",
          "max-age" in rz.headers.get("cache-control", ""),
          rz.headers.get("cache-control", "(ninguna)"))
    check("el HTML si sigue comprimido",
          cli.get("/", headers={"Accept-Encoding": "gzip"})
          .headers.get("content-encoding") == "gzip")


def probar_sistema_visual():
    """
    Rediseno con las referencias alethia.earth y fuselabcreative.

    No comprueba "que se vea bien" -eso no se puede automatizar- sino los
    valores concretos que definen cada gesto, para que un retoque futuro no
    los deshaga sin querer.
    """
    print("\n== Sistema visual ==")
    html = (RAIZ / "index.html").read_text(encoding="utf-8")

    # -- Tipografia --
    check("carga Geist y Fragment Mono",
          "family=Geist" in html and "family=Fragment+Mono" in html)
    check("no queda nada de Archivo ni DM Mono",
          "family=Archivo" not in html and "DM+Mono" not in html)
    check("sin ejes de ancho variable (Geist no tiene wdth)",
          "font-variation-settings" not in html)

    # -- Titulo, con los valores medidos en el hero de alethia.earth --
    import re
    bloque = re.search(r"\.hero-title \{(.*?)\}", html, re.S)
    check("existe la regla del titulo", bloque is not None)
    if bloque:
        t = bloque.group(1)
        check("titulo en peso medio, no negro", "font-weight: 500" in t)
        check("titulo con tracking cerrado", "letter-spacing: -0.05em" in t)
        check("titulo con interlineado 0.95", "line-height: 0.95" in t)
        check("titulo sin mayusculas forzadas", "text-transform" not in t)
        check("titulo sin inclinacion", "skew" not in t)

    # -- Franja de telemetria --
    check("la franja separa etiqueta y valor",
          'class="kpi-lbl"' in html and 'class="kpi-val"' in html)
    check("la franja lleva fondo propio para leerse sobre el video",
          ".hero-kpis {" in html and "backdrop-filter" in
          html[html.index(".hero-kpis {"):html.index(".hero-kpis {") + 400])
    check("el reloj ya no repite la palabra UTC en el valor",
          "`${hm}Z`" in html)

    # -- Encabezados de seccion --
    check("encabezado en mayusculas espaciadas",
          ".section-title {" in html and
          "letter-spacing: 0.13em" in html[html.index(".section-title {"):
                                           html.index(".section-title {") + 300])

    # -- Boton contorneado --
    check("el boton lleva el cuadro de acento con flecha",
          "btn-flecha" in html)
    check("se quito el barrido de luz del boton",
          "translateX(-120%)" not in html.split(".btn-notam")[0])

    # -- Pestanas --
    check("sin corchetes en las pestanas",
          "content: '['" not in html)

    # -- Cifras --
    check("las cifras usan la tipografia de titulares",
          ".geo-metric-val {" in html and
          "var(--font-ui)" in html[html.index(".geo-metric-val {"):
                                   html.index(".geo-metric-val {") + 200])

    # -- Tokens de color: el tema claro debe poder cambiarlos todos --
    for token in ("--text-nav", "--text-3", "--text-4", "--accent-hi"):
        en_raiz = f"{token}:" in html.split("body.light")[0]
        en_claro = f"{token}:" in html.split("body.light {")[1][:900]
        check(f"{token} definido en ambos temas", en_raiz and en_claro)
    check("ningun color de texto escrito a pelo en las pestanas",
          "#98A1AE; border: none" not in html)


def probar_analisis_zona():
    """
    El panel de Coordenadas llamaba a api.anthropic.com desde el navegador
    del visitante y sin credencial: no fallaba a ratos, no podia funcionar.
    Ahora pasa por el backend. Se comprueba tanto que funcione como que se
    rinda a tiempo, que era el otro fallo: sin plazo, una peticion sin
    respuesta dejaba la conexion colgada mas de dos minutos.
    """
    print("\n== Analisis de zona ==")
    html = (RAIZ / "index.html").read_text(encoding="utf-8")

    check("el navegador ya no llama a Anthropic",
          "fetch('https://api.anthropic.com" not in html
          and 'fetch("https://api.anthropic.com' not in html)
    check("llama al endpoint propio", "/api/analizar_zona" in html)

    import importlib.util
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("api_zona", RAIZ / "api_notams.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)

    # --- Gemini simulado: responde ---
    class ModeloOk:
        def __init__(self, *a, **k): pass
        def generate_content(self, prompt, **k):
            class R: text = "UBICACION: Colombia, Atlantico, Barranquilla."
            ModeloOk.ultimo_prompt = prompt
            return R()

    original = api.genai.GenerativeModel
    api.genai.GenerativeModel = ModeloOk
    cli = TestClient(api.app)
    try:
        r = cli.get("/api/analizar_zona",
                    params={"coords": "10.9685,-74.7813\n10.8,-74.6", "tipo": "line"})
        check("devuelve 200 con el analisis", r.status_code == 200, f"HTTP {r.status_code}")
        j = r.json()
        check("el texto llega al cliente", "Barranquilla" in j.get("analisis", ""))
        check("cuenta bien los puntos", j["puntos"] == 2, str(j.get("puntos")))
        check("calcula el centro", abs(j["centro"]["lat"] - 10.88425) < 1e-4)

        # Al modelo se le mandan numeros ya parseados, no el texto crudo
        # que el usuario haya pegado en el area de texto.
        check("el prompt lleva las coordenadas normalizadas",
              "10.968500, -74.781300" in ModeloOk.ultimo_prompt)

        r = cli.get("/api/analizar_zona", params={"coords": "esto no son coordenadas"})
        check("rechaza texto sin coordenadas", r.status_code == 400, f"HTTP {r.status_code}")
        r = cli.get("/api/analizar_zona", params={"coords": "200,-74"})
        check("rechaza latitud imposible", r.status_code == 400, f"HTTP {r.status_code}")

        muchas = "\n".join(f"{4+i*0.01:.4f},{-74-i*0.01:.4f}" for i in range(100))
        j = cli.get("/api/analizar_zona", params={"coords": muchas}).json()
        check("recorta a 60 puntos", j["puntos"] == 60, str(j["puntos"]))
    finally:
        api.genai.GenerativeModel = original

    # --- Gemini simulado: se cuelga ---
    import time
    class ModeloColgado:
        def __init__(self, *a, **k): pass
        def generate_content(self, prompt, **k):
            time.sleep(30)
    api.IA_TIMEOUT = 2
    api.genai.GenerativeModel = ModeloColgado
    try:
        t0 = time.time()
        r = cli.get("/api/analizar_zona", params={"coords": "10.9,-74.7"})
        tardo = time.time() - t0
        check("no se cuelga si Gemini no responde", tardo < 8, f"{tardo:.1f}s")
        check("responde 200 con un mensaje legible",
              r.status_code == 200 and "no esta disponible" in r.json()["analisis"])
        check("las mediciones siguen disponibles", r.json()["puntos"] == 1)

        t0 = time.time()
        r = cli.get("/api/traducir", params={"texto": "SKBQ 172200Z 04007KT"})
        check("/api/traducir tampoco se cuelga", time.time() - t0 < 8)
    finally:
        api.genai.GenerativeModel = original


def probar_cierres():
    """
    Seccion de Cierres y Restricciones.

    Las reglas se escribieron mirando los 453 NOTAMs reales del servidor.
    Estas pruebas fijan el comportamiento con casos tomados de ahi, para que
    un retoque futuro no deje de reconocer un cierre en silencio.
    """
    print("\n== Cierres y restricciones ==")
    sys.path.insert(0, str(RAIZ))
    import cierres

    # --- Clasificacion, con textos reales ---
    casos = [
        ("SKPP", "AD CLSD 260818 1837 9",                     "ad_cerrado",  "cierre"),
        ("SKBG", "RWY 17/35 CLSD",                            "pista",       "cierre"),
        ("SKPB", "AD LTD, AVBL ACFT HASTA CAT B",              "ad_limitado", "limitacion"),
        ("SKBG", "TWY A BTN TWY C Y TWY A2 CLSD",              "rodaje",      "cierre"),
        ("SKEC", "VOR CJN 113.40 MHZ U/S RPLC NOTAM",          "navegacion",  "limitacion"),
        ("SKED", "SSR ETB U/S RPLC NOTAM",                     "navegacion",  "limitacion"),
        ("SKMD", "REIL 20 U/S RPLC NOTAM",                     "iluminacion", "limitacion"),
        ("SKED", "UA SE REALIZARA NXT COORD: 0220N0763W",      "espacio",     "limitacion"),
        ("SKBS", "TWY B, BACHE EXER CTN RPLC NOTAM",           "pavimento",   "limitacion"),
        ("SKCG", "OBST ERIGIDO, EXER CTN TIPO: GRUA",          "obstaculo",   "limitacion"),
        ("SKAS", "RWY 01/19 LTD. BACHE FST 750 MTS THR 19",    "pista",       "limitacion"),
    ]
    for icao, texto, cat_esp, sev_esp in casos:
        r = cierres.clasificar(icao, texto)
        ok = r is not None and r[0] == cat_esp and r[2] == sev_esp
        check(f"clasifica '{texto[:34]}'", ok,
              "" if ok else f"dio {r[0] if r else None}/{r[2] if r else None}, se esperaba {cat_esp}/{sev_esp}")

    # La frecuencia lleva punto y el punto separaba equipo de estado: por eso
    # antes las radioayudas caian todas al cajon de "otras".
    r = cierres.clasificar("SKED", "DVOR/DME ABL 112.700 MHZ CH74X U/S RPLC NOTAM")
    check("el punto de la frecuencia no rompe la deteccion",
          r is not None and r[0] == "navegacion", str(r))

    # Ruido administrativo que menciona de todo y no restringe nada.
    check("descarta la lista de verificacion",
          cierres.clasificar("SKED", "LISTA DE VERIFICACION YEAR=2026 NIL RWY CLSD") is None)
    check("descarta el trigger NOTAM",
          cierres.clasificar("SKED", "TRIGGER NOTAM - AIRAC AIP AMDT") is None)

    # Cambios de carta: no son restricciones operativas.
    for texto in ("STAR ISNO1F AVBL RWY 20/02 REF: SKAR AD 2.24",
                  "DIST DECLARADAS RWY 02/20 MODIFICADAS: TORA 1860"):
        check(f"no confunde con restriccion: '{texto[:30]}'",
              cierres.clasificar("SKAR", texto) is None)

    check("reconoce los dos FIR colombianos",
          set(cierres.FIRS) == {"SKED", "SKEC"}, str(sorted(cierres.FIRS)))

    # --- Lectura del NOTAM crudo ---
    crudo = ("C3308/24 260818 1837 3 C 1297 / 25 ARMENIA/EL EDEN (SKAR) "
             "2504062143 / PERM , IAC ILS Z CAT 1 RWY 20 U/S")
    check("extrae el nombre sin arrastrar la cabecera",
          cierres.nombre_aerodromo(crudo) == "ARMENIA/EL EDEN",
          repr(cierres.nombre_aerodromo(crudo)))
    check("la vigencia es la del encabezado, con PERM",
          cierres.vigencia(crudo) == ("2504062143", "PERM"), str(cierres.vigencia(crudo)))
    check("el resumen es el cuerpo operativo",
          cierres.resumen_operativo(crudo).startswith("IAC ILS"),
          cierres.resumen_operativo(crudo)[:40])
    check("nombra los FIR, que no llevan (ICAO)",
          cierres.nombre_aerodromo("C 2030 / 26 FIR/UIR BARRANQUILLA 2605 / 2608 , VOR U/S")
          == "FIR/UIR BARRANQUILLA")

    # --- Endpoint ---
    import importlib.util
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("api_cie", RAIZ / "api_notams.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)

    import tempfile as _tmp, sqlite3 as _sq
    caja = Path(_tmp.mkdtemp())
    bd = caja / "cierres.db"
    con = _sq.connect(bd)
    con.executescript(
        "CREATE TABLE notams (id INTEGER PRIMARY KEY AUTOINCREMENT, icao_code TEXT,"
        " notam_id TEXT UNIQUE, content TEXT, last_updated DATETIME);"
        "CREATE TABLE metadatos (clave TEXT PRIMARY KEY, valor TEXT);")
    con.executemany("INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)", [
        ("SKPP", "D0696/26", "D 0696 / 26 POPAYAN/GUILLERMO (SKPP) 2608181537 / 2608182359 , AD CLSD"),
        ("SKBG", "D0551/26", "D 0551 / 26 BUCARAMANGA (SKBG) 2608230000 / 2610251500 , RWY 17/35 CLSD"),
        ("SKED", "C2148/26", "C 2248 / 26 FIR/UIR BOGOTA 2606041148 / 2609012359 , DVOR/DME ABL 112.700 MHZ U/S"),
        ("SKEC", "C2030/26", "C 2030 / 26 FIR/UIR BARRANQUILLA 2605221257 / 2608192359 , VOR CJN 113.40 MHZ U/S"),
        ("SKAR", "C1297/25", "C 1297 / 25 ARMENIA/EL EDEN (SKAR) 2504062143 / PERM , STAR ISNO1F AVBL RWY 20"),
    ])
    con.commit(); con.close()
    api.DB_PATH = bd
    cli = TestClient(api.app)

    j = cli.get("/api/cierres").json()
    check("el endpoint responde", j["total"] == 4, f"total={j['total']} (el STAR no cuenta)")
    check("cuenta los cierres aparte", j["resumen"]["cierres"] == 2, str(j["resumen"]))
    check("cuenta los NOTAMs de FIR", j["resumen"]["notams_fir"] == 2, str(j["resumen"]))
    check("no cuenta los FIR como aerodromos afectados",
          j["resumen"]["aerodromos_afectados"] == 2, str(j["resumen"]))
    check("el aerodromo cerrado va primero",
          j["grupos"][0]["severidad"] == "cierre", j["grupos"][0]["etiqueta"])
    claves = [g["clave"] for g in j["grupos"]]
    check("cierres y limitaciones no se mezclan en un grupo",
          all("_" in k for k in claves), str(claves))

    j = cli.get("/api/cierres", params={"severidad": "cierre"}).json()
    check("el filtro de solo cierres funciona", j["total"] == 2, f"total={j['total']}")
    j = cli.get("/api/cierres", params={"ambito": "fir"}).json()
    check("el filtro de solo FIR devuelve SKED y SKEC",
          j["total"] == 2 and all(n["es_fir"] for g in j["grupos"] for n in g["notams"]),
          f"total={j['total']}")
    j = cli.get("/api/cierres", params={"ambito": "aerodromos"}).json()
    check("el filtro de solo aerodromos excluye los FIR", j["total"] == 2, f"total={j['total']}")
    check("rechaza un ambito inventado",
          cli.get("/api/cierres", params={"ambito": "marte"}).status_code == 422)

    shutil.rmtree(caja, ignore_errors=True)

    # --- Frontend ---
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    check("la pestana existe", 'id="btn-cierres"' in html and 'id="tab-cierres"' in html)
    check("consume el endpoint", "/api/cierres" in html)
    check("los cuatro filtros estan", all(f'data-f="{f}"' in html
          for f in ("todos", "cierre", "aerodromos", "fir")))


def probar_directorio_ad():
    """
    Directorio de aerodromos.

    Cuatro codigos OACI estaban mal, todos por letras cambiadas de sitio. No
    era cosmetico: la alerta de cierre se busca por codigo, asi que Popayan
    podia estar cerrado y la tabla no decia nada. Estas pruebas fijan los
    codigos correctos para que no vuelvan a colarse.
    """
    print("\n== Directorio de aerodromos ==")
    import re as _re
    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    m = _re.search(r"const ads = \[(.*?)\n\];", html, _re.S)
    check("el directorio existe", m is not None)
    if not m:
        return
    filas = _re.findall(r'icao:"([A-Z]{4})",nombre:"([^"]*)"\s*,ciudad:"([^"]*)"\s*,horario:"([^"]*)"',
                        m.group(1))
    codigos = [f[0] for f in filas]

    check("tiene los aerodromos de los NOTAMs", len(filas) >= 60, f"{len(filas)} filas")
    check("no hay codigos repetidos", len(codigos) == len(set(codigos)),
          f"{len(codigos)} filas, {len(set(codigos))} codigos")

    # Codigos verificados uno a uno contra fuentes externas.
    correctos = {
        "SKPP": "Popayán",    # antes SKPV, que no existe
        "SKUC": "Arauca",     # antes SKGI, que es Flandes
        "SKIP": "Ipiales",    # antes SKPI, que es Pitalito
        "SKLC": "Carepa",     # antes SKAP, que es la base de Apiay
    }
    porCodigo = {f[0]: f for f in filas}
    for icao, ciudad in correctos.items():
        f = porCodigo.get(icao)
        check(f"{icao} existe y es {ciudad}",
              f is not None and ciudad.lower() in f[2].lower(),
              f[2] if f else "no esta en la tabla")

    # SKPV no existe como aerodromo: tenia que desaparecer.
    check("SKPV ya no aparece (era Popayan mal escrito)", "SKPV" not in porCodigo)

    # SKGI, SKAP y SKPI si existen, pero son otros aerodromos: lo que no
    # pueden es seguir con la etiqueta equivocada.
    for icao, noEs in (("SKGI", "arauca"), ("SKAP", "carepa"), ("SKPI", "ipiales")):
        f = porCodigo.get(icao)
        if f:
            check(f"{icao} ya no se etiqueta como {noEs}",
                  noEs not in f[2].lower(), f[2])

    check("el horario desconocido va como raya y no inventado",
          all(f[3] for f in filas), "hay horarios vacios")

    # El aviso de cierre tiene que llevar el texto del NOTAM, no solo el numero.
    check("el aviso muestra el contenido del NOTAM", "ad-tip-txt" in html)
    check("y tambien la vigencia", "adVigencia" in html)
    check("el globo cuelga de body y no de la tarjeta",
          "document.body.appendChild" in html and "adTipGlobal" in html)
    check("el globo de cada fila no se pinta", ".ad-tip { display: none; }" in html)


def probar_parseo():
    """
    Parseo del boletin. Los casos vienen de NOTAMs reales del servidor donde
    el extractor fallaba: fusionaba dos NOTAMs en un registro y asignaba al
    aerodromo equivocado los del FIR.
    """
    print("\n== Parseo del boletin ==")
    sandbox = Path(tempfile.mkdtemp())
    shutil.copy(RAIZ / "extractor.py", sandbox / "extractor.py")
    cwd = os.getcwd(); os.chdir(sandbox); sys.path.insert(0, str(sandbox))
    import extractor as ex

    def separar(texto):
        pid = re.compile(r"([A-Z]\s*\d{4}\s*/\s*\d{2})")
        cand = []
        for m in pid.finditer(texto):
            if ex.PALABRAS_REFERENCIA.search(texto[max(0, m.start()-15):m.start()].upper()):
                continue
            if not ex.SEGUIDOR_ENCABEZADO.search(texto[m.end():m.end()+180]):
                continue
            if cand and ex.RELLENO_ENTRE_NUMERACIONES.match(texto[cand[-1].end():m.start()]):
                continue
            cand.append(m)
        piezas = []
        for k, a in enumerate(cand):
            fin = cand[k+1].start() if k+1 < len(cand) else len(texto)
            piezas.append(texto[a.start():fin].strip())
        return piezas

    # Dos NOTAMs pegados: el segundo va detras de "RPLC NOTAM C1208/26", y
    # buscar palabras de referencia 80 caracteres atras lo daba por
    # referencia en vez de por encabezado nuevo.
    dos = ("C 1241 / 26 FIR/UIR BOGOTA 2603252042 / PERM , RTE ATS A301/W25 MODIFICA "
           "REF AIP ENR 3.1 RPLC NOTAM C1208/26 "
           "C 1919 / 26 ARMENIA/EL EDEN (SKAR) 2605131538 / PERM , CTR CAMBIA")
    check("separa dos NOTAMs pegados", len(separar(dos)) == 2, f"{len(separar(dos))} piezas")

    # La misma numeracion repetida NO es un NOTAM nuevo, ni con sello en medio.
    uno = ("C3308/24 260818 1837 3 C 1297 / 25 ARMENIA/EL EDEN (SKAR) "
           "2504062143 / PERM , IAC ILS Z CAT 1 RWY 20 U/S")
    check("no parte la numeracion repetida", len(separar(uno)) == 1, f"{len(separar(uno))} piezas")

    # El nombre del aerodromo lleva coma: "BOGOTA, D.C./BOGOTA - EL DORADO".
    coma = ("C 5572 / 25 BOGOTÁ, D.C./BOGOTA - EL DORADO LUIS CARLOS (SKBO) "
            "2601220000 / PERM , STAR PBN SKBO SE MODIFICA")
    check("reconoce nombres con coma", len(separar(coma)) == 1, f"{len(separar(coma))} piezas")

    # "NR" sin limite de palabra coincidia dentro de "ENR 6.1" y de "ACFT NR 19".
    for texto in ("REF AIP ENR 3.3, ENR 4.4, ENR 6.1 "
                  "C 3237 / 26 SAN ANDRÉS/SAN ANDRES (SKSP) 2608062258 / PERM , X",
                  "PSN PRKG ACFT NR 19 CLSD "
                  "C 3139 / 26 BOGOTÁ, D.C./BOGOTA (SKBO) 2608031300 / 2608252359 , Y"):
        check(f"'{texto[:22]}...' no bloquea el encabezado siguiente",
              len(separar(texto)) == 1, f"{len(separar(texto))} piezas")

    os.chdir(cwd); sys.path.remove(str(sandbox))
    del sys.modules["extractor"]
    shutil.rmtree(sandbox, ignore_errors=True)

    # --- Asignacion del codigo OACI ---
    fuente = (RAIZ / "extractor.py").read_text(encoding="utf-8")
    check("el codigo entre parentesis manda sobre el suelto",
          'entre_parentesis = re.search(r"\\((SK[A-Z]{2})\\)", contenido)' in fuente)
    check("el FIR se decide por la cabecera y no por el cuerpo",
          'arriba = contenido[:120].upper()' in fuente)


def probar_escape_html():
    """
    Seis NOTAMs reales llevan comillas dobles -CENTRO ADM. "MARANDUA"-. El
    texto viajaba dentro de onclick="consultarIA('...')", asi que el atributo
    se cerraba antes de tiempo: el boton mandaba medio NOTAM a la IA.
    """
    print("\n== Escape de HTML ==")
    html = (RAIZ / "index.html").read_text(encoding="utf-8")

    check("existe un escape unico", "function esc(t)" in html)
    check("los datos ya no viajan dentro de onclick",
          "consultarIA('${clean}')" not in html and 'consultarIAReg' in html)
    check("hay un registro para pasar el texto por clave",
          "regGuardar" in html and "_regTextos" in html)

    for patron in ("${n.texto}", "${data.raw}", "${n.id_notam}", "${n.aerodromo}",
                   "${metStr}", "${tafStr}"):
        check(f"'{patron}' ya no se inserta sin escapar", patron not in html)

    check("la respuesta de la IA se escapa", "esc(data.explicacion)" in html)
    check("el indicador OACI se sanea en el origen", "function leerIcaos()" in html)
    check("y se codifica al ir en una URL", "encodeURIComponent(icao)" in html)


def probar_marca_version():
    """
    Python no relee un modulo ya cargado. Al anadir /api/cierres el servidor
    en marcha siguio sirviendo la version anterior y el lanzador no lo
    detecto, porque comprobaba una ruta concreta que ya existia.
    """
    print("\n== Marca de version del codigo ==")
    import importlib.util
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("api_ver", RAIZ / "api_notams.py")
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    cli = TestClient(api.app)

    j = cli.get("/health").json()
    check("/health informa si el codigo esta al dia", "codigo_actualizado" in j, str(j)[:90])
    check("recien cargado, esta al dia", j.get("codigo_actualizado") is True)

    # Se simula un despliegue con el servidor en marcha.
    api.FIRMA_AL_ARRANCAR = api._firma_codigo() - 10
    j = cli.get("/health").json()
    check("detecta que el codigo del disco es mas nuevo",
          j.get("codigo_actualizado") is False and j["estado"] == "degradado", str(j)[:110])

    bat = RAIZ.parent / "PORTAL.bat"
    if bat.is_file():
        t = bat.read_text(encoding="utf-8-sig", errors="replace")
        check("el lanzador comprueba la marca y no una ruta concreta",
              "codigo_actualizado" in t)
        check("findstr sin contrabarras, que cmd no interpreta",
              '\\"codigo_actualizado' not in t)



# ---------------------------------------------------------------------------
# Buscador de Reglamentos Aeronauticos (RAC)
# ---------------------------------------------------------------------------
def probar_rac():
    print("\n== Reglamentos: segmentacion del PDF ==")
    sys.path.insert(0, str(RAIZ / "tests"))
    import pdfplumber
    import fixture_rac
    from rac_segmentar import segmentar, patron_numeral

    carpeta = Path(tempfile.mkdtemp())
    pdf = fixture_rac.crear(carpeta / "RAC_91.pdf")
    with pdfplumber.open(pdf) as doc:
        paginas = [(pg.extract_text() or "") for pg in doc.pages]
    apartados = segmentar(paginas, "91")
    por_numeral = {a["numeral"]: a for a in apartados}

    check("reconoce los numerales del articulado",
          {"91.001", "91.005", "91.310", "91.315", "91.1000", "91.2001"}
          <= set(por_numeral))
    check("conserva los capitulos y apendices",
          {"CAPITULO A", "CAPITULO B", "APENDICE 1"} <= set(por_numeral))

    # La tabla de contenido repite los mismos titulos. Si se cuela, aparece un
    # apartado con el titulo correcto y el cuerpo vacio, que es el peor
    # resultado posible: parece la norma y no dice nada.
    vacios = [a["numeral"] for a in apartados
              if a["numeral"].startswith("91.") and not a["texto"].strip()]
    check("ningun numeral queda sin cuerpo (indice descartado)",
          not vacios, f"vacios: {vacios}")

    # "1.500 m" y "0.5 NM" viven dentro del texto de 91.001.
    check("una cifra decimal no se confunde con un numeral",
          "1.500 m" in por_numeral["91.001"]["texto"]
          and not any(a["numeral"].startswith("1.5") for a in apartados))

    check("el titulo que cae en la linea siguiente se recupera",
          por_numeral["91.310"]["titulo"].startswith("Plan de vuelo"))
    check("el cuerpo del numeral es el suyo, no el del vecino",
          "sesenta" in por_numeral["91.310"]["texto"]
          and "combustible" not in por_numeral["91.310"]["texto"].lower())

    # El pie cambia en cada pagina ("RAC 91 91-2"), asi que solo lo detecta la
    # comparacion por forma.
    check("el encabezado y el pie no entran en el texto",
          not any("Unidad Administrativa" in a["texto"] for a in apartados)
          and not any(re.search(r"RAC 91 91-\d", a["texto"]) for a in apartados))

    check("el orden es el del documento, no el alfabetico",
          [a["numeral"] for a in apartados].index("91.315")
          < [a["numeral"] for a in apartados].index("91.1000"))

    check("el numeral exige el numero del reglamento",
          patron_numeral("91").match("91.310 Plan") is not None
          and patron_numeral("91").match("215.500 Otro") is None)

    print("\n== Reglamentos: indice y busqueda ==")
    base = carpeta / "rac.db"
    os.environ["RAC_PDF_DIR"] = str(carpeta)
    os.environ["RAC_DB"] = str(base)
    os.environ["RAC_APARTADOS_MINIMOS"] = "5"
    for modulo in ("rac_indexar", "rac"):
        sys.modules.pop(modulo, None)
    import rac_indexar
    import rac_catalogo

    resumen = rac_indexar.construir(
        [r for r in rac_catalogo.CATALOGO if r.rac == "91"], solo_indexar=True)
    check("el indexador construye la base", base.exists(),
          f"{resumen['apartados']} apartados")

    import rac
    # "APENDICE 1 FORMULARIO DE PLAN DE VUELO" coincide palabra por palabra con
    # esta busqueda y no tiene cuerpo: si apareciera, se llevaria el primer
    # puesto para mostrar un apartado vacio. (No se cuenta apartados_fts: en
    # una tabla FTS5 de contenido externo, COUNT(*) recorre la tabla de
    # contenido y devuelve el total de apartados, no el de entradas indexadas.)
    encabezados = [x["numeral"] for x in rac.buscar("plan de vuelo")["resultados"]
                   if x["numeral"].startswith(("CAPITULO", "APENDICE"))]
    check("los encabezados vacios no compiten en la busqueda",
          not encabezados, f"colados: {encabezados}")

    r = rac.buscar("plan de vuelo")
    check("busca por palabra clave", r["total"] >= 1
          and r["resultados"][0]["numeral"] == "91.310")
    check("cada resultado dice de que version salio",
          r["resultados"][0]["fecha_version"] == "30/07/2026")

    check("una sigla encuentra el termino del reglamento (FPL)",
          rac.buscar("FPL")["resultados"][0]["numeral"] == "91.310")
    check("y una palabra corriente tambien (gasolina)",
          rac.buscar("gasolina")["resultados"][0]["numeral"] == "91.1000")
    check("las tildes no cambian el resultado",
          rac.buscar("aerodromo alternativa")["total"]
          == rac.buscar("aeródromo alternativa")["total"])
    check("un numeral suelto va directo a su apartado",
          rac.buscar("91.310")["modo"] == "numeral")
    check("una frase entre comillas se busca literal",
          rac.buscar('"casilla 18"')["resultados"][0]["numeral"] == "91.2001")
    check("sin coincidencias exactas, reintenta con alguno de los terminos",
          rac.buscar("cuanto tiempo antes se presenta el plan")["modo"]
          == "alguno_de_los_terminos")

    # FTS5 aborta la consulta ante sintaxis suya sin cerrar. Sin sanear, esto
    # no da cero resultados: da un error 500.
    for veneno in ['combustible AND "', 'NEAR(a b', '*', '""', 'x OR OR y',
                   "vuelo)('"]:
        try:
            rac.buscar(veneno)
            ok = True
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"       {veneno!r} -> {e}")
        check(f"la consulta {veneno!r} no rompe la busqueda", ok)

    check("el contexto para la IA rotula cada apartado con su numeral",
          "[RAC 91 — 91.310" in rac.contexto_para_ia(r["resultados"]))
    check("y el prompt obliga a citar y a no inventar",
          "(RAC 91.310)" in rac.PROMPT and "No completes con conocimiento" in rac.PROMPT)

    print("\n== Reglamentos: API y portal ==")
    for modulo in ("api_notams",):
        sys.modules.pop(modulo, None)
    from fastapi.testclient import TestClient
    import api_notams
    cliente = TestClient(api_notams.app)

    check("/api/rac/documentos lista los reglamentos con su fecha",
          cliente.get("/api/rac/documentos").json()["documentos"][0]
          ["fecha_version"] == "30/07/2026")
    check("/api/rac/buscar responde 200",
          cliente.get("/api/rac/buscar", params={"q": "plan de vuelo"}).status_code == 200)
    check("un numero de RAC no valido se rechaza",
          cliente.get("/api/rac/buscar",
                      params={"q": "plan", "rac": "a/b"}).status_code in (400, 422))
    check("un numeral inexistente da 404",
          cliente.get("/api/rac/apartado/91/99.999").status_code == 404)

    html = (RAIZ / "index.html").read_text(encoding="utf-8")
    check("la pestana Reglamentos existe en el portal",
          "switchTab('rac')" in html and 'id="tab-rac"' in html)
    check("el texto del reglamento se escapa antes de pintarlo",
          "esc(r.texto" in html and "+ r.texto +" not in html)
    check("el resaltado se aplica despues de escapar",
          "esc(txt == null" in html
          and html.index("esc(txt == null") < html.index("'<mark>'"))
    check("el texto literal se muestra siempre, no solo el resumen de la IA",
          "rac-completo" in html and "rac-ia-pie" in html)

    for var in ("RAC_PDF_DIR", "RAC_DB", "RAC_APARTADOS_MINIMOS"):
        os.environ.pop(var, None)



# ---------------------------------------------------------------------------
# Los cuatro fallos que solo aparecieron con los RAC de verdad
# ---------------------------------------------------------------------------
def probar_rac_casos_reales():
    print("\n== Reglamentos: casos vistos en los PDF oficiales ==")
    from rac_segmentar import (segmentar, encabezado_de_seccion, LINEA_INDICE,
                               patron_numeral, trocear, partir_glosario)

    # 1) RAC 215, pagina 170: el final de una frase que empieza en minuscula.
    #    Creaba un "CAPITULO E" falso que se llevaba 4.709 caracteres del
    #    numeral anterior.
    check("una frase que empieza en 'capitulo' no es un encabezado",
          encabezado_de_seccion("capítulo E)") is None)
    check("ni aunque venga en mayuscula si sigue la frase",
          encabezado_de_seccion(
              "CAPÍTULO 11, 11.2.1.2, salvo que el ATS prescriba lo contrario.")
          is None)
    check("el encabezado de verdad si se reconoce",
          encabezado_de_seccion("CAPÍTULO E") is not None
          and encabezado_de_seccion("APÉNDICE 7 Formato OACI plan de vuelo") is not None)

    # 2) RAC 14: 28 renglones del indice se colaron como apartados con cuerpo
    #    porque el numero de pagina no quedaba al final de la linea.
    renglon = ("14.2.1. Ámbito de aplicación ......................................"
               "..........................................")
    check("un renglon de indice se reconoce por los puntos guia",
          LINEA_INDICE.search(renglon) is not None)
    check("aunque el numero de pagina no este al final",
          LINEA_INDICE.search("14.1. DEFINICIONES ........................") is not None)

    # 3) RAC 1: "1.944" es un ano escrito con separador de miles.
    check("en un RAC de un digito, 1.944 no es un numeral",
          patron_numeral("1").match(
              "1.944 Sobre Aviación Civil Internacional y sus anexos técnicos.")
          is None)
    check("pero 1.2.1 si lo es",
          patron_numeral("1").match("1.2.1. Definiciones.") is not None)
    check("y en el RAC 91, 91.1000 sigue siendo valido",
          patron_numeral("91").match("91.1000 Combustible y aceite") is not None)

    # 4) Apartados enormes: el APENDICE 7 del RAC 215 son 33.000 caracteres de
    #    una pieza. Sin trocear, la busqueda devuelve un bloque ilegible.
    largo = {"numeral": "APENDICE 7", "titulo": "Formato OACI plan de vuelo",
             "texto": "\n".join(f"renglon numero {i} del formato" for i in range(600)),
             "pagina": 150, "capitulo": "", "orden": 0}
    trozos = trocear([largo], tope=6000)
    check("un apartado enorme se parte en trozos manejables",
          len(trozos) > 1 and all(len(t["texto"]) <= 6200 for t in trozos),
          f"{len(trozos)} trozos")
    check("y cada trozo dice de que parte viene",
          trozos[0]["titulo"].endswith(f"(parte 1 de {len(trozos)})"))
    check("sin perder texto por el camino",
          sum(len(t["texto"]) for t in trozos) >= len(largo["texto"]) - len(trozos))

    # 5) Glosario: un apartado con 855 definiciones dentro no sirve para
    #    buscar. Se parte por termino.
    glosario = {"numeral": "1.2.1", "titulo": "Definiciones", "pagina": 11,
                "capitulo": "", "orden": 0, "texto": "\n".join(
                    [f"Termino numero {i}: definicion suficientemente larga del "
                     f"concepto numero {i}." for i in range(60)]
                    + ["Nota: esta aclaracion pertenece al termino anterior y no "
                       "abre uno nuevo."])}
    piezas = partir_glosario(glosario)
    check("el glosario se parte por termino", len(piezas) == 60,
          f"{len(piezas)} terminos")
    check("el termino queda como titulo",
          piezas[0]["titulo"] == "Termino numero 0")
    check("una 'Nota:' no abre un termino nuevo",
          "Nota:" in piezas[-1]["texto"])
    check("todas las definiciones conservan su numeral",
          {x["numeral"] for x in piezas} == {"1.2.1"})

    pocos = dict(glosario, texto="Uno: algo bastante largo aqui.\nDos: otra cosa.")
    check("si no reconoce suficientes terminos, no parte nada",
          partir_glosario(pocos) == [])

    # 6) Un glosario de verdad, de punta a punta.
    paginas = [
        "CAPITULO II\nDEFINICIONES Y ABREVIATURAS\n1.2.1. Definiciones.\n"
        + "\n".join(f"Concepto {i}: significado del concepto {i} para efectos "
                     f"de este reglamento." for i in range(70))
        + "\n1.944 Sobre Aviación Civil Internacional y sus anexos técnicos.\n"
        + "\n".join(f"Concepto {i}: significado del concepto {i} para efectos "
                     f"de este reglamento." for i in range(70, 90)),
        "1.2.2. Abreviaturas\n"
        + "\n".join(f"SIG{i}: sigla numero {i} usada en este reglamento y en "
                     f"los demas." for i in range(60)),
    ]
    ap = segmentar(paginas, "1", glosario=True)
    numerales = {a["numeral"] for a in ap}
    check("en modo glosario el articulado y las siglas quedan separados",
          {"1.2.1", "1.2.2"} <= numerales, f"numerales: {sorted(numerales)}")
    check("y el ano con separador de miles no creo un apartado",
          "1.944" not in numerales)
    check("cada definicion es un resultado propio",
          len([a for a in ap if a["numeral"] == "1.2.1"]) >= 85)


def probar_rac_busqueda_fina():
    print("\n== Reglamentos: consulta y orden de los resultados ==")
    import rac

    fts = rac.construir_consulta("que significa AIS")
    check("las muletillas de pregunta no entran en la busqueda",
          "significa" not in fts, fts)
    check("el termino original se conserva junto a sus sinonimos",
          "ais" in fts and "informacion" in fts, fts)

    fts2 = rac.construir_consulta("combustible para vuelo IFR")
    check("los sinonimos van en un grupo O, no exigidos todos a la vez",
          "(ifr OR instrumentos*)" in fts2, fts2)
    check("y los terminos distintos van unidos por Y explicito",
          " AND " in fts2, fts2)

    # FTS5 admite la Y implicita entre palabras, pero no en cuanto aparece un
    # parentesis: sin el AND explicito la consulta entera aborta.
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.execute("CREATE VIRTUAL TABLE t USING fts5(texto)")
    con.execute("INSERT INTO t VALUES ('combustible para vuelo por instrumentos')")
    for consulta in (fts, fts2, rac.construir_consulta("plan de vuelo"),
                     rac.construir_consulta('"casilla 18" alterno')):
        try:
            con.execute("SELECT COUNT(*) FROM t WHERE t MATCH ?", (consulta,)).fetchone()
            valido = True
        except sqlite3.OperationalError as e:
            valido = False
            print(f"       {consulta!r} -> {e}")
        check(f"FTS5 acepta la consulta generada: {consulta[:44]!r}", valido)

    # Con AND estricto, una pregunta en lenguaje natural puede dar un unico
    # resultado casual y esconder el que responde. Se completa con la busqueda
    # flexible, dejando arriba los aciertos exactos.
    filas = [
        {"apartado_id": 1, "rac": "91", "numeral": "91.310", "puntaje": -2.0},
        {"apartado_id": 2, "rac": "91", "numeral": "91.315", "puntaje": -9.0},
        {"apartado_id": 3, "rac": "91", "numeral": "91.320", "puntaje": -5.0},
    ]
    orden = [f["numeral"] for f in
             rac.reordenar(filas, "plan de vuelo", 5, exactos={1})]
    check("los aciertos exactos van primero aunque puntuen peor",
          orden[0] == "91.310", str(orden))
    check("y el resto queda ordenado por relevancia",
          orden[1:] == ["91.315", "91.320"], str(orden))

    muchos = [{"apartado_id": i, "rac": "1", "numeral": "1.2.1",
               "puntaje": -float(i)} for i in range(1, 8)]
    check("un mismo numeral no ocupa toda la pantalla",
          len(rac.reordenar(muchos, "plan de vuelo", 5)) == rac.TOPE_POR_NUMERAL)

    glosario = [{"apartado_id": 1, "rac": "1", "numeral": "1.2.1", "puntaje": -9.0},
                {"apartado_id": 2, "rac": "91", "numeral": "91.310", "puntaje": -6.0}]
    operativa = rac.reordenar([dict(f) for f in glosario], "cuando se presenta", 5)
    definicion = rac.reordenar([dict(f) for f in glosario], "que es un plan de vuelo", 5)
    check("en una pregunta operativa el articulado va delante del glosario",
          operativa[0]["rac"] == "91", operativa[0]["rac"])
    check("y en una pregunta por definicion, al reves",
          definicion[0]["rac"] == "1", definicion[0]["rac"])

    check("el portal avisa cuando completo los resultados",
          "completado_con_alguno" in (RAIZ / "index.html").read_text(encoding="utf-8"))

    check("una pregunta por definicion se detecta",
          rac.PREGUNTA_DE_DEFINICION.search("que significa AIS") is not None
          and rac.PREGUNTA_DE_DEFINICION.search(
              "con cuanta antelacion se presenta el plan") is None)

    # El usuario escribe con tildes. Sin quitarlas, "que es" no coincide y la
    # pregunta se trata como operativa: el glosario queda castigado justo
    # cuando es la respuesta. Se comprobo en el navegador con el indice real.
    con_tilde = rac.reordenar(
        [{"apartado_id": 1, "rac": "1", "numeral": "1.2.1", "puntaje": -9.0},
         {"apartado_id": 2, "rac": "91", "numeral": "91.310", "puntaje": -7.0}],
        "qué es un aeródromo de alternativa", 5)
    check("una pregunta con tildes tambien se reconoce como definicion",
          con_tilde[0]["rac"] == "1", con_tilde[0]["rac"])



# ---------------------------------------------------------------------------
# Radio del circulo en el visualizador de coordenadas
# ---------------------------------------------------------------------------
def probar_radio_coordenadas():
    print("\n== Coordenadas: deteccion del radio ==")
    html = (RAIZ / "index.html").read_text(encoding="utf-8")

    # Se prueban las expresiones tal como viajan en el portal, no una copia:
    # si alguien las edita en el HTML, estas pruebas lo ven.
    def sacar(nombre):
        m = re.search(nombre + r"\s*=\s*/(.+?)/i;", html)
        assert m, f"no se encontro {nombre} en index.html"
        return re.compile(m.group(1), re.IGNORECASE)

    con_centro = sacar("GEO_RADIO_CON_CENTRO")
    suelto = sacar("GEO_RADIO_SUELTO")
    metros = sacar("GEO_RADIO_METROS")

    def detectar(texto):
        m = con_centro.search(texto)
        if m:
            return m.group(1), m.group(2).upper(), m.group(3)
        m = suelto.search(texto) or metros.search(texto)
        if m:
            return m.group(1), m.group(2).upper(), None
        return None

    COORD = "084925.00N0755402.00W"

    # La forma natural al copiar de un NOTAM: el radio pegado a la coordenada.
    # Antes solo se reconocia "RADIO 5 NM CENTRO <coord>" y el resto se perdia
    # en silencio: el mapa pintaba un punto sin avisar de nada.
    for texto, valor in ((f"{COORD} 15NM", "15"), (f"{COORD} 15 NM", "15"),
                         (f"{COORD} R 5NM", "5"), (f"{COORD} RADIO 2.5 NM", "2.5"),
                         (f"{COORD} RADIUS 8NM", "8"), (f"15NM {COORD}", "15")):
        hallazgo = detectar(texto)
        check(f"detecta el radio en {texto[-14:]!r}",
              hallazgo is not None and hallazgo[0] == valor,
              str(hallazgo))

    check("sigue funcionando la forma con CENTRO",
          detectar(f"RADIO 5 NM CENTRO {COORD}") == ("5", "NM", COORD))
    check("y la inglesa con CENTRE",
          detectar(f"RADIUS 3NM CENTRE {COORD}") == ("3", "NM", COORD))

    check("acepta kilometros", detectar("10.9685, -74.7813 20KM")[1] == "KM")
    check("y el decimal con coma", detectar(f"{COORD} 15,5NM")[0] == "15,5")

    # Una coordenada sola no lleva radio, y un "1500M" suelto suele ser una
    # altitud: exigir la palabra RADIO evita dibujar un circulo inventado.
    check("una coordenada sin radio no inventa uno", detectar(COORD) is None)
    check("ni una coordenada decimal", detectar("10.9685, -74.7813") is None)
    check("un valor en metros sin la palabra RADIO se ignora",
          detectar(f"ALTITUD 1500M {COORD}") is None)
    check("pero con la palabra si se acepta",
          detectar(f"RADIO 1500 M CENTRO {COORD}")[1] == "M")

    check("un punto con radio se dibuja como circulo, no como punto",
          "(coords.length===1&&radio)?'circle'" in html)
    check("y si falta el radio se avisa en vez de callar",
          "hay que indicar el radio" in html)
    check("la ayuda documenta el formato nuevo",
          "084925.00N0755402.00W 15NM</b>" in html)



# ---------------------------------------------------------------------------
# NOTAMs pegados: corte, vigilancia y reparacion
# ---------------------------------------------------------------------------
def probar_notams_pegados():
    print("\n== NOTAMs pegados ==")
    sandbox = Path(tempfile.mkdtemp())
    shutil.copy(RAIZ / "extractor.py", sandbox / "extractor.py")
    cwd = os.getcwd()
    os.chdir(sandbox)
    sys.path.insert(0, str(sandbox))
    for modulo in ("extractor",):
        sys.modules.pop(modulo, None)
    import extractor as ex
    ex.configurar_logging()

    # Caso real del boletin: el C3425 de Barranquilla se quedo con el C3427
    # del FIR Bogota pegado detras. Lo delata "COMBATE NR3" justo antes de la
    # numeracion siguiente: parece una referencia y no lo es.
    pegado = (
        "C 3425 / 26 BARRANQUILLA/BARRANQUILLA-E. CORTISSOZ (SKBQ) "
        "2609051415 / 2609060730 SEP 05 1415-1525, SEP 06 0600-0730 , AD LTD, "
        "AVBL UNICAMENTE ACFT STS/HOSP, MEDEVAC, SAR, HEAD, RMK/OP. DEMAS "
        "AVIACION PREVIA COOR COMANDO AEREO DE COMBATE NR3 "
        "C 3427 / 26 FIR/UIR BOGOTA 2608191300 / 2608201800 1300-1800 , "
        "RESTRICCION DE ESPACIO AEREO ACT RADIO 0.8 NM CENTRO "
        "043408.06N0754504.79W GND TIL 5500FT FT AM")

    trozos = ex.dividir_en_notams(pegado)
    check("el C3425 y el C3427 quedan separados", len(trozos) == 2,
          f"{len(trozos)} trozos: {[t[1] for t in trozos]}")
    if len(trozos) == 2:
        check("cada uno con su indicador",
              [t[0] for t in trozos] == ["SKBQ", "SKED"], str([t[0] for t in trozos]))
        check("el del aerodromo no se queda con el texto del FIR",
              "RESTRICCION DE ESPACIO AEREO" not in trozos[0][2])
        check("y el del FIR conserva su coordenada",
              "043408.06N0754504.79W" in trozos[1][2])

    # Una referencia de verdad NO debe cortar: aqui "RPLC NOTAM C1208/26" es
    # una mencion al NOTAM que se reemplaza, no el comienzo de otro.
    referencia = (
        "C 1919 / 26 ARMENIA/EL EDEN (SKAR) 2606211641 / 2609172359 EST , "
        "TWR LTD, LAMPARA DE SENALES U/S RPLC NOTAM C1208/26")
    check("una referencia RPLC no parte el NOTAM",
          len(ex.dividir_en_notams(referencia)) == 1)

    # La vigilancia no depende de las reglas de corte: cuenta cabeceras.
    check("la vigilancia detecta el registro pegado",
          ex.contar_pegados([("SKBQ", "C3425/26", pegado)]) == [("C3425/26", 2)])
    check("y no marca uno bien cortado",
          ex.contar_pegados([("SKAR", "C1919/26", referencia)]) == [])

    # Reparseo de una base ya guardada, sin descargar nada.
    conexion = ex.crear_base_temporal()
    conexion.execute(
        "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
        ("SKBQ", "C3425/26", pegado))
    conexion.execute(
        "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
        ("SKAR", "C1919/26", referencia))
    conexion.commit()
    conexion.close()
    os.replace(ex.DB_TMP_PATH, ex.DB_PATH)

    rehechos = ex.reparsear_base()
    check("el reparseo convierte 2 registros en 3 NOTAMs", len(rehechos) == 3,
          str([r[1] for r in rehechos]))
    check("y despues no queda ninguno pegado", ex.contar_pegados(rehechos) == [])

    # Un registro que las reglas no reconocen se conserva: perder un NOTAM es
    # peor que dejarlo pegado. En la base real hay uno asi, truncado en origen.
    conexion = ex.crear_base_temporal()
    conexion.execute(
        "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
        ("OTROS", "C3129/26", "C 3129 / 26 04/26) RPLC NOTAM"))
    conexion.commit()
    conexion.close()
    os.replace(ex.DB_TMP_PATH, ex.DB_PATH)
    check("un registro irreconocible no se pierde",
          len(ex.reparsear_base()) == 1)

    # Windows no deja reemplazar un archivo que otro proceso tiene abierto: si
    # el portal esta sirviendo una consulta justo en ese instante, os.replace
    # falla con "Acceso denegado" y se pierde todo el trabajo del reparseo.
    conexion = ex.crear_base_temporal()
    conexion.execute(
        "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
        ("SKBQ", "C1/26", "C 1 / 26 X (SKBQ) 2609051415 / 2609060730 ALGO"))
    conexion.commit()
    conexion.close()

    replace_real = os.replace
    fallos = {"n": 0}

    def replace_ocupado(origen, destino):
        fallos["n"] += 1
        if fallos["n"] <= 2:      # ocupado las dos primeras veces
            raise PermissionError(5, "Acceso denegado")
        return replace_real(origen, destino)

    os.replace = replace_ocupado
    ex.time.sleep = lambda _s: None       # sin esperas reales en la prueba
    try:
        publicado = ex.publicar(1, forzar=True)
    finally:
        os.replace = replace_real
    check("si el archivo esta ocupado, reintenta y acaba publicando",
          publicado and fallos["n"] == 3, f"{fallos['n']} intentos")

    conexion = ex.crear_base_temporal()
    conexion.execute(
        "INSERT INTO notams (icao_code, notam_id, content) VALUES (?,?,?)",
        ("SKBQ", "C2/26", "C 2 / 26 X (SKBQ) 2609051415 / 2609060730 ALGO"))
    conexion.commit()
    conexion.close()

    def replace_siempre_ocupado(origen, destino):
        raise PermissionError(5, "Acceso denegado")

    os.replace = replace_siempre_ocupado
    try:
        publicado = ex.publicar(1, forzar=True)
    finally:
        os.replace = replace_real
    check("si nunca se libera, avisa y no revienta", publicado is False)
    check("y el trabajo hecho se conserva en el archivo temporal",
          ex.DB_TMP_PATH.exists())
    check("el mensaje dice que hay que cerrar el portal",
          "PORTAL.bat" in (RAIZ / "extractor.py").read_text(encoding="utf-8"))

    os.chdir(cwd)
    sys.path.remove(str(sandbox))
    sys.modules.pop("extractor", None)

    api = (RAIZ / "api_notams.py").read_text(encoding="utf-8")
    check("/health publica cuantos registros vienen pegados",
          "notams_pegados" in api and "--reparsear" in api)

    # Reparar la base no toca la red, pero selenium se importaba al principio
    # del archivo y lo exigia igual: en un equipo sin Chrome, --reparsear moria
    # con ModuleNotFoundError antes de ejecutar una linea util.
    fuente = (RAIZ / "extractor.py").read_text(encoding="utf-8")
    # Se busca un IMPORT de verdad al margen izquierdo, no una mencion: el
    # comentario que explica por que el import es diferido tambien nombra a
    # selenium, y una comprobacion por texto suelto se marcaria a si misma.
    import ast as _ast
    arbol = _ast.parse(fuente)
    al_cargar = set()
    for nodo in arbol.body:
        if isinstance(nodo, (_ast.Import, _ast.ImportFrom)):
            origen = getattr(nodo, "module", None) or ""
            al_cargar.add(origen.split(".")[0])
            for alias in nodo.names:
                al_cargar.add(alias.name.split(".")[0])
    check("selenium no se importa al cargar el modulo",
          not ({"selenium", "webdriver_manager"} & al_cargar),
          str(sorted(al_cargar)))
    check("se importa dentro de la funcion que descarga",
          "    from selenium import webdriver" in fuente)
    check("y si falta, el mensaje dice que --reparsear no lo necesita",
          "no la necesitan" in fuente)


if __name__ == "__main__":
    probar_extractor()
    probar_api()
    probar_fondo_video()
    probar_sistema_visual()
    probar_analisis_zona()
    probar_cierres()
    probar_directorio_ad()
    probar_parseo()
    probar_escape_html()
    probar_marca_version()
    probar_rac()
    probar_rac_casos_reales()
    probar_rac_busqueda_fina()
    probar_radio_coordenadas()
    probar_notams_pegados()
    print()
    if FALLOS:
        print(f">>> {len(FALLOS)} PRUEBA(S) FALLARON: {', '.join(FALLOS)}")
        sys.exit(1)
    print(">>> TODAS LAS PRUEBAS PASARON")
