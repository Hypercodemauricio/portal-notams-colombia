#!/usr/bin/env python3
"""
Descarga los RAC del gestor documental de la Aerocivil y construye
`sistema_rac.db`, la base que alimenta el buscador de reglamentos.

Uso:
    python3 rac_indexar.py                 descarga lo que falte e indexa todo
    python3 rac_indexar.py --solo-indexar  usa los PDF ya bajados en rac_pdf/
    python3 rac_indexar.py --rac 91 215    trabaja solo con esos reglamentos
    python3 rac_indexar.py --revisar       compara con el catalogo y no escribe

Se escribe primero en `sistema_rac.db.tmp` y solo al final se reemplaza la
base buena, igual que hace el extractor de NOTAMs: si el proceso muere a la
mitad, el portal sigue respondiendo con el indice anterior en vez de quedarse
con una base a medio construir.
"""

import argparse
import hashlib
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import pdfplumber

import rac_catalogo
from rac_segmentar import segmentar

BASE_DIR = Path(__file__).resolve().parent
CARPETA_PDF = BASE_DIR / os.getenv("RAC_PDF_DIR", "rac_pdf")
DESTINO = BASE_DIR / os.getenv("RAC_DB", "sistema_rac.db")
TEMPORAL = Path(str(DESTINO) + ".tmp")

INTENTOS = int(os.getenv("RAC_INTENTOS", "3"))
ESPERA = int(os.getenv("RAC_ESPERA", "5"))
APARTADOS_MINIMOS = int(os.getenv("RAC_APARTADOS_MINIMOS", "10"))

ESQUEMA = """
CREATE TABLE documentos (
    id            INTEGER PRIMARY KEY,
    rac           TEXT UNIQUE NOT NULL,
    titulo        TEXT NOT NULL,
    grupo         TEXT,
    fecha_version TEXT,
    fuente        TEXT,
    archivo       TEXT,
    sha256        TEXT,
    paginas       INTEGER,
    apartados     INTEGER,
    indexado_en   TEXT
);
CREATE TABLE apartados (
    id           INTEGER PRIMARY KEY,
    documento_id INTEGER NOT NULL REFERENCES documentos(id),
    rac          TEXT NOT NULL,
    numeral      TEXT NOT NULL,
    titulo       TEXT,
    capitulo     TEXT,
    texto        TEXT,
    pagina       INTEGER,
    orden        INTEGER
);
CREATE INDEX idx_apartados_rac ON apartados(rac, numeral);

-- remove_diacritics 2 hace que "aerodromo" encuentre "aeródromo". Sin esto,
-- media busqueda en espanol falla por una tilde.
CREATE VIRTUAL TABLE apartados_fts USING fts5(
    numeral, titulo, texto,
    content='apartados', content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);
-- Solo entran al indice los apartados CON CUERPO. Un encabezado de capitulo
-- vacio ("APENDICE 1 FORMULARIO DE PLAN DE VUELO") coincide palabra por
-- palabra con la busqueda "plan de vuelo" y se llevaba el primer puesto por
-- encima del numeral que si responde. Sigue guardado en `apartados` porque da
-- contexto de ubicacion, pero no compite en la busqueda.
CREATE TRIGGER apartados_ai AFTER INSERT ON apartados
WHEN length(trim(new.texto)) > 0 BEGIN
    INSERT INTO apartados_fts(rowid, numeral, titulo, texto)
    VALUES (new.id, new.numeral, new.titulo, new.texto);
END;
"""


def log(msg):
    print(f"{datetime.now():%H:%M:%S}  {msg}", flush=True)


# --------------------------------------------------------------------------
# Descarga
# --------------------------------------------------------------------------

def descargar(reglamento, forzar=False) -> Path:
    """
    Baja el PDF probando los hosts del catalogo en orden. Un PDF valido empieza
    por %PDF: sin esa comprobacion, cuando el portal responde con su pagina de
    error, guardariamos un HTML con extension .pdf y el fallo solo aparece
    despues, al intentar leerlo.
    """
    import requests  # solo se necesita al descargar

    CARPETA_PDF.mkdir(exist_ok=True)
    destino = CARPETA_PDF / reglamento.archivo
    if destino.exists() and not forzar:
        log(f"RAC {reglamento.rac}: ya estaba descargado")
        return destino

    ultimo_error = None
    for url in reglamento.urls():
        for intento in range(1, INTENTOS + 1):
            try:
                r = requests.get(url, timeout=90, headers={
                    "User-Agent": "portal-notams/1.0 (indexador RAC)"})
                r.raise_for_status()
                if not r.content.startswith(b"%PDF"):
                    raise ValueError(
                        f"la respuesta no es un PDF ({len(r.content)} bytes)")
                destino.write_bytes(r.content)
                log(f"RAC {reglamento.rac}: {len(r.content)//1024} KB desde "
                    f"{url.split('/')[2]}")
                return destino
            except Exception as e:  # noqa: BLE001
                ultimo_error = e
                log(f"RAC {reglamento.rac}: intento {intento} fallo ({e})")
                time.sleep(ESPERA * intento)
    raise RuntimeError(f"no se pudo descargar el RAC {reglamento.rac}: {ultimo_error}")


# --------------------------------------------------------------------------
# Indexado
# --------------------------------------------------------------------------

def leer_paginas(ruta: Path) -> list:
    with pdfplumber.open(ruta) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def indexar_documento(con, reglamento, ruta: Path) -> int:
    paginas = leer_paginas(ruta)
    apartados = segmentar(paginas, reglamento.rac,
                          glosario=getattr(reglamento, "glosario", False))

    if len(apartados) < APARTADOS_MINIMOS:
        raise ValueError(
            f"RAC {reglamento.rac}: solo se reconocieron {len(apartados)} "
            f"apartados en {len(paginas)} paginas. Probablemente el PDF es una "
            f"imagen escaneada o cambio la numeracion.")

    # Un apartado gigante casi siempre significa que se perdio un corte. No es
    # motivo para descartar el documento, pero tiene que verse en el log en
    # vez de quedarse escondido dentro de la base.
    enormes = [a for a in apartados if len(a["texto"]) > 12000]
    if enormes:
        log(f"RAC {reglamento.rac}: {len(enormes)} apartado(s) muy largos, "
            f"revisa el corte: "
            + ", ".join(f"{a['numeral']} ({len(a['texto'])//1000} KB)"
                        for a in enormes[:5]))

    sha = hashlib.sha256(ruta.read_bytes()).hexdigest()
    cur = con.execute(
        "INSERT INTO documentos (rac, titulo, grupo, fecha_version, fuente, "
        "archivo, sha256, paginas, apartados, indexado_en) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (reglamento.rac, reglamento.titulo, reglamento.grupo,
         reglamento.fecha_version, reglamento.urls()[0], ruta.name, sha,
         len(paginas), len(apartados), datetime.now().isoformat(timespec="seconds")))
    doc_id = cur.lastrowid

    con.executemany(
        "INSERT INTO apartados (documento_id, rac, numeral, titulo, capitulo, "
        "texto, pagina, orden) VALUES (?,?,?,?,?,?,?,?)",
        [(doc_id, reglamento.rac, a["numeral"], a["titulo"], a["capitulo"],
          a["texto"], a["pagina"], a["orden"]) for a in apartados])
    return len(apartados)


def construir(reglamentos, solo_indexar=False, forzar=False) -> dict:
    if TEMPORAL.exists():
        TEMPORAL.unlink()
    con = sqlite3.connect(TEMPORAL)
    con.executescript(ESQUEMA)

    resumen = {"indexados": [], "fallidos": [], "apartados": 0}
    for reglamento in reglamentos:
        try:
            ruta = CARPETA_PDF / reglamento.archivo
            if not solo_indexar:
                ruta = descargar(reglamento, forzar=forzar)
            if not ruta.exists():
                raise FileNotFoundError(f"falta {ruta.name} en {CARPETA_PDF}")
            n = indexar_documento(con, reglamento, ruta)
            con.commit()
            resumen["indexados"].append((reglamento.rac, n))
            resumen["apartados"] += n
            log(f"RAC {reglamento.rac}: {n} apartados")
        except Exception as e:  # noqa: BLE001
            con.rollback()
            resumen["fallidos"].append((reglamento.rac, str(e)))
            log(f"RAC {reglamento.rac}: NO indexado -> {e}")

    con.execute("INSERT INTO apartados_fts(apartados_fts) VALUES ('optimize')")
    con.commit()
    con.close()

    if not resumen["indexados"]:
        TEMPORAL.unlink(missing_ok=True)
        raise RuntimeError("ningun reglamento se pudo indexar; no se toca la "
                           "base existente")

    if DESTINO.exists():
        respaldo = Path(str(DESTINO) + ".prev")
        respaldo.unlink(missing_ok=True)
        DESTINO.replace(respaldo)
    os.replace(TEMPORAL, DESTINO)
    return resumen


def main():
    p = argparse.ArgumentParser(description="Indexador de los RAC")
    p.add_argument("--solo-indexar", action="store_true",
                   help="no descarga; usa los PDF que ya estan en rac_pdf/")
    p.add_argument("--forzar-descarga", action="store_true",
                   help="vuelve a bajar los PDF aunque ya existan")
    p.add_argument("--rac", nargs="*", help="numeros de RAC a procesar")
    p.add_argument("--revisar", action="store_true",
                   help="muestra que hay en el catalogo y en disco, sin escribir")
    args = p.parse_args()

    reglamentos = rac_catalogo.CATALOGO
    if args.rac:
        pedidos = {str(x) for x in args.rac}
        reglamentos = [r for r in reglamentos if r.rac in pedidos]
        faltan = pedidos - {r.rac for r in reglamentos}
        if faltan:
            log(f"No estan en el catalogo: {', '.join(sorted(faltan))}")

    if args.revisar:
        for r in reglamentos:
            ruta = CARPETA_PDF / r.archivo
            estado = f"{ruta.stat().st_size//1024} KB" if ruta.exists() else "falta"
            print(f"  RAC {r.rac:<4} {r.fecha_version}  {estado:>9}  {r.titulo}")
        return 0

    resumen = construir(reglamentos, solo_indexar=args.solo_indexar,
                        forzar=args.forzar_descarga)
    log(f"Listo: {len(resumen['indexados'])} reglamentos, "
        f"{resumen['apartados']} apartados -> {DESTINO.name}")
    if resumen["fallidos"]:
        log("Quedaron fuera: " + ", ".join(r for r, _ in resumen["fallidos"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
